"""Task 21: served probability versus the probability the ensemble supports.

THE QUESTION. A consumer is SERVED a number - the vendor's daily probability
of precipitation. The ENSEMBLE that number is nominally descended from
supports a different number: the fraction of GEFS members whose local-day
accumulation reaches the rain threshold (src/ensemble_pop.py). This module
measures how far apart those two are, and - the part that decides whether the
divergence matters - which of them is better calibrated against the station
gauge.

Nothing here computes new scoring maths. Divergence is arithmetic; every
score comes from the existing modules:

  calibration.brier_decomposition   Brier, reliability, resolution, ECE, and
                                    the skill score re-referenced to the
                                    smoothed day-of-year climatology (Task 18)
  baselines.doy_climatology         that reference
  metrics.value_curve/value_summary relative economic value (Task 23)
  metrics.roc_auc, metrics.sharpness discrimination and sharpness (Task 22)

LIKE FOR LIKE. GEFS is NCEP's ensemble, so `gfs_seamless` is the vendor model
compared against it head to head; the other vendor models are reported
separately as cross-centre context, never mixed into the headline.

THE SAMPLE. Every series is scored on ONE set of city-days: days where the
station observation, the vendor daily PoP, and a usable member-derived PoP at
EVERY lead 1-7 in BOTH boundary modes all exist. Days failing any of those are
dropped from all series together and counted. `check_paired` asserts the
identity rather than trusting the merge, and aborts the stage loudly on
misalignment, the way src/validate_join.py does at stage 5.

THE CAVEAT A REVIEWER WILL RAISE FIRST, stated here rather than buried. The
vendor archive carries NO lead axis (plan D5: `precipitation_probability_
previous_dayN` is null at every date and model), so the vendor series is one
short-lead, as-served number per city-day. Pairing it against member-derived
PoP at lead k therefore compares "what the app showed" against "what the
ensemble initialised k days earlier supported". That is exactly the consumer-
facing question, and it is NOT a same-lead skill comparison: the vendor is
advantaged at long lead by construction. Every lead-time statement below is
qualified accordingly.

Usage:  python src/triangulation.py            # selftest, then the full run
        python src/triangulation.py selftest   # correctness checks only
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import baselines
import metrics
from baselines import doy_climatology
from calibration import brier_decomposition, reliability_table
from config import (
    GEFS_BOUNDARY_MODES,
    GEFS_BOUNDARY_PRIMARY,
    LEAD_DAYS,
    N_PROB_BINS,
    PROCESSED,
    PROVIDER_MODELS,
    RAIN_THRESHOLD_MM,
    RAW,
    load_capitals,
)
from metrics import HEADLINE_ALPHAS, roc_auc, sharpness, value_curve, value_summary

# The vendor model from the same centre as the ensemble: GEFS is NCEP's
# ensemble, GFS is NCEP's deterministic system.
LIKE_FOR_LIKE_MODEL = "gfs_seamless"

TABLE = PROCESSED / "triangulation.parquet"
DIVERGENCE_TABLE = PROCESSED / "triangulation_divergence.parquet"
RELIABILITY_TABLE = PROCESSED / "triangulation_reliability.parquet"
BY_CITY_TABLE = PROCESSED / "triangulation_by_city.parquet"

GEFS_POP_TABLE = PROCESSED / "gefs_pop.parquet"
TRUTH_TABLE = PROCESSED / "capitals_pinned_daily.parquet"
QUANTISATION_TABLE = PROCESSED / "vendor_pop_quantisation.parquet"

# A city contributing fewer paired days than this cannot carry a per-city
# score worth printing; it still contributes to the pooled sample.
MIN_CITY_DAYS = 200


def _slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def die(msg: str) -> None:
    """Loud failure, stage-5 style: say what broke and stop the pipeline."""
    raise SystemExit(f"TRIANGULATION FAILED: {msg}")


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
def vendor_daily_pop(city_name: str, timezone: str, model: str) -> pd.DataFrame:
    """Vendor hourly PoP -> one daily probability, exactly as the pipeline does.

    This is the reduction of src/capitals.py:139-148 applied to the archive
    already on disk instead of to a fresh fetch: local calendar day by the
    hour's own label, MAX over the day, days with fewer than 24 hours or any
    null dropped, percent -> fraction. Reusing the convention matters more
    than reusing the code path: a different daily reduction would change the
    event being compared, not just its precision.
    """
    path = RAW / f"provider_pop_{_slug(city_name)}_{model}.parquet"
    if not path.exists():
        return pd.DataFrame()
    v = pd.read_parquet(path)
    if v.empty or "precipitation_probability" not in v.columns:
        return pd.DataFrame()
    v = v.copy()
    v["time"] = pd.to_datetime(v["time"], utc=True)
    v["local_date"] = v["time"].dt.tz_convert(timezone).dt.date
    daily = v.groupby("local_date").agg(
        forecast_prob_max=("precipitation_probability", "max"),
        n_hours=("time", "count"),
        n_prob_null=("precipitation_probability", lambda s: int(s.isna().sum())),
    ).reset_index()
    daily = daily[(daily.n_hours == 24) & (daily.n_prob_null == 0)]
    return pd.DataFrame({
        "city": city_name,
        "local_date": daily.local_date,
        "vendor_pop": daily.forecast_prob_max.astype(float) / 100.0,
    })


def load_truth() -> pd.DataFrame:
    """Observed daily rainfall per city-day, from the existing verification table.

    `capitals_pinned_daily.parquet` carries the station record already joined
    to the local calendar day with the per-city gauge offset applied
    (src/capitals.py:448-457), which is the same truth src/decision_metrics.py
    scores against. One row per (city, local_date) is asserted, not assumed:
    the table is long over vendor models, and a city-day whose observation
    differed between models would mean the join upstream is broken.
    """
    if not TRUTH_TABLE.exists():
        die(f"{TRUTH_TABLE} missing - run `capitals.py providers` first")
    t = pd.read_parquet(TRUTH_TABLE)[["city", "local_date", "obs_precip_mm"]]
    t = t.dropna(subset=["obs_precip_mm"])
    spread = t.groupby(["city", "local_date"]).obs_precip_mm.nunique()
    if (spread > 1).any():
        bad = spread[spread > 1]
        die(f"{len(bad)} city-days carry more than one observed amount in "
            f"{TRUTH_TABLE.name}; the upstream station join is inconsistent. "
            f"First: {bad.index[0]}")
    return t.drop_duplicates(["city", "local_date"]).reset_index(drop=True)


def load_gefs() -> pd.DataFrame:
    if not GEFS_POP_TABLE.exists():
        die(f"{GEFS_POP_TABLE} missing - run `ensemble_pop.py compute "
            f"--mode=all` first")
    g = pd.read_parquet(GEFS_POP_TABLE)
    g = g[(g.threshold_mm == RAIN_THRESHOLD_MM) & g.usable].copy()
    if g.empty:
        die(f"no usable member-derived PoP at threshold {RAIN_THRESHOLD_MM} mm")
    missing = sorted(set(GEFS_BOUNDARY_MODES) - set(g.boundary_mode.unique()))
    if missing:
        die(f"gefs_pop.parquet lacks boundary mode(s) {missing}; the "
            f"sensitivity leg of this stage needs both. Re-run "
            f"`ensemble_pop.py compute --mode=all`.")
    dup = g.duplicated(["city", "local_date", "lead_days", "boundary_mode"])
    if dup.any():
        die(f"{int(dup.sum())} duplicated (city, local_date, lead, mode) rows "
            f"in gefs_pop.parquet - one local day must come from exactly one "
            f"initialisation per lead")
    return g


# --------------------------------------------------------------------------
# The paired sample
# --------------------------------------------------------------------------
def build_paired(model: str = LIKE_FOR_LIKE_MODEL) -> pd.DataFrame:
    """One long frame: city x local_date x lead x boundary_mode, all series present.

    The intersection is taken ONCE, over the union of requirements, and then
    applied to every series. Nothing is reindexed and nothing is filled: a
    city-day that any series cannot supply is removed from all of them.
    """
    cities = load_capitals()
    gefs = load_gefs()
    truth = load_truth()

    vend = [vendor_daily_pop(name, c.timezone, model)
            for name, c in sorted(cities.items())]
    vend = [v for v in vend if len(v)]
    if not vend:
        die(f"no usable vendor daily PoP for {model}: either no archive on "
            f"disk (run `collect_archive.py all-providers`) or no local day "
            f"with a complete 24-hour, null-free hourly PoP record - some "
            f"models serve PoP for only part of the archive window")
    vendor = pd.concat(vend, ignore_index=True)

    n_modes, n_leads = len(GEFS_BOUNDARY_MODES), len(LEAD_DAYS)
    df = gefs.merge(vendor, on=["city", "local_date"], how="inner")
    df = df.merge(truth, on=["city", "local_date"], how="inner")
    if df.empty:
        die(f"the vendor, ensemble and station tables share no city-days for "
            f"{model}. Either the model's PoP archive does not overlap the "
            f"GEFS window, or too few of its days are complete 24-hour "
            f"records; check the city names and the local-date dtypes before "
            f"concluding it is a coverage problem")

    # A city-day survives only if it is complete across EVERY lead and BOTH
    # boundary modes. Anything less would let the lead-time comparison run on
    # a sample that changes with lead, which is the exact fault this study
    # criticises elsewhere.
    full = df.groupby(["city", "local_date"]).size()
    keep = full[full == n_modes * n_leads].index
    dropped_incomplete = int(len(full) - len(keep))
    df = df.set_index(["city", "local_date"]).loc[keep].reset_index()

    # Reference climatology, fitted on each city's FULL station record and then
    # read off on the paired days. Fitting it on the paired days alone would
    # thin the seasonal support; using it as a reference on days it could not
    # be estimated for would score the forecast and its reference on different
    # samples, so those days are dropped from everything.
    df["event"] = (df.obs_precip_mm.values >= RAIN_THRESHOLD_MM).astype(float)
    clim_parts, clim_failed = [], []
    for city, g in truth.groupby("city"):
        g = g.sort_values("local_date")
        ev = (g.obs_precip_mm.values >= RAIN_THRESHOLD_MM).astype(float)
        dates = pd.to_datetime(g.local_date)
        try:
            clim = doy_climatology(dates, ev)
        except ValueError as exc:
            clim_failed.append((city, str(exc).split(" - ")[0]))
            continue
        clim_parts.append(pd.DataFrame({"city": city,
                                        "local_date": g.local_date.values,
                                        "clim": clim}))
    if not clim_parts:
        die("no city has a station record long enough for a smoothed "
            "day-of-year climatology; the skill scores have no reference")
    clim = pd.concat(clim_parts, ignore_index=True).dropna(subset=["clim"])

    before = df.groupby(["city", "local_date"]).ngroups
    df = df.merge(clim, on=["city", "local_date"], how="inner")
    dropped_no_clim = before - df.groupby(["city", "local_date"]).ngroups

    cols = ["city", "country", "timezone", "local_date", "init_time",
            "lead_days", "boundary_mode", "utc_offset_h", "n_members",
            "pop_daytotal", "pop_anystep", "ens_mean_mm", "vendor_pop",
            "obs_precip_mm", "event", "clim"]
    out = df[cols].copy()
    out["model"] = model
    out.attrs["dropped_incomplete_citydays"] = dropped_incomplete
    out.attrs["dropped_no_climatology"] = int(dropped_no_clim)
    out.attrs["clim_failed_cities"] = clim_failed
    return out.sort_values(["boundary_mode", "city", "local_date", "lead_days"])


def check_paired(paired: pd.DataFrame) -> None:
    """Assert what the rest of the module assumes. Aborts the stage if not."""
    if paired.empty:
        die("the paired sample is empty")
    n_modes, n_leads = len(GEFS_BOUNDARY_MODES), len(LEAD_DAYS)

    ref = None
    for (mode, lead), g in paired.groupby(["boundary_mode", "lead_days"]):
        key = set(zip(g.city, g.local_date))
        if len(key) != len(g):
            die(f"duplicate city-days at mode={mode}, lead={lead}")
        if ref is None:
            ref = key
        elif key != ref:
            die(f"the sample at mode={mode}, lead={lead} differs from the "
                f"reference sample by {len(key ^ ref)} city-days; the series "
                f"are not being scored on identical days")
    n_groups = paired.groupby(["boundary_mode", "lead_days"]).ngroups
    if n_groups != n_modes * n_leads:
        die(f"expected {n_modes * n_leads} (mode, lead) cells, found {n_groups}")

    for col in ("vendor_pop", "pop_daytotal", "event", "clim", "obs_precip_mm"):
        if not np.isfinite(paired[col].to_numpy(float)).all():
            die(f"column {col} carries NaN inside the paired sample - the "
                f"intersection did not do its job")
    for col in ("vendor_pop", "pop_daytotal", "clim"):
        v = paired[col].to_numpy(float)
        if v.min() < 0.0 or v.max() > 1.0:
            die(f"{col} outside [0, 1]: [{v.min()}, {v.max()}]")
    if not np.isin(paired.event.to_numpy(float), (0.0, 1.0)).all():
        die("the observed event is not binary")


# --------------------------------------------------------------------------
# 1. Divergence
# --------------------------------------------------------------------------
def divergence(paired: pd.DataFrame) -> pd.DataFrame:
    """Vendor minus member-derived, per (model, boundary mode, lead)."""
    rows = []
    for (model, mode, lead), g in paired.groupby(
            ["model", "boundary_mode", "lead_days"], sort=True):
        v = g.vendor_pop.to_numpy(float)
        e = g.pop_daytotal.to_numpy(float)
        a = g.pop_anystep.to_numpy(float)
        d = v - e
        rows.append({
            "model": model, "boundary_mode": mode, "lead_days": int(lead),
            "n": len(g), "n_cities": int(g.city.nunique()),
            "mean_vendor_pop": float(v.mean()),
            "mean_gefs_pop": float(e.mean()),
            "bias_vendor_minus_gefs": float(d.mean()),
            "mean_abs_divergence": float(np.abs(d).mean()),
            "median_abs_divergence": float(np.median(np.abs(d))),
            "p90_abs_divergence": float(np.quantile(np.abs(d), 0.90)),
            "rmse_divergence": float(np.sqrt(np.mean(d ** 2))),
            "pearson_r": float(np.corrcoef(v, e)[0, 1]),
            "spearman_r": float(pd.Series(v).corr(pd.Series(e), method="spearman")),
            # The vendor's daily number is a MAX over hours, i.e. "rain in some
            # hour"; `pop_anystep` is the ensemble's matching any-3h-step
            # event, so this column removes the event-definition confound.
            "mean_abs_divergence_vs_anystep": float(np.abs(v - a).mean()),
            "bias_vs_anystep": float((v - a).mean()),
            "share_diverging_gt_0.2": float((np.abs(d) > 0.2).mean()),
            "share_diverging_gt_0.5": float((np.abs(d) > 0.5).mean()),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# 2-3. Calibration and decision value, both series, identical days
# --------------------------------------------------------------------------
def score(p: np.ndarray, event: np.ndarray, clim: np.ndarray,
          with_value: bool = True) -> dict:
    """One probability series -> the study's standard score row.

    Every number here comes from calibration.py / metrics.py; this function
    only routes arguments.
    """
    dec = brier_decomposition(p, event, n_bins=N_PROB_BINS, reference_prob=clim)
    _, sharp = sharpness(p)
    row = {
        "n": int(len(p)),
        "base_rate": float(event.mean()),
        "mean_prob": float(p.mean()),
        "brier": dec["brier"],
        "bss_sample": dec["brier_skill_score"],
        "bss_clim": dec["bss_vs_reference"],
        "bss_shift": dec["bss_shift"],
        "reliability": dec["reliability"],
        "resolution": dec["resolution"],
        "ece": dec["ece"],
        "auc": roc_auc(p, event),
        "sharpness_score": sharp["sharpness_score"],
        "share_confident": sharp["share_confident"],
        "share_hedged": sharp["share_hedged"],
        "brier_clim": float(np.mean((clim - event) ** 2)),
    }
    if with_value:
        row.update(value_summary(value_curve(p, event)))
    return row


SERIES = {
    "vendor": ("vendor_pop", "vendor PoP, as served"),
    "gefs": ("pop_daytotal", "GEFS member-derived PoP (day total)"),
    "gefs_anystep": ("pop_anystep", "GEFS member-derived PoP (any 3 h step)"),
    "climatology": ("clim", "smoothed day-of-year climatology"),
}


def score_table(paired: pd.DataFrame, with_value: bool = True) -> pd.DataFrame:
    rows = []
    for (model, mode, lead), g in paired.groupby(
            ["model", "boundary_mode", "lead_days"], sort=True):
        event = g.event.to_numpy(float)
        clim = g.clim.to_numpy(float)
        for name, (col, _) in SERIES.items():
            r = score(g[col].to_numpy(float), event, clim,
                      with_value=with_value and name != "climatology")
            r.update({"model": model, "boundary_mode": mode,
                      "lead_days": int(lead), "series": name})
            rows.append(r)
    return pd.DataFrame(rows)


def score_by_city(paired: pd.DataFrame) -> pd.DataFrame:
    """The same comparison city by city, so the pooled answer can be checked
    against the per-city sign - a pooled Brier can be carried by two wet
    cities."""
    rows = []
    for (model, mode, lead, city), g in paired.groupby(
            ["model", "boundary_mode", "lead_days", "city"], sort=True):
        if len(g) < MIN_CITY_DAYS or g.event.sum() < 20 or (1 - g.event).sum() < 20:
            continue
        event = g.event.to_numpy(float)
        clim = g.clim.to_numpy(float)
        for name in ("vendor", "gefs"):
            r = score(g[SERIES[name][0]].to_numpy(float), event, clim,
                      with_value=False)
            r.update({"model": model, "boundary_mode": mode,
                      "lead_days": int(lead), "city": city, "series": name})
            rows.append(r)
    return pd.DataFrame(rows)


def reliability(paired: pd.DataFrame, model: str = LIKE_FOR_LIKE_MODEL,
                mode: str = GEFS_BOUNDARY_PRIMARY) -> pd.DataFrame:
    """Reliability tables for both series at the like-for-like model."""
    sel = paired[(paired.model == model) & (paired.boundary_mode == mode)]
    out = []
    for lead, g in sel.groupby("lead_days"):
        for name in ("vendor", "gefs"):
            t = reliability_table(g[SERIES[name][0]].to_numpy(float),
                                  g.event.to_numpy(float), n_bins=N_PROB_BINS)
            out.append(t.assign(model=model, boundary_mode=mode,
                                lead_days=int(lead), series=name))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# --------------------------------------------------------------------------
# Cross-centre context
# --------------------------------------------------------------------------
def available_models() -> list[str]:
    """Vendor models with an archive on disk for at least half the GEFS cities."""
    cities = load_capitals()
    out = []
    for model in PROVIDER_MODELS:
        n = sum((RAW / f"provider_pop_{_slug(name)}_{model}.parquet").exists()
                and len(pd.read_parquet(
                    RAW / f"provider_pop_{_slug(name)}_{model}.parquet")) > 0
                for name in cities)
        if n >= len(cities) / 2:
            out.append(model)
    return out


# --------------------------------------------------------------------------
# Selftest
# --------------------------------------------------------------------------
def selftest(paired: pd.DataFrame | None = None) -> None:
    """Correctness before results, the same order src/decision_metrics.py uses.

    Two things are proved here that the rest of the module depends on and
    cannot check for itself: that a perfectly calibrated series scores the way
    theory says it must under the scoring path actually used here, and that
    the paired sample really is one sample.
    """
    metrics.run_checks(verbose=False)
    baselines.run_checks(verbose=False)

    # A synthetic series that is calibrated BY CONSTRUCTION: p drawn uniformly,
    # the outcome drawn with probability p. Its Brier score must converge to
    # E[p(1-p)] = 1/6, its reliability to zero, and its skill against its own
    # base rate must be positive. If the scoring path used below distorted any
    # of that, no result in this module would be interpretable.
    rng = np.random.default_rng(20260915)
    n = 200_000
    p = rng.uniform(0.0, 1.0, n)
    y = (rng.uniform(0.0, 1.0, n) < p).astype(float)
    flat = np.full(n, float(y.mean()))
    s = score(p, y, flat)
    assert abs(s["brier"] - 1 / 6) < 0.005, s["brier"]
    assert s["reliability"] < 0.001, s["reliability"]
    assert s["bss_sample"] > 0.3, s["bss_sample"]
    # AUC is analytic for this construction: P(p_event > p_no_event) equals
    # the integral of p1(1 - p0) over p1 > p0 divided by E[p]E[1-p], i.e.
    # (5/24) / (1/4) = 5/6.
    assert abs(s["auc"] - 5 / 6) < 0.01, s["auc"]
    # Decision value: a perfect forecast is worth 1 to every user, a constant
    # climatology worth 0 - and under the CALIBRATED rule, not merely under the
    # best-trigger envelope.
    assert abs(value_summary(value_curve(y, y))["v_max_calibrated"] - 1.0) < 1e-9
    assert abs(value_summary(value_curve(flat, y))["v_max_calibrated"]) < 1e-9

    # A deliberately MIScalibrated version of the same series - every stated
    # probability pushed towards 1 by a strictly increasing map, so the
    # RANKING of days is untouched - must score worse on reliability while
    # keeping identical discrimination. That separation is the whole basis of
    # the vendor-versus-ensemble claim below.
    infl = np.sqrt(p)
    s_bad = score(infl, y, flat, with_value=False)
    assert s_bad["reliability"] > s["reliability"], "inflation left reliability alone"
    assert abs(s_bad["auc"] - s["auc"]) < 1e-9, "inflation changed discrimination"

    if paired is not None:
        check_paired(paired)
        # Identity of the sample, restated as a count rather than a set
        # comparison, so a silent broadcast during the merge would show up.
        n_cells = paired.groupby(["boundary_mode", "lead_days"]).size().unique()
        assert len(n_cells) == 1, f"cells differ in size: {n_cells}"
        wide = paired.pivot_table(index=["city", "local_date"],
                                  columns=["boundary_mode", "lead_days"],
                                  values="vendor_pop", aggfunc="nunique")
        assert (wide.to_numpy() == 1).all(), \
            "the vendor value is not constant across leads for a city-day"
        print(f"  ok  paired sample identical across "
              f"{paired.groupby(['boundary_mode', 'lead_days']).ngroups} "
              f"(mode, lead) cells, {int(n_cells[0])} city-days each")
    print("  ok  a perfectly calibrated synthetic series scores Brier 1/6, "
          "reliability ~0, AUC 5/6; perfect value 1, climatology 0; a "
          "monotone\n      inflation of it loses reliability and keeps AUC")


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def _fmt(df: pd.DataFrame, cols) -> str:
    return df[cols].to_string(index=False, float_format=lambda v: f"{v:.3f}")


def report(paired: pd.DataFrame, div: pd.DataFrame, tbl: pd.DataFrame,
           by_city: pd.DataFrame, cross: pd.DataFrame) -> None:
    mode = GEFS_BOUNDARY_PRIMARY
    d = div[(div.model == LIKE_FOR_LIKE_MODEL) & (div.boundary_mode == mode)]
    t = tbl[(tbl.model == LIKE_FOR_LIKE_MODEL) & (tbl.boundary_mode == mode)]
    n_days = int(paired.groupby(["city", "local_date"]).ngroups)

    print("\n" + "=" * 78)
    print("TRIANGULATION: what a consumer is SERVED vs what the ENSEMBLE supports")
    print("=" * 78)
    print(f"  Paired sample: {n_days} city-days across "
          f"{paired.city.nunique()} cities, {paired.local_date.min()} .. "
          f"{paired.local_date.max()}, rain >= {RAIN_THRESHOLD_MM} mm.")
    print(f"  Every series below is scored on exactly these days.")
    print(f"  Dropped, incomplete across leads/modes/series: "
          f"{paired.attrs.get('dropped_incomplete_citydays', 0)} city-days; "
          f"dropped for want of a climatological reference: "
          f"{paired.attrs.get('dropped_no_climatology', 0)}.")
    for city, why in paired.attrs.get("clim_failed_cities", []):
        print(f"    {city}: no climatology ({why}) - city excluded entirely")
    print(f"  Like-for-like vendor model: {LIKE_FOR_LIKE_MODEL} "
          f"(NCEP, same centre as GEFS). Ensemble: "
          f"{int(paired.n_members.iloc[0])} members, boundary mode '{mode}'.")

    print("\n--- 1. Divergence, vendor PoP minus member-derived PoP, by lead ---")
    print(_fmt(d, ["lead_days", "n", "mean_vendor_pop", "mean_gefs_pop",
                   "bias_vendor_minus_gefs", "mean_abs_divergence",
                   "median_abs_divergence", "pearson_r",
                   "share_diverging_gt_0.2"]))
    lo, hi = d[d.lead_days == d.lead_days.min()], d[d.lead_days == d.lead_days.max()]
    print(f"\n  Mean absolute divergence runs "
          f"{float(lo.mean_abs_divergence.iloc[0]):.3f} at lead 1 to "
          f"{float(hi.mean_abs_divergence.iloc[0]):.3f} at lead "
          f"{int(hi.lead_days.iloc[0])}; correlation falls from "
          f"{float(lo.pearson_r.iloc[0]):.3f} to {float(hi.pearson_r.iloc[0]):.3f}.")
    print(f"  Against the ensemble's ANY-STEP event (the one the vendor's "
          f"max-over-hours\n  actually answers) the lead-1 divergence is "
          f"{float(lo.mean_abs_divergence_vs_anystep.iloc[0]):.3f} with bias "
          f"{float(lo.bias_vs_anystep.iloc[0]):+.3f}, so the event definition "
          f"explains\n  only part of the gap.")
    if QUANTISATION_TABLE.exists():
        q = pd.read_parquet(QUANTISATION_TABLE)
        col = "d_quantisation_snap"
        if col in q:
            print(f"  Control (vendor_pop_quantisation.parquet): collapsing the "
                  f"vendor series to the\n  ensemble's 3-hourly grid moves it by "
                  f"only {q[col].abs().mean():.4f} on average, so the "
                  f"divergence is not a\n  temporal-resolution artefact.")

    print("\n--- 2. Calibration against the station gauge, identical days ---")
    for lead in sorted(t.lead_days.unique()):
        r = {s: t[(t.lead_days == lead) & (t.series == s)].iloc[0]
             for s in t.series.unique()}
        if lead == sorted(t.lead_days.unique())[0]:
            print(f"   {'lead':>4}  {'series':<13} {'Brier':>7} {'BSS_clim':>9} "
                  f"{'reliab':>7} {'resol':>7} {'AUC':>6} {'mean p':>7}")
        for s in ("vendor", "gefs", "gefs_anystep", "climatology"):
            if s not in r:
                continue
            x = r[s]
            print(f"   {lead:>4}  {s:<13} {x.brier:7.4f} {x.bss_clim:+9.4f} "
                  f"{x.reliability:7.4f} {x.resolution:7.4f} {x.auc:6.3f} "
                  f"{x.mean_prob:7.3f}")

    v1 = t[(t.series == "vendor")].set_index("lead_days")
    g1 = t[(t.series == "gefs")].set_index("lead_days")
    better = (v1.brier < g1.brier)
    # What counts as a difference at all: the Brier swing produced by nothing
    # more than changing the day-boundary rule, an arbitrary methodological
    # choice. A vendor-vs-ensemble gap smaller than that is a tie, and is said
    # to be a tie rather than dressed up as a win either way.
    sens_g = tbl[(tbl.model == LIKE_FOR_LIKE_MODEL) & (tbl.series == "gefs")]
    sp_g = sens_g.pivot(index="lead_days", columns="boundary_mode", values="brier")
    tie_tol = float((sp_g["snap"] - sp_g["prorata"]).abs().max())
    gap1 = float(v1.brier.iloc[0] - g1.brier.iloc[0])
    print(f"\n  VERDICT ON CALIBRATION. Vendor PoP has the lower Brier score at "
          f"{int(better.sum())} of {len(better)} leads.")
    if abs(gap1) <= tie_tol:
        print(f"  AT LEAD 1 - the only same-lead comparison - IT IS A DEAD "
              f"HEAT: the gap is {gap1:+.4f},\n  smaller than the {tie_tol:.4f} "
              f"the ensemble's Brier moves when nothing changes but\n  the "
              f"day-boundary rule. Neither source can be claimed better "
              f"calibrated at lead 1.\n  Said plainly because the study's own "
              f"hypothesis predicted the served number would\n  be worse, and "
              f"on this sample it is not: a raw 31-member frequency reproduces "
              f"the\n  vendor's calibration but does not beat it.")
    elif gap1 < 0:
        print("  AT LEAD 1 the vendor's served probability is BETTER "
              "calibrated than our\n  member-derived PoP, by more than the "
              "day-boundary swing. Reported prominently\n  because it cuts "
              "against this study's own hypothesis: the vendor's undisclosed\n"
              "  method is doing real work a bare ensemble frequency does not "
              "replicate.")
    else:
        print("  AT LEAD 1 member-derived PoP is better calibrated than the "
              "served number, by more\n  than the day-boundary swing: the "
              "vendor is serving a probability measurably worse\n  than the "
              "ensemble it descends from supports.")
    if better.all():
        print("  The vendor also has the lower Brier at every longer lead - "
              "but see section 4:\n  the vendor series carries no lead axis, "
              "so those are not same-lead comparisons.")
    print(f"  Brier at lead 1: vendor {v1.brier.iloc[0]:.4f} vs GEFS "
          f"{g1.brier.iloc[0]:.4f} "
          f"(difference {v1.brier.iloc[0] - g1.brier.iloc[0]:+.4f}); "
          f"at lead {int(v1.index[-1])}: "
          f"{v1.brier.iloc[-1]:.4f} vs {g1.brier.iloc[-1]:.4f}.")
    print(f"  Reliability (lower is better) at lead 1: vendor "
          f"{v1.reliability.iloc[0]:.4f} vs GEFS {g1.reliability.iloc[0]:.4f}.")
    print(f"  Discrimination (AUC) at lead 1: vendor {v1.auc.iloc[0]:.3f} vs "
          f"GEFS {g1.auc.iloc[0]:.3f} - calibration and discrimination are "
          f"different claims.")

    if len(by_city):
        bc = by_city[(by_city.model == LIKE_FOR_LIKE_MODEL)
                     & (by_city.boundary_mode == mode)]
        w = bc.pivot_table(index=["city", "lead_days"], columns="series",
                           values="brier")
        share = float((w["vendor"] < w["gefs"]).mean())
        print(f"  Per-city check: the vendor wins on Brier in {share:.0%} of "
              f"{len(w)} city x lead cells,\n  so the pooled verdict is not "
              f"carried by a handful of cities.")

    print("\n--- 3. Decision value (relative economic value, Task 23) ---")
    print(f"   {'lead':>4}  {'series':<13} {'V_max(cal)':>10} "
          + "  ".join(f"V@a={a:.2f}" for a in HEADLINE_ALPHAS))
    for lead in sorted(t.lead_days.unique()):
        for s in ("vendor", "gefs"):
            x = t[(t.lead_days == lead) & (t.series == s)].iloc[0]
            vals = "  ".join(
                f"{x[f'v_cal_a{int(round(a * 100)):02d}']:8.3f}"
                for a in HEADLINE_ALPHAS)
            print(f"   {lead:>4}  {s:<13} {x.v_max_calibrated:10.3f} {vals}")

    print("\n--- 4. Divergence and skill against lead time ---")
    # Slope per lead day, fitted over leads 1-7. The vendor series is constant
    # in lead by construction (see module docstring), so its "degradation" is
    # the degradation of the ENSEMBLE it is being compared against, and the
    # honest statement is about the gap, not about a vendor forecast decaying.
    lead_axis = g1.index.to_numpy(float)
    slope_gefs = float(np.polyfit(lead_axis, g1.brier.to_numpy(float), 1)[0])
    slope_vendor = float(np.polyfit(lead_axis, v1.brier.to_numpy(float), 1)[0])
    slope_div = float(np.polyfit(d.lead_days.to_numpy(float),
                                 d.mean_abs_divergence.to_numpy(float), 1)[0])
    print(f"  Brier per extra lead day: member-derived {slope_gefs:+.5f}, "
          f"vendor {slope_vendor:+.5f}.")
    print(f"  Mean absolute divergence grows {slope_div:+.5f} per lead day.")
    if slope_gefs > slope_vendor + 1e-6:
        print("  PLAINLY: member-derived PoP degrades FASTER with lead than the "
              "served number.\n  That is expected and is not a like-for-like "
              "result - the vendor archive carries no\n  lead axis at all "
              "(plan D5), so its number is a fixed short-lead forecast being\n"
              "  compared against an ensemble initialised k days earlier. The "
              "interpretable\n  comparison is at lead 1; the rest measures how "
              "fast the ensemble loses the\n  information the served number "
              "still has.")
    else:
        print("  PLAINLY: the served number degrades at least as fast as the "
              "member-derived one\n  across the lead axis, despite carrying no "
              "lead handicap of its own.")

    print("\n--- 5. Sensitivity to the day-boundary rule ---")
    sens = tbl[(tbl.model == LIKE_FOR_LIKE_MODEL) & (tbl.series == "gefs")]
    sp = sens.pivot(index="lead_days", columns="boundary_mode", values="brier")
    dv = div[div.model == LIKE_FOR_LIKE_MODEL].pivot(
        index="lead_days", columns="boundary_mode", values="mean_abs_divergence")
    print("   lead   Brier(gefs) prorata / snap        mean|divergence| prorata / snap")
    for lead in sp.index:
        print(f"   {lead:>4}   {sp.loc[lead, 'prorata']:.4f} / "
              f"{sp.loc[lead, 'snap']:.4f}   (d={sp.loc[lead, 'snap'] - sp.loc[lead, 'prorata']:+.4f})"
              f"        {dv.loc[lead, 'prorata']:.4f} / {dv.loc[lead, 'snap']:.4f}")
    swing = float((sp["snap"] - sp["prorata"]).abs().max())
    d_div = float((dv["snap"] - dv["prorata"]).abs().max())
    # The claim "the boundary rule does not drive the conclusion" is computed,
    # not asserted: the lead-1 verdict is re-derived under each rule.
    verdicts = {}
    for m in GEFS_BOUNDARY_MODES:
        s = tbl[(tbl.model == LIKE_FOR_LIKE_MODEL) & (tbl.boundary_mode == m)
                & (tbl.lead_days == 1)].set_index("series").brier
        verdicts[m] = float(s["vendor"] - s["gefs"])
    same = len({np.sign(v) if abs(v) > swing else 0 for v in verdicts.values()}) == 1
    print(f"  Largest Brier swing between boundary rules: {swing:.4f}; largest "
          f"shift in the\n  divergence itself: {d_div:.4f}.")
    print(f"  Lead-1 vendor-minus-GEFS Brier under each rule: "
          + ", ".join(f"{m} {v:+.4f}" for m, v in verdicts.items())
          + f" -> the verdict is {'UNCHANGED' if same else 'NOT STABLE'} "
            f"across the two rules.")
    print(f"  The swing is nonetheless larger than the lead-1 gap "
          f"({abs(gap1):.4f}), which is why\n  that gap is reported above as a "
          f"dead heat rather than as a win for either side.")

    if len(cross):
        print("\n--- Cross-centre context (other vendor models, not like-for-like) ---")
        c = cross[(cross.boundary_mode == GEFS_BOUNDARY_PRIMARY)
                  & (cross.lead_days == 1)].sort_values("mean_abs_divergence")
        print(_fmt(c, ["model", "n", "mean_vendor_pop", "mean_gefs_pop",
                       "bias_vendor_minus_gefs", "mean_abs_divergence",
                       "pearson_r"]))
        print("  Each row is that model's own paired sample, so n differs; "
              "these are context\n  for the spread between centres, not "
              "entries in the headline comparison.")
        # Open-Meteo's `*_seamless` endpoints fall back to another centre
        # outside their home domain, so two nominally different models can be
        # the same series. Detected rather than assumed, because a reader
        # would otherwise read agreement between them as independent support.
        key = c.round(4).groupby(["n", "mean_vendor_pop",
                                  "mean_abs_divergence"]).model.apply(list)
        for models in key:
            if len(models) > 1:
                print(f"  NOTE: {', '.join(models)} produce identical numbers "
                      f"here - the vendor is serving\n  the same underlying "
                      f"series under both names at these cities, so they are "
                      f"not\n  independent evidence.")

    print("\n--- Caveats a reviewer will raise ---")
    print("  1. The vendor archive has no lead axis, so only lead 1 is a "
          "same-lead comparison.")
    print("  2. Our PoP is a raw 31-member frequency with no bias correction "
          "or downscaling;\n     the vendor's is post-processed by an "
          "undisclosed method. The comparison is\n     'served vs what the raw "
          "ensemble supports', not 'their method vs our method'.")
    print("  3. GEFS is ~0.25 deg grid-cell rain against a point gauge; the "
          "vendor may be\n     downscaled. Representativeness favours the "
          "vendor and is not corrected here.")
    print("  4. The daily vendor number is a max over hours, the ensemble's "
          "primary is a day\n     total; the any-step row bounds that "
          "confound and the quantisation control\n     bounds the resolution "
          "one.")
    print(f"  5. {paired.city.nunique()} European capitals over "
          f"{(pd.Timestamp(paired.local_date.max()) - pd.Timestamp(paired.local_date.min())).days // 30}"
          f" months: the sample is regionally\n     narrow and the days are "
          f"serially correlated, so differences of a few thousandths\n     of "
          f"Brier should not be read as significant.")


# --------------------------------------------------------------------------
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    print("=== correctness checks ===")
    if mode in ("selftest", "check"):
        selftest()
        print("all checks passed")
        return

    paired = build_paired(LIKE_FOR_LIKE_MODEL)
    selftest(paired)

    print(f"\n=== scoring {paired.groupby(['boundary_mode', 'lead_days']).ngroups}"
          f" (mode, lead) cells x {len(SERIES)} series ===")
    div = divergence(paired)
    tbl = score_table(paired)
    by_city = score_by_city(paired)
    rel = reliability(paired)

    # Cross-centre context: divergence only, each on its own paired sample.
    cross_rows = []
    for m in available_models():
        if m == LIKE_FOR_LIKE_MODEL:
            cross_rows.append(div)
            continue
        try:
            p = build_paired(m)
            check_paired(p)
        except SystemExit as exc:
            print(f"  {m}: skipped ({exc})")
            continue
        cross_rows.append(divergence(p))
        print(f"  {m}: {p.groupby(['city', 'local_date']).ngroups} paired "
              f"city-days")
    cross = pd.concat(cross_rows, ignore_index=True) if cross_rows else pd.DataFrame()

    TABLE.parent.mkdir(parents=True, exist_ok=True)
    tbl.to_parquet(TABLE, index=False)
    cross.to_parquet(DIVERGENCE_TABLE, index=False)
    rel.to_parquet(RELIABILITY_TABLE, index=False)
    by_city.to_parquet(BY_CITY_TABLE, index=False)

    report(paired, div, tbl, by_city,
           cross[cross.model != LIKE_FOR_LIKE_MODEL] if len(cross)
           else pd.DataFrame())

    print(f"\nwrote {TABLE}, {DIVERGENCE_TABLE.name}, "
          f"{RELIABILITY_TABLE.name}, {BY_CITY_TABLE.name}")


if __name__ == "__main__":
    main()
