"""Tasks 10-11: build the tidy verification tables and flag data quality.

All timestamps are held in UTC internally and converted to Europe/Bucharest
exactly once, here, at aggregation time. Off-by-one-day joins are the most
common bug in verification studies, so the conversion happens in one place and
is regression-tested by src/validate_join.py (Task 12).

Produces two tables, matching the two archive-backed tracks:

  verification_pop.parquet   Track B - native PoP, short lead, 2024-04-25+
  verification_leads.parquet Track A - deterministic, leads 1-7, 2024-01-01+

Usage:  python src/build_dataset.py
"""

from __future__ import annotations

import pandas as pd

from config import (
    LEAD_DAYS,
    LOCAL_TZ,
    OBS_END,
    PRIMARY_STATION,
    PROCESSED,
    RAIN_THRESHOLD_MM,
    RAW,
    TEMPERATURE_STATION,
)


def _to_local_days(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the Europe/Bucharest calendar date to a UTC hourly frame."""
    local = df["time"].dt.tz_convert(LOCAL_TZ)
    df = df.copy()
    df["local_date"] = local.dt.date
    return df


def _load_obs() -> tuple[pd.DataFrame, pd.DataFrame]:
    obs = pd.read_parquet(RAW / "station_observations.parquet")
    obs = obs[obs.local_date <= pd.Timestamp(OBS_END).date()]
    rain = obs[obs.station == PRIMARY_STATION][["local_date", "prcp"]]
    rain = rain.rename(columns={"prcp": "obs_precip_mm"})
    temp = obs[obs.station == TEMPERATURE_STATION][["local_date", "tmax", "tmin"]]
    temp = temp.rename(columns={"tmax": "obs_tmax", "tmin": "obs_tmin"})
    return rain, temp


def build_pop_table() -> pd.DataFrame:
    """Track B: daily PoP vs observed rain occurrence."""
    df = _to_local_days(pd.read_parquet(RAW / "historical_forecast_pop.parquet"))

    daily = df.groupby("local_date").agg(
        forecast_prob_max=("precipitation_probability", "max"),
        forecast_prob_mean=("precipitation_probability", "mean"),
        forecast_precip_mm=("precipitation", "sum"),
        n_hours=("time", "count"),
        n_prob_null=("precipitation_probability", lambda s: int(s.isna().sum())),
    ).reset_index()

    rain, _ = _load_obs()
    out = daily.merge(rain, on="local_date", how="inner")

    # Task 11: quality flags. A day is usable only if the forecast day is
    # complete and the station reported.
    out["flag_incomplete_day"] = out["n_hours"] != 24
    out["flag_prob_missing"] = out["n_prob_null"] > 0
    out["flag_obs_missing"] = out["obs_precip_mm"].isna()
    out["usable"] = ~(out.flag_incomplete_day | out.flag_prob_missing
                      | out.flag_obs_missing)

    out["forecast_prob"] = out["forecast_prob_max"] / 100.0
    out["observed_event"] = out["obs_precip_mm"] >= RAIN_THRESHOLD_MM
    out["lead_days"] = 0  # stitched analysis series: nominal lead ~0-1 day
    out["track"] = "B_native_pop"

    path = PROCESSED / "verification_pop.parquet"
    out.to_parquet(path, index=False)
    _report("Track B (native PoP)", out, path)
    return out


def build_lead_table() -> pd.DataFrame:
    """Track A: deterministic forecasts at fixed lead offsets 1-7 days."""
    df = _to_local_days(pd.read_parquet(RAW / "previous_runs_ecmwf_ifs025.parquet"))
    rain, temp = _load_obs()

    rows = []
    for lead in LEAD_DAYS:
        p_col, t_col = f"precipitation_previous_day{lead}", f"temperature_2m_previous_day{lead}"
        daily = df.groupby("local_date").agg(
            forecast_precip_mm=(p_col, "sum"),
            forecast_tmax=(t_col, "max"),
            forecast_tmin=(t_col, "min"),
            n_hours=("time", "count"),
            n_precip_null=(p_col, lambda s: int(s.isna().sum())),
        ).reset_index()
        daily["lead_days"] = lead
        rows.append(daily)

    out = pd.concat(rows, ignore_index=True)
    out = out.merge(rain, on="local_date", how="inner").merge(
        temp, on="local_date", how="left")

    out["flag_incomplete_day"] = out["n_hours"] != 24
    out["flag_forecast_missing"] = out["n_precip_null"] > 0
    out["flag_obs_missing"] = out["obs_precip_mm"].isna()
    out["usable"] = ~(out.flag_incomplete_day | out.flag_forecast_missing
                      | out.flag_obs_missing)

    out["observed_event"] = out["obs_precip_mm"] >= RAIN_THRESHOLD_MM
    out["forecast_event"] = out["forecast_precip_mm"] >= RAIN_THRESHOLD_MM
    out["track"] = "A_deterministic_leads"

    path = PROCESSED / "verification_leads.parquet"
    out.to_parquet(path, index=False)
    _report("Track A (deterministic leads)", out, path)
    return out


def _report(label: str, df: pd.DataFrame, path) -> None:
    u = df[df.usable]
    print(f"\n{label} -> {path.name}")
    print(f"  rows={len(df)}  usable={len(u)}  dropped={len(df) - len(u)}")
    for flag in [c for c in df.columns if c.startswith("flag_")]:
        n = int(df[flag].sum())
        if n:
            print(f"    {flag}: {n}")
    if "lead_days" in u and u.lead_days.nunique() > 1:
        for lead, g in u.groupby("lead_days"):
            print(f"    lead {lead}: n={len(g)} base rate={g.observed_event.mean():.3f}")
    else:
        print(f"    n={len(u)}  base rate={u.observed_event.mean():.3f}  "
              f"({u.local_date.min()} .. {u.local_date.max()})")


if __name__ == "__main__":
    build_pop_table()
    build_lead_table()
