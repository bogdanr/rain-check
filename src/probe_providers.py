"""Phase 1, Task 2: which models actually have a usable PoP archive, where?

The multi-provider league table (src/league.py) ranks models against each
other, but Open-Meteo's Historical Forecast API does not serve every model at
every location: regional models cover only part of the map, and several models
have PoP archives that start later than others. Committing to a model set
before measuring that would bias the ranking - a model missing half the cities
is not comparable to one covering all of them.

This probe asks the API directly, with small date-ranged requests (a few
fortnights spread over the archive), for every capital x every model in
PROVIDER_MODELS. For each pair it records whether precipitation probability is
served at all, the fraction of non-null hours, and the earliest probed window
with usable data - a lower bound on the archive depth, which is what the
ranking needs (matched windows, not claimed depth).

Design notes:

  * Resumable: the verdict JSON is rewritten after every (city, model) pair,
    and each HTTP response is cached by src/fetch.py, so an interrupted run
    resumes for free.
  * Rate-limit aware: fetch_json already throttles and backs off on 429; an
    extra polite sleep between models keeps the request pattern bursty rather
    than sustained. A 429 that survives the backoff is recorded as an error
    verdict, not raised - the probe must degrade gracefully.
  * A model that returns no data for a city is a finding, not a failure: the
    league table marks it unavailable rather than hiding it (plan risk 4).

Usage:  python src/probe_providers.py              # all cities x all models
        python src/probe_providers.py Bucharest    # one city
        python src/probe_providers.py Bucharest icon_eu   # one city, one model
"""

from __future__ import annotations

import json
import sys
import time

import pandas as pd

from config import (
    API_HISTORICAL_FORECAST,
    PROVIDER_COVERAGE,
    PROVIDER_MODELS,
    PROVIDER_SPACING_S,
)
from fetch import fetch_json, is_error, reason

# Short probe windows spread across the archive: an early, a middle and a late
# fortnight. Three windows are enough to (a) tell "no archive" from "archive",
# (b) bound the archive depth from below, and (c) catch a model whose PoP
# starts mid-archive - at a twentieth of the cost of probing every month.
PROBE_WINDOWS = [
    ("2024-05-01", "2024-05-14"),
    ("2025-06-01", "2025-06-14"),
    ("2026-07-01", "2026-07-14"),
]

# A window counts as "usable" if at least this fraction of its hours carry a
# non-null PoP. Some models report PoP only in part of the hour range; below
# this the series is too thin to verify daily maxima against.
MIN_USABLE_FRACTION = 0.5

# Polite pause between models (within a city). fetch_json spaces individual
# requests; this spaces the bursts so one model's archive walk does not run
# straight into the next one's.
MODEL_PAUSE_S = PROVIDER_SPACING_S


def _probe_window(lat: float, lon: float, model: str, s: str, e: str) -> dict:
    """One small request. Returns {n_hours, n_non_null} or {error}."""
    payload = fetch_json(API_HISTORICAL_FORECAST, {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "timezone": "UTC",
        "hourly": "precipitation_probability,precipitation",
        "models": model, "start_date": s, "end_date": e,
    })
    if is_error(payload):
        return {"error": reason(payload)}
    h = payload.get("hourly", {})
    pop = h.get("precipitation_probability") or []
    return {"n_hours": len(pop),
            "n_non_null": sum(1 for v in pop if v is not None)}


def probe_city_model(city_name: str, lat: float, lon: float,
                     model: str) -> dict:
    """The availability verdict for one (city, model) pair."""
    windows = {}
    for s, e in PROBE_WINDOWS:
        windows[s[:7]] = _probe_window(lat, lon, model, s, e)
        if "error" in windows[s[:7]]:
            # Deterministic refusals (model not served here) repeat identically
            # and are cached by fetch.py; a rate-limit error is transient, so
            # pause and let a later re-run retry it rather than recording it.
            if "429" in windows[s[:7]]["error"]:
                time.sleep(60)
            break
        time.sleep(0.5)

    usable = [k for k, w in windows.items()
              if "error" not in w and w["n_hours"]
              and w["n_non_null"] / w["n_hours"] >= MIN_USABLE_FRACTION]
    if usable:
        earliest = min(usable)
        return {
            "available": True,
            "archive_depth_from": earliest + "-01",
            "usable_windows": usable,
            "windows": windows,
        }
    err = next((w["error"] for w in windows.values() if "error" in w), None)
    return {
        "available": False,
        "archive_depth_from": None,
        "usable_windows": [],
        "windows": windows,
        "why": err or "no window with usable precipitation probability",
    }


def load_verdicts() -> dict:
    if PROVIDER_COVERAGE.exists():
        return json.loads(PROVIDER_COVERAGE.read_text())
    return {"probed": [], "cities": {}}


def save_verdicts(v: dict) -> None:
    PROVIDER_COVERAGE.parent.mkdir(parents=True, exist_ok=True)
    PROVIDER_COVERAGE.write_text(json.dumps(v, indent=2, sort_keys=True))


def main() -> None:
    from config import load_capitals

    cities = load_capitals()
    models = list(PROVIDER_MODELS)

    # Optional positional filters: [city] [model]
    args = sys.argv[1:]
    if args and args[0] in cities:
        cities = {args[0]: cities[args[0]]}
        args = args[1:]
    if args:
        models = [m for m in models if m == args[0]]

    verdicts = load_verdicts()
    total = len(cities) * len(models)
    done = 0
    for city_name, city in sorted(cities.items()):
        cver = verdicts["cities"].setdefault(city_name, {})
        for model in models:
            done += 1
            key = f"{city_name}|{model}"
            if key in verdicts["probed"] and model in cver:
                print(f"  [{done}/{total}] {city_name:14s} {model:34s} cached")
                continue
            res = probe_city_model(city_name, city.latitude, city.longitude,
                                   model)
            cver[model] = res
            verdicts["probed"].append(key)
            save_verdicts(verdicts)
            tag = (f"OK depth>={res['archive_depth_from']}" if res["available"]
                   else f"NO ({res.get('why', 'unavailable')})")
            print(f"  [{done}/{total}] {city_name:14s} {model:34s} {tag}")
            time.sleep(MODEL_PAUSE_S)

    # Summary
    rows = []
    for city_name, cver in verdicts["cities"].items():
        for model, res in cver.items():
            rows.append({"city": city_name, "model": model,
                         "available": res["available"],
                         "depth": res["archive_depth_from"]})
    df = pd.DataFrame(rows)
    print(f"\nwrote {PROVIDER_COVERAGE}")
    if len(df):
        piv = df.pivot_table(index="model", columns="city",
                             values="available", aggfunc="first")
        print("\nAvailability (True = usable PoP archive at this city):")
        print(piv.to_string())
        print("\nModels usable in at least one city: "
              + ", ".join(sorted(df[df.available].model.unique())))


if __name__ == "__main__":
    main()
