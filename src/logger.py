"""Track C / Task 8: forward-collecting daily forecast logger.

Run daily via cron. Each run snapshots what is believed *today* about every day
up to 16 days ahead and appends it to the log. That (issued_date, valid_date)
pairing is exactly what a lead-resolved reliability diagram needs, and it is the
only available source of it: the probe on 2026-09-12 established that no
Open-Meteo archive serves native precipitation probability at fixed lead times.

Usage:  python src/logger.py
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from config import API_FORECAST, LATITUDE, LONGITUDE, RAW, TIMEZONE
from fetch import fetch_json, is_error, reason

LOG_PATH = RAW / "forecast_log.parquet"

DAILY_VARS = [
    "precipitation_probability_max",
    "precipitation_probability_mean",
    "precipitation_sum",
    "temperature_2m_max",
    "temperature_2m_min",
    "weather_code",
]


def run_logger() -> pd.DataFrame:
    issued_at = datetime.now(timezone.utc)
    payload = fetch_json(
        API_FORECAST,
        {
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "timezone": TIMEZONE,
            "daily": ",".join(DAILY_VARS),
            "forecast_days": 16,
        },
        use_cache=False,  # the whole point is a fresh snapshot each day
    )
    if is_error(payload):
        raise RuntimeError(f"logger fetch failed: {reason(payload)}")

    df = pd.DataFrame(payload["daily"]).rename(columns={"time": "valid_date"})
    df["valid_date"] = pd.to_datetime(df["valid_date"]).dt.date
    df["issued_at"] = issued_at
    df["issued_date"] = issued_at.date()
    df["lead_days"] = [(d - issued_at.date()).days for d in df["valid_date"]]

    if LOG_PATH.exists():
        df = pd.concat([pd.read_parquet(LOG_PATH), df])
        # one snapshot per (issued_date, valid_date); latest write wins
        df = df.drop_duplicates(subset=["issued_date", "valid_date"], keep="last")
    df = df.sort_values(["issued_date", "valid_date"])
    df.to_parquet(LOG_PATH, index=False)

    print(
        f"logged {len(payload['daily']['time'])} lead days at "
        f"{issued_at:%Y-%m-%d %H:%M} UTC -> {LOG_PATH} (total rows {len(df)}, "
        f"{df.issued_date.nunique()} issue days)"
    )
    return df


if __name__ == "__main__":
    run_logger()
