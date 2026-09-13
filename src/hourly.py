"""Hourly verification track: does hourly PoP hold up at hourly resolution?

Usage:  python src/hourly.py

The daily track answers "will it rain today". This answers "will it rain at
6pm", which is both more useful and far better powered: roughly 11,000 matched
hours against 731 matched days.

Truth is present-weather occurrence from NOAA ISD, not precipitation amount -
Bucharest stations do not report hourly amounts. See observations_hourly.py.

Both sides are timestamped in UTC, so this join needs no local-day conversion
at all, which removes the entire class of off-by-one error that corrupted the
daily track. The shift scan below verifies that claim rather than assuming it.
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from calibration import (
    brier_decomposition,
    consistency_bars,
    effective_sample_size,
    reliability_table,
)
from config import (FIGURES, HOURLY_PRIMARY_STATION, ISD_STATIONS, MIN_BIN_COUNT,
                    N_PROB_BINS, PROCESSED, RAW)


def build() -> pd.DataFrame:
    pop = pd.read_parquet(RAW / "historical_forecast_pop.parquet")
    pop = pop.dropna(subset=["precipitation_probability"]).copy()
    pop["valid_hour"] = pd.to_datetime(pop["time"], utc=True)
    pop["forecast_prob"] = pop["precipitation_probability"] / 100.0

    obs = pd.read_parquet(RAW / "station_hourly.parquet")
    obs["valid_hour"] = pd.to_datetime(obs["valid_hour"], utc=True)

    prim = obs[obs.station == HOURLY_PRIMARY_STATION][
        ["valid_hour", "obs_precip", "n_reports"]]

    df = pop[["valid_hour", "forecast_prob", "precipitation"]].merge(
        prim, on="valid_hour", how="inner")
    df = df.rename(columns={"precipitation": "forecast_precip_mm"})
    df["observed_event"] = df["obs_precip"].astype(bool)
    return df.sort_values("valid_hour").reset_index(drop=True)


def shift_scan(df: pd.DataFrame) -> pd.Series:
    """Correlation of forecast probability with observation at +/- N hours.

    If the timestamp conventions match, this peaks at zero. A peak elsewhere
    means the hourly values are offset (for instance if the API labels an hour
    by its end rather than its start).
    """
    p = df.forecast_prob.reset_index(drop=True)
    e = df.observed_event.astype(float).reset_index(drop=True)
    return pd.Series({k: p.corr(e.shift(k)) for k in range(-3, 4)})


def analyse(df: pd.DataFrame) -> dict:
    prob = df.forecast_prob.values
    event = df.observed_event.values.astype(float)

    # Equal-WIDTH bins here, unlike the daily track. 77% of hours carry a PoP of
    # exactly zero, so equal-count quantile edges collapse onto each other and
    # yield only three usable bins - too coarse to see the shape of the curve.
    # With ~10,700 samples there is ample data to fill equal-width bins instead.
    kw = dict(n_bins=N_PROB_BINS, equal_count=False)

    tbl = reliability_table(prob, event, **kw)
    tbl = tbl.merge(consistency_bars(prob, event, **kw), on="bin", how="left")
    tbl["significant"] = (tbl.obs_freq < tbl.cons_lo) | (tbl.obs_freq > tbl.cons_hi)
    tbl["is_sparse"] = tbl["n"] < MIN_BIN_COUNT

    m = brier_decomposition(prob, event, **kw)
    m["ess"] = effective_sample_size(event)
    return {"tbl": tbl, "m": m}


def plot(df: pd.DataFrame, res: dict) -> None:
    tbl, m = res["tbl"], res["m"]
    fig, (ax, axh) = plt.subplots(
        2, 1, figsize=(7.0, 8.2), sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08})

    ax.plot([0, 1], [0, 1], "k--", lw=1, label="perfect calibration")
    ax.fill_between(tbl.mean_prob, tbl.cons_lo, tbl.cons_hi, color="#2ca02c",
                    alpha=0.13, label="range a perfect forecast would give (95%)")
    ax.axhline(m["base_rate"], color="grey", lw=0.8, ls=":",
               label=f"climatology ({m['base_rate']:.1%} of hours)")

    ok = ~tbl.significant
    ax.plot(tbl.mean_prob[ok], tbl.obs_freq[ok], "o", ms=7, color="#1f77b4",
            label="within chance")
    ax.plot(tbl.mean_prob[~ok], tbl.obs_freq[~ok], "o", ms=7, color="#cc4b3d",
            label="significantly miscalibrated")
    ax.plot(tbl.mean_prob, tbl.obs_freq, "-", lw=1.8, color="#1f77b4")
    for _, r in tbl.iterrows():
        ax.annotate(f"n={int(r.n)}", (r.mean_prob, r.obs_freq),
                    textcoords="offset points", xytext=(6, -11), fontsize=7.5,
                    color="#444")

    ax.set_ylabel("Fraction of hours with precipitation observed")
    ax.set_title("Bucharest HOURLY precipitation forecast calibration\n"
                 f"Open-Meteo hourly PoP vs ISD present weather at "
                 f"{ISD_STATIONS[HOURLY_PRIMARY_STATION][2]}  (n={len(df):,} hours)",
                 fontsize=10.5)
    ax.set_xlim(-0.02, 1.02); ax.set_ylim(-0.02, 1.02)
    ax.grid(alpha=0.25); ax.legend(fontsize=8.5, loc="upper left")

    axh.hist(df.forecast_prob, bins=np.linspace(0, 1, 21), color="#777",
             edgecolor="white")
    axh.set_yscale("log")
    axh.set_xlabel("Forecast probability of precipitation for that hour")
    axh.set_ylabel("Hours (log)")
    axh.grid(alpha=0.25)
    axh.set_title("Sharpness: how often each probability is issued", fontsize=9)

    path = FIGURES / "reliability_hourly.png"
    fig.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {path}")


def main() -> None:
    df = build()
    out = PROCESSED / "verification_hourly.parquet"
    df.to_parquet(out, index=False)
    print(f"built {out}: {len(df):,} matched hours, "
          f"{df.valid_hour.min()} to {df.valid_hour.max()}")

    print("\n=== alignment shift scan (must peak at 0) ===")
    sc = shift_scan(df)
    print("  " + "  ".join(f"{k:+d}h:{v:.3f}" for k, v in sc.items()))
    peak = int(sc.idxmax())
    print(f"  peak at {peak:+d} h -> {'OK' if peak == 0 else 'MISALIGNED'}")
    assert peak == 0, f"hourly join misaligned: correlation peaks at {peak:+d} h"

    res = analyse(df)
    m = res["m"]
    print("\n=== hourly reliability table ===")
    print(res["tbl"][["lo", "hi", "mean_prob", "obs_freq", "cons_lo", "cons_hi",
                      "n", "significant"]]
          .to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    print("\n=== hourly Brier decomposition ===")
    for k in ("n", "base_rate", "brier", "reliability", "resolution",
              "uncertainty", "brier_skill_score", "ece", "identity_error"):
        print(f"  {k:20s} {m[k]:.5f}")
    print(f"  {'effective_n':20s} {m['ess']:.0f}  (raw n={m['n']})")

    plot(df, res)

    # Station-to-station spread: an observing-practice caveat, not forecast error.
    obs = pd.read_parquet(RAW / "station_hourly.parquet")
    print("\n=== detection rate by station (observing-practice caveat) ===")
    for name, g in obs.groupby("station"):
        km = ISD_STATIONS[name][1]
        print(f"  {name:14s} {km:5.1f} km  {len(g):6,} hours  "
              f"precip in {g.obs_precip.mean():.1%}  "
              f"({g.n_reports.mean():.1f} reports/hour)")


if __name__ == "__main__":
    main()
