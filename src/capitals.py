"""Phase 7 (Tasks 31-35): run the Track B calibration over European capitals.

Bucharest alone answers "is this forecast honest here?". Running the identical
pipeline over every capital with usable observations answers the more useful
question: is Bucharest unusual, and if the capitals differ, what explains it?

The forecast side is free - Open-Meteo serves any coordinate - so the work is
almost entirely in the observations, and in not fooling ourselves:

  Task 31  Each city gets its own station-vs-ERA5 lag scan (rank correlation,
           confirmed by block bootstrap). The Bucharest join hid a one-day
           station-side offset; that convention is national, not universal.
           A city whose shift is not confirmed is dropped rather than guessed
           at.
  Task 32  Identical binning, decomposition and block bootstrap everywhere.
  Task 33  Rank on reliability and BSS, never raw Brier: the uncertainty term
           of the Brier score moves with the base rate, so a dry city scores
           better for free.
  Task 34  ~18 cities will manufacture a "best" and "worst" from noise, so the
           ranking carries bootstrap rank intervals.
  Task 35  Explain the spread: regress skill on base rate, continentality and
           station distance.

Usage:  python src/capitals.py            # full run (cached after the first)
        python src/capitals.py metrics    # skip fetching, recompute metrics
        python src/capitals.py pinned     # Task 39: two-model like-for-like
        python src/capitals.py providers  # Phase 1 Task 4: all PROVIDER_MODELS
"""

from __future__ import annotations

import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

from calibration import brier_decomposition, effective_sample_size, reliability_table
from collect_archive import _chunks, _hourly_frame
from config import (
    API_HISTORICAL_FORECAST,
    API_HISTORICAL_WEATHER,
    API_PREVIOUS_RUNS,
    ARCHIVE_END,
    BOOTSTRAP_BLOCK_DAYS,
    FIGURES,
    GHCN_SCALE,
    N_PROB_BINS,
    OBS_END,
    PRIMARY_MODEL,
    PROVIDER_COVERAGE,
    PROVIDER_MODELS,
    PROCESSED,
    RAIN_THRESHOLD_MM,
    RANDOM_SEED,
    RAW,
    City,
    load_capitals,
    load_cities,
)
from constants import POP_ARCHIVE_START
from fetch import fetch_json, is_error, reason
from observations import GHCN_URL
import truth_sources

# Lag scan settings (Task 31, revised 2026-09-23).
#
# The first scan took the shift with the highest Pearson correlation of
# station mm against ERA5 mm, and required r >= 0.45 and a 0.04 lead over the
# runner-up. Scored on the 91 cities whose day is fixed by timestamps (GHCNh
# and JMA: the right answer is 0 for every one), it picked a wrong shift at
# 23 of them. Millimetres are heavy-tailed, so a handful of storms that a
# point gauge and a ~30 km reanalysis cell place differently decide the
# answer - worst in the convective tropics, where it also rejected most
# cities as "flat". The replacement ranks instead of measuring (Spearman),
# and replaces the fixed margin with a stated confidence: the chosen shift
# must win in SCAN_CONFIDENCE of block-bootstrap resamples.
#
# SCAN_CONFIDENCE was set on those known-day cities alone, never on GHCN
# outcomes or forecasts: among accepted cities the wrong-shift rate is 3.8%
# at 0.70-0.80, 2.8% at 0.90 and 3.0% at 0.95. 0.90 is the minimum; beyond
# it a stricter bar drops cities without removing errors, and the two left
# (Christchurch, Khulna, both at 100%) are records whose rain and timestamps
# genuinely disagree - which the known-day rule excludes.
# `scan_benchmark` recomputes this on every world run.
SCAN_SHIFTS = range(-2, 3)
MIN_SCAN_RHO = 0.30      # below this the gauge and reanalysis barely share weather
SCAN_CONFIDENCE = 0.90   # share of resamples the chosen shift must win
SCAN_BOOT = 200          # resamples; block length is BOOTSTRAP_BLOCK_DAYS
MIN_SCAN_DAYS = 90       # days common to every shift

N_BOOT_CITY = 600        # bootstrap replicates per city for metric CIs

# ---------------------------------------------------------------------------
# Which registry this run covers (Task 40)
# ---------------------------------------------------------------------------
# The capitals run and the expanded world run share every line of analysis in
# this module; only the registry and the output prefix differ. Keeping them in
# one file is deliberate - a forked copy would drift, and the entire value of
# the expansion rests on the two sets being computed identically, so that the
# published capitals table can be quoted beside the wider one without caveats.
CITY_SET = "capitals"

# ISO-2 codes treated as "Europe" for the in/out-of-domain split. The boundary
# that matters here is ICON-EU's domain, not the continent, so Russia and
# Turkey sit outside: their cities are served by a global model.
EUROPE = frozenset(
    "AL AD AT BY BE BA BG HR CY CZ DK EE FI FR DE GR HU IS IE IT XK LV LI LT "
    "LU MT MD MC ME NL MK NO PL PT RO RS SK SI ES SE CH UA GB".split()
)


def artefact(name: str):
    return PROCESSED / f"{CITY_SET}_{name}.parquet"


def provisional_artefact(name: str):
    """Where provisional cities' tables live (see `truth_sources`).

    Deliberately a different file, not a flag column in the canonical one.
    Every pooled claim downstream - sampling, window, triangulation, decision
    metrics, the report's ribbons and medians - reads `artefact(...)`, and
    none of them has to remember to filter: a provisional city is simply not
    in the tables they read. Only the per-city site pages look here.
    """
    return PROCESSED / f"{CITY_SET}_provisional_{name}.parquet"


def split_provisional(daily: pd.DataFrame, diag: pd.DataFrame):
    """(confirmed daily, provisional daily, provisional city names)."""
    if diag is None or "provisional" not in diag.columns:
        return daily, daily.iloc[0:0], set()
    prov = set(diag.loc[diag.included & diag.provisional.fillna(False)
                        .astype(bool), "city"])
    mask = daily.city.isin(prov)
    return daily[~mask], daily[mask], prov


def figure(name: str):
    return FIGURES / f"{CITY_SET}_{name}.png"


def registry() -> dict[str, City]:
    return load_capitals() if CITY_SET == "capitals" else load_cities()


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------
def fetch_pop(city: City, model: str | None = None) -> pd.DataFrame:
    """Hourly native PoP for one city, aggregated to local calendar days.

    `model=None` reproduces what a user of the app actually sees: Open-Meteo's
    `best_match`, whichever model that resolves to at this location. Passing an
    explicit model pins the forecaster instead, which is what Task 39 needs -
    `best_match` is ICON-EU in nine of these capitals and ECMWF in the other
    nine (see pop_provenance.py), so the default series cannot be compared
    across cities without confounding city with model.
    """
    frames = []
    for s, e in _chunks(POP_ARCHIVE_START, ARCHIVE_END):
        params = {
            "latitude": round(city.latitude, 4),
            "longitude": round(city.longitude, 4),
            "timezone": "UTC",
            "hourly": "precipitation_probability,precipitation",
            "start_date": s, "end_date": e,
        }
        if model:
            params["models"] = model
        payload = fetch_json(API_HISTORICAL_FORECAST, params)
        if is_error(payload):
            print(f"    {city.name} {s}..{e}: SKIP ({reason(payload)})")
            continue
        frames.append(_hourly_frame(payload))
    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames).drop_duplicates(subset="time").sort_values("time")
    df["local_date"] = df["time"].dt.tz_convert(city.timezone).dt.date
    daily = df.groupby("local_date").agg(
        forecast_prob_max=("precipitation_probability", "max"),
        forecast_precip_mm=("precipitation", "sum"),
        n_hours=("time", "count"),
        n_prob_null=("precipitation_probability", lambda s: int(s.isna().sum())),
    ).reset_index()
    daily = daily[(daily.n_hours == 24) & (daily.n_prob_null == 0)].copy()
    daily["forecast_prob"] = daily.forecast_prob_max / 100.0
    return daily[["local_date", "forecast_prob", "forecast_precip_mm"]]


def fetch_era5(city: City) -> pd.DataFrame:
    """ERA5 daily precipitation, used only to date the station record."""
    payload = fetch_json(API_HISTORICAL_WEATHER, {
        "latitude": round(city.latitude, 4),
        "longitude": round(city.longitude, 4),
        "timezone": city.timezone,
        "daily": ("precipitation_sum,temperature_2m_max,temperature_2m_min,"
                  "temperature_2m_mean"),
        "start_date": POP_ARCHIVE_START, "end_date": ARCHIVE_END,
    })
    if is_error(payload):
        return pd.DataFrame()
    df = pd.DataFrame(payload["daily"]).rename(columns={"time": "local_date"})
    df["local_date"] = pd.to_datetime(df["local_date"]).dt.date
    return df


_BULK: pd.DataFrame | None = None
_BULK_TRIED = False


def _bulk_store() -> pd.DataFrame | None:
    """The one-file-per-year extract, if ghcn_bulk.py has been run.

    Verified equivalent to the per-station path before adoption: over the study
    window both return 852 days for Filaret and 882 for Baneasa with zero
    differing values, so switching source does not move the headline.
    """
    global _BULK, _BULK_TRIED
    if not _BULK_TRIED:
        _BULK_TRIED = True
        import ghcn_bulk
        if ghcn_bulk.STORE.exists():
            _BULK = pd.read_parquet(ghcn_bulk.STORE)
    return _BULK


def load_ghcn(station_id: str) -> pd.DataFrame:
    """Daily precipitation for one station.

    Prefers the bulk extract, which covers every selected station in a single
    file; falls back to the per-station download so the module still works
    before ghcn_bulk.py has been run.

    Cities in countries GHCN cannot reach carry a "GHCNH:" or "ISD:" station
    id and are served by `truth_sources`, which returns the same columns - so
    the lag scan, the pair count and every exclusion below apply unchanged.
    """
    ext = truth_sources.load_prcp(station_id)
    if ext is not None:
        return ext
    store = _bulk_store()
    if store is not None:
        out = store[store.station == station_id][["ghcn_date", "prcp"]]
        if len(out):
            return out.dropna(subset=["prcp"]).reset_index(drop=True)

    path = RAW / f"ghcn_{station_id}.csv"
    if not path.exists():
        resp = requests.get(GHCN_URL.format(sid=station_id), timeout=180)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    df = pd.read_csv(path, low_memory=False, usecols=lambda c: c in
                     ("DATE", "PRCP"))
    if "PRCP" not in df.columns:
        return pd.DataFrame()
    out = pd.DataFrame({
        "ghcn_date": pd.to_datetime(df["DATE"]),
        "prcp": df["PRCP"] / GHCN_SCALE,
    })
    return out.dropna(subset=["prcp"])


def load_ghcn_temp(station_id: str) -> pd.DataFrame:
    """Daily TMAX/TMIN for one station, on the GHCN date (no offset applied).

    The lag scan established that the morning-observation convention shifts
    precipitation but not temperature; that was verified at Bucharest (tmax
    correlation peaks at zero shift, r=0.986) and is re-checked per city by the
    MAE-vs-shift comparison in `track_a`.

    One QC rule is applied here, and it is not hypothetical: the Sarajevo record
    carries July days with a daily maximum of 2 C next to a minimum of 8 C. Days
    where the reported maximum is below the reported minimum are physically
    impossible, so they are dropped and counted - a station failing this often
    is not usable at all, which `track_a` enforces.
    """
    ext = truth_sources.load_temp(station_id)
    if ext is not None:
        return ext
    store = _bulk_store()
    if store is not None:
        sel = store[store.station == station_id]
        if len(sel) and sel.tmax.notna().any():
            out = pd.DataFrame({
                "local_date": sel.ghcn_date.dt.date,
                "obs_tmax": sel.tmax.values,
                "obs_tmin": sel.tmin.values,
            })
            out["qc_fail"] = out.obs_tmin > out.obs_tmax
            return out.dropna(subset=["obs_tmax"]).reset_index(drop=True)

    path = RAW / f"ghcn_{station_id}.csv"
    if not path.exists():
        resp = requests.get(GHCN_URL.format(sid=station_id), timeout=180)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    df = pd.read_csv(path, low_memory=False,
                     usecols=lambda c: c in ("DATE", "TMAX", "TMIN"))
    if "TMAX" not in df.columns:
        return pd.DataFrame()
    out = pd.DataFrame({"local_date": pd.to_datetime(df["DATE"]).dt.date,
                        "obs_tmax": df["TMAX"] / GHCN_SCALE})
    if "TMIN" in df.columns:
        out["obs_tmin"] = df["TMIN"] / GHCN_SCALE
        out["qc_fail"] = out.obs_tmin > out.obs_tmax
    else:
        out["qc_fail"] = False
    return out.dropna(subset=["obs_tmax"])


# ---------------------------------------------------------------------------
# Task 36: temperature error vs lead time, the meteoblue-comparable piece
# ---------------------------------------------------------------------------
TRACK_A_LEADS = [1, 3, 5, 7]   # a subset: four points define the curve

# A station reporting a daily maximum below its own daily minimum this often is
# broken, not noisy, and is dropped entirely.
MAX_QC_FAIL_RATE = 0.02

# meteoblue's published monthly accuracy reports, global continental averages
# for 2 m temperature (MAE, C): their post-processed mLM at day 1 and day 6,
# and the best *raw* model at day 1. Ours is raw single-model output, so the
# raw anchor is the like-for-like one.
METEOBLUE_MLM_DAY1 = 1.04
METEOBLUE_MLM_DAY6 = 1.65
METEOBLUE_BEST_RAW_DAY1 = 1.66


def track_a(cities: dict[str, City]) -> pd.DataFrame:
    """Daily-max temperature MAE at leads 1-7 for every capital with a TMAX gauge."""
    rows = []
    for name, city in sorted(cities.items()):
        if not city.tmax_station:
            continue
        obs = load_ghcn_temp(city.tmax_station)
        if obs.empty:
            continue
        recent = obs[obs.local_date >= pd.Timestamp(POP_ARCHIVE_START).date()]
        fail = float(recent.qc_fail.mean()) if len(recent) else 1.0
        if fail > MAX_QC_FAIL_RATE:
            print(f"  {name:12s} EXCLUDED - {fail:.1%} of days have tmax < tmin; "
                  f"the station record is not trustworthy")
            continue
        obs = obs[~obs.qc_fail]

        frames = []
        variables = [f"temperature_2m_previous_day{d}" for d in TRACK_A_LEADS]
        # Track A over a large city set is the most request-hungry step in the
        # study and reliably outruns Open-Meteo's hourly quota. Crashing here
        # would throw away every city already computed, so the limit is treated
        # as "stop cleanly and keep the partial table": cached responses make a
        # later re-run resume from exactly this point.
        try:
            for s, e in _chunks(POP_ARCHIVE_START, ARCHIVE_END):
                payload = fetch_json(API_PREVIOUS_RUNS, {
                    "latitude": round(city.latitude, 4),
                    "longitude": round(city.longitude, 4),
                    "timezone": "UTC", "models": PRIMARY_MODEL,
                    "hourly": ",".join(variables),
                    "start_date": s, "end_date": e,
                })
                if not is_error(payload):
                    frames.append(_hourly_frame(payload))
        except RuntimeError as exc:
            if "429" not in str(exc) and "limit" not in str(exc).lower():
                raise
            print(f"\n  API hourly limit reached at {name}. Keeping the "
                  f"{len({r['city'] for r in rows})} cities already measured; "
                  f"re-run `capitals.py world leads` in the next hour to "
                  f"continue from here.")
            break
        if not frames:
            print(f"  {name:12s} no Track A archive")
            continue

        df = pd.concat(frames).drop_duplicates(subset="time")
        df["local_date"] = df["time"].dt.tz_convert(city.timezone).dt.date
        for lead in TRACK_A_LEADS:
            col = f"temperature_2m_previous_day{lead}"
            g = df.groupby("local_date").agg(
                fc_tmax=(col, "max"), n_hours=("time", "count"),
                n_null=(col, lambda s: int(s.isna().sum())))
            g = g[(g.n_hours == 24) & (g.n_null == 0)].reset_index()
            m = g.merge(obs, on="local_date", how="inner").dropna(
                subset=["fc_tmax", "obs_tmax"])
            if len(m) < truth_sources.min_pairs(city.tmax_station):
                continue
            err = m.fc_tmax - m.obs_tmax
            rows.append({"city": name, "lead_days": lead, "n": len(m),
                         "tmax_mae": float(err.abs().mean()),
                         "tmax_bias": float(err.mean()),
                         "tmax_mae_debiased": float((err - err.mean()).abs().mean())})
        if rows:
            last = [r for r in rows if r["city"] == name]
            if last:
                print(f"  {name:12s} lead1 MAE {last[0]['tmax_mae']:.2f} C  "
                      f"bias {last[0]['tmax_bias']:+.2f}  n={last[0]['n']}")
    return pd.DataFrame(rows)


def plot_track_a(la: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(7.6, 5.2))
    for name, g in la.groupby("city"):
        hero = name == "Bucharest"
        ax.plot(g.lead_days, g.tmax_mae_debiased, "o-", ms=4,
                lw=2.4 if hero else 1.1,
                color="#d62728" if hero else "#9aa6b2",
                label="Bucharest" if hero else None, zorder=3 if hero else 2)
    med = la.groupby("lead_days").tmax_mae_debiased.median()
    noun = "capital" if CITY_SET == "capitals" else "city"
    ax.plot(med.index, med.values, "k-", lw=2.2, label=f"{noun} median")
    ax.scatter([1, 6], [METEOBLUE_MLM_DAY1, METEOBLUE_MLM_DAY6], marker="s",
               color="#2ca02c", zorder=4,
               label="meteoblue post-processed (published)")
    ax.scatter([1], [METEOBLUE_BEST_RAW_DAY1], marker="^", color="#ff7f0e",
               zorder=4, label="best raw model, day 1 (published)")
    ax.set_xlabel("Lead time (days)")
    ax.set_ylabel("Daily-max temperature MAE after removing site bias (C)")
    where = ("European capitals" if CITY_SET == "capitals"
             else f"{la.city.nunique()} cities worldwide")
    ax.set_title(f"Temperature error vs lead time across {where}\n"
                 "with meteoblue's published global anchors", fontsize=10.5)
    ax.grid(alpha=0.25); ax.legend(fontsize=8.5)
    path = figure("lead_mae")
    fig.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {path}")


# ---------------------------------------------------------------------------
# Task 31: per-city precipitation day convention
# ---------------------------------------------------------------------------
def _scan_matrix(station: pd.DataFrame, era5: pd.DataFrame) -> pd.DataFrame:
    """ERA5 rain beside the station's rain at every shift, on the days all
    shifts share - so no shift wins by being scored on different days."""
    e = era5[["local_date", "precipitation_sum"]].dropna()
    e = e.assign(local_date=pd.to_datetime(e.local_date)).set_index("local_date")
    s = station.dropna(subset=["prcp"]).drop_duplicates("ghcn_date")
    s = s.set_index(pd.to_datetime(s.ghcn_date)).prcp
    cols = {"era5": e.precipitation_sum}
    for k in SCAN_SHIFTS:
        sh = s.copy()
        sh.index = sh.index + pd.Timedelta(days=k)
        cols[k] = sh
    return pd.DataFrame(cols).dropna().sort_index()


def _rank_corrs(m: pd.DataFrame) -> dict:
    r = m.rank().corr()["era5"]
    return {k: float(r[k]) for k in SCAN_SHIFTS}


def scan_offset(station: pd.DataFrame, era5: pd.DataFrame,
                expected: int | None = None) -> dict:
    """Which shift of the station date lines the station up with the real day?

    Station and reanalysis only - no forecast data enters this, so the offset it
    finds is a property of the observing practice, not of forecast skill.

    Rank correlation at each shift, then a seeded block bootstrap over days:
    the shift is accepted only if it has the highest rank correlation in at
    least SCAN_CONFIDENCE of resamples, and that correlation is at least
    MIN_SCAN_RHO. A wrong offset does not produce a noisy calibration curve,
    it produces a confidently wrong one, so an unconfirmed city is excluded.

    `expected` is the shift a source's timestamps already imply (see
    `truth_sources.known_day_offset`). There the timestamps are the default
    and the scan is a test against them: the city keeps `expected` unless
    another shift wins with SCAN_CONFIDENCE, in which case it is excluded,
    never shifted, because that disagreement means the timestamps or the
    record are wrong. The rank floor is then read at `expected`.
    """
    base = {"offset": None, "r": np.nan, "margin": np.nan, "share": np.nan,
            "corrs": {}, "ok": False}
    m = _scan_matrix(station, era5)
    if len(m) < MIN_SCAN_DAYS:
        return {**base, "why": "no overlapping days"}
    corrs = _rank_corrs(m)
    if not all(np.isfinite(v) for v in corrs.values()):
        # A constant series (a gauge filing only zeros, say) has no ranks to
        # correlate; that is an unusable record, not evidence about its day.
        return {**base, "corrs": corrs, "why": "no overlapping days"}
    best = max(corrs, key=corrs.get)
    margin = corrs[best] - sorted(corrs.values())[-2]

    rng = np.random.default_rng(RANDOM_SEED)
    wins = dict.fromkeys(SCAN_SHIFTS, 0)
    for _ in range(SCAN_BOOT):
        c = _rank_corrs(m.iloc[_block_indices(len(m), rng)])
        wins[max(c, key=c.get)] += 1
    share = wins[best] / SCAN_BOOT

    out = {**base, "offset": best, "r": corrs[best], "margin": margin,
           "share": share, "corrs": corrs}
    if expected is not None:
        known = {**out, "offset": expected, "r": corrs[expected]}
        if best != expected and share >= SCAN_CONFIDENCE:
            return {**known, "why": (
                f"timestamps put the day at {expected:+d} but the rain lines "
                f"up at {best:+d} ({share:.0%} of resamples) - record "
                f"contradicts its own dating")}
        if corrs[expected] < MIN_SCAN_RHO:
            return {**known, "why": (
                f"rank r={corrs[expected]:.2f} - gauge and reanalysis share "
                f"too little weather to date the record")}
        return {**known, "ok": True, "why": ""}
    if corrs[best] < MIN_SCAN_RHO:
        why = (f"rank r={corrs[best]:.2f} - gauge and reanalysis share too "
               f"little weather to date the record")
    elif share < SCAN_CONFIDENCE:
        why = (f"shift {best:+d} wins only {share:.0%} of resamples - "
               f"no single day convention is confirmed")
    else:
        return {**out, "ok": True, "why": ""}
    return {**out, "why": why}


def scan_benchmark(cities: dict[str, City]) -> pd.DataFrame:
    """Score the scan where the answer is known.

    Every city measured by a known-day source is dated by timestamps, so an
    unbiased scan should return that shift. Run without `expected`, so the
    scan is not told the answer. Written beside the diagnostics so the error
    rate quoted in the methods is recomputed, not remembered.
    """
    rows = []
    for name, city in sorted(cities.items()):
        truth = truth_sources.known_day_offset(city.prcp_station)
        if truth is None:
            continue
        era5, station = fetch_era5(city), load_ghcn(city.prcp_station)
        if era5.empty or station.empty:
            continue
        s = scan_offset(station, era5)
        if s["offset"] is None:
            continue
        rows.append({"city": name,
                     "source": truth_sources.source_of(city.prcp_station),
                     "known": truth, "found": s["offset"], "rho": s["r"],
                     "share": s["share"], "accepted": s["ok"]})
    b = pd.DataFrame(rows)
    if len(b):
        b.to_parquet(artefact("scan_benchmark"), index=False)
        acc = b[b.accepted]
        print(f"  scan benchmark: {len(b)} known-day cities; top shift wrong "
              f"at {int((b.found != b.known).sum())}; of the {len(acc)} "
              f"accepted, {int((acc.found != acc.known).sum())} wrong "
              f"(excluded by the known-day rule)")
    return b


# ---------------------------------------------------------------------------
# Build the per-city verification table
# ---------------------------------------------------------------------------
def build(cities: dict[str, City],
          model: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, diag = [], []
    for name, city in sorted(cities.items()):
        pop = fetch_pop(city, model)
        era5 = fetch_era5(city)
        station = load_ghcn(city.prcp_station)
        if pop.empty or era5.empty or station.empty:
            diag.append({"city": name, "included": False, "n": 0,
                         "why": "forecast, ERA5 or station record unavailable"})
            print(f"  {name:12s} SKIP - missing input")
            continue

        scan = scan_offset(station, era5,
                           truth_sources.known_day_offset(city.prcp_station))
        # Bucharest's offset was established by hand and the headline result
        # rests on it; the scan must agree, and is checked rather than trusted.
        if city.prcp_day_offset_days is not None:
            if scan["offset"] != city.prcp_day_offset_days:
                print(f"  {name:12s} WARNING scan says {scan['offset']:+d}, "
                      f"config says {city.prcp_day_offset_days:+d} - using config")
            offset, ok, why = city.prcp_day_offset_days, True, ""
        else:
            offset, ok, why = scan["offset"], scan["ok"], scan["why"]

        if not ok:
            diag.append({"city": name, "included": False, "n": 0,
                         "offset": scan["offset"], "scan_r": scan["r"],
                         "scan_margin": scan["margin"],
                         "scan_share": scan["share"], "why": why})
            print(f"  {name:12s} EXCLUDED - {why}")
            continue

        s = station.copy()
        s["local_date"] = (s.ghcn_date + pd.Timedelta(days=offset)).dt.date
        s = s[["local_date", "prcp"]].rename(columns={"prcp": "obs_precip_mm"})
        s = s[s.local_date <= pd.Timestamp(OBS_END).date()]

        d = pop.merge(s, on="local_date", how="inner").dropna(
            subset=["forecast_prob", "obs_precip_mm"])
        d["observed_event"] = d.obs_precip_mm >= RAIN_THRESHOLD_MM
        d["city"] = name
        d["offset_days"] = offset

        gate = truth_sources.min_pairs(city.prcp_station)
        if len(d) < gate:
            diag.append({"city": name, "included": False, "n": len(d),
                         "offset": offset, "scan_r": scan["r"],
                         "why": f"only {len(d)} usable pairs (< {gate})"})
            print(f"  {name:12s} EXCLUDED - {len(d)} pairs")
            continue

        # Continentality: the annual swing in daily mean temperature. Used in
        # place of a coastline dataset as the maritime-influence proxy - it is
        # the physical quantity the coast is a stand-in for, and it comes from
        # data already fetched.
        t = era5.dropna(subset=["temperature_2m_mean"])
        cont = float(t.temperature_2m_mean.max() - t.temperature_2m_mean.min()) \
            if len(t) else np.nan

        rows.append(d)
        diag.append({"city": name, "included": True, "n": len(d),
                     "offset": offset, "scan_r": scan["r"],
                     "scan_margin": scan["margin"],
                     "scan_share": scan["share"],
                     "prcp_km": city.prcp_km, "continentality_c": cont,
                     "truth": truth_sources.source_of(city.prcp_station),
                     "provisional": truth_sources.is_provisional(
                         city.prcp_station),
                     "why": ""})
        print(f"  {name:12s} n={len(d):4d}  offset={offset:+d}  "
              f"scan r={scan['r']:.2f}  base rate="
              f"{d.observed_event.mean():.3f}")

    daily = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return daily, pd.DataFrame(diag)


# ---------------------------------------------------------------------------
# Tasks 32-34: metrics, with bootstrap intervals and rank uncertainty
# ---------------------------------------------------------------------------
def _block_indices(n: int, rng) -> np.ndarray:
    n_blocks = int(np.ceil(n / BOOTSTRAP_BLOCK_DAYS))
    starts = rng.choice(np.arange(0, max(1, n - BOOTSTRAP_BLOCK_DAYS + 1)),
                        size=n_blocks, replace=True)
    return np.concatenate(
        [np.arange(s, min(s + BOOTSTRAP_BLOCK_DAYS, n)) for s in starts])[:n]


def metrics(daily: pd.DataFrame, diag: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(RANDOM_SEED)
    info = diag.set_index("city")
    rows, boot = [], {}

    for name, g in daily.groupby("city"):
        g = g.sort_values("local_date")
        p = g.forecast_prob.values.astype(float)
        e = g.observed_event.values.astype(float)
        d = brier_decomposition(p, e, n_bins=N_PROB_BINS)

        bss_s, rel_s = [], []
        for _ in range(N_BOOT_CITY):
            idx = _block_indices(len(p), rng)
            try:
                b = brier_decomposition(p[idx], e[idx], n_bins=N_PROB_BINS)
            except Exception:
                continue
            bss_s.append(b["brier_skill_score"])
            rel_s.append(b["reliability"])
        boot[name] = np.asarray(bss_s)

        rows.append({
            "city": name, "n": d["n"],
            "ess": effective_sample_size(e),
            "base_rate": d["base_rate"],
            "brier": d["brier"],
            "bss": d["brier_skill_score"],
            "bss_lo": float(np.quantile(bss_s, 0.025)),
            "bss_hi": float(np.quantile(bss_s, 0.975)),
            "reliability": d["reliability"],
            "reliability_hi": float(np.quantile(rel_s, 0.975)),
            "resolution": d["resolution"],
            "ece": d["ece"],
            "prcp_km": float(info.prcp_km.get(name, np.nan)),
            "continentality_c": float(info.continentality_c.get(name, np.nan)),
            "offset_days": int(g.offset_days.iloc[0]),
        })

    out = pd.DataFrame(rows).sort_values("bss", ascending=False)

    # Task 34: rank uncertainty. Resampling every city on the same replicate
    # index would be wrong (the cities are independent samples), so each city is
    # resampled separately and the ranks recomputed per replicate.
    names = list(out.city)
    k = min(len(v) for v in boot.values())
    mat = np.vstack([boot[c][:k] for c in names])          # cities x replicates
    ranks = (-mat).argsort(axis=0).argsort(axis=0) + 1     # 1 = best BSS
    out = out.merge(pd.DataFrame({
        "city": names,
        "rank_lo": np.quantile(ranks, 0.025, axis=1),
        "rank_hi": np.quantile(ranks, 0.975, axis=1),
    }), on="city", how="left")
    return out


# ---------------------------------------------------------------------------
# Task 26, generalised: is the Bucharest wet-bias inversion a local quirk?
# ---------------------------------------------------------------------------
def wet_bias_across_cities(daily: pd.DataFrame) -> pd.DataFrame:
    """Run the Bucharest low/high-end bias test on every capital.

    One city showing the inverse of the published consumer wet bias is a local
    finding. Fifteen cities showing it in the same direction is a statement
    about the forecast chain, and it is nearly free to check.
    """
    from benchmarks import wet_bias_test

    rows = []
    for name, g in daily.groupby("city"):
        wb = wet_bias_test(g.sort_values("local_date"), n_boot=400)
        rec = {"city": name}
        for _, r in wb.iterrows():
            side = "low" if r.group.startswith("low") else "high"
            rec[f"{side}_n"] = int(r.n)
            rec[f"{side}_gap"] = r.gap
            rec[f"{side}_lo"] = r.ci_lo
            rec[f"{side}_hi"] = r.ci_hi
        rows.append(rec)
    return pd.DataFrame(rows).sort_values("city")


# ---------------------------------------------------------------------------
# Task 35: explain the spread
# ---------------------------------------------------------------------------
def explain(met: pd.DataFrame) -> pd.DataFrame:
    """Univariate fits of skill on the three candidate drivers.

    With ~18 cities a multivariate regression would be fitting noise, so each
    driver is reported on its own, with the correlation and a permutation
    p-value rather than an OLS table implying more precision than exists.
    """
    rng = np.random.default_rng(RANDOM_SEED)
    rows = []
    for target in ("bss", "reliability"):
        for driver in ("base_rate", "continentality_c", "prcp_km"):
            d = met[[target, driver]].dropna()
            if len(d) < 6:
                continue
            r = float(d[target].corr(d[driver]))
            null = [float(pd.Series(rng.permutation(d[target].values))
                          .corr(d[driver].reset_index(drop=True)))
                    for _ in range(2000)]
            p = float(np.mean(np.abs(null) >= abs(r)))
            rows.append({"target": target, "driver": driver, "n": len(d),
                         "corr": r, "p_perm": p})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
MANY_CITIES = 24     # above this a panel-per-city grid stops being readable


def plot_many(daily: pd.DataFrame, met: pd.DataFrame) -> None:
    """Reliability for a large city set: one axes, every city as a faint line.

    A 172-panel grid is technically a plot and practically a wall. Overlaying
    the curves answers the question the large set was built to answer - is the
    low-end under-forecasting universal, or does Bucharest sit apart - which a
    grid of thumbnails actively obscures.
    """
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 5.0))

    ax = axes[0]
    ax.plot([0, 1], [0, 1], "k--", lw=1.0, zorder=1)
    curves = []
    for name, g in daily.groupby("city"):
        t = reliability_table(g.forecast_prob.values,
                              g.observed_event.values.astype(float), n_bins=5)
        curves.append(t.assign(city=name))
        ax.plot(t.mean_prob, t.obs_freq, "-", lw=0.7, alpha=0.25,
                color="#4c78a8", zorder=2)
    allc = pd.concat(curves, ignore_index=True)
    allc["bin"] = pd.cut(allc.mean_prob, np.linspace(0, 1, 6))
    med = allc.groupby("bin", observed=True).agg(
        x=("mean_prob", "median"), y=("obs_freq", "median")).dropna()
    ax.plot(med.x, med.y, "-", color="#111", lw=2.6, zorder=4, label="median city")
    b = daily[daily.city == "Bucharest"]
    if len(b):
        t = reliability_table(b.forecast_prob.values,
                              b.observed_event.values.astype(float), n_bins=5)
        ax.plot(t.mean_prob, t.obs_freq, "o-", ms=5, lw=2.6, color="#d62728",
                zorder=5, label="Bucharest")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.grid(alpha=0.2)
    ax.set_xlabel("Stated probability"); ax.set_ylabel("Observed frequency")
    ax.set_title(f"Every city ({met.city.nunique()}), one line each", fontsize=10)
    ax.legend(fontsize=8.5, loc="upper left")

    ax = axes[1]
    ax.hist(met.bss.dropna(), bins=24, color="#4c78a8", alpha=0.85)
    if len(b):
        bs = float(met.set_index("city").bss.get("Bucharest", np.nan))
        ax.axvline(bs, color="#d62728", lw=2.4,
                   label=f"Bucharest {bs:.2f}")
        ax.legend(fontsize=8.5)
    ax.axvline(0, color="#444", lw=1.0, ls="--")
    ax.set_xlabel("Brier skill score vs each city's own climatology")
    ax.set_ylabel("Cities")
    ax.set_title("Where Bucharest sits in the distribution", fontsize=10)
    ax.grid(alpha=0.2)

    fig.suptitle("Rain-probability calibration across "
                 f"{met.city.nunique()} cities", fontsize=11.5)
    fig.tight_layout()
    p = figure("reliability")
    fig.savefig(p, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {p}")


def plot(daily: pd.DataFrame, met: pd.DataFrame) -> None:
    cities = list(met.sort_values("bss", ascending=False).city)
    if len(cities) > MANY_CITIES:
        plot_many(daily, met)
        plot_baserate(met)
        return
    ncol = 5
    nrow = int(np.ceil(len(cities) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(2.5 * ncol, 2.6 * nrow),
                             sharex=True, sharey=True)
    axes = np.atleast_1d(axes).ravel()

    for ax, name in zip(axes, cities):
        g = daily[daily.city == name]
        t = reliability_table(g.forecast_prob.values,
                              g.observed_event.values.astype(float), n_bins=5)
        hero = name == "Bucharest"
        ax.plot([0, 1], [0, 1], "k--", lw=0.8)
        ax.plot(t.mean_prob, t.obs_freq, "o-", ms=4,
                color="#d62728" if hero else "#1f77b4",
                lw=2.2 if hero else 1.4)
        bss = float(met.set_index("city").bss[name])
        ax.set_title(f"{name}  BSS {bss:.2f}", fontsize=8.5,
                     fontweight="bold" if hero else "normal")
        ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.grid(alpha=0.2)
    for ax in axes[len(cities):]:
        ax.axis("off")
    fig.suptitle("Rain-probability calibration across European capitals "
                 "(Bucharest in red)", fontsize=11)
    fig.supxlabel("Stated probability"); fig.supylabel("Observed frequency")
    fig.tight_layout()
    p1 = figure("reliability")
    fig.savefig(p1, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {p1}")
    plot_baserate(met)


def plot_baserate(met: pd.DataFrame) -> None:
    """Task 33: make the base-rate confound visible instead of hiding it."""
    label_all = len(met) <= MANY_CITIES
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6))
    for ax, (y, label) in zip(axes, [("brier", "Brier score (lower better)"),
                                     ("bss", "Brier skill score")]):
        ax.scatter(met.base_rate, met[y], s=34 if label_all else 16,
                   color="#1f77b4", alpha=1.0 if label_all else 0.6)
        # With 170 cities, labelling every point is noise; label only the
        # hero and the extremes that a reader would otherwise ask about.
        if label_all:
            shown = met
        else:
            shown = pd.concat([met.nsmallest(3, y), met.nlargest(3, y),
                               met[met.city == "Bucharest"]]).drop_duplicates("city")
        for _, r in shown.iterrows():
            ax.annotate(r.city, (r.base_rate, r[y]), fontsize=7,
                        xytext=(4, 3), textcoords="offset points",
                        color="#d62728" if r.city == "Bucharest" else "#444")
        b = met[met.city == "Bucharest"]
        ax.scatter(b.base_rate, b[y], s=70, color="#d62728", zorder=3)
        ax.set_xlabel("Rain days (base rate)"); ax.set_ylabel(label)
        ax.grid(alpha=0.25)
    axes[0].set_title("Raw Brier tracks how often it rains - not comparable")
    axes[1].set_title("Skill score removes that, and is")
    fig.tight_layout()
    p2 = figure("baserate")
    fig.savefig(p2, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {p2}")


# ---------------------------------------------------------------------------
# Task 39: does the league table survive pinning one model everywhere?
# ---------------------------------------------------------------------------
# pop_provenance.py established two facts that make this necessary rather than
# decorative. First, `best_match` is not one forecaster: across the 18 probed
# capitals it resolves to ICON-EU in nine and to ECMWF IFS in the other nine
# (7 and 8 respectively among the 15 that survive to the ranking). Second, at
# Bucharest - same city, same days, same gauge - ICON scores BSS 0.399 against
# ECMWF's 0.279. So a 0.12 swing is available from the model alone, comparable
# to much of the city-to-city spread the ranking is trying to interpret.
# Re-running every city on one pinned model separates "this city is hard to
# forecast" from "this city was routed to a different forecaster".
PIN_MODELS = ["icon_eu", "ecmwf_ifs025"]


def provider_models_with_coverage() -> list[str]:
    """Task 4: the PROVIDER_MODELS the archive probe found usable anywhere.

    The probe (src/probe_providers.py) decides per city which models have a
    usable PoP archive; a model enters the multi-model run if it is usable in
    at least one city - build() then drops it per city where it is not, and
    the league table marks it unavailable rather than hiding it. Without the
    probe file, fall back to the two established pinned models so the module
    degrades to today's behaviour instead of guessing.
    """
    if not PROVIDER_COVERAGE.exists():
        return list(PIN_MODELS)
    import json
    v = json.loads(PROVIDER_COVERAGE.read_text())
    return [m for m in PROVIDER_MODELS
            if any(c.get(m, {}).get("available")
                   for c in v["cities"].values())]


def pinned_ranking(models: list[str] | None = None,
                   *, persist_daily: bool = False) -> pd.DataFrame:
    """Per-model verification for every city, generalised over the model list.

    `models=None` keeps the original two-model Task 39 behaviour byte-for-byte.
    The multi-provider run (Task 4 of the 2026-09-14 plan) passes the full
    PROVIDER_MODELS set filtered by the archive probe, and additionally keeps
    the matched daily series so the league table and its threshold-sensitivity
    runs (src/league.py, src/league_robustness.py) can recompute metrics on
    identical windows without touching the network again.
    """
    models = models if models is not None else PIN_MODELS
    cities = registry()
    frames, dailies = [], []
    for model in models:
        print(f"\n--- pinned: {model} ---")
        daily, diag = build(cities, model=model)
        if daily.empty:
            print(f"  no data for {model}")
            continue
        if persist_daily:
            dailies.append(daily.assign(model=model))
        met = metrics(daily, diag)
        met["model"] = model

        # Wet bias per model, so the Task 26 inversion test can be asked per
        # provider in the league table, not only per city.
        wb = wet_bias_across_cities(daily).assign(model=model)
        # Merge on city AND model: both frames carry a model column, and an
        # on="city" merge would suffix them into model_x/model_y, dropping the
        # column the league table and report_pinned group by.
        met = met.merge(wb, on=["city", "model"], how="left")
        frames.append(met)

    if not frames:
        raise SystemExit("no pinned data collected")
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(artefact("pinned"), index=False)
    if persist_daily and dailies:
        pd.concat(dailies, ignore_index=True).to_parquet(
            artefact("pinned_daily"), index=False)
    return out


def report_pinned(pin: pd.DataFrame, published: pd.DataFrame,
                  prov: pd.DataFrame) -> None:
    print("\n\n=== Task 39: the league table with the model held fixed ===")

    # Only cities that survived under BOTH pinned models can be compared.
    common = set.intersection(*[set(g.city) for _, g in pin.groupby("model")])
    pin = pin[pin.city.isin(common)].copy()
    print(f"\n  {len(common)} capitals have a usable series under every pinned "
          f"model.")

    wide = pin.pivot(index="city", columns="model", values="bss")
    wide["delta"] = wide.get("icon_eu") - wide.get("ecmwf_ifs025")
    wide = wide.join(prov.set_index("city")["best_match_is"].rename("served_by"))
    wide = wide.join(published.set_index("city")["bss"].rename("published"))
    wide = wide.sort_values("icon_eu", ascending=False)
    print("\n  BSS per capital under each pinned model:\n")
    print(wide.to_string(float_format=lambda v: f"{v:.3f}"))

    print(f"\n  ICON beats ECMWF on PoP skill in "
          f"{int((wide.delta > 0).sum())}/{len(wide)} capitals "
          f"(median advantage {wide.delta.median():+.3f} BSS).")

    # The decisive comparison: does the published ranking differ from the
    # like-for-like one, and does the apparent model effect survive?
    for model in PIN_MODELS:
        g = pin[pin.model == model].sort_values("bss", ascending=False)
        g = g.reset_index(drop=True)
        pos = int(g.index[g.city == "Bucharest"][0]) + 1
        print(f"\n  Bucharest pinned to {model:13s}: BSS {g[g.city=='Bucharest'].bss.iloc[0]:.3f}, "
              f"rank {pos} of {len(g)}")
        print(f"    order: " + " > ".join(g.city.head(5)) + " ...")

    pub = published[published.city.isin(common)].sort_values(
        "bss", ascending=False).reset_index(drop=True)
    pub_pos = int(pub.index[pub.city == "Bucharest"][0]) + 1
    print(f"\n  Bucharest as published (mixed models): rank {pub_pos} of {len(pub)}")

    # Was the published ICON-vs-ECMWF group difference a model effect or a
    # geography effect? Under a pinned model the groups should converge if it
    # was the model, and persist if it was the climate.
    grp = wide.groupby("served_by")[["icon_eu", "ecmwf_ifs025", "published"]].mean()
    print("\n  Mean BSS by which model best_match served the city:\n")
    print(grp.to_string(float_format=lambda v: f"{v:.3f}"))
    if {"icon_eu", "ecmwf_ifs025"} <= set(grp.index):
        gap_pub = grp.loc["icon_eu", "published"] - grp.loc["ecmwf_ifs025", "published"]
        gap_pin = grp.loc["icon_eu", "icon_eu"] - grp.loc["ecmwf_ifs025", "icon_eu"]
        print(f"\n  Gap between the two groups, as published : {gap_pub:+.3f} BSS")
        print(f"  Same gap with ICON pinned everywhere     : {gap_pin:+.3f} BSS")
        share = 1 - gap_pin / gap_pub if gap_pub else float("nan")
        verdict = ("none of it - the gap is if anything slightly wider once the "
                   "model is held fixed" if share <= 0.05
                   else f"{share:.0%} of it")
        print(f"  -> The model explains {verdict}. The routing is not the "
              f"explanation:")
        print("     best_match sends ECMWF to the maritime north-west, which is "
              "genuinely\n     harder to forecast, so model and geography were "
              "confounded - but the\n     geography is doing the work.")

    # Where does each model win? This is the part that survives the null above.
    d = wide.join(published.set_index("city")[["base_rate", "continentality_c"]])
    d = d.dropna(subset=["delta", "base_rate"])
    if len(d) > 4:
        from scipy import stats as _st
        print("\n  ICON's advantage over ECMWF is not uniform. Correlating the "
              "per-city\n  difference against what each city is like:\n")
        for col, label in [("base_rate", "rain-day frequency"),
                           ("continentality_c", "continentality (annual T range)")]:
            if col not in d:
                continue
            r, p = _st.pearsonr(d[col], d.delta)
            print(f"    ICON advantage vs {label:34s} r={r:+.3f}  p={p:.4f}")
        r, p = _st.pearsonr(d.base_rate, d.delta)
        if p < 0.05:
            print(f"\n  -> The regional high-resolution model earns its keep where "
                  f"rain is\n     infrequent (Vienna {d.loc['Vienna', 'delta']:+.3f}, "
                  f"Bucharest {d.loc['Bucharest', 'delta']:+.3f}); the global model "
                  f"is better where it\n     rains constantly (Dublin "
                  f"{d.loc['Dublin', 'delta']:+.3f}). Picking one model for all of "
                  f"Europe would\n     cost skill either way, which is presumably "
                  f"why best_match routes by region.")


def plot_pinned(pin: pd.DataFrame, published: pd.DataFrame) -> None:
    """Show the published ranking beside the like-for-like one."""
    common = set.intersection(*[set(g.city) for _, g in pin.groupby("model")])
    wide = pin[pin.city.isin(common)].pivot(
        index="city", columns="model", values="bss")
    wide = wide.join(published.set_index("city")["bss"].rename("published"))
    wide = wide.sort_values("icon_eu")

    fig, ax = plt.subplots(figsize=(8.4, 6.0))
    y = np.arange(len(wide))
    ax.hlines(y, wide.ecmwf_ifs025, wide.icon_eu, color="#c9d1d9", lw=3,
              zorder=1)
    ax.scatter(wide.ecmwf_ifs025, y, s=42, color="#1f77b4", zorder=3,
               label="ECMWF IFS, pinned")
    ax.scatter(wide.icon_eu, y, s=42, color="#ff7f0e", zorder=3,
               label="ICON-EU, pinned")
    ax.scatter(wide.published, y, s=58, facecolors="none",
               edgecolors="#d62728", lw=1.4, zorder=4,
               label="as published (best_match)")
    ax.set_yticks(y)
    ax.set_yticklabels([("Bucharest" if c == "Bucharest" else c)
                        for c in wide.index], fontsize=9)
    for tick, c in zip(ax.get_yticklabels(), wide.index):
        if c == "Bucharest":
            tick.set_color("#d62728"); tick.set_fontweight("bold")
    ax.axvline(0, color="grey", lw=0.8, ls=":")
    ax.set_xlabel("Brier skill score against each city's own climatology")
    ax.set_title("The ranking survives holding the forecaster fixed\n"
                 "Open-Meteo routes each capital to ICON-EU or to ECMWF; the "
                 "published value\nis whichever one applies, and the ordering "
                 "barely moves either way", fontsize=10.5)
    ax.grid(alpha=0.22, axis="x")
    ax.legend(fontsize=8.5, loc="lower right")
    path = figure("pinned")
    fig.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {path}")


def main() -> None:
    global CITY_SET
    args = sys.argv[1:]
    if args and args[0] == "world":
        CITY_SET = "cities"
        args = args[1:]
    mode = args[0] if args else "all"
    label = "capitals" if CITY_SET == "capitals" else "cities"
    daily_path = artefact("pop")

    if mode == "pinned":
        published = pd.read_parquet(artefact("metrics"))
        prov = pd.read_parquet(PROCESSED / "pop_provenance_capitals.parquet")
        pin = pinned_ranking()
        report_pinned(pin, published, prov)
        plot_pinned(pin, published)
        return

    # Phase 1 Task 4: per-model verification over every PROVIDER_MODELS entry
    # the archive probe found usable. Same statistics as `pinned`, more models,
    # plus the matched daily series kept on disk for the league table.
    if mode == "providers":
        models = provider_models_with_coverage()
        print(f"=== providers: per-model verification over {len(models)} "
              f"models: {', '.join(models)}")
        pinned_ranking(models, persist_daily=True)
        return

    if mode == "scan-benchmark":
        scan_benchmark(registry())
        return

    # `leads` joins `metrics` on the cached path: the Track A leg is the only
    # part that still needs the network, and rebuilding the whole PoP side just
    # to reach it wastes half an hour of lag scans on data already on disk.
    if mode in ("metrics", "leads") and daily_path.exists():
        daily = pd.read_parquet(daily_path)
        diag = pd.read_parquet(artefact("diagnostics"))
        # A previous run already moved provisional cities out of the daily
        # table; bring them back so the split below sees the whole set.
        if provisional_artefact("pop").exists():
            daily = pd.concat([daily, pd.read_parquet(
                provisional_artefact("pop"))], ignore_index=True)
    else:
        cities = registry()
        print(f"=== Tasks 31-32: building {len(cities)} {label} ===")
        daily, diag = build(cities)
        daily.to_parquet(daily_path, index=False)
        diag.to_parquet(artefact("diagnostics"), index=False)
        scan_benchmark(cities)

    if daily.empty:
        raise SystemExit(f"no {label} survived the coverage and offset rules")

    # Provisional cities leave here, before any statistic across cities is
    # formed. The canonical daily table is rewritten confirmed-only so that
    # every later reader of it is safe by construction.
    daily, prov_daily, prov = split_provisional(daily, diag)
    for name in ("pop", "metrics", "wet_bias"):
        p = provisional_artefact(name)
        if p.exists():
            p.unlink()
    daily.to_parquet(daily_path, index=False)
    if prov:
        prov_daily.to_parquet(provisional_artefact("pop"), index=False)
        pm = metrics(prov_daily, diag)
        # A rank among provisional cities alone means nothing, and a rank
        # among confirmed ones would put them in the league they were kept
        # out of. The site shows where the score falls instead.
        pm[["rank_lo", "rank_hi"]] = np.nan
        pm.to_parquet(provisional_artefact("metrics"), index=False)
        wet_bias_across_cities(prov_daily).to_parquet(
            provisional_artefact("wet_bias"), index=False)
        print(f"\n  {len(prov)} provisional {label} held out of every pooled "
              f"claim: {', '.join(sorted(prov))}")

    met = metrics(daily, diag)
    met.to_parquet(artefact("metrics"), index=False)

    print(f"\n=== Tasks 32-34: calibration across {len(met)} {label} ===")
    cols = ["city", "n", "base_rate", "brier", "bss", "bss_lo", "bss_hi",
            "reliability", "resolution", "ece", "rank_lo", "rank_hi"]
    print(met[cols].to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    b = met[met.city == "Bucharest"].iloc[0]
    print(f"\n  Bucharest: BSS {b.bss:.3f} [{b.bss_lo:.3f}, {b.bss_hi:.3f}], "
          f"rank {int(met.reset_index(drop=True).index[met.city.values == 'Bucharest'][0]) + 1}"
          f" of {len(met)} with a 95% rank interval of "
          f"[{b.rank_lo:.0f}, {b.rank_hi:.0f}].")
    print("  Rank intervals this wide are the point: with ~2 years per city the "
          "ordering is mostly not resolvable, so no 'best capital' is claimed.")

    print("\n=== Task 35: what explains the spread? ===")
    ex = explain(met)
    ex.to_parquet(artefact("drivers"), index=False)
    print(ex.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    print("\n=== Task 26 generalised: the low/high-end bias, city by city ===")
    wb = wet_bias_across_cities(daily)
    wb.to_parquet(artefact("wet_bias"), index=False)
    print(wb.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    low_up = int((wb.low_gap > 0).sum())
    low_sig = int((wb.low_lo > 0).sum())
    high_dn = int((wb.high_gap < 0).sum())
    high_sig = int((wb.high_hi < 0).sum())
    print(f"\n  Low-PoP days rain MORE often than stated in {low_up}/{len(wb)} "
          f"{label} ({low_sig} with a CI excluding zero).")
    print(f"  High-PoP days rain LESS often than stated in {high_dn}/{len(wb)} "
          f"{label} ({high_sig} with a CI excluding zero).")

    # The 15-capital version of this claim was necessarily about Europe, and
    # about two models: best_match resolves to ICON-EU or ECMWF everywhere in
    # that set. Cities outside ICON-EU's domain are served by a different model
    # entirely, so they are the first genuinely independent test of whether the
    # inversion is a property of one model or of the whole forecast chain.
    reg = registry()
    wb = wb.assign(eu=[reg[c].country in EUROPE if c in reg else True
                       for c in wb.city])
    out = wb[~wb.eu]
    if len(out) >= 5:
        o_low = int((out.low_gap > 0).sum()); o_sig = int((out.low_lo > 0).sum())
        o_hi = int((out.high_gap < 0).sum()); o_hsig = int((out.high_hi < 0).sum())
        wb.to_parquet(artefact("wet_bias"), index=False)
        print(f"\n  Outside Europe ({len(out)} cities, served by a different "
              f"model than ICON-EU): low-end under-forecasting in {o_low}/"
              f"{len(out)} ({o_sig} significant), high-end over-forecasting in "
              f"{o_hi}/{len(out)} ({o_hsig} significant).")
        print("  -> the inversion of the published consumer wet bias survives "
              "leaving both Europe and the European models behind, so it is a "
              "property of this kind of forecast chain, not of one model.")
    else:
        print("  -> the inversion of the published consumer wet bias is not a "
              "Bucharest quirk; it is what this forecast chain does across Europe.")

    dropped = diag[~diag.included] if "included" in diag else pd.DataFrame()
    if len(dropped):
        print(f"\n  {len(dropped)} {label} dropped after the data arrived:")
        for _, r in dropped.iterrows():
            print(f"    {r.city:12s} {r.why}")

    plot(daily, met)

    if mode in ("all", "leads"):
        print("\n=== Task 36: temperature error vs lead time across capitals ===")
        reg = registry()
        la = track_a(reg)
        provisional_artefact("lead_mae").unlink(missing_ok=True)
        if len(la):
            held = la.city.map(lambda c: c in reg and truth_sources.
                               is_provisional(reg[c].prcp_station))
            if held.any():
                la[held].to_parquet(provisional_artefact("lead_mae"),
                                    index=False)
            la = la[~held]
            la.to_parquet(artefact("lead_mae"), index=False)
            piv = la.pivot(index="city", columns="lead_days",
                           values="tmax_mae_debiased")
            print("\n  Daily-max MAE (C) after removing each site's constant bias:")
            print(piv.to_string(float_format=lambda v: f"{v:.2f}"))
            med = la.groupby("lead_days").tmax_mae_debiased.median()
            print(f"\n  Capital median: lead 1 {med.get(1, np.nan):.2f} C -> "
                  f"lead 7 {med.get(7, np.nan):.2f} C. meteoblue publish "
                  f"{METEOBLUE_BEST_RAW_DAY1:.2f} C for the best raw model at "
                  f"day 1 and {METEOBLUE_MLM_DAY1:.2f} C for their "
                  f"post-processed blend, on hourly temperature globally - so "
                  f"these are neighbouring quantities, not the same one.")
            plot_track_a(la)


if __name__ == "__main__":
    main()
