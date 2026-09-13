"""Calibration metrics (Tasks 13-16, 20).

Nothing here plots; `analyze.py` does that. Keeping the maths separate makes the
Brier decomposition self-check meaningful as a unit test of the numerics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import BOOTSTRAP_BLOCK_DAYS, BOOTSTRAP_N, MIN_BIN_COUNT, RANDOM_SEED


def equal_count_bins(prob: np.ndarray, n_bins: int) -> np.ndarray:
    """Bin edges giving roughly equal sample counts.

    Equal-count rather than equal-width binning: PoP is heavily concentrated
    near zero, so equal-width bins would leave the upper bins with a handful of
    days and unusable error bars.
    """
    qs = np.linspace(0, 1, n_bins + 1)
    edges = np.unique(np.quantile(prob, qs))
    edges[0], edges[-1] = -1e-9, 1.0 + 1e-9
    return edges


def assign_bins(prob, n_bins: int = 10, equal_count: bool = True):
    """Single source of truth for bin membership.

    Both the reliability table and the Brier decomposition must agree exactly on
    which sample belongs to which bin. Re-deriving membership from bin edges
    with float tolerances does not achieve that: a probability sitting exactly
    on an edge (0.35 occurs six times in this dataset) can be claimed by two
    bins at once, which silently corrupts the decomposition. Everything goes
    through this one digitize call instead.
    """
    prob = np.asarray(prob, float)
    edges = (equal_count_bins(prob, n_bins) if equal_count
             else np.linspace(-1e-9, 1 + 1e-9, n_bins + 1))
    idx = np.clip(np.digitize(prob, edges) - 1, 0, len(edges) - 2)
    return idx, edges


def reliability_table(prob, event, n_bins: int = 10, equal_count: bool = True) -> pd.DataFrame:
    prob, event = np.asarray(prob, float), np.asarray(event, float)
    idx, edges = assign_bins(prob, n_bins, equal_count)

    rows = []
    for b in range(len(edges) - 1):
        m = idx == b
        if not m.any():
            continue
        rows.append({
            "bin": b,
            "lo": max(0.0, edges[b]),
            "hi": min(1.0, edges[b + 1]),
            "mean_prob": prob[m].mean(),
            "obs_freq": event[m].mean(),
            "n": int(m.sum()),
        })
    out = pd.DataFrame(rows)
    out["is_sparse"] = out["n"] < MIN_BIN_COUNT
    return out


def brier_decomposition(prob, event, n_bins: int = 10,
                        equal_count: bool = True) -> dict:
    """Murphy decomposition: Brier = reliability - resolution + uncertainty.

    The identity holds exactly for the *binned* forecast, where every member of
    a bin is replaced by that bin's mean probability. It does NOT hold for the
    raw Brier score: the difference includes a within-bin covariance between
    forecast and outcome, not just the within-bin forecast variance. So the
    self-check is run against `brier_binned`, where an exact result is
    meaningful, and `brier` is reported separately as the headline number.

    `equal_count` must match whatever the caller used to build its reliability
    table, or the two will disagree about bin membership.
    """
    prob, event = np.asarray(prob, float), np.asarray(event, float)
    n = len(prob)
    brier = float(np.mean((prob - event) ** 2))
    base = float(event.mean())
    uncertainty = base * (1 - base)

    tbl = reliability_table(prob, event, n_bins=n_bins, equal_count=equal_count)
    w = tbl["n"] / n
    reliability = float((w * (tbl["mean_prob"] - tbl["obs_freq"]) ** 2).sum())
    resolution = float((w * (tbl["obs_freq"] - base) ** 2).sum())

    # Replace each forecast by its bin mean, then the decomposition is exact.
    idx, _ = assign_bins(prob, n_bins, equal_count)
    bin_mean = dict(zip(tbl["bin"], tbl["mean_prob"]))
    binned_prob = np.array([bin_mean[b] for b in idx])
    brier_binned = float(np.mean((binned_prob - event) ** 2))

    return {
        "brier": brier,
        "brier_binned": brier_binned,
        "reliability": reliability,
        "resolution": resolution,
        "uncertainty": uncertainty,
        "base_rate": base,
        "identity_error": abs(reliability - resolution + uncertainty - brier_binned),
        "brier_skill_score": 1 - brier / uncertainty if uncertainty > 0 else np.nan,
        "ece": float((w * (tbl["mean_prob"] - tbl["obs_freq"]).abs()).sum()),
        "n": n,
    }




def block_bootstrap_ci(dates, prob, event, n_bins: int = 10,
                       n_boot: int = BOOTSTRAP_N,
                       block_days: int = BOOTSTRAP_BLOCK_DAYS,
                       alpha: float = 0.05) -> pd.DataFrame:
    """Per-bin CIs from a moving block bootstrap.

    Plain binomial intervals would be too narrow: Bucharest weather persists for
    days, so consecutive samples are not independent. Resampling contiguous
    blocks preserves that dependence.
    """
    rng = np.random.default_rng(RANDOM_SEED)
    order = np.argsort(np.asarray(dates))
    prob = np.asarray(prob, float)[order]
    event = np.asarray(event, float)[order]
    n = len(prob)

    edges = equal_count_bins(prob, n_bins)
    n_blocks = int(np.ceil(n / block_days))
    starts_pool = np.arange(0, max(1, n - block_days + 1))

    acc: dict[int, list[float]] = {b: [] for b in range(len(edges) - 1)}
    for _ in range(n_boot):
        starts = rng.choice(starts_pool, size=n_blocks, replace=True)
        idx = np.concatenate([np.arange(s, min(s + block_days, n)) for s in starts])[:n]
        p, e = prob[idx], event[idx]
        bi = np.clip(np.digitize(p, edges) - 1, 0, len(edges) - 2)
        for b in range(len(edges) - 1):
            m = bi == b
            if m.sum() >= 5:
                acc[b].append(e[m].mean())

    rows = []
    for b, vals in acc.items():
        if not vals:
            continue
        rows.append({
            "bin": b,
            "ci_lo": float(np.quantile(vals, alpha / 2)),
            "ci_hi": float(np.quantile(vals, 1 - alpha / 2)),
        })
    return pd.DataFrame(rows)


def effective_sample_size(event) -> float:
    """Rough ESS from lag-1 autocorrelation, to temper the raw n."""
    e = np.asarray(event, float)
    if len(e) < 3:
        return float(len(e))
    r1 = np.corrcoef(e[:-1], e[1:])[0, 1]
    r1 = 0.0 if np.isnan(r1) else float(np.clip(r1, -0.99, 0.99))
    return len(e) * (1 - r1) / (1 + r1)


def isotonic_recalibration(prob, event):
    """Task 20: map raw PoP to calibrated PoP.

    DESCRIPTIVE ONLY. Fitted on all data, so the mapping summarises *how* the
    forecast is biased, but the apparent improvement it gives on these same days
    is not a measured gain. Use `cross_validated_recalibration` for that.
    """
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip")
    iso.fit(np.asarray(prob, float), np.asarray(event, float))
    return iso


def cross_validated_recalibration(dates, prob, event, n_folds: int = 5) -> dict:
    """Honest, out-of-sample value of recalibrating the forecast.

    Fitting isotonic regression and scoring it on the same days is circular:
    isotonic regression is flexible enough to absorb noise, so an in-sample
    improvement is guaranteed and says nothing about whether the miscalibration
    is real. Folds are *contiguous time blocks* rather than random draws,
    because Bucharest weather persists for days; random folds would put
    neighbouring days on both sides of the split and leak the answer.

    If the out-of-sample gain is near zero, the S-shape in the reliability
    diagram is mostly sampling noise rather than a correctable bias.
    """
    from sklearn.isotonic import IsotonicRegression

    order = np.argsort(np.asarray(dates))
    prob = np.asarray(prob, float)[order]
    event = np.asarray(event, float)[order]
    n = len(prob)
    folds = np.array_split(np.arange(n), n_folds)

    oos = np.full(n, np.nan)
    for f in folds:
        train = np.setdiff1d(np.arange(n), f)
        iso = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip")
        iso.fit(prob[train], event[train])
        oos[f] = iso.predict(prob[f])

    raw = float(np.mean((prob - event) ** 2))
    cal = float(np.mean((oos - event) ** 2))
    base = float(event.mean())
    unc = base * (1 - base)
    return {
        "raw_brier": raw,
        "calibrated_brier_oos": cal,
        "improvement": raw - cal,
        "improvement_pct": 100.0 * (raw - cal) / raw if raw > 0 else np.nan,
        "raw_bss": 1 - raw / unc if unc > 0 else np.nan,
        "calibrated_bss_oos": 1 - cal / unc if unc > 0 else np.nan,
        "n_folds": n_folds,
        "n": n,
    }


def consistency_bars(prob, event, n_bins: int = 10, n_boot: int = BOOTSTRAP_N,
                     alpha: float = 0.05, equal_count: bool = True) -> pd.DataFrame:
    """Range each bin's observed frequency would span IF the forecast were perfect.

    The block-bootstrap CI answers "how uncertain is this point?". That is a
    different question from "is this point further from the diagonal than chance
    allows?", which is what a reader actually wants when judging an S-shape.
    Here each day's outcome is re-simulated as a coin flip weighted by its own
    forecast probability - i.e. assuming the forecast is perfectly calibrated -
    and the resulting spread of bin frequencies is recorded. A point outside
    its consistency bar is evidence of real miscalibration; inside it, the
    deviation is what perfect forecasts do anyway at this sample size.
    """
    rng = np.random.default_rng(RANDOM_SEED)
    prob = np.asarray(prob, float)
    idx, edges = assign_bins(prob, n_bins, equal_count)
    n_b = len(edges) - 1

    acc: dict[int, list[float]] = {b: [] for b in range(n_b)}
    for _ in range(n_boot):
        sim = (rng.random(len(prob)) < prob).astype(float)
        for b in range(n_b):
            m = idx == b
            if m.any():
                acc[b].append(sim[m].mean())

    rows = []
    for b, vals in acc.items():
        if not vals:
            continue
        rows.append({
            "bin": b,
            "cons_lo": float(np.quantile(vals, alpha / 2)),
            "cons_hi": float(np.quantile(vals, 1 - alpha / 2)),
        })
    return pd.DataFrame(rows)
