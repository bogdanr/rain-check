"""Hourly station observations from NOAA ISD (Integrated Surface Database).

Why present weather rather than precipitation amounts:

Bucharest stations do NOT report hourly precipitation amounts. Filaret reports
only 6- and 12-hour accumulations; the airports report mostly missing or 24-hour
totals. Measured on 2026-09-13, a full year of Baneasa data contained just one
hourly-period accumulation record.

What they DO report is present weather (`MW1`), roughly hourly, covering 85% of
hours, and crucially including explicit "no significant weather" codes - so a
dry hour is an observation, not an absence of one. That gives hourly
precipitation *occurrence*, which is exactly what a probability of
precipitation forecast claims. Occurrence is the better match for calibration
anyway: PoP is a probability of an event, not of an amount.

WMO code table 4677 (manual present weather), at time of observation:
    00-19  no precipitation falling now (mist, fog, haze, lightning...)
    20-29  precipitation in the PRECEDING hour, but not now  -> not counted
    30-39  duststorm, sandstorm, drifting snow                -> not counted
    40-49  fog                                                -> not counted
    50-59  drizzle          60-69  rain
    70-79  solid precipitation (snow, ice)
    80-90  showers          91-99  thunderstorm
So "precipitation observed now" is simply code >= 50.
"""

from __future__ import annotations

import io
import sys
import urllib.error
import urllib.request

import pandas as pd

from config import CACHE, ISD_STATIONS, RAW

BASE = "https://www.ncei.noaa.gov/data/global-hourly/access"
PRECIP_CODE_MIN = 50


def _fetch_year(station_id: str, year: int) -> pd.DataFrame | None:
    """Download one station-year, cached on disk."""
    cache = CACHE / f"isd_{station_id}_{year}.csv"
    if cache.exists():
        raw = cache.read_bytes()
    else:
        url = f"{BASE}/{year}/{station_id}.csv"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "calibration-study"})
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            print(f"  {station_id} {year}: HTTP {e.code} (no data)")
            return None
        cache.write_bytes(raw)
    return pd.read_csv(io.BytesIO(raw), low_memory=False)


def _codes(df: pd.DataFrame) -> pd.Series:
    """Extract present-weather codes from whichever columns carry them.

    MW1/MW2 are the manual observations and are far better populated here than
    the automated AW1, but AW1 is used as a fallback so hours covered only by an
    automated report are not silently discarded.
    """
    out = pd.DataFrame(index=df.index)
    for col in ("MW1", "MW2", "AW1", "AW2"):
        if col in df.columns:
            out[col] = (df[col].astype(str).str.split(",").str[0]
                        .pipe(pd.to_numeric, errors="coerce"))
    return out


def load_station(station_id: str, name: str, years: range) -> pd.DataFrame:
    frames = []
    for y in years:
        d = _fetch_year(station_id, y)
        if d is not None:
            frames.append(d)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)

    codes = _codes(df)
    if codes.empty:
        return pd.DataFrame()

    # A report counts as "precipitation now" if ANY of its weather slots says so.
    has_any = codes.notna().any(axis=1)
    is_precip = (codes >= PRECIP_CODE_MIN).any(axis=1)

    obs = pd.DataFrame({
        "valid_hour": pd.to_datetime(df["DATE"], utc=True).dt.floor("h"),
        "reported": has_any,
        "precip": is_precip & has_any,
    })
    obs = obs[obs.reported]

    # An hour is wet if any report within it saw precipitation; dry only if at
    # least one report explicitly said otherwise. Hours with no report at all
    # are dropped rather than assumed dry.
    hourly = (obs.groupby("valid_hour")
                 .agg(obs_precip=("precip", "any"), n_reports=("reported", "sum"))
                 .reset_index())
    hourly["station"] = name
    return hourly


def main() -> None:
    years = range(2024, 2027)
    frames = []
    for name, (sid, km, desc) in ISD_STATIONS.items():
        print(f"fetching ISD {desc} ({sid}, {km} km)")
        h = load_station(sid, name, years)
        if h.empty:
            print(f"  no usable present-weather data for {name}")
            continue
        rate = h.obs_precip.mean()
        print(f"  {len(h)} hours, {h.valid_hour.min()} to {h.valid_hour.max()}, "
              f"precip in {rate:.1%} of them")
        frames.append(h)

    if not frames:
        sys.exit("no hourly observations retrieved")

    out = pd.concat(frames, ignore_index=True)
    path = RAW / "station_hourly.parquet"
    out.to_parquet(path, index=False)
    print(f"wrote {path}  rows={len(out)}")


if __name__ == "__main__":
    main()
