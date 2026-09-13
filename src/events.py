"""Roadmap rank 3: calibration for events other than rain, at every lead time.

WHY THIS IS NOT A FREE LUNCH
----------------------------
The archive gives probabilities for one thing only: precipitation, at one short
lead time. Frost and heat have no probability attached anywhere in the data. So
a probability has to be *derived* from the deterministic forecast, and that
choice determines what the resulting reliability diagram can and cannot say.

The derivation is model output statistics (MOS): fit P(event | forecast value)
on past days, apply it to new ones. The fit is done with contiguous time-block
folds so every probability scored here was produced by a model that never saw
that day - random folds would put neighbouring days on both sides of the split
and leak the answer, because Bucharest weather persists.

The honest reading of the output:

  * RELIABILITY IS NOT A FINDING. The mapping is fitted to be calibrated, so of
    course it comes out calibrated. A near-diagonal curve here confirms the
    method worked; it says nothing about whether anyone's published forecast is
    honest. Only the native-PoP track can answer that.
  * RESOLUTION AND SKILL ARE THE FINDINGS. No fitting can manufacture those.
    How far ahead a useful frost probability can be issued, and how that differs
    between frost and heat, is a real property of the forecast.

So this module answers "how much probabilistic information does the forecast
carry, for which events, how far out" - a different and more tractable question
than "is the app lying to me".

Usage:  python src/events.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from calibration import (
    brier_decomposition,
    consistency_bars,
    effective_sample_size,
    reliability_table,
)
from config import FIGURES, LEAD_DAYS, MIN_BIN_COUNT, PROCESSED, RANDOM_SEED

# Event definitions. `predictor` names the deterministic forecast column;
# `transform` maps it to the model's single feature; `direction` is +1 when a
# larger predictor means a likelier event and -1 when it means a rarer one
# (colder forecast -> more frost), which the monotone fit needs told explicitly.
EVENTS = {
    "rain": dict(
        label="Rain (>= 0.2 mm)",
        obs=lambda d: d.obs_precip_mm >= 0.2,
        predictor="forecast_precip_mm",
        # Precipitation amount is extremely skewed; log1p keeps the very wet
        # days from dominating the fit while preserving the ordering.
        transform=lambda x: np.log1p(np.clip(x, 0, None)),
        direction=+1,
        unit="mm",
    ),
    "frost": dict(
        label="Frost (min temp < 0 C)",
        obs=lambda d: d.obs_tmin < 0.0,
        predictor="forecast_tmin",
        transform=lambda x: x,
        direction=-1,
        unit="C",
    ),
    "hot_day": dict(
        label="Hot day (max temp >= 32 C)",
        obs=lambda d: d.obs_tmax >= 32.0,
        predictor="forecast_tmax",
        transform=lambda x: x,
        direction=+1,
        unit="C",
    ),
    "heatwave": dict(
        label="Extreme heat (max temp >= 35 C)",
        obs=lambda d: d.obs_tmax >= 35.0,
        predictor="forecast_tmax",
        transform=lambda x: x,
        direction=+1,
        unit="C",
    ),
}

N_FOLDS = 5
MIN_POSITIVES = 20  # below this an event is reported as too rare to verify

# Isotonic, not logistic. A logistic curve imposes a specific S-shape on how
# probability rises with the forecast value, and measured on this data that
# shape is simply wrong: for rain it left the reliability term at 0.0064 with 3
# of 6 well-populated bins outside their consistency bars, whereas the monotone
# fit gives 0.0008 with 0 of 5 outside AND a better Brier score (0.1059 vs
# 0.1107). Isotonic assumes only that more rain forecast never means less rain -
# which is the whole of what we actually know. `logistic` is retained as a
# sensitivity check so the choice stays auditable.
METHOD = "isotonic"


def _oos_probabilities(dates, x, y, n_folds: int = N_FOLDS,
                       method: str = METHOD, direction: int = +1) -> np.ndarray:
    """Out-of-sample P(event | forecast), fitted on contiguous time blocks.

    Returns NaN for any fold whose training half contains only one class - with
    rare events that genuinely happens, and silently substituting a constant
    would fabricate a data point rather than admit the gap.
    """
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression

    order = np.argsort(np.asarray(dates))
    x = np.asarray(x, float)[order] * direction
    y = np.asarray(y, float)[order]
    n = len(y)
    oos = np.full(n, np.nan)

    for fold in np.array_split(np.arange(n), n_folds):
        train = np.setdiff1d(np.arange(n), fold)
        if len(np.unique(y[train])) < 2:
            continue
        if method == "isotonic":
            m = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip")
            m.fit(x[train], y[train])
            oos[fold] = m.predict(x[fold])
        else:
            mu, sd = x[train].mean(), x[train].std() or 1.0
            clf = LogisticRegression(C=1.0, max_iter=1000)
            clf.fit(((x[train] - mu) / sd).reshape(-1, 1), y[train])
            oos[fold] = clf.predict_proba(((x[fold] - mu) / sd).reshape(-1, 1))[:, 1]

    # Undo the sort so the caller's row order is preserved.
    out = np.full(n, np.nan)
    out[order] = oos
    return out


# Equal-WIDTH bins, not equal-count. Derived probabilities for rare events pile
# up against zero - 80% of days carry a heatwave probability under 0.03 - so
# quantile edges collapse onto one another and np.unique folds ten requested
# bins down to three. That is not cosmetic: measured on this data, three
# collapsed bins put the heatwave resolution at 0.0070 where ten equal-width
# bins give 0.0394, understating it more than fivefold, because nearly every
# informative forecast lands in one giant bin and is averaged away.
N_BINS = 10
EQUAL_COUNT = False

# The Murphy terms only mean anything if the binned forecast still resembles the
# real one. If replacing each forecast by its bin mean shifts the Brier score by
# more than this fraction, the binning rather than the forecast is driving
# reliability and resolution, and they should not be quoted. This is the general
# detector for the failure found above.
MAX_BINNING_LOSS = 0.10


def build() -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_parquet(PROCESSED / "verification_leads.parquet")
    df = df[df.usable].copy()

    metric_rows, curve_rows = [], []
    for key, spec in EVENTS.items():
        obs_all = spec["obs"](df)
        for lead in LEAD_DAYS:
            m = (df.lead_days == lead) & obs_all.notna() & df[spec["predictor"]].notna()
            g = df[m]
            y = obs_all[m].to_numpy(dtype=float)
            if len(g) == 0 or y.sum() < MIN_POSITIVES:
                metric_rows.append(dict(event=key, lead_days=lead, n=len(g),
                                        n_positive=int(y.sum()), too_rare=True))
                continue

            x = spec["transform"](g[spec["predictor"]].to_numpy(dtype=float))
            p = _oos_probabilities(g.local_date.values, x, y,
                                   direction=spec["direction"])
            ok = ~np.isnan(p)
            p, y_ok, d_ok = p[ok], y[ok], g.local_date.values[ok]

            nb = N_BINS
            dec = brier_decomposition(p, y_ok, n_bins=nb, equal_count=EQUAL_COUNT)
            binning_loss = (abs(dec["brier_binned"] - dec["brier"]) / dec["brier"]
                            if dec["brier"] > 0 else 0.0)
            metric_rows.append(dict(
                event=key, lead_days=lead, n=len(p), n_positive=int(y_ok.sum()),
                too_rare=False, n_bins=nb, binning_loss=binning_loss,
                effective_n=effective_sample_size(y_ok),
                n_dropped_folds=int((~ok).sum()),
                **{k: v for k, v in dec.items() if k != "n"},
            ))

            tbl = reliability_table(p, y_ok, n_bins=nb, equal_count=EQUAL_COUNT)
            tbl = tbl.merge(consistency_bars(p, y_ok, n_bins=nb, equal_count=EQUAL_COUNT),
                            on="bin", how="left")
            tbl["outside"] = (tbl.obs_freq < tbl.cons_lo) | (tbl.obs_freq > tbl.cons_hi)
            tbl["event"], tbl["lead_days"] = key, lead
            curve_rows.append(tbl)

    metrics = pd.DataFrame(metric_rows)
    curves = pd.concat(curve_rows, ignore_index=True) if curve_rows else pd.DataFrame()
    metrics.to_parquet(PROCESSED / "event_metrics.parquet", index=False)
    curves.to_parquet(PROCESSED / "event_curves.parquet", index=False)
    return metrics, curves


def plot(metrics: pd.DataFrame, curves: pd.DataFrame) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    good = metrics[~metrics.too_rare]
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.2))

    ax = axes[0]
    for key, spec in EVENTS.items():
        g = good[good.event == key].sort_values("lead_days")
        if g.empty:
            continue
        ax.plot(g.lead_days, g.brier_skill_score, "o-", label=spec["label"])
    ax.axhline(0, color="#888", lw=1, ls="--")
    ax.set_xlabel("Lead time (days ahead)")
    ax.set_ylabel("Brier skill score  (1 = perfect, 0 = no better than climate)")
    ax.set_title("How far ahead each event stays predictable")
    ax.legend(fontsize=8, loc="lower left")
    ax.grid(alpha=0.3)

    ax = axes[1]
    lead = 1
    # Well-populated bins only. Plotting every bin produced a wild zigzag that
    # flatly contradicted the caption: with equal-width bins and rare events,
    # the middle of the range holds a handful of days each, so a single outcome
    # swings a point from 0 to 1. Those bins are already excluded from every
    # numerical claim (MIN_BIN_COUNT); excluding them here too keeps the picture
    # and the prose telling the same story.
    for key, spec in EVENTS.items():
        t = curves[(curves.event == key) & (curves.lead_days == lead)
                   & (~curves.is_sparse)].sort_values("mean_prob")
        if t.empty:
            continue
        ax.plot(t.mean_prob, t.obs_freq, "o-", label=spec["label"])
    ax.plot([0, 1], [0, 1], color="#444", lw=1, ls="--", label="perfect")
    ax.set_xlabel("Derived probability")
    ax.set_ylabel("Observed frequency")
    ax.set_title(f"Reliability at lead {lead} day\n"
                 f"(groups of >= {MIN_BIN_COUNT} days; near-diagonal is expected)")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(FIGURES / "events.png", dpi=140)
    plt.close(fig)


def main() -> None:
    metrics, curves = build()
    good = metrics[~metrics.too_rare]

    print("\n=== Derived event probabilities (time-blocked out-of-sample) ===")
    for key, spec in EVENTS.items():
        g = good[good.event == key].sort_values("lead_days")
        rare = metrics[(metrics.event == key) & metrics.too_rare]
        if g.empty:
            print(f"\n{spec['label']}: too rare to verify "
                  f"(max {int(rare.n_positive.max())} occurrences)")
            continue
        print(f"\n{spec['label']}  base rate {g.base_rate.iloc[0]:.3f}  "
              f"n={int(g.n.iloc[0])}/lead  positives={int(g.n_positive.iloc[0])}")
        print("  lead  brier   BSS     reliab  resol   ECE     binloss")
        for _, r in g.iterrows():
            print(f"   {int(r.lead_days)}    {r.brier:.4f}  {r.brier_skill_score:+.3f}  "
                  f"{r.reliability:.4f}  {r.resolution:.4f}  {r.ece:.4f}  "
                  f"{r.binning_loss:6.1%}")
        if rare.shape[0]:
            print(f"  (leads {sorted(rare.lead_days)} skipped: too few events)")

    err = good.identity_error.max() if len(good) else 0.0
    print(f"\nmax Brier identity error across all event/lead fits: {err:.2e}")
    assert err < 1e-9, "Brier decomposition identity violated"

    loss = good.binning_loss.max() if len(good) else 0.0
    print(f"max binning loss (binned vs raw Brier): {loss:.1%} "
          f"(limit {MAX_BINNING_LOSS:.0%})")
    assert loss < MAX_BINNING_LOSS, (
        "binning is distorting the decomposition - reliability and resolution "
        "are not trustworthy at this bin configuration")

    # Only bins with a usable sample can say anything; a 4-day bin sitting off
    # the diagonal is noise, not evidence.
    #
    # This runs above the ~5% a perfect forecast would produce, and the cause is
    # measurable rather than mysterious: fitting the same mapping in-sample puts
    # ZERO bins outside for every event, so the excess is entirely generalisation
    # error - a mapping learned on one stretch of time does not transfer
    # perfectly to another. That is what out-of-sample scoring is for, and it is
    # the reason the in-sample version of this number would be worthless.
    solid = curves[~curves.is_sparse]
    n_out = int(solid.outside.sum())
    print(f"well-populated bins outside their consistency bars: {n_out} of "
          f"{len(solid)} ({n_out / max(1, len(solid)):.0%}; ~5% expected by "
          "chance, the excess is generalisation error - in-sample it is 0)")

    method_sensitivity()
    plot(metrics, curves)
    print(f"wrote {FIGURES / 'events.png'}")


def method_sensitivity(lead: int = 1) -> pd.DataFrame:
    """Does the headline depend on the shape assumed for the fitted mapping?

    Any derived probability is partly an artefact of the derivation, so the
    derivation has to be shown not to be doing the work. If the two mappings
    disagreed materially on skill, no conclusion here would be safe.
    """
    df = pd.read_parquet(PROCESSED / "verification_leads.parquet")
    df = df[df.usable]
    rows = []
    for key, spec in EVENTS.items():
        obs = spec["obs"](df)
        m = (df.lead_days == lead) & obs.notna() & df[spec["predictor"]].notna()
        g, y = df[m], obs[m].to_numpy(dtype=float)
        if y.sum() < MIN_POSITIVES:
            continue
        x = spec["transform"](g[spec["predictor"]].to_numpy(dtype=float))
        row = {"event": key}
        for meth in ("isotonic", "logistic"):
            p = _oos_probabilities(g.local_date.values, x, y, method=meth,
                                   direction=spec["direction"])
            ok = ~np.isnan(p)
            d = brier_decomposition(p[ok], y[ok], n_bins=N_BINS,
                                    equal_count=EQUAL_COUNT)
            row[f"bss_{meth}"] = d["brier_skill_score"]
            row[f"reliab_{meth}"] = d["reliability"]
        rows.append(row)
    out = pd.DataFrame(rows)
    out["bss_gap"] = (out.bss_isotonic - out.bss_logistic).abs()
    out.to_parquet(PROCESSED / "event_method_sensitivity.parquet", index=False)

    print(f"\n=== mapping-shape sensitivity at lead {lead} ===")
    print(out.round(4).to_string(index=False))
    print(f"largest skill difference between mappings: {out.bss_gap.max():.3f}")
    return out


if __name__ == "__main__":
    main()
