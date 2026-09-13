"""Analysis driver: reliability diagram, sharpness, Brier decomposition (Tasks 13-18).

Usage:  python src/analyze.py
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from calibration import (
    block_bootstrap_ci,
    brier_decomposition,
    consistency_bars,
    cross_validated_recalibration,
    effective_sample_size,
    isotonic_recalibration,
    reliability_table,
)
from config import FIGURES, MIN_BIN_COUNT, N_PROB_BINS, PROCESSED, RAIN_THRESHOLD_MM


def load_pop() -> pd.DataFrame:
    df = pd.read_parquet(PROCESSED / "verification_pop.parquet")
    return df[df.usable].sort_values("local_date").reset_index(drop=True)


def load_leads() -> pd.DataFrame:
    df = pd.read_parquet(PROCESSED / "verification_leads.parquet")
    return df[df.usable].sort_values(["lead_days", "local_date"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Task 13-15: headline reliability diagram with sharpness
# ---------------------------------------------------------------------------
def plot_reliability(df: pd.DataFrame) -> pd.DataFrame:
    prob, event = df.forecast_prob.values, df.observed_event.values.astype(float)
    tbl = reliability_table(prob, event, n_bins=N_PROB_BINS)
    ci = block_bootstrap_ci(df.local_date.values, prob, event, n_bins=N_PROB_BINS)
    tbl = tbl.merge(ci, on="bin", how="left")
    tbl = tbl.merge(consistency_bars(prob, event, n_bins=N_PROB_BINS), on="bin", how="left")
    # A bin is only evidence of real miscalibration if it falls outside the
    # range perfect forecasts would produce at this sample size.
    tbl["significant"] = (tbl.obs_freq < tbl.cons_lo) | (tbl.obs_freq > tbl.cons_hi)

    fig, (ax, axh) = plt.subplots(
        2, 1, figsize=(7.2, 8.4), sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08})

    ax.plot([0, 1], [0, 1], "k--", lw=1, label="perfect calibration", zorder=1)
    ax.fill_between(tbl.mean_prob, tbl.cons_lo, tbl.cons_hi, color="#2ca02c",
                    alpha=0.13, zorder=1,
                    label="range a perfect forecast would give (95%)")
    base = event.mean()
    ax.axhline(base, color="grey", lw=0.8, ls=":", zorder=1,
               label=f"climatology ({base:.0%})")

    solid = ~tbl.is_sparse
    ax.errorbar(tbl.mean_prob[solid], tbl.obs_freq[solid],
                yerr=[(tbl.obs_freq - tbl.ci_lo)[solid].clip(lower=0),
                      (tbl.ci_hi - tbl.obs_freq)[solid].clip(lower=0)],
                fmt="o-", color="#1f77b4", capsize=3, lw=1.8, ms=7, zorder=3,
                label="observed frequency (95% block bootstrap)")
    if tbl.is_sparse.any():
        ax.errorbar(tbl.mean_prob[tbl.is_sparse], tbl.obs_freq[tbl.is_sparse],
                    yerr=[(tbl.obs_freq - tbl.ci_lo)[tbl.is_sparse].clip(lower=0),
                          (tbl.ci_hi - tbl.obs_freq)[tbl.is_sparse].clip(lower=0)],
                    fmt="o", mfc="white", color="#1f77b4", capsize=3, ms=7,
                    alpha=0.55, zorder=3, label=f"n < {MIN_BIN_COUNT} (indicative)")

    for _, r in tbl.iterrows():
        ax.annotate(f"n={r.n:.0f}", (r.mean_prob, r.obs_freq),
                    textcoords="offset points", xytext=(6, -11), fontsize=7.5,
                    color="#444")

    ax.set_ylabel("Observed frequency of rain")
    ax.set_title("Bucharest precipitation forecast calibration\n"
                 f"Open-Meteo PoP vs station observations, rain = "
                 f"{RAIN_THRESHOLD_MM} mm/day  (n={len(df)} days, "
                 f"{df.local_date.min()} to {df.local_date.max()})",
                 fontsize=10.5)
    ax.set_xlim(-0.02, 1.02); ax.set_ylim(-0.02, 1.02)
    ax.grid(alpha=0.25); ax.legend(fontsize=8.5, loc="upper left")

    axh.hist(prob, bins=np.linspace(0, 1, 21), color="#777", edgecolor="white")
    axh.set_yscale("log")
    axh.set_xlabel("Forecast probability of precipitation")
    axh.set_ylabel("Days (log)")
    axh.grid(alpha=0.25)
    axh.set_title("Sharpness: how often each probability is issued", fontsize=9)

    path = FIGURES / "reliability_pop.png"
    fig.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {path}")
    return tbl


SEASONS = {0: "winter", 1: "spring", 2: "summer", 3: "autumn"}


def plot_seasonal_reliability(df: pd.DataFrame) -> pd.DataFrame:
    """Calibration split by season.

    Summer rain in Bucharest is convective - small, short-lived cells that a
    grid cell cannot resolve - whereas winter rain is frontal and large-scale.
    An annual average can therefore hide two opposite errors that cancel, which
    is exactly what the temperature bias already does.
    """
    d = df.copy()
    d["season"] = pd.to_datetime(d.local_date).dt.month % 12 // 3

    fig, ax = plt.subplots(figsize=(6.4, 6.0))
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="perfect calibration")

    rows = []
    for s, g in d.groupby("season"):
        p, e = g.forecast_prob.values, g.observed_event.values.astype(float)
        # Fewer bins per season: the sample is roughly a quarter of the whole.
        t = reliability_table(p, e, n_bins=5)
        ax.plot(t.mean_prob, t.obs_freq, "o-", ms=5, lw=1.6, label=SEASONS[s])
        dec = brier_decomposition(p, e, n_bins=5)
        rows.append({"season": SEASONS[s], "n": len(g), "base_rate": e.mean(),
                     "brier": dec["brier"], "bss": dec["brier_skill_score"],
                     "reliability": dec["reliability"],
                     "resolution": dec["resolution"], "ece": dec["ece"]})

    ax.set_xlabel("Forecast probability of precipitation")
    ax.set_ylabel("Observed frequency of rain")
    ax.set_title("Calibration by season\n(convective summer rain vs frontal winter rain)",
                 fontsize=10.5)
    ax.set_xlim(-0.02, 1.02); ax.set_ylim(-0.02, 1.02)
    ax.grid(alpha=0.25); ax.legend(fontsize=9)

    path = FIGURES / "reliability_seasonal.png"
    fig.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {path}")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Task 18: deterministic temperature and precipitation vs lead time
# ---------------------------------------------------------------------------
def plot_lead_diagnostics(leads: pd.DataFrame) -> pd.DataFrame:
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))

    err = leads.dropna(subset=["obs_tmax"]).copy()
    err["abs_err"] = (err.forecast_tmax - err.obs_tmax).abs()
    err["bias"] = err.forecast_tmax - err.obs_tmax
    err["season"] = pd.to_datetime(err.local_date).dt.month % 12 // 3
    names = {0: "winter", 1: "spring", 2: "summer", 3: "autumn"}

    ax = axes[0]
    for s, g in err.groupby("season"):
        m = g.groupby("lead_days").abs_err.mean()
        ax.plot(m.index, m.values, "o-", ms=4, label=names[s])
    m = err.groupby("lead_days").abs_err.mean()
    ax.plot(m.index, m.values, "k-", lw=2.2, label="all")
    ax.set_xlabel("Lead time (days)"); ax.set_ylabel("MAE of daily max temp (C)")
    ax.set_title("Temperature error grows with lead time"); ax.grid(alpha=0.25)
    ax.legend(fontsize=8)

    ax = axes[1]
    for s, g in err.groupby("season"):
        m = g.groupby("lead_days").bias.mean()
        ax.plot(m.index, m.values, "o-", ms=4, label=names[s])
    ax.axhline(0, color="k", lw=1, ls="--")
    ax.set_xlabel("Lead time (days)"); ax.set_ylabel("Mean bias (C)")
    ax.set_title("Seasonal bias (cancels in the annual mean)"); ax.grid(alpha=0.25)
    ax.legend(fontsize=8)

    # Calibration-style curve for a deterministic variable: given a forecast
    # amount, how often did it actually rain?
    ax = axes[2]
    for lead in (1, 3, 5, 7):
        g = leads[leads.lead_days == lead]
        bins = [-1e-9, 0.05, 0.2, 0.5, 1, 2, 5, 10, 1e6]
        cut = pd.cut(g.forecast_precip_mm, bins)
        agg = g.groupby(cut, observed=True).observed_event.agg(["mean", "count"])
        centres = [min(b.right, 12) for b in agg.index]
        ax.plot(centres, agg["mean"], "o-", ms=4, label=f"lead {lead}d")
    ax.set_xscale("symlog", linthresh=0.5)
    ax.set_xlabel("Forecast precipitation (mm/day)")
    ax.set_ylabel("Observed frequency of rain")
    ax.set_title("Deterministic amount vs rain frequency"); ax.grid(alpha=0.25)
    ax.legend(fontsize=8)

    path = FIGURES / "lead_diagnostics.png"
    fig.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {path}")
    return err.groupby("lead_days").agg(mae=("abs_err", "mean"), bias=("bias", "mean"))


def main() -> None:
    pop = load_pop()
    leads = load_leads()

    print("\n=== Task 13-15: reliability diagram ===")
    tbl = plot_reliability(pop)
    print(tbl[["lo", "hi", "mean_prob", "obs_freq", "cons_lo", "cons_hi",
               "n", "is_sparse", "significant"]]
          .to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print(f"  bins outside the perfect-forecast range: "
          f"{int(tbl.significant.sum())} of {len(tbl)}")

    print("\n=== Task 16: Brier decomposition ===")
    d = brier_decomposition(pop.forecast_prob.values,
                            pop.observed_event.values.astype(float),
                            n_bins=N_PROB_BINS)
    for k in ("n", "base_rate", "brier", "brier_binned", "reliability",
              "resolution", "uncertainty", "brier_skill_score", "ece",
              "identity_error"):
        print(f"  {k:20s} {d[k]:.5f}")
    ess = effective_sample_size(pop.observed_event.values.astype(float))
    print(f"  {'effective_n':20s} {ess:.0f}  (raw n={len(pop)})")

    print("\n=== Task 20: isotonic recalibration ===")
    print("  (in-sample mapping: describes HOW it is biased, not a measured gain)")
    iso = isotonic_recalibration(pop.forecast_prob.values,
                                 pop.observed_event.values.astype(float))
    grid = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    for raw, cal in zip(grid, iso.predict(grid)):
        print(f"  stated {raw:.0%}  ->  calibrated {cal:.0%}")

    print("\n  -- honest out-of-sample value (time-blocked 5-fold CV) --")
    cv = cross_validated_recalibration(pop.local_date.values,
                                       pop.forecast_prob.values,
                                       pop.observed_event.values.astype(float))
    for k in ("raw_brier", "calibrated_brier_oos", "improvement",
              "improvement_pct", "raw_bss", "calibrated_bss_oos"):
        print(f"  {k:24s} {cv[k]:.5f}")

    print("\n=== Seasonal calibration ===")
    seas = plot_seasonal_reliability(pop)
    print(seas.to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    print("\n=== Task 18: lead-time diagnostics ===")
    summary = plot_lead_diagnostics(leads)
    print(summary.to_string(float_format=lambda v: f"{v:.3f}"))


if __name__ == "__main__":
    main()
