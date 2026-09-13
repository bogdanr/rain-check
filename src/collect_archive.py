"""Archive collectors: Track A (Task 6), Track B, and ERA5 truth (Task 4).

Usage:
    python src/collect_archive.py previous_runs [model]
    python src/collect_archive.py hist_pop
    python src/collect_archive.py era5
    python src/collect_archive.py all
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
    LATITUDE,
    LEAD_DAYS,
    LONGITUDE,
    PRIMARY_MODEL,
    RAW,
)
from constants import POP_ARCHIVE_START
from fetch import fetch_json, is_error, reason

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


def collect_era5() -> pd.DataFrame:
    """Task 4: ERA5 secondary ground truth, aggregated on local calendar days."""
    payload = fetch_json(API_HISTORICAL_WEATHER, {
        "latitude": LATITUDE, "longitude": LONGITUDE, "timezone": "Europe/Bucharest",
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


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "previous_runs":
        collect_previous_runs(sys.argv[2] if len(sys.argv) > 2 else PRIMARY_MODEL)
    elif cmd == "hist_pop":
        collect_hist_pop()
    elif cmd == "era5":
        collect_era5()
    elif cmd == "all":
        collect_previous_runs()
        collect_hist_pop()
        collect_era5()
    else:
        raise SystemExit(f"unknown command: {cmd}")
