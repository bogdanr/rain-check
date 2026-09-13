"""Task 37: whose probability is the headline actually measuring?

Track A pins its model explicitly (config.py:47-52 states the policy and
collect_archive.py:72 implements it). Track B does not: collect_archive.py:82-85
requests `precipitation_probability` with no `models` parameter, so the archive
is served by Open-Meteo's `best_match`, which is free to change which model
backs a location at any time.

That would be harmless if PoP were model-independent. It is not. A ten-day
probe at Bucharest returns mean PoP 2.55% for icon_eu, 6.54% for ecmwf_ifs025
and 12.56% for gfs_seamless over identical hours - a factor of five. So the
question is not cosmetic: if `best_match` switched provider mid-archive, the
headline reliability curve splices two different forecasters together and the
S-shape could be an artefact of the join date rather than a property of anyone's
forecast.

This module answers three things with data rather than assertion:

  1. Which pinned model does `best_match` actually equal, month by month, over
     the whole archive? A switch shows up as agreement transferring from one
     column to another.
  2. Does the headline conclusion survive being recomputed on explicitly pinned
     models - same days, same observations, same binning?
  3. Is the low-bin wet-bias inversion (Task 26) a property of one provider or
     of all of them?

Usage:  python src/pop_provenance.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from calibration import block_bootstrap_ci, brier_decomposition, reliability_table
from config import (
    API_HISTORICAL_FORECAST,
    ARCHIVE_END,
    LATITUDE,
    LOCAL_TZ,
    LONGITUDE,
    N_PROB_BINS,
    OBS_END,
    PRIMARY_STATION,
    PROCESSED,
    RAIN_THRESHOLD_MM,
    RAW,
)
from constants import POP_ARCHIVE_START
from fetch import fetch_json, is_error, reason

# The models worth pinning. ecmwf_ifs025 is the study's primary (Track A uses
# it, so pinning Track B to it makes the two tracks describe one forecaster);
# icon_eu is the established comparator; gfs_seamless is included because the
# probe showed it is the extreme of the spread and therefore the strongest test
# of whether provider choice matters.
PINNED_MODELS = ["ecmwf_ifs025", "icon_eu", "gfs_seamless"]

# Below this PoP a day counts as "the forecast said it would stay dry". Matches
# the low bin used by the Task 26 wet-bias test so the two are comparable.
LOW_POP = 0.10
HIGH_POP = 0.60


def _chunks(start: str, end: str, months: int = 6):
    from datetime import date, timedelta
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    while s <= e:
        nxt = min(e, s + timedelta(days=months * 30))
        yield s.isoformat(), nxt.isoformat()
        s = nxt + timedelta(days=1)


def fetch_pinned(model: str) -> pd.DataFrame:
    """Hourly PoP for one explicitly pinned model over the Track B archive."""
    cache = RAW / f"hist_pop_{model}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)

    frames = []
    for s, e in _chunks(POP_ARCHIVE_START, ARCHIVE_END):
        payload = fetch_json(API_HISTORICAL_FORECAST, {
            "latitude": LATITUDE, "longitude": LONGITUDE, "timezone": "UTC",
            "hourly": "precipitation_probability,precipitation",
            "models": model, "start_date": s, "end_date": e,
        })
        if is_error(payload):
            print(f"  pop[{model}] {s}..{e}: SKIP ({reason(payload)})")
            continue
        df = pd.DataFrame(payload["hourly"])
        df["time"] = pd.to_datetime(df["time"], utc=True)
        frames.append(df)
        print(f"  pop[{model}] {s}..{e}: {len(df)} hours")
    if not frames:
        raise RuntimeError(f"no data for {model}")
    out = pd.concat(frames).drop_duplicates(subset="time").sort_values("time")
    out.to_parquet(cache, index=False)
    return out


# ---------------------------------------------------------------------------
# 1. What is best_match, month by month?
# ---------------------------------------------------------------------------
def agreement_table(bm: pd.DataFrame, pinned: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per-month fraction of hours where best_match equals each pinned model.

    Exact equality, not correlation: PoP is reported in whole percent, so if
    best_match *is* a given model the two series are byte-identical. A value
    that is high but not 1.0 is the interesting case - it means the series
    agree most of the time but not always, which correlation would hide.
    """
    base = bm[["time", "precipitation_probability"]].rename(
        columns={"precipitation_probability": "bm"})
    for m, df in pinned.items():
        base = base.merge(
            df[["time", "precipitation_probability"]].rename(
                columns={"precipitation_probability": m}),
            on="time", how="left")

    base["month"] = base.time.dt.to_period("M").astype(str)
    rows = []
    for month, g in base.groupby("month"):
        row = {"month": month, "n_hours": len(g)}
        for m in pinned:
            both = g[["bm", m]].dropna()
            row[m] = float((both.bm == both[m]).mean()) if len(both) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def describe_agreement(agr: pd.DataFrame) -> dict:
    """Reduce the monthly table to a verdict about provider stability."""
    models = [c for c in agr.columns if c not in ("month", "n_hours")]
    winner = agr[models].idxmax(axis=1)
    best = agr[models].max(axis=1)
    # A month is "resolved" if some pinned model matches nearly every hour.
    resolved = best >= 0.98
    switches = int((winner[resolved].shift() != winner[resolved]).sum() - 1) if resolved.any() else 0
    return {
        "identified_as": winner[resolved].mode().iat[0] if resolved.any() else None,
        "months_resolved": int(resolved.sum()),
        "months_total": len(agr),
        "switches": max(0, switches),
        "min_agreement": float(best.min()),
    }


# ---------------------------------------------------------------------------
# 2. Does the headline survive pinning?
# ---------------------------------------------------------------------------
def _daily(df: pd.DataFrame) -> pd.DataFrame:
    """Same aggregation contract as build_dataset.build_pop_table."""
    d = df.copy()
    d["local_date"] = d["time"].dt.tz_convert(LOCAL_TZ).dt.date
    daily = d.groupby("local_date").agg(
        forecast_prob_max=("precipitation_probability", "max"),
        n_hours=("time", "count"),
        n_prob_null=("precipitation_probability", lambda s: int(s.isna().sum())),
    ).reset_index()
    daily = daily[(daily.n_hours == 24) & (daily.n_prob_null == 0)].copy()
    daily["forecast_prob"] = daily.forecast_prob_max / 100.0
    return daily[["local_date", "forecast_prob"]]


def _observations() -> pd.DataFrame:
    obs = pd.read_parquet(RAW / "station_observations.parquet")
    obs = obs[(obs.station == PRIMARY_STATION)
              & (obs.local_date <= pd.Timestamp(OBS_END).date())]
    out = obs[["local_date", "prcp"]].dropna().copy()
    out["observed_event"] = out.prcp >= RAIN_THRESHOLD_MM
    return out[["local_date", "observed_event"]]


def score(series: pd.DataFrame, label: str, obs: pd.DataFrame,
          common: set | None = None) -> dict:
    j = series.merge(obs, on="local_date", how="inner")
    if common is not None:
        j = j[j.local_date.isin(common)]
    j = j.sort_values("local_date")
    prob = j.forecast_prob.values
    event = j.observed_event.values.astype(float)
    dec = brier_decomposition(prob, event, n_bins=N_PROB_BINS)

    lo = j[j.forecast_prob <= LOW_POP]
    hi = j[j.forecast_prob >= HIGH_POP]
    return {
        "model": label,
        "n": dec["n"],
        "mean_pop": float(prob.mean()),
        "base_rate": dec["base_rate"],
        "brier": dec["brier"],
        "bss": dec["brier_skill_score"],
        "reliability": dec["reliability"],
        "resolution": dec["resolution"],
        "identity_error": dec["identity_error"],
        "n_low": len(lo),
        "low_stated": float(lo.forecast_prob.mean()) if len(lo) else np.nan,
        "low_observed": float(lo.observed_event.mean()) if len(lo) else np.nan,
        "n_high": len(hi),
        "high_stated": float(hi.forecast_prob.mean()) if len(hi) else np.nan,
        "high_observed": float(hi.observed_event.mean()) if len(hi) else np.nan,
    }


# ---------------------------------------------------------------------------
# 5. Is best_match the same model in every capital? (Task 38)
# ---------------------------------------------------------------------------
# Open-Meteo picks best_match per location, preferring whichever high-resolution
# regional model covers that point. Bucharest and Reykjavik are not covered by
# the same regional model, so the Phase 7 league table may be ranking different
# forecasters against each other while presenting the result as a difference
# between cities. This samples three widely separated months rather than the
# full archive: enough to identify the provider and to catch a mid-archive
# switch, at a fraction of the request cost.
PROBE_WINDOWS = [("2024-05-01", "2024-05-31"),
                 ("2025-06-01", "2025-06-30"),
                 ("2026-07-01", "2026-07-31")]

# The candidate pool is wider here than for Bucharest: the point is to catch
# regional models that cover only part of Europe.
CANDIDATE_MODELS = ["icon_eu", "icon_d2", "ecmwf_ifs025", "gfs_seamless",
                    "meteofrance_arome_france_hd", "ukmo_uk_deterministic_2km",
                    "knmi_harmonie_arome_europe", "dmi_harmonie_arome_europe",
                    "metno_seamless", "arpae_cosmo_5m"]


def _probe_hours(lat: float, lon: float, model: str | None) -> pd.Series:
    """PoP over the three probe windows, indexed by timestamp. Empty on failure."""
    out = []
    for s, e in PROBE_WINDOWS:
        params = {"latitude": round(lat, 4), "longitude": round(lon, 4),
                  "timezone": "UTC", "hourly": "precipitation_probability",
                  "start_date": s, "end_date": e}
        if model:
            params["models"] = model
        payload = fetch_json(API_HISTORICAL_FORECAST, params)
        if is_error(payload):
            continue
        h = payload["hourly"]
        out.append(pd.Series(h["precipitation_probability"], index=h["time"]))
    return pd.concat(out) if out else pd.Series(dtype=float)


def capitals_provenance() -> pd.DataFrame:
    """Which model backs best_match in each capital, and does it hold steady?"""
    from config import load_capitals

    cities = load_capitals()
    rows = []
    for name, city in sorted(cities.items()):
        bm = _probe_hours(city.latitude, city.longitude, None)
        if bm.empty:
            print(f"  {name:14s} best_match fetch failed")
            continue

        best_model, best_frac, per_window = None, 0.0, {}
        for m in CANDIDATE_MODELS:
            cand = _probe_hours(city.latitude, city.longitude, m)
            if cand.empty:
                continue
            common = bm.index.intersection(cand.index)
            if len(common) < 100:
                continue
            frac = float((bm[common] == cand[common]).mean())
            if frac > best_frac:
                # Per-window agreement separates "this model all year" from
                # "this model in summer only".
                w = []
                for s, _e in PROBE_WINDOWS:
                    sel = [t for t in common if t.startswith(s[:7])]
                    w.append(float((bm[sel] == cand[sel]).mean()) if sel else np.nan)
                best_model, best_frac = m, frac
                per_window = dict(zip([s[:7] for s, _ in PROBE_WINDOWS], w))

        rows.append({
            "city": name,
            "best_match_is": best_model if best_frac >= 0.98 else "unidentified",
            "agreement": best_frac,
            **{f"w_{k}": v for k, v in per_window.items()},
        })
        print(f"  {name:14s} -> {rows[-1]['best_match_is']:28s} "
              f"agreement={best_frac:.3f}")

    out = pd.DataFrame(rows)
    out.to_parquet(PROCESSED / "pop_provenance_capitals.parquet", index=False)
    return out


def main_capitals() -> None:
    print("=== Task 38: which model backs best_match in each capital? ===\n")
    out = capitals_provenance()
    print("\n" + out.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    ident = out[out.best_match_is != "unidentified"]
    n_models = ident.best_match_is.nunique()
    print(f"\n  capitals identified : {len(ident)}/{len(out)}")
    print(f"  distinct models     : {n_models}")
    if n_models > 1:
        print("  distribution        : "
              + ", ".join(f"{k}={v}" for k, v in
                          ident.best_match_is.value_counts().items()))
        print("\n  -> The league table compares DIFFERENT forecasters. A city's rank\n"
              "     confounds how predictable its weather is with which model\n"
              "     Open-Meteo happens to route it to. The ranking must either pin\n"
              "     one model everywhere, or name the backing model beside the rank.")
    else:
        print("  -> One model backs every capital; the ranking compares like with like.")

    wcols = [c for c in out.columns if c.startswith("w_")]
    if wcols:
        unstable = out[(out[wcols].min(axis=1) < 0.9) & (out.agreement >= 0.5)]
        if len(unstable):
            print("\n  Provider appears to change mid-archive in: "
                  + ", ".join(unstable.city))
        else:
            print("\n  No mid-archive provider switch detected in any capital.")
    print("\nwrote data/processed/pop_provenance_capitals.parquet")


def main() -> None:
    print("=== Task 37: Track B provider provenance ===\n")

    bm = pd.read_parquet(RAW / "historical_forecast_pop.parquet")
    bm["time"] = pd.to_datetime(bm["time"], utc=True)
    pinned = {m: fetch_pinned(m) for m in PINNED_MODELS}
    for df in pinned.values():
        df["time"] = pd.to_datetime(df["time"], utc=True)

    # --- 1. identity of best_match ---------------------------------------
    agr = agreement_table(bm, pinned)
    verdict = describe_agreement(agr)
    agr.to_parquet(PROCESSED / "pop_provenance_agreement.parquet", index=False)

    print("Monthly agreement between the stored best_match series and each "
          "pinned model\n(fraction of hours with an identical value):\n")
    print(agr.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    print(f"\n  best_match resolves to : {verdict['identified_as']}")
    print(f"  months resolved        : {verdict['months_resolved']}/{verdict['months_total']}")
    print(f"  provider switches      : {verdict['switches']}")
    print(f"  worst monthly match    : {verdict['min_agreement']:.3f}")
    if verdict["switches"] == 0 and verdict["months_resolved"] == verdict["months_total"]:
        print("  -> The unpinned series is a single model throughout. The headline\n"
              "     is not spliced; the provenance gap is a documentation defect,\n"
              "     not a data defect. It is still a live risk for any re-run.")
    else:
        print("  -> The unpinned series changes provider mid-archive. The headline\n"
              "     reliability curve splices forecasters and must be recomputed\n"
              "     on a pinned model.")

    # --- 2. headline under each pinned model ------------------------------
    obs = _observations()
    dailies = {"best_match (as published)": _daily(bm)}
    for m, df in pinned.items():
        dailies[m] = _daily(df)

    # Score every model on the identical set of days, so differences are the
    # forecast rather than the sample.
    common = set.intersection(*[
        set(d.merge(obs, on="local_date").local_date) for d in dailies.values()])
    rows = [score(d, lbl, obs, common) for lbl, d in dailies.items()]
    res = pd.DataFrame(rows)
    res.to_parquet(PROCESSED / "pop_provenance_scores.parquet", index=False)

    print(f"\n\nHeadline recomputed on {len(common)} days common to all four series:\n")
    show = res[["model", "n", "mean_pop", "base_rate", "brier", "bss",
                "reliability", "resolution"]]
    print(show.to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    pub = res[res.model.str.startswith("best_match")].iloc[0]
    spread = res.bss.max() - res.bss.min()
    print(f"\n  BSS spread across providers : {spread:.3f} "
          f"({res.loc[res.bss.idxmin(), 'model']} {res.bss.min():.3f} .. "
          f"{res.loc[res.bss.idxmax(), 'model']} {res.bss.max():.3f})")
    print(f"  published series            : {pub.bss:.3f}")
    print(f"  mean PoP spread             : {res.mean_pop.min():.3f} .. "
          f"{res.mean_pop.max():.3f} against a base rate of {pub.base_rate:.3f}")

    # --- 3. is the wet-bias inversion provider-specific? ------------------
    print("\n\nWet-bias test (Task 26) repeated per provider:\n")
    print(f"{'model':26s} {'n_low':>6s} {'stated':>7s} {'rained':>7s} {'gap':>8s} "
          f"| {'n_high':>6s} {'stated':>7s} {'rained':>7s} {'gap':>8s}")
    for _, r in res.iterrows():
        lg = r.low_observed - r.low_stated
        hg = r.high_observed - r.high_stated
        print(f"{r.model:26s} {r.n_low:6.0f} {r.low_stated:7.3f} "
              f"{r.low_observed:7.3f} {lg:+8.3f} | {r.n_high:6.0f} "
              f"{r.high_stated:7.3f} {r.high_observed:7.3f} {hg:+8.3f}")

    inverted = int(((res.low_observed - res.low_stated) > 0).sum())
    print(f"\n  low-bin under-forecasting present in {inverted}/{len(res)} providers")
    if inverted == len(res):
        print("  -> The inversion is a property of the forecast chain at this site,\n"
              "     not of whichever model best_match happened to serve.")

    # --- 4. how much of the difference is just sharpness? -----------------
    print("\n\nPer-provider reliability curve (equal-count bins):\n")
    for lbl, d in dailies.items():
        j = d.merge(obs, on="local_date")
        j = j[j.local_date.isin(common)].sort_values("local_date")
        tbl = reliability_table(j.forecast_prob.values,
                                j.observed_event.values.astype(float),
                                n_bins=5)
        cells = "  ".join(f"{r.mean_prob:.2f}->{r.obs_freq:.2f}(n={int(r.n)})"
                          for _, r in tbl.iterrows())
        print(f"  {lbl:26s} {cells}")

    print("\nwrote data/processed/pop_provenance_agreement.parquet")
    print("wrote data/processed/pop_provenance_scores.parquet")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("bucharest", "all"):
        main()
    if cmd in ("capitals", "all"):
        print()
        main_capitals()
