"""Archive collectors: Track A (Task 6), Track B, and ERA5 truth (Task 4).

Usage:
    python src/collect_archive.py previous_runs [model]
    python src/collect_archive.py hist_pop
    python src/collect_archive.py era5
    python src/collect_archive.py all
    python src/collect_archive.py all-providers [city]   # Phase 1, Task 3
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

import pandas as pd

from config import (
    API_HISTORICAL_FORECAST,
    API_HISTORICAL_WEATHER,
    API_PREVIOUS_RUNS,
    ARCHIVE_END,
    ARCHIVE_START,
    ERA5_MODEL,
    LATITUDE,
    LEAD_DAYS,
    LONGITUDE,
    PRIMARY_MODEL,
    PROVIDER_MODELS,
    PROVIDER_SPACING_S,
    RAW,
    City,
    load_capitals,
    load_cities,
)
from constants import POP_ARCHIVE_START
from fetch import fetch_json, is_error, network_calls, reason

BASE = {"latitude": LATITUDE, "longitude": LONGITUDE, "timezone": "UTC"}


def _chunks(start: str, end: str, months: int = 6):
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    while s <= e:
        nxt = min(e, s + timedelta(days=months * 30))
        yield s.isoformat(), nxt.isoformat()
        s = nxt + timedelta(days=1)


def _hourly_frame(payload: dict) -> pd.DataFrame:
    df = pd.DataFrame(payload["hourly"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def _collect_hourly(url, variables, start, end, label, extra=None):
    frames = []
    for s, e in _chunks(start, end):
        params = {**BASE, "hourly": ",".join(variables), "start_date": s, "end_date": e}
        if extra:
            params.update(extra)
        payload = fetch_json(url, params)
        if is_error(payload):
            print(f"  {label} {s}..{e}: SKIP ({reason(payload)})")
            continue
        frames.append(_hourly_frame(payload))
        print(f"  {label} {s}..{e}: {len(frames[-1])} hours")
    if not frames:
        raise RuntimeError(f"{label}: no data collected")
    return pd.concat(frames).drop_duplicates(subset="time").sort_values("time")


def collect_previous_runs(model: str = PRIMARY_MODEL) -> pd.DataFrame:
    """Track A: deterministic forecasts at fixed lead offsets, days 1-7."""
    variables = [f"temperature_2m_previous_day{d}" for d in LEAD_DAYS]
    variables += [f"precipitation_previous_day{d}" for d in LEAD_DAYS]
    variables += ["temperature_2m", "precipitation"]
    df = _collect_hourly(API_PREVIOUS_RUNS, variables, ARCHIVE_START, ARCHIVE_END,
                         f"prev[{model}]", extra={"models": model})
    df["model"] = model
    out = RAW / f"previous_runs_{model}.parquet"
    df.to_parquet(out, index=False)
    print(f"wrote {out}  rows={len(df)}  {df.time.min()} .. {df.time.max()}")
    return df


def collect_hist_pop() -> pd.DataFrame:
    """Track B: native precipitation probability at short lead."""
    variables = ["precipitation_probability", "precipitation", "temperature_2m",
                 "cloud_cover", "weather_code"]
    df = _collect_hourly(API_HISTORICAL_FORECAST, variables, POP_ARCHIVE_START,
                         ARCHIVE_END, "hist_pop")
    out = RAW / "historical_forecast_pop.parquet"
    df.to_parquet(out, index=False)
    print(f"wrote {out}  rows={len(df)}  "
          f"PoP non-null={int(df.precipitation_probability.notna().sum())}")
    return df


def collect_provider_pop(city: City, model: str, *, force: bool = False
                         ) -> tuple[pd.DataFrame, int]:
    """Task 3: hourly PoP for one pinned model at one city, resumable.

    The per-chunk HTTP responses are cached by fetch.py, so an interrupted
    archive walk resumes from the first missing chunk. The assembled parquet
    is the unit of completion: if it exists and is fresh the model is skipped
    entirely, which is what makes a multi-day, multi-model collection
    idempotent. Returns (series, fetched) - `fetched` is True only when this
    call issued at least one network request, so the caller can spend its
    politeness pauses only where they are actually needed. The second element is
    the number of network requests issued, so the caller can scale its pause
    to the size of the burst.
    """
    out = RAW / f"provider_pop_{city.name.lower().replace(' ', '_')}_{model}.parquet"
    if out.exists() and not force:
        df = pd.read_parquet(out)
        # A finished series is only reused while it reaches ARCHIVE_END. Once
        # the archive advances past the stored last day, re-walk the chunks:
        # every chunk except the current tail is a cache hit (no network, no
        # throttle), so refreshing costs one request per stale series per day.
        last = df.time.max().date() if len(df) else None
        if last is None:
            # Empty parquet = a cached "no archive data here" verdict: the
            # provider deterministically serves nothing for this pair, so
            # there is nothing to refresh and nothing to be polite about.
            print(f"  {city.name} {model}: cached no-data verdict")
            return df, 0
        if last >= date.fromisoformat(ARCHIVE_END) - timedelta(days=1):
            print(f"  {city.name} {model}: cached ({out.name})")
            return df, 0
        print(f"  {city.name} {model}: refreshing (stored through {last})")

    start = max(POP_ARCHIVE_START,
                PROVIDER_MODELS[model].archive_depth) \
        if model in PROVIDER_MODELS else POP_ARCHIVE_START
    variables = ["precipitation_probability", "precipitation"]
    frames = []
    net0 = network_calls()
    for s, e in _chunks(start, ARCHIVE_END):
        payload = fetch_json(API_HISTORICAL_FORECAST, {
            "latitude": round(city.latitude, 4),
            "longitude": round(city.longitude, 4),
            "timezone": "UTC", "hourly": ",".join(variables),
            "models": model, "start_date": s, "end_date": e,
        })
        if is_error(payload):
            print(f"  {city.name} {model} {s}..{e}: SKIP ({reason(payload)})")
            continue
        frames.append(_hourly_frame(payload))
    if not frames:
        print(f"  {city.name} {model}: no archive data - skipping")
        # Persist the verdict so later runs skip this pair without a pause.
        pd.DataFrame({"model": pd.Series(dtype=str)}).to_parquet(out, index=False)
        return pd.DataFrame(), network_calls() - net0
    df = pd.concat(frames).drop_duplicates(subset="time").sort_values("time")
    df["model"] = model
    df.to_parquet(out, index=False)
    print(f"wrote {out}  rows={len(df)}  "
          f"PoP non-null={int(df.precipitation_probability.notna().sum())}")
    return df, network_calls() - net0


def collect_all_providers(city_name: str | None = None) -> None:
    """Task 3: loop PROVIDER_MODELS with polite spacing between models.

    Stays under Open-Meteo's hourly limit the same way the Track A leg does:
    fetch.py throttles and backs off per request, and PROVIDER_SPACING_S adds
    a pause between each model's burst of chunk requests. A rate-limit failure
    on one model does not abort the rest - the cache makes a later re-run
    resume exactly where this one stopped.
    """
    import time

    cities = load_capitals()
    if city_name:
        cities = {k: v for k, v in cities.items() if k == city_name}
    for city in sorted(cities.values(), key=lambda c: c.name):
        print(f"\n=== providers @ {city.name} ===")
        for i, model in enumerate(PROVIDER_MODELS):
            try:
                _, n_req = collect_provider_pop(city, model)
            except RuntimeError as exc:
                if "429" not in str(exc) and "limit" not in str(exc).lower():
                    raise
                print(f"  {city.name} {model}: hourly limit reached - "
                      f"re-run later to resume from here")
                n_req = 99
            # Pause only after a real burst. A fully cached walk (0 requests)
            # or a one-chunk freshness refresh needs no pause beyond
            # fetch.py's per-request throttle; 5 s x models x cities of
            # unconditional sleep was the dominant cost of warm runs.
            if n_req > 2 and i < len(PROVIDER_MODELS) - 1:
                time.sleep(PROVIDER_SPACING_S)


def collect_era5() -> pd.DataFrame:
    """Task 4: ERA5 secondary ground truth, aggregated on local calendar days."""
    payload = fetch_json(API_HISTORICAL_WEATHER, {
        "latitude": LATITUDE, "longitude": LONGITUDE, "timezone": "Europe/Bucharest",
        # Pinned: the archive's default is ECMWF IFS (see config.ERA5_MODEL).
        "models": ERA5_MODEL,
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,temperature_2m_mean",
        "start_date": ARCHIVE_START, "end_date": ARCHIVE_END,
    })
    if is_error(payload):
        raise RuntimeError(f"ERA5 fetch failed: {reason(payload)}")
    df = pd.DataFrame(payload["daily"]).rename(columns={"time": "local_date"})
    df["local_date"] = pd.to_datetime(df["local_date"]).dt.date
    out = RAW / "era5_daily.parquet"
    df.to_parquet(out, index=False)
    print(f"wrote {out}  rows={len(df)}")
    return df


def collect_one_model_world(model: str) -> None:
    """One pinned model across the whole world-city set (Task 10a).

    The capitals carry all twelve provider models because the league table
    ranks them against each other. The world cities do not, and do not need
    to: the served-versus-ensemble comparison pins exactly one vendor model -
    the one from the same centre as the ensemble it is compared against - so
    widening that comparison needs this model and no other.

    Kept apart from `collect_all_providers` rather than folded into it with a
    flag, because that function's contract is "every model at every capital"
    and this one's is "one model everywhere". Running the twelve-model loop
    over 109 cities would be roughly 5,000 requests to answer a question that
    needs 500.
    """
    import time

    caps = set(load_capitals())
    cities = [c for name, c in sorted(load_cities().items()) if name not in caps]
    print(f"{model}: {len(cities)} world cities beyond the {len(caps)} capitals")
    for i, city in enumerate(cities):
        try:
            _, n_req = collect_provider_pop(city, model)
        except RuntimeError as exc:
            if "429" not in str(exc) and "limit" not in str(exc).lower():
                raise
            print(f"  {city.name}: hourly limit reached - re-run to resume")
            n_req = 99
        if n_req > 2 and i < len(cities) - 1:
            time.sleep(PROVIDER_SPACING_S)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "previous_runs":
        collect_previous_runs(sys.argv[2] if len(sys.argv) > 2 else PRIMARY_MODEL)
    elif cmd == "hist_pop":
        collect_hist_pop()
    elif cmd == "era5":
        collect_era5()
    elif cmd == "all-providers":
        collect_all_providers(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "model-world":
        collect_one_model_world(sys.argv[2] if len(sys.argv) > 2
                                else "gfs_seamless")
    elif cmd == "all":
        collect_previous_runs()
        collect_hist_pop()
        collect_era5()
    else:
        raise SystemExit(f"unknown command: {cmd}")
