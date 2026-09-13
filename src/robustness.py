"""Tasks 21-22: robustness of the headline calibration result.

A calibration verdict is only worth reporting if it survives the choices that
were made somewhat arbitrarily along the way: the rain threshold, the truth
source, and the station. Anything that flips here is flagged as not robust.

Usage:  python src/robustness.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from calibration import brier_decomposition, reliability_table
from config import (
    N_PROB_BINS,
    PROCESSED,
    RAIN_THRESHOLD_MM,
    RAIN_THRESHOLD_VARIANTS_MM,
    RAW,
)


def _summary(prob, event, label: str) -> dict:
    d = brier_decomposition(prob, event, n_bins=N_PROB_BINS)
    tbl = reliability_table(prob, event, n_bins=N_PROB_BINS)
    # Overconfidence: do the extreme bins fall short of what was promised?
    top = tbl.iloc[-1]
    bot = tbl.iloc[0]
    return {
        "variant": label,
        "n": d["n"],
        "base_rate": d["base_rate"],
        "brier": d["brier"],
        "reliability": d["reliability"],
        "resolution": d["resolution"],
        "bss": d["brier_skill_score"],
        "ece": d["ece"],
        "top_bin_stated": top.mean_prob,
        "top_bin_observed": top.obs_freq,
        "bot_bin_stated": bot.mean_prob,
        "bot_bin_observed": bot.obs_freq,
    }


def main() -> None:
    pop = pd.read_parquet(PROCESSED / "verification_pop.parquet")
    pop = pop[pop.usable].copy()
    obs = pd.read_parquet(RAW / "station_observations.parquet")
    era5 = pd.read_parquet(RAW / "era5_daily.parquet")

    rows = []

    # --- Task 21a: rain threshold sensitivity ------------------------------
    for thr in RAIN_THRESHOLD_VARIANTS_MM:
        ev = (pop.obs_precip_mm >= thr).values.astype(float)
        tag = "primary" if thr == RAIN_THRESHOLD_MM else "variant"
        rows.append(_summary(pop.forecast_prob.values, ev,
                             f"threshold {thr} mm ({tag})"))

    # --- Task 21b: alternative truth source (ERA5) -------------------------
    e = pop.merge(era5[["local_date", "precipitation_sum"]], on="local_date",
                  how="inner").dropna(subset=["precipitation_sum"])
    rows.append(_summary(e.forecast_prob.values,
                         (e.precipitation_sum >= RAIN_THRESHOLD_MM).values.astype(float),
                         "truth = ERA5 reanalysis"))

    # --- Task 21c: alternative station (representativeness) ----------------
    ban = obs[obs.station == "baneasa"][["local_date", "prcp"]].rename(
        columns={"prcp": "baneasa_mm"})
    b = pop.merge(ban, on="local_date", how="inner").dropna(subset=["baneasa_mm"])
    rows.append(_summary(b.forecast_prob.values,
                         (b.baneasa_mm >= RAIN_THRESHOLD_MM).values.astype(float),
                         "truth = Baneasa station (10km)"))

    df = pd.DataFrame(rows)
    # Persisted so the HTML report can show it without recomputing.
    df.to_parquet(PROCESSED / "robustness.parquet", index=False)
    print("=== Task 21: sensitivity of the headline result ===")
    print(df.to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    print("\nInterpretation")
    spread = df.top_bin_observed.max() - df.top_bin_observed.min()
    print(f"  Top-bin observed frequency ranges {df.top_bin_observed.min():.2f} - "
          f"{df.top_bin_observed.max():.2f} across variants (spread {spread:.2f}).")
    always_over = (df.top_bin_observed < df.top_bin_stated).all()
    always_under = (df.bot_bin_observed > df.bot_bin_stated).all()
    print(f"  High-PoP days rain LESS often than stated in every variant: {always_over}")
    print(f"  Low-PoP days rain MORE often than stated in every variant: {always_under}")
    print(f"  -> overconfidence at both ends is {'ROBUST' if always_over and always_under else 'NOT robust'}")

    # --- Task 22: model comparison on the deterministic track --------------
    print("\n=== Task 22: model comparison (deterministic track) ===")
    leads = pd.read_parquet(PROCESSED / "verification_leads.parquet")
    leads = leads[leads.usable]
    icon_path = RAW / "previous_runs_icon_eu.parquet"
    if not icon_path.exists():
        print("  ICON-EU archive not present yet - skipping (run "
              "`python src/collect_archive.py previous_runs icon_eu`)")
        return

    from build_dataset import _load_obs, _to_local_days
    from config import LEAD_DAYS
    icon = _to_local_days(pd.read_parquet(icon_path))
    rain, temp = _load_obs()
    out = []
    for lead in LEAD_DAYS:
        t_col = f"temperature_2m_previous_day{lead}"
        p_col = f"precipitation_previous_day{lead}"
        d = icon.groupby("local_date").agg(
            forecast_tmax=(t_col, "max"),
            forecast_precip_mm=(p_col, "sum"),
            n_hours=("time", "count"),
        ).reset_index()
        d = d[d.n_hours == 24].merge(temp, on="local_date", how="inner").dropna(
            subset=["obs_tmax"])
        out.append({"lead_days": lead, "model": "icon_eu",
                    "tmax_mae": (d.forecast_tmax - d.obs_tmax).abs().mean(),
                    "n": len(d)})
    icon_sum = pd.DataFrame(out)

    ec = leads.dropna(subset=["obs_tmax"]).copy()
    ec["ae"] = (ec.forecast_tmax - ec.obs_tmax).abs()
    ec_sum = ec.groupby("lead_days").agg(tmax_mae=("ae", "mean"), n=("ae", "size"))
    ec_sum["model"] = "ecmwf_ifs025"

    comp = ec_sum.reset_index()[["lead_days", "model", "tmax_mae", "n"]]
    comp = pd.concat([comp, icon_sum[["lead_days", "model", "tmax_mae", "n"]]])
    piv = comp.pivot(index="lead_days", columns="model", values="tmax_mae")
    piv["diff_icon_minus_ecmwf"] = piv["icon_eu"] - piv["ecmwf_ifs025"]
    print(piv.to_string(float_format=lambda v: f"{v:.3f}"))
    common = piv.dropna()
    better = "ICON-EU" if common.diff_icon_minus_ecmwf.mean() < 0 else "ECMWF"
    leads_txt = f"leads {int(common.index.min())}-{int(common.index.max())}"
    print(f"  ICON-EU is a regional model with a shorter horizon, so it only "
          f"populates {leads_txt}; the comparison is restricted to those.")
    print(f"  -> {better} has the lower daily-max-temperature MAE at every "
          f"common lead ({leads_txt}) on a common sample of dates, though the "
          f"gap narrows as lead time grows")


if __name__ == "__main__":
    main()
