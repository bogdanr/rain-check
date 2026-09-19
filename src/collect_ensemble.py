"""Forward collection of Open-Meteo ENSEMBLE MEMBERS (Phase 1, Task 8).

Why this module exists, and why it is urgent
--------------------------------------------
Every other data track in this study can be rebuilt from a cold cache at any
time: the Historical Forecast, Previous Runs and ERA5 archives are all
retrospective. Individual ensemble members are not. Open-Meteo's Ensemble API
documents a retention of **three days** for members (means and spreads are kept
longer), and a probe on 2026-09-15 measured data reaching back to
2026-09-10T22:00 - about 4.1 days, i.e. three full days plus the tail of a
fourth. A day not collected is a day permanently lost, so this collector is
designed to be run unattended, daily, from the first day it exists.

The members are route (iii) of the plan's probability-triangulation design
(D3): they bridge vendor-published PoP, already on disk, and self-computed PoP
from external ensemble archives. A member archive lets us compute PoP the way a
forecaster would - the fraction of members exceeding a threshold - at the same
cities, hours and thresholds as the vendor series, which is what makes the
vendor-versus-ensemble divergence a measurable result rather than a caveat.

What it collects
----------------
For every capital in `load_capitals()` and every ensemble model that the probe
found working, hourly `precipitation` and `temperature_2m` for every member,
over `past_days=3` (recover the retention window, so one missed day is still
recoverable the next day) and `forecast_days=7` (exactly `LEAD_DAYS`, the lead
range the rest of the study verifies).

The ensemble endpoint has its OWN model namespace: `PROVIDER_MODELS` ids such
as `gfs_seamless` or `ukmo_seamless` are deterministic-archive ids and mostly
do not exist here. The ids below were discovered by probing (see
`ENSEMBLE_MODELS`), and the probe is re-run rather than trusted, because two
silent failure modes exist and neither raises:

  1. **Out of domain.** A regional model queried outside its area returns
     HTTP 200 with `{"latitude":nan,...}` and NO `hourly` key at all. Worse,
     the body is not valid JSON (`nan` is lowercase), so a naive `.json()`
     raises rather than returning an error payload. Handled in `_fetch`.
  2. **Schema without data.** `bom_access_global_ensemble` returns its full
     18-member column set with every value null, at every city probed
     including Sydney. A collector that checked only "did we get columns?"
     would archive 18 columns of nothing, daily, forever. Availability is
     therefore decided on the number of hours that actually carry
     precipitation (`MIN_USABLE_HOURS`).

Output layout
-------------
    data/raw/ensemble/<collection_date>/ens_<city>_<model>.parquet
    data/raw/ensemble/<collection_date>/manifest.json

One parquet per city per model per collection date, in long form
(`time, member, precipitation, temperature_2m, city, model, collection_date`).
The dated partitions ARE the growing archive: a run only ever adds files and
never rewrites an earlier day, which is what makes daily accumulation lossless.
`consolidate` concatenates them into data/processed/ensemble_members.parquet
for analysis. Idempotent and resumable in the repo's usual sense - the parquet
is the unit of completion, so a re-run on the same day skips finished pairs and
an interrupted run resumes at the first missing file, costing nothing for what
is already on disk.

Note that HTTP caching is deliberately disabled here (`write_cache=False`).
The request URL carries no dates, so it hashes to the same cache key every day:
a cache hit would silently serve yesterday's members as today's.

Rate limits
-----------
Free tier: 600/minute, 5,000/hour, 10,000/day, 300,000/month, and the ensemble
endpoint counts one request as several calls because each member is a variable.
`_call_weight` estimates that cost so a run can report its own budget usage;
`estimate_cost` prints the projection before spending anything. src/fetch.py
throttles every request and backs off on 429; `MODEL_PAUSE_S` spaces the bursts.

Running it daily
----------------
Scheduled as a systemd timer, NOT cron. This machine has crontab(1) installed
but no cron daemon running, so a crontab entry would be accepted and then
silently never fire -- the one failure mode this archive cannot survive, since
unfetched members are unrecoverable after ~4 days.

    /etc/systemd/system/rain-check-ensemble.service
    /etc/systemd/system/rain-check-ensemble.timer

    systemctl status rain-check-ensemble.timer   # is it armed?
    systemctl list-timers rain-check-ensemble.timer
    journalctl -u rain-check-ensemble.service    # why did a run fail?

05:40 local sits after the 00z runs of every model here have been ingested. The
timer sets Persistent=true so a machine that was off at 05:40 runs the job at
next boot instead of skipping the day, and the service retries on failure --
retries are free because a repeat run on an already-collected day issues zero
HTTP requests.

Run `status` to confirm no collection date is missing; it names any gap and
warns when the newest partition is older than today.

Usage:
    python src/collect_ensemble.py probe [city] [model]  # discover working ids
    python src/collect_ensemble.py estimate              # API cost, no fetching
    python src/collect_ensemble.py collect [city]        # the daily run
    python src/collect_ensemble.py consolidate           # -> processed parquet
    python src/collect_ensemble.py status                # what is on disk
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from config import (
    API_ENSEMBLE,
    ENSEMBLE_COVERAGE,
    ENSEMBLE_RAW,
    LEAD_DAYS,
    PROCESSED,
    PROVIDER_SPACING_S,
    City,
    load_capitals,
)
from fetch import fetch_json, is_error, network_calls, reason

# --------------------------------------------------------------------------
# Collection parameters
# --------------------------------------------------------------------------
# Three days back is the documented member retention. Requesting the whole of
# it on every run is not redundancy for its own sake: it means a single missed
# day (cron failure, outage, rate limit) is recovered by the next successful
# run rather than lost, and it gives three overlapping observations of the same
# valid hour at different lead times, which is exactly what the lead-time
# analysis needs.
PAST_DAYS = 3

# Seven days forward = LEAD_DAYS. Several models run far longer (GFS 0.5 to 35
# days), but the study verifies leads 1-7, and the call weight is linear in the
# number of hours requested, so paying for day 8-16 members we will not verify
# would multiply the daily budget for nothing.
FORECAST_DAYS = max(LEAD_DAYS)

# Precipitation is the study's event variable; temperature rides along because
# it is the same request (the weight scales with variables, but a second
# variable at the same hours is far cheaper than a second request) and it gives
# a continuous variable for CRPS alongside the binary rain event.
HOURLY_VARS = ["precipitation", "temperature_2m"]

# A (city, model) pair counts as working only if it returns at least this many
# member-hours carrying precipitation.
#
# Deliberately an ABSOLUTE count, not the non-null FRACTION used by
# src/probe_providers.py:63. There the window is the archive itself, so a
# fraction is meaningful; here the window is ours (PAST_DAYS + FORECAST_DAYS =
# 10 days) while the models' horizons run from 33 hours (ICON-CH1) to 15 days,
# so a fraction measures our choice of window as much as the model. A 0.5
# threshold was tried first and silently rejected ICON-CH1 at all four of its
# cities - 109 real hours, 45% of a window two thirds of which lies beyond the
# model's horizon. What must be rejected is the feed that serves a full member
# schema containing nothing at all (bom_access_global_ensemble: 0 hours), and
# an absolute floor does exactly that without punishing short-range ensembles.
# 12 hours is half a day: below it there is no verifiable calendar day.
MIN_USABLE_HOURS = 12

# Polite pause between models within a city: each model is one large request,
# and spacing the bursts keeps the pattern well inside the per-minute quota.
MODEL_PAUSE_S = PROVIDER_SPACING_S

# Model ids and domains change rarely; the probe costs a real slice of the
# daily budget, so `collect` re-probes only when the coverage file is missing
# or older than this. A new model appearing a month late is a nuisance; paying
# for a full probe every day is a permanent tax.
PROBE_MAX_AGE_DAYS = 30

CONSOLIDATED = PROCESSED / "ensemble_members.parquet"


# --------------------------------------------------------------------------
# Candidate registry
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class EnsembleModel:
    """One candidate ensemble model id on the Ensemble API."""

    model: str                 # ensemble-API `models` id
    display_name: str
    organization: str
    nominal_members: int       # members the vendor documents (probe measures)
    horizon_days: float        # vendor-documented forecast length
    domain: str                # "global" or "regional"
    collect: bool              # part of the default daily set?
    note: str                  # why it is in or out of that set


# Candidates, including the ones that turned out not to work: a model that
# fails is recorded, not deleted, so the failure is not re-discovered by the
# next person reading this file. `collect=False` entries are still probed.
ENSEMBLE_MODELS: dict[str, EnsembleModel] = {
    m.model: m for m in [
        EnsembleModel(
            "ecmwf_ifs025", "ECMWF IFS 0.25° EPS", "ECMWF", 51, 15, "global",
            True, "The reference global ensemble; pairs with the deterministic "
            "ecmwf_ifs025 already collected, so vendor PoP and member-derived "
            "PoP come from the same forecasting system."),
        EnsembleModel(
            "ecmwf_aifs025", "ECMWF AIFS 0.25° ensemble", "ECMWF", 51, 15,
            "global", True, "Machine-learning ensemble from the same centre - "
            "the physics-versus-ML comparison (plan Task 30) with the model "
            "family held fixed."),
        EnsembleModel(
            "icon_global", "DWD ICON-EPS global", "DWD", 40, 7.5, "global",
            True, "Second independent global physics ensemble."),
        EnsembleModel(
            "icon_eu", "DWD ICON-EU-EPS", "DWD", 40, 5, "regional", True,
            "Regional refinement over Europe, where most capitals in the "
            "current registry sit; out-of-domain cities are dropped by the "
            "probe rather than collected as nulls."),
        EnsembleModel(
            "icon_d2", "DWD ICON-D2-EPS", "DWD", 20, 2, "regional", True,
            "Convection-permitting, 48 h only; the sharpest ensemble available "
            "for the central-European capitals."),
        EnsembleModel(
            "gfs025", "NCEP GEFS 0.25°", "NOAA", 31, 10, "global", True,
            "The US global ensemble at the resolution matching the "
            "deterministic GFS track."),
        EnsembleModel(
            "gfs05", "NCEP GEFS 0.5°", "NOAA", 31, 35, "global", False,
            "Same 31 members as gfs025 at coarser resolution; collecting both "
            "doubles the cost for a near-duplicate. Probed, not collected."),
        EnsembleModel(
            "gem_global", "GEM global ensemble", "ECCC", 21, 16, "global",
            True, "Canadian global ensemble."),
        EnsembleModel(
            "ukmo_global_ensemble_20km", "UKMO MOGREPS-G", "Met Office (UK)",
            18, 7, "global", True,
            "UK global ensemble. NOTE: UK Met Office data are CC-BY-SA, so "
            "anything derived from it must be segregated in the released "
            "dataset (plan G15)."),
        EnsembleModel(
            "ukmo_uk_ensemble_2km", "UKMO MOGREPS-UK", "Met Office (UK)", 3,
            2, "regional", True,
            "Convective-scale ensemble over the British Isles and the nearby "
            "continent; the probe found it at 5 capitals (Amsterdam, Dublin, "
            "Luxembourg, Oslo, Paris) with only THREE members, so it is a "
            "cheap sharpness comparison, not a probability estimator in its "
            "own right. Same CC-BY-SA caveat."),
        EnsembleModel(
            "bom_access_global_ensemble", "BOM ACCESS-GE", "BOM (Australia)",
            18, 10, "global", False,
            "PROBED AND REJECTED 2026-09-15: returns all 18 member columns "
            "with every value null, at all 19 capitals and at Sydney alike. "
            "Kept here so the null result is documented, and re-probed in case "
            "the feed starts."),
        EnsembleModel(
            "icon_seamless", "DWD ICON-EPS seamless", "DWD", 40, 7.5, "global",
            False, "Alias that stitches icon_d2/icon_eu/icon_global; the "
            "blend hides which system produced each hour, which is precisely "
            "what this study must not do (cf. best_match, config.py:49)."),
        EnsembleModel(
            "gfs_seamless", "NCEP GEFS seamless", "NOAA", 31, 35, "global",
            False, "Alias over gfs025/gfs05 - same objection as "
            "icon_seamless."),
        EnsembleModel(
            "ecmwf_ifs04", "ECMWF IFS 0.4° (single)", "ECMWF", 1, 15, "global",
            False, "Responds, but with ONE member: it is a deterministic "
            "series on an ensemble endpoint, not an ensemble. It also served "
            "no precipitation at all in the probe window."),
        EnsembleModel(
            "ecmwf_aifs025_single", "ECMWF AIFS 0.25° Single", "ECMWF", 1, 15,
            "global", False, "Single-member, as above."),
        EnsembleModel(
            "ecmwf_ifs_europe", "ECMWF IFS Europe (O1280)", "ECMWF", 51, 15,
            "regional", False,
            "Documented on the vendor page, but this id is refused by the API "
            "(\"Cannot initialize MultiDomains\") at every capital, so the real "
            "id differs. Left in the probe list - it is cheap - so that a "
            "future rename is picked up automatically."),
        EnsembleModel(
            "ecmwf_aifs_europe", "ECMWF AIFS Europe (N320)", "ECMWF", 51, 15,
            "regional", False, "As above."),
        EnsembleModel(
            "meteoswiss_icon_ch1", "MeteoSwiss ICON-CH1-EPS", "MeteoSwiss",
            11, 1.4, "regional", True,
            "Kilometre-scale Alpine ensemble. The probe found it well beyond "
            "Switzerland - Luxembourg, Monaco, Paris and Vienna - so it is "
            "collected: 11 members at 4 cities is a negligible share of the "
            "daily budget and the highest resolution available anywhere in "
            "the set."),
        EnsembleModel(
            "meteoswiss_icon_ch2", "MeteoSwiss ICON-CH2-EPS", "MeteoSwiss",
            21, 5, "regional", True,
            "The 2 km, 21-member companion to ICON-CH1, over the same four "
            "capitals and out to 5 days; collected for the same reason."),
    ]
}


# --------------------------------------------------------------------------
# API call cost
# --------------------------------------------------------------------------
# Open-Meteo counts one HTTP request as several "API calls" when it returns a
# lot of cells, which for an ensemble is the normal case: every member of every
# variable is a column. The vendor does not publish the formula, but its docs
# page annotates the default Ensemble request (GEFS 0.25, 31 members, 1
# variable, 7 forecast days = 31 x 168 cells) as "equivalent to 4.0 calls",
# which pins the divisor at 10 variables x 7 days x 24 h = 1680 cells per call.
# Treat the result as an estimate with the right order of magnitude, not as a
# contract: it exists to keep the daily run visibly inside the quota, and the
# quota headroom below is large enough that a factor-of-two error is harmless.
CELLS_PER_CALL = 10 * 7 * 24

FREE_TIER = {"minute": 600, "hour": 5_000, "day": 10_000, "month": 300_000}


def _call_weight(n_columns: int, n_timesteps: int) -> int:
    """Estimated number of billed API calls for one ensemble request."""
    return max(1, math.ceil(n_columns * n_timesteps / CELLS_PER_CALL))


# --------------------------------------------------------------------------
# Fetch layer
# --------------------------------------------------------------------------
def _params(city: City, model: str, *, past_days: int, forecast_days: int,
            variables: list[str]) -> dict:
    return {
        "latitude": round(city.latitude, 4),
        "longitude": round(city.longitude, 4),
        "timezone": "UTC",                  # every internal timestamp is UTC
        "hourly": ",".join(variables),
        "models": model,
        "past_days": past_days,
        "forecast_days": forecast_days,
    }


def _fetch(city: City, model: str, *, past_days: int = PAST_DAYS,
           forecast_days: int = FORECAST_DAYS,
           variables: list[str] | None = None) -> dict:
    """One ensemble request, with the endpoint's two silent failures handled.

    Returns the payload, or an `{"error": True, "reason": ...}` dict shaped
    like the one src/fetch.py produces for a 4xx, so callers can keep using
    `is_error`/`reason` uniformly.

    Caching is off in both directions (see the module docstring): the URL has
    no dates in it, so a cached response would be yesterday's forecast served
    as today's, and storing megabytes per city per model daily would bloat the
    cache for data we already persist as parquet.
    """
    params = _params(city, model, past_days=past_days,
                     forecast_days=forecast_days,
                     variables=variables or HOURLY_VARS)
    try:
        payload = fetch_json(API_ENSEMBLE, params, use_cache=False,
                             write_cache=False)
    except ValueError as exc:
        # Out-of-domain regional model: HTTP 200 carrying `{"latitude":nan,...}`
        # which is not valid JSON (lowercase `nan`). This is a verdict about
        # coverage, not a transient fault, so it must not abort the run.
        return {"error": True,
                "reason": f"unparseable body, out of model domain ({exc})"}
    if is_error(payload):
        return payload
    if "hourly" not in payload:
        # Same refusal, parseable variant: 200 with no data block at all.
        return {"error": True, "reason": "no hourly block, out of model domain"}
    return payload


# --------------------------------------------------------------------------
# Payload -> long frame
# --------------------------------------------------------------------------
def _member_columns(hourly: dict, variable: str) -> dict[int, str]:
    """Map member index -> column name for one variable.

    Open-Meteo names the control member after the bare variable and the
    perturbed ones `<variable>_memberNN`. The regex is anchored so that
    `precipitation` never picks up `precipitation_probability`.
    """
    pat = re.compile(rf"^{re.escape(variable)}(?:_member(\d+))?$")
    out: dict[int, str] = {}
    for col in hourly:
        m = pat.match(col)
        if m:
            out[int(m.group(1)) if m.group(1) else 0] = col
    return out


def _long_frame(payload: dict, city: City, model: str,
                collection_date: date) -> pd.DataFrame:
    """Long-form frame: one row per (time, member).

    Long rather than wide because member counts differ by model (18 to 51) and
    change when a vendor resizes its ensemble; a wide schema would have to be
    migrated every time, while a long one simply gains rows.
    """
    hourly = payload["hourly"]
    times = pd.to_datetime(hourly["time"], utc=True)

    cols = {v: _member_columns(hourly, v) for v in HOURLY_VARS}
    missing = [v for v, c in cols.items() if not c]
    if missing:
        raise RuntimeError(
            f"{city.name} {model}: response carries no columns for {missing} "
            f"- got {sorted(hourly)[:8]}")

    # Every variable must describe the same ensemble. If precipitation had 51
    # members and temperature 50, joining them by member index would silently
    # pair member k of one with member k of another realisation.
    member_sets = {v: set(c) for v, c in cols.items()}
    first = member_sets[HOURLY_VARS[0]]
    for v, s in member_sets.items():
        if s != first:
            raise RuntimeError(
                f"{city.name} {model}: member sets differ between variables "
                f"({HOURLY_VARS[0]}={len(first)}, {v}={len(s)}) - refusing to "
                f"align members across variables")

    frames = []
    for member in sorted(first):
        data = {"time": times, "member": member}
        for v in HOURLY_VARS:
            data[v] = pd.to_numeric(pd.Series(hourly[cols[v][member]]),
                                    errors="coerce").astype("float32")
        frames.append(pd.DataFrame(data))
    df = pd.concat(frames, ignore_index=True)

    df["member"] = df["member"].astype("int16")
    df["city"] = city.name
    df["model"] = model
    df["collection_date"] = collection_date.isoformat()
    return df.sort_values(["member", "time"], ignore_index=True)


def _usable_hours(df: pd.DataFrame) -> int:
    """Distinct hours for which the first member carries a precipitation value.

    Measured on one member rather than on all of them because members of one
    run always share a time axis; counting every member would just multiply the
    same number and make the threshold depend on ensemble size.
    """
    if df.empty:
        return 0
    first = df[df["member"] == df["member"].min()]
    return int(first.loc[first["precipitation"].notna(), "time"].nunique())


def _usable_fraction(df: pd.DataFrame) -> float:
    """Fraction of member-hours carrying a precipitation value (diagnostic).

    Not a pass/fail criterion (see MIN_USABLE_HOURS), but recorded in the
    coverage and manifest files because it is how a short horizon shows up:
    ICON-D2 at 0.52 and ICON-CH1 at 0.45 are horizons, while 0.00 is a dead
    feed.
    """
    if df.empty:
        return 0.0
    return float(df["precipitation"].notna().mean())


# --------------------------------------------------------------------------
# Probe: which ids actually work, where
# --------------------------------------------------------------------------
def _probe_pair(city: City, model: str) -> dict:
    """Availability verdict for one (city, model), from one small request.

    The probe asks for a single forecast day and no past days: enough to
    establish that the id exists, that the city is inside the model's domain
    and that the members carry data, at roughly a tenth of the weight of a
    collection request.
    """
    payload = _fetch(city, model, past_days=0, forecast_days=1)
    if is_error(payload):
        return {"available": False, "why": reason(payload)}
    try:
        df = _long_frame(payload, city, model, date.today())
    except RuntimeError as exc:
        return {"available": False, "why": str(exc)}

    n_members = int(df["member"].nunique())
    frac = _usable_fraction(df)
    hours = _usable_hours(df)
    if hours < MIN_USABLE_HOURS:
        # The schema-without-data case. Recorded as unavailable with its
        # measured coverage so the verdict can be audited later.
        return {"available": False, "n_members": n_members,
                "usable_hours": hours,
                "non_null_fraction": round(frac, 3),
                "why": f"members present but only {hours} hours carry "
                       f"precipitation"}
    return {"available": True, "n_members": n_members,
            "n_hours": int(df["time"].nunique()),
            "usable_hours": hours,
            "non_null_fraction": round(frac, 3)}


def load_coverage() -> dict:
    if ENSEMBLE_COVERAGE.exists():
        return json.loads(ENSEMBLE_COVERAGE.read_text())
    return {"probed": [], "cities": {}, "probed_utc": None}


def save_coverage(cov: dict) -> None:
    ENSEMBLE_COVERAGE.parent.mkdir(parents=True, exist_ok=True)
    ENSEMBLE_COVERAGE.write_text(json.dumps(cov, indent=2, sort_keys=True))


def probe(city_name: str | None = None, model_name: str | None = None,
          *, force: bool = False) -> dict:
    """Probe every (capital, candidate model) pair; resumable.

    Written back to disk after every pair, exactly as src/probe_providers.py
    does, so an interrupted or rate-limited probe resumes where it stopped.
    """
    cities = load_capitals()
    if city_name:
        cities = {k: v for k, v in cities.items() if k == city_name}
        if not cities:
            raise SystemExit(f"unknown city: {city_name}")
    models = list(ENSEMBLE_MODELS)
    if model_name:
        models = [m for m in models if m == model_name]
        if not models:
            raise SystemExit(f"unknown model: {model_name}")

    cov = load_coverage()
    total, done = len(cities) * len(models), 0
    for cname, city in sorted(cities.items()):
        per_city = cov["cities"].setdefault(cname, {})
        for model in models:
            done += 1
            key = f"{cname}|{model}"
            if not force and key in cov["probed"] and model in per_city:
                print(f"  [{done}/{total}] {cname:14s} {model:28s} cached")
                continue
            res = _probe_pair(city, model)
            per_city[model] = res
            if key not in cov["probed"]:
                cov["probed"].append(key)
            cov["probed_utc"] = datetime.now(timezone.utc).isoformat()
            save_coverage(cov)
            tag = (f"OK members={res['n_members']}" if res["available"]
                   else f"NO ({res['why'][:60]})")
            print(f"  [{done}/{total}] {cname:14s} {model:28s} {tag}")
            time.sleep(0.5)

    working = sorted({m for c in cov["cities"].values()
                      for m, r in c.items() if r.get("available")})
    cov["working_models"] = working
    save_coverage(cov)
    print(f"\nwrote {ENSEMBLE_COVERAGE}")
    print("working ensemble model ids: " + (", ".join(working) or "none"))
    return cov


def _coverage_age_days(cov: dict) -> float:
    if not cov.get("probed_utc"):
        return float("inf")
    probed = datetime.fromisoformat(cov["probed_utc"])
    return (datetime.now(timezone.utc) - probed).total_seconds() / 86400


def models_for(city_name: str, cov: dict) -> list[str]:
    """Default daily model set for one city: available AND worth collecting."""
    per_city = cov.get("cities", {}).get(city_name, {})
    return [m for m in ENSEMBLE_MODELS
            if ENSEMBLE_MODELS[m].collect and per_city.get(m, {}).get("available")]


# --------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------
def _slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def partition_dir(collection_date: date) -> Path:
    return ENSEMBLE_RAW / collection_date.isoformat()


def out_path(city: City, model: str, collection_date: date) -> Path:
    return partition_dir(collection_date) / f"ens_{_slug(city.name)}_{model}.parquet"


def _manifest_path(collection_date: date) -> Path:
    return partition_dir(collection_date) / "manifest.json"


def _record(collection_date: date, key: str, entry: dict) -> None:
    """Append one measurement to the day's manifest, rewritten atomically.

    The manifest is what lets a later reader answer "what was actually
    retained, and what did the day cost?" without re-opening every parquet -
    the retention window in particular is an observation that can only be made
    at collection time.
    """
    path = _manifest_path(collection_date)
    man = json.loads(path.read_text()) if path.exists() else {
        "collection_date": collection_date.isoformat(),
        "past_days": PAST_DAYS, "forecast_days": FORECAST_DAYS,
        "variables": HOURLY_VARS, "pairs": {},
    }
    man["pairs"][key] = entry
    man["updated_utc"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(man, indent=2, sort_keys=True))
    tmp.replace(path)


def collect_city_model(city: City, model: str, collection_date: date,
                       *, force: bool = False) -> tuple[pd.DataFrame, int]:
    """Collect one (city, model) for one collection date. Returns (df, weight).

    The parquet is the unit of completion, as in
    collect_archive.collect_provider_pop: if today's file exists the pair is
    skipped without a request, which is what makes a re-run - after a crash, a
    rate limit, or a nervous operator running it twice - free and harmless.
    """
    out = out_path(city, model, collection_date)
    if out.exists() and not force:
        print(f"  {city.name} {model}: already collected ({out.name})")
        return pd.read_parquet(out), 0

    payload = _fetch(city, model)
    if is_error(payload):
        print(f"  {city.name} {model}: SKIP ({reason(payload)})")
        _record(collection_date, f"{city.name}|{model}",
                {"ok": False, "why": reason(payload)})
        return pd.DataFrame(), 1

    df = _long_frame(payload, city, model, collection_date)
    n_members = int(df["member"].nunique())
    n_hours = int(df["time"].nunique())
    frac = _usable_fraction(df)
    usable = _usable_hours(df)

    if usable < MIN_USABLE_HOURS:
        # Do NOT persist a file of nulls: an empty parquet here would be read
        # by `consolidate` as real data, and its presence would also mark the
        # pair complete, so a feed that recovers tomorrow would never be
        # retried. Skipping leaves the pair open for the next run.
        print(f"  {city.name} {model}: SKIP (only {usable} member-hours carry "
              f"precipitation)")
        _record(collection_date, f"{city.name}|{model}",
                {"ok": False, "why": "all-null members",
                 "n_members": n_members, "usable_hours": usable,
                 "non_null_fraction": round(frac, 3)})
        return pd.DataFrame(), _call_weight(n_members * len(HOURLY_VARS), n_hours)

    # Loud validation: the whole point of the module is the retained past, so
    # a response that silently stops carrying it must be visible in the log.
    observed = df.loc[df["precipitation"].notna(), "time"]
    first, last = observed.min(), observed.max()
    retention_days = (pd.Timestamp(collection_date, tz="UTC") - first) \
        .total_seconds() / 86400
    if retention_days < PAST_DAYS - 1:
        print(f"  WARNING {city.name} {model}: only {retention_days:.1f} days "
              f"of past members returned, expected ~{PAST_DAYS} - retention "
              f"may have been shortened")

    weight = _call_weight(n_members * len(HOURLY_VARS), n_hours)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    _record(collection_date, f"{city.name}|{model}", {
        "ok": True, "n_members": n_members, "n_hours": n_hours,
        "n_rows": int(len(df)), "usable_hours": usable,
        "non_null_fraction": round(frac, 3),
        "first_valid_utc": first.isoformat(), "last_valid_utc": last.isoformat(),
        "observed_past_days": round(retention_days, 2),
        "estimated_api_calls": weight,
        "bytes": int(out.stat().st_size),
    })
    print(f"  {city.name} {model}: members={n_members} hours={n_hours} "
          f"rows={len(df)} {first:%Y-%m-%d %H:%M}..{last:%Y-%m-%d %H:%M} "
          f"~{weight} calls -> {out.name}")
    return df, weight


def collect(city_name: str | None = None, *, force: bool = False) -> None:
    """The daily run: every capital x every working model, once per day."""
    collection_date = datetime.now(timezone.utc).date()

    cov = load_coverage()
    age = _coverage_age_days(cov)
    if age > PROBE_MAX_AGE_DAYS:
        print(f"== coverage probe missing or {age:.0f} days old - re-probing ==")
        cov = probe(force=age != float("inf"))

    cities = load_capitals()
    if city_name:
        cities = {k: v for k, v in cities.items() if k == city_name}
        if not cities:
            raise SystemExit(f"unknown city: {city_name}")

    print(f"== ensemble members for {collection_date} "
          f"(past_days={PAST_DAYS}, forecast_days={FORECAST_DAYS}) ==")
    total_weight, net0, collected = 0, network_calls(), 0
    for cname, city in sorted(cities.items()):
        models = models_for(cname, cov)
        if not models:
            print(f"\n=== {cname}: no working ensemble model - run `probe` ===")
            continue
        print(f"\n=== ensemble @ {cname} ({len(models)} models) ===")
        for i, model in enumerate(models):
            try:
                df, weight = collect_city_model(city, model, collection_date,
                                                force=force)
            except RuntimeError as exc:
                # One malformed model response must not cost the remaining
                # cities their only chance at today's members.
                print(f"  {cname} {model}: ERROR {exc}")
                continue
            total_weight += weight
            collected += int(not df.empty)
            if weight and i < len(models) - 1:
                time.sleep(MODEL_PAUSE_S)

    print(f"\n{collected} series collected for {collection_date}; "
          f"{network_calls() - net0} HTTP requests, ~{total_weight} billed API "
          f"calls ({total_weight / FREE_TIER['day']:.0%} of the daily free-tier "
          f"quota, ~{total_weight * 30 / FREE_TIER['month']:.0%} of the monthly "
          f"quota at this rate)")
    print(f"archive: {ENSEMBLE_RAW}")


def estimate_cost() -> None:
    """Project the daily API cost from the coverage probe, fetching nothing."""
    cov = load_coverage()
    if not cov.get("cities"):
        raise SystemExit("no coverage yet - run `probe` first")
    rows, total = [], 0
    for cname in sorted(cov["cities"]):
        for model in models_for(cname, cov):
            res = cov["cities"][cname][model]
            n_members = res.get("n_members", ENSEMBLE_MODELS[model].nominal_members)
            hours = (PAST_DAYS + FORECAST_DAYS) * 24
            w = _call_weight(n_members * len(HOURLY_VARS), hours)
            total += w
            rows.append({"city": cname, "model": model,
                         "members": n_members, "est_calls": w})
    df = pd.DataFrame(rows)
    print(df.groupby("model")[["members", "est_calls"]]
          .agg({"members": "max", "est_calls": "sum"}).to_string())
    print(f"\nrequests per run: {len(df)}")
    print(f"estimated billed calls per run: ~{total} "
          f"({total / FREE_TIER['day']:.0%} of {FREE_TIER['day']}/day, "
          f"{total * 30 / FREE_TIER['month']:.0%} of "
          f"{FREE_TIER['month']}/month)")


# --------------------------------------------------------------------------
# Consolidation and status
# --------------------------------------------------------------------------
def consolidate() -> pd.DataFrame:
    """Concatenate every dated partition into one analysis-ready parquet.

    Rebuilt from the partitions rather than appended to, so it is a pure
    function of what is on disk: the dated files remain the archive of record
    and this file can be deleted and regenerated at any time.
    """
    files = sorted(ENSEMBLE_RAW.glob("*/ens_*.parquet"))
    if not files:
        raise SystemExit(f"no ensemble partitions under {ENSEMBLE_RAW} - "
                         f"run `collect` first")
    df = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    # A valid hour appears once per collection date (that is the point: the
    # same hour seen at several lead times), so the key includes the date.
    before = len(df)
    df = df.drop_duplicates(subset=["collection_date", "city", "model",
                                    "member", "time"], ignore_index=True)
    if before != len(df):
        print(f"  dropped {before - len(df)} duplicate member-hours")
    df = df.sort_values(["collection_date", "city", "model", "member", "time"],
                        ignore_index=True)
    CONSOLIDATED.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CONSOLIDATED, index=False)
    print(f"wrote {CONSOLIDATED}  rows={len(df)}  files={len(files)}  "
          f"dates={df.collection_date.nunique()}  "
          f"cities={df.city.nunique()}  models={df.model.nunique()}")
    return df


def status() -> None:
    """What the archive holds, and whether yesterday's run happened."""
    files = sorted(ENSEMBLE_RAW.glob("*/ens_*.parquet"))
    if not files:
        print(f"no ensemble data yet under {ENSEMBLE_RAW}")
        return
    rows = []
    for f in files:
        d = f.parent.name
        rows.append({"collection_date": d, "file": f.name,
                     "mb": f.stat().st_size / 1e6})
    df = pd.DataFrame(rows)
    per_day = df.groupby("collection_date").agg(series=("file", "size"),
                                                mb=("mb", "sum"))
    print(per_day.to_string())
    print(f"\ntotal {len(df)} series, {df.mb.sum():.1f} MB, "
          f"{df.collection_date.nunique()} collection dates")

    # Gaps are the one failure this archive cannot recover from, so name them.
    days = sorted(date.fromisoformat(d) for d in df.collection_date.unique())
    missing = [(days[0] + timedelta(days=i)).isoformat()
               for i in range((days[-1] - days[0]).days + 1)
               if days[0] + timedelta(days=i) not in days]
    if missing:
        print(f"MISSING collection dates (permanently lost): {', '.join(missing)}")
    today = datetime.now(timezone.utc).date()
    if days[-1] < today:
        print(f"WARNING: last collection {days[-1]}, today is {today} - "
              f"members older than three days are already unrecoverable")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "collect"
    args = sys.argv[2:]
    if cmd == "probe":
        probe(args[0] if args else None, args[1] if len(args) > 1 else None,
              force="--force" in args)
    elif cmd == "collect":
        collect(next((a for a in args if not a.startswith("-")), None),
                force="--force" in args)
    elif cmd == "estimate":
        estimate_cost()
    elif cmd == "consolidate":
        consolidate()
    elif cmd == "status":
        status()
    else:
        raise SystemExit(f"unknown command: {cmd}")
