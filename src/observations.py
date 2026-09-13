"""Task 9: station observations from GHCN-Daily.

GHCN-Daily is the primary ground truth. ERA5 (collected separately) is only a
cross-check: verifying an ECMWF forecast against an ECMWF reanalysis flatters
the forecast, so the headline numbers must rest on independent observations.

Usage:  python src/observations.py
"""

from __future__ import annotations

import pandas as pd

from config import GHCN_SCALE, PRCP_DAY_OFFSET_DAYS, RAW, STATIONS

GHCN_URL = ("https://www.ncei.noaa.gov/data/"
            "global-historical-climatology-network-daily/access/{sid}.csv")


def load_station(name: str) -> pd.DataFrame:
    """Load one station's daily record, scaled to mm and degrees Celsius.

    `local_date` is the day the weather actually happened. For temperature that
    is the GHCN date as-is; for precipitation the GHCN date is shifted by
    PRCP_DAY_OFFSET_DAYS to undo the morning-observation convention (config.py
    records the evidence establishing the offset).
    """
    sid = STATIONS[name][0]
    path = RAW / f"ghcn_{sid}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing - download it from {GHCN_URL.format(sid=sid)}"
        )

    df = pd.read_csv(path, low_memory=False)
    ghcn_date = pd.to_datetime(df["DATE"])

    scaled = pd.DataFrame({"ghcn_date": ghcn_date})
    for col in ("PRCP", "TMAX", "TMIN", "TAVG"):
        if col in df.columns:
            # GHCN stores these in tenths; verified against ERA5 magnitudes.
            scaled[col.lower()] = df[col] / GHCN_SCALE

    # Temperature keeps the stated date.
    temp_cols = [c for c in ("tmax", "tmin", "tavg") if c in scaled]
    temperature = scaled[["ghcn_date"] + temp_cols].copy()
    temperature["local_date"] = temperature.pop("ghcn_date").dt.date

    # Precipitation moves back to the day it actually fell.
    precip = scaled[["ghcn_date", "prcp"]].copy()
    precip["local_date"] = (
        precip.pop("ghcn_date") + pd.Timedelta(days=PRCP_DAY_OFFSET_DAYS)
    ).dt.date

    merged = precip.merge(temperature, on="local_date", how="outer")
    merged["station"] = name
    return merged.sort_values("local_date")


def build_observations() -> pd.DataFrame:
    frames = [load_station(n) for n in STATIONS]
    obs = pd.concat(frames, ignore_index=True).sort_values(["station", "local_date"])

    out = RAW / "station_observations.parquet"
    obs.to_parquet(out, index=False)

    print(f"wrote {out}  rows={len(obs)}")
    for name, g in obs.groupby("station"):
        g24 = g[g.local_date >= pd.Timestamp("2024-01-01").date()]
        cols = [c for c in ("prcp", "tmax", "tmin") if c in g24 and g24[c].notna().any()]
        summary = ", ".join(f"{c}={int(g24[c].notna().sum())}" for c in cols)
        print(f"  {name:8s} 2024+: {len(g24)} days  non-null {summary}  "
              f"({g24.local_date.min()} .. {g24.local_date.max()})")
    return obs


if __name__ == "__main__":
    build_observations()
