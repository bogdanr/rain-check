"""Phase 0, Tasks 1-2: is probability-of-precipitation served (a) at lead, (b) for ML models?

Two decisive availability questions, both answerable only by asking the live
API, and both of which change what the paper can claim:

  Q1. Does the Previous Runs API serve `precipitation_probability_previous_dayN`
      (N = 1..7)?  Track A currently collects only amounts
      (src/collect_archive.py:73-74).  If probabilities exist at fixed lead
      offsets, the study's central "trust versus lead time" axis can be
      probabilistic; if not, the probabilistic results are day-ahead only and
      the lead-time axis stays deterministic (plan risk 3).

  Q2. Does the Historical Forecast API serve `precipitation_probability` for
      the machine-learning systems (ECMWF AIFS, NCEP AIGFS, NCEP HGEFS)?  The
      physics-versus-ML calibration comparison is the strongest novelty hook
      and exists only if those models carry PoP in the archive.

Design follows src/probe_providers.py:

  * Small date-ranged windows spread across the archive rather than a full
    walk: enough to separate "no archive" from "archive", to bound the archive
    start from below, and to catch a field that turns on mid-archive.
  * Resumable: the verdict JSON is rewritten after every probe and every HTTP
    response is cached by src/fetch.py, so an interrupted run resumes free.
  * Rate-limit aware: fetch_json throttles and backs off; a surviving 429 is
    recorded and the probe pauses rather than raising.
  * "No data" is a finding, not an error.  Open-Meteo answers an unknown
    *model* with HTTP 400, but answers a *known variable with no data* with
    HTTP 200 and an all-null series - so the two must be distinguished, and a
    deliberately bogus variable name is probed as a control to prove that a
    200 really does mean "the variable name is valid".

Usage:  .venv/bin/python src/probe_pop_leads_and_ml.py          # everything
        .venv/bin/python src/probe_pop_leads_and_ml.py leads    # Q1 only
        .venv/bin/python src/probe_pop_leads_and_ml.py ml       # Q2 only
        .venv/bin/python src/probe_pop_leads_and_ml.py refine   # bisect starts
        .venv/bin/python src/probe_pop_leads_and_ml.py context  # follow-ups
"""

from __future__ import annotations

import json
import sys
import time

from config import (
    API_HISTORICAL_FORECAST,
    API_PREVIOUS_RUNS,
    LATITUDE,
    LEAD_DAYS,
    LONGITUDE,
    RAW,
)
from fetch import fetch_json, is_error, reason

OUT = RAW / "pop_leads_and_ml_probe.json"

# A window counts as usable if at least this fraction of its hours carry a
# non-null value - the same bar src/probe_providers.py:63 applies, so the
# verdicts here are comparable with the existing coverage table.
MIN_USABLE_FRACTION = 0.5

# ---------------------------------------------------------------------------
# Q1: Previous Runs API
# ---------------------------------------------------------------------------
PREV_MODELS = ["ecmwf_ifs025", "icon_eu", "gfs_seamless"]

# Six one-week windows spread over the PoP archive (which starts 2024-04-25,
# src/constants.py:5) up to the archive end.  A field that switched on part
# way through would show as a run of empty early windows followed by full
# later ones, which a single window could not distinguish from "never served".
PREV_WINDOWS = [
    ("2024-05-01", "2024-05-07"),
    ("2024-10-01", "2024-10-07"),
    ("2025-06-01", "2025-06-07"),
    ("2026-01-05", "2026-01-11"),
    ("2026-05-01", "2026-05-07"),
    ("2026-09-01", "2026-09-07"),
]

# Probed alongside the lead variables in the same request:
#   * precipitation_previous_day1 - a variable known to be served, so an empty
#     response cannot be blamed on the window, the model or the endpoint;
#   * precipitation_probability - the day-0 field, to show the endpoint knows
#     the quantity at all;
#   * a nonsense name - the control that establishes what an *invalid* name
#     does, without which "200 with nulls" is uninterpretable.
PREV_CONTROLS = ["precipitation_previous_day1", "precipitation_probability"]
BOGUS_VARIABLE = "precipitation_probability_previous_day99"

# ---------------------------------------------------------------------------
# Q2: Historical Forecast API, machine-learning models
# ---------------------------------------------------------------------------
# Both the documented id and the plausible aliases are probed: the vendor's
# naming is inconsistent across families ("_single" suffixes, optional vendor
# prefixes), and an invalid id is cheap to rule out (one cached 400) whereas a
# missed valid id would wrongly close off the whole ML comparison.
ML_MODELS = [
    "ecmwf_aifs025_single",
    "ecmwf_aifs025",
    "ecmwf_aifs025_ensemble",
    "ncep_aigfs025",
    "aigfs025",
    "ncep_hgefs025",
    "hgefs025",
    # The documented "NCEP HGEFS 0.25 Ensemble Mean" entry: every short id
    # above is rejected, and this is the spelling the API accepts.
    "ncep_hgefs025_ensemble_mean",
]

# Windows chosen around the documented archive starts (AIFS 2025-02-20, AIGFS
# and HGEFS 2026-01-07), plus one pre-start window per family to test whether
# the vendor's stated start is real, and recent windows to confirm the feed is
# still live.
ML_WINDOWS = [
    ("2024-06-01", "2024-06-07"),   # before every ML archive start
    ("2025-02-21", "2025-02-27"),   # just after the documented AIFS start
    ("2025-06-01", "2025-06-07"),
    ("2025-12-01", "2025-12-07"),   # before the AIGFS/HGEFS start
    ("2026-01-08", "2026-01-14"),   # just after the documented AIGFS start
    ("2026-05-01", "2026-05-07"),
    ("2026-09-01", "2026-09-07"),
]

# A deliberately global spread: ML systems are global by construction, but the
# archive may be populated regionally, and a Bucharest-only answer could not
# tell "the model has no PoP" from "the model has no PoP here".
ML_CITIES = {
    "Bucharest": (LATITUDE, LONGITUDE),
    "London": (51.5072, -0.1276),
    "New York": (40.7128, -74.0060),
    "Tokyo": (35.6762, 139.6503),
    "Nairobi": (-1.2864, 36.8172),
    "Sydney": (-33.8688, 151.2093),
    "Sao Paulo": (-23.5505, -46.6333),
    "Delhi": (28.6139, 77.2090),
}


# ---------------------------------------------------------------------------
# Probe machinery
# ---------------------------------------------------------------------------
def _series_stats(payload: dict, variables: list[str]) -> dict:
    """Per-variable {n, n_non_null} plus the variables the API actually echoed.

    A variable the API declines to recognise is simply absent from `hourly`,
    which is the difference between "unknown name" and "known but empty".
    """
    h = payload.get("hourly", {})
    n = len(h.get("time", []))
    stats = {}
    for v in variables:
        if v not in h:
            stats[v] = {"served": False}
            continue
        vals = h[v] or []
        stats[v] = {"served": True, "n": len(vals),
                    "n_non_null": sum(1 for x in vals if x is not None)}
    return {"n_hours": n, "variables": stats}


def _probe(url: str, params: dict, variables: list[str]) -> dict:
    payload = fetch_json(url, params)
    if is_error(payload):
        why = reason(payload)
        if "429" in why:
            time.sleep(60)
        return {"error": why}
    return _series_stats(payload, variables)


def _usable(window_result: dict, variable: str) -> bool:
    if "error" in window_result:
        return False
    s = window_result["variables"].get(variable, {})
    if not s.get("served") or not s.get("n"):
        return False
    return s["n_non_null"] / s["n"] >= MIN_USABLE_FRACTION


def probe_leads(results: dict, save) -> None:
    """Q1: precipitation_probability_previous_dayN on the Previous Runs API."""
    lead_vars = [f"precipitation_probability_previous_day{d}" for d in LEAD_DAYS]
    variables = lead_vars + PREV_CONTROLS + [BOGUS_VARIABLE]
    node = results.setdefault("q1_previous_runs_pop_at_lead", {})
    node.setdefault("variables_requested", variables)

    for model in PREV_MODELS:
        mnode = node.setdefault("models", {}).setdefault(model, {})
        for s, e in PREV_WINDOWS:
            if s in mnode and "error" not in mnode[s]:
                print(f"  prev {model:14s} {s} cached")
                continue
            mnode[s] = _probe(API_PREVIOUS_RUNS, {
                "latitude": LATITUDE, "longitude": LONGITUDE, "timezone": "UTC",
                "hourly": ",".join(variables),
                "models": model, "start_date": s, "end_date": e,
            }, variables)
            save(results)
            r = mnode[s]
            if "error" in r:
                print(f"  prev {model:14s} {s} ERROR {r['error'][:70]}")
                continue
            served = [v for v, st in r["variables"].items() if st.get("served")]
            non_null = {v: r["variables"][v]["n_non_null"] for v in lead_vars
                        if r["variables"][v].get("served")}
            print(f"  prev {model:14s} {s} n={r['n_hours']:3d} "
                  f"served={len(served)}/{len(variables)} "
                  f"lead-PoP non-null={sum(non_null.values())}")
            time.sleep(0.5)


def probe_ml(results: dict, save) -> None:
    """Q2: precipitation_probability for the ML models in the archive."""
    variables = ["precipitation_probability", "precipitation"]
    node = results.setdefault("q2_ml_models_historical_forecast", {})

    for model in ML_MODELS:
        mnode = node.setdefault("models", {}).setdefault(model, {})
        # One cheap city-window first: an unknown model id fails identically
        # everywhere, so spending eight cities x seven windows on it is waste.
        probe_city = "Bucharest"
        lat, lon = ML_CITIES[probe_city]
        first = mnode.setdefault(probe_city, {})
        if "2025-06-01" not in first:
            first["2025-06-01"] = _probe(API_HISTORICAL_FORECAST, {
                "latitude": round(lat, 4), "longitude": round(lon, 4),
                "timezone": "UTC", "hourly": ",".join(variables),
                "models": model,
                "start_date": "2025-06-01", "end_date": "2025-06-07",
            }, variables)
            save(results)
        if "error" in first["2025-06-01"]:
            why = first["2025-06-01"]["error"]
            print(f"  ml   {model:22s} INVALID ID / refused: {why[:80]}")
            if "429" not in why:
                continue

        for city, (lat, lon) in ML_CITIES.items():
            cnode = mnode.setdefault(city, {})
            for s, e in ML_WINDOWS:
                if s in cnode and "error" not in cnode[s]:
                    continue
                cnode[s] = _probe(API_HISTORICAL_FORECAST, {
                    "latitude": round(lat, 4), "longitude": round(lon, 4),
                    "timezone": "UTC", "hourly": ",".join(variables),
                    "models": model, "start_date": s, "end_date": e,
                }, variables)
                save(results)
                r = cnode[s]
                if "error" in r:
                    print(f"  ml   {model:22s} {city:10s} {s} "
                          f"ERROR {r['error'][:50]}")
                    continue
                pop = r["variables"]["precipitation_probability"]
                pr = r["variables"]["precipitation"]
                print(f"  ml   {model:22s} {city:10s} {s} n={r['n_hours']:3d} "
                      f"PoP {pop.get('n_non_null', 0)}/{pop.get('n', 0)} "
                      f"prcp {pr.get('n_non_null', 0)}/{pr.get('n', 0)}")
                time.sleep(0.5)


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------
def summarise_q1(results: dict) -> None:
    node = results.get("q1_previous_runs_pop_at_lead")
    if not node:
        return
    lead_vars = [f"precipitation_probability_previous_day{d}" for d in LEAD_DAYS]
    print("\n=== Q1: Previous Runs API, PoP at fixed lead offsets ===")
    for model, windows in sorted(node.get("models", {}).items()):
        print(f"\n  {model}")
        for var in lead_vars + PREV_CONTROLS + [BOGUS_VARIABLE]:
            served = [w for w, r in windows.items()
                      if "error" not in r
                      and r["variables"].get(var, {}).get("served")]
            tot = sum(r["variables"][var]["n"] for w, r in windows.items()
                      if w in served)
            nn = sum(r["variables"][var]["n_non_null"] for w, r in windows.items()
                     if w in served)
            usable = sorted(w for w, r in windows.items() if _usable(r, var))
            frac = f"{nn / tot:.3f}" if tot else "n/a"
            print(f"    {var:46s} name_accepted={'yes' if served else 'NO ':3s} "
                  f"non_null={nn}/{tot} ({frac}) "
                  f"earliest_usable={usable[0] if usable else '-'}")


def summarise_q2(results: dict) -> None:
    node = results.get("q2_ml_models_historical_forecast")
    if not node:
        return
    print("\n=== Q2: Historical Forecast API, machine-learning models ===")
    for model, cities in sorted(node.get("models", {}).items()):
        errs = {r["error"] for c in cities.values() for r in c.values()
                if "error" in r}
        ok = [(c, w, r) for c, wins in cities.items() for w, r in wins.items()
              if "error" not in r]
        if not ok:
            print(f"\n  {model:22s} INVALID / not served  "
                  f"({'; '.join(sorted(errs))[:110]})")
            continue
        tot = sum(r["variables"]["precipitation_probability"].get("n", 0)
                  for _, _, r in ok)
        nn = sum(r["variables"]["precipitation_probability"].get("n_non_null", 0)
                 for _, _, r in ok)
        pr_nn = sum(r["variables"]["precipitation"].get("n_non_null", 0)
                    for _, _, r in ok)
        usable = sorted({w for c, w, r in ok
                         if _usable(r, "precipitation_probability")})
        cities_ok = sorted({c for c, w, r in ok
                            if _usable(r, "precipitation_probability")})
        frac = f"{nn / tot:.3f}" if tot else "n/a"
        print(f"\n  {model:22s} valid id; PoP non-null {nn}/{tot} ({frac}); "
              f"precipitation non-null {pr_nn}/{tot}")
        print(f"    earliest usable PoP window: {usable[0] if usable else '-'}")
        print(f"    cities with usable PoP: "
              f"{', '.join(cities_ok) if cities_ok else '-'}")


# ---------------------------------------------------------------------------
# Refinement: bisect the true archive start
# ---------------------------------------------------------------------------
# The window scan bounds a start date only to within months. Where a field is
# served at all, the paper needs the day, because the matched-window design
# (plan D1) intersects archives across models. Bisection costs ~9 one-day
# requests per (model, variable) and assumes the archive is monotone in time -
# nothing before the start, data after - which the window scan verifies first.
REFINE_MODELS = [
    ("ecmwf_aifs025_single", "2024-06-01", "2026-09-07"),
    ("ecmwf_aifs025", "2024-06-01", "2026-09-07"),
    ("ncep_aigfs025", "2025-12-01", "2026-09-07"),
    ("ncep_hgefs025_ensemble_mean", "2025-12-01", "2026-09-07"),
]


def _has_data(model: str, day: str, variable: str) -> bool:
    lat, lon = ML_CITIES["Bucharest"]
    r = _probe(API_HISTORICAL_FORECAST, {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "timezone": "UTC", "hourly": "precipitation_probability,precipitation",
        "models": model, "start_date": day, "end_date": day,
    }, [variable])
    if "error" in r:
        return False
    s = r["variables"].get(variable, {})
    return bool(s.get("served") and s.get("n_non_null"))


def probe_refine(results: dict, save) -> None:
    from datetime import date

    node = results.setdefault("q2_archive_start_bisection", {})
    for model, lo_s, hi_s in REFINE_MODELS:
        mnode = node.setdefault(model, {})
        for variable in ("precipitation", "precipitation_probability"):
            if variable in mnode:
                print(f"  bisect {model:28s} {variable:26s} cached "
                      f"-> {mnode[variable]}")
                continue
            lo, hi = date.fromisoformat(lo_s), date.fromisoformat(hi_s)
            if not _has_data(model, hi.isoformat(), variable):
                mnode[variable] = None      # never served, even at the end
                save(results)
                print(f"  bisect {model:28s} {variable:26s} never served")
                continue
            if _has_data(model, lo.isoformat(), variable):
                mnode[variable] = f"<={lo.isoformat()}"
                save(results)
                print(f"  bisect {model:28s} {variable:26s} "
                      f"starts at or before {lo}")
                continue
            while (hi - lo).days > 1:
                mid = lo + (hi - lo) / 2
                if _has_data(model, mid.isoformat(), variable):
                    hi = mid
                else:
                    lo = mid
            mnode[variable] = hi.isoformat()
            save(results)
            print(f"  bisect {model:28s} {variable:26s} first day {hi}")


def summarise_refine(results: dict) -> None:
    node = results.get("q2_archive_start_bisection")
    if not node:
        return
    print("\n=== Q2b: first archive day at Bucharest (bisected) ===")
    for model, v in sorted(node.items()):
        print(f"  {model:30s} precipitation={str(v.get('precipitation')):12s} "
              f"precipitation_probability={v.get('precipitation_probability')}")


# ---------------------------------------------------------------------------
# Context probes: what the two negative answers leave on the table
# ---------------------------------------------------------------------------
# A bare "PoP is not served" is not actionable on its own. Three follow-ups
# turn each negative into a decision:
#   1. Which variables ARE archived at lead, i.e. what a derived or
#      deterministic lead-time analysis can be built from.
#   2. Whether an ML model id is an alias for a discontinued archive - a model
#      that stops mid-window cannot enter a matched-window comparison even for
#      amounts.
#   3. Whether PoP for the ML models is served by the live Forecast API, which
#      decides whether forward collection (plan Task 8) could recover it.
LEAD_INVENTORY_VARIABLES = [
    "temperature_2m", "precipitation", "rain", "showers", "snowfall",
    "cloud_cover", "weather_code", "relative_humidity_2m", "wind_speed_10m",
    "pressure_msl", "cape", "precipitation_probability",
]
ALIAS_SCAN_MODELS = ["ecmwf_aifs025", "ecmwf_aifs025_single"]
LIVE_POP_MODELS = ["ecmwf_aifs025_single", "ncep_aigfs025",
                   "ncep_hgefs025_ensemble_mean", "ecmwf_ifs025"]


def probe_context(results: dict, save) -> None:
    from datetime import date

    from config import API_FORECAST

    lat, lon = ML_CITIES["Bucharest"]

    # 1. Lead-time variable inventory (Previous Runs, day-1 offset).
    inv = results.setdefault("q1b_lead_variable_inventory", {})
    for model in PREV_MODELS:
        mnode = inv.setdefault(model, {})
        for base in LEAD_INVENTORY_VARIABLES:
            if base in mnode:
                continue
            var = f"{base}_previous_day1"
            r = _probe(API_PREVIOUS_RUNS, {
                "latitude": lat, "longitude": lon, "timezone": "UTC",
                "hourly": var, "models": model,
                "start_date": "2025-06-01", "end_date": "2025-06-02",
            }, [var])
            mnode[base] = ({"error": r["error"]} if "error" in r
                           else r["variables"][var])
            save(results)

    # 2. Monthly presence scan for the AIFS ids (is one a dead alias?).
    scan = results.setdefault("q2c_ml_monthly_presence", {})
    days = [f"{y}-{m:02d}-15" for y in (2024, 2025, 2026) for m in range(1, 13)]
    days = [d for d in days if d <= "2026-09-07"]
    for model in ALIAS_SCAN_MODELS:
        mnode = scan.setdefault(model, {})
        for d in days:
            if d[:7] in mnode:
                continue
            r = _probe(API_HISTORICAL_FORECAST, {
                "latitude": lat, "longitude": lon, "timezone": "UTC",
                "hourly": "precipitation_probability,precipitation",
                "models": model, "start_date": d, "end_date": d,
            }, ["precipitation_probability", "precipitation"])
            if "error" in r:
                mnode[d[:7]] = {"error": r["error"]}
            else:
                mnode[d[:7]] = {
                    "pop": r["variables"]["precipitation_probability"]
                    .get("n_non_null", 0),
                    "prcp": r["variables"]["precipitation"].get("n_non_null", 0),
                }
            save(results)

    # 3. Is ML PoP available live, so forward collection could recover it?
    live = results.setdefault("q2d_live_forecast_pop", {})
    for model in LIVE_POP_MODELS:
        if model in live:
            continue
        r = _probe(API_FORECAST, {
            "latitude": lat, "longitude": lon, "timezone": "UTC",
            "hourly": "precipitation_probability,precipitation",
            "models": model, "past_days": 3, "forecast_days": 3,
        }, ["precipitation_probability", "precipitation"])
        live[model] = {"probed_on": date.today().isoformat(), **r}
        save(results)


def summarise_context(results: dict) -> None:
    inv = results.get("q1b_lead_variable_inventory")
    if inv:
        print("\n=== Q1b: which variables ARE archived at lead (day-1, 48 h) ===")
        for model, vars_ in sorted(inv.items()):
            served = [b for b, s in vars_.items() if s.get("n_non_null")]
            empty = [b for b, s in vars_.items()
                     if s.get("served") and not s.get("n_non_null")]
            print(f"  {model:14s} populated: {', '.join(sorted(served))}")
            print(f"  {'':14s} null      : {', '.join(sorted(empty))}")

    scan = results.get("q2c_ml_monthly_presence")
    if scan:
        print("\n=== Q2c: monthly presence of the AIFS ids at Bucharest ===")
        for model, months in sorted(scan.items()):
            have = sorted(m for m, v in months.items() if v.get("prcp"))
            print(f"  {model:22s} precipitation present "
                  f"{have[0] if have else '-'} .. {have[-1] if have else '-'} "
                  f"({len(have)}/{len(months)} months sampled)")

    live = results.get("q2d_live_forecast_pop")
    if live:
        print("\n=== Q2d: PoP on the live Forecast API (forward collection) ===")
        for model, r in sorted(live.items()):
            if "error" in r:
                print(f"  {model:30s} ERROR {r['error'][:60]}")
                continue
            s = r["variables"]["precipitation_probability"]
            print(f"  {model:30s} PoP {s.get('n_non_null', 0)}/{s.get('n', 0)}")


def load() -> dict:
    return json.loads(OUT.read_text()) if OUT.exists() else {}


def save(results: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2, sort_keys=True))


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    results = load()
    if which in ("all", "leads"):
        probe_leads(results, save)
    if which in ("all", "ml"):
        probe_ml(results, save)
    if which in ("all", "refine"):
        probe_refine(results, save)
    if which in ("all", "context"):
        probe_context(results, save)
    save(results)
    summarise_q1(results)
    summarise_q2(results)
    summarise_refine(results)
    summarise_context(results)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
