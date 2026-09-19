"""Tasks 25 and 26: is the vendor-versus-ensemble verdict a result or a rounding error?

WHY THIS MODULE EXISTS. `triangulation.py` reports that at lead 1 the served
probability and the member-derived one score Brier 0.1669 and 0.1672. It calls
that a dead heat. Nothing in it is entitled to: a difference of 0.0003 on 8,572
city-days is either a tie, or a real effect the sample is too small and too
correlated to see, and only a test can say which. Worse, "not significant" does
not mean "equivalent" - the usual abuse of a null result, and precisely the
abuse this study would be committing if it stopped at a p-value. So this module
does three things:

  1. PAIRED TESTS that respect how the data are actually dependent, for every
     comparison the paper makes a claim about.
  2. AN EQUIVALENCE TEST (TOST), so "tie" is something demonstrated rather than
     something left over when significance fails.
  3. FDR CONTROL across the 105 city x lead cells, so the per-city panel cannot
     be mined for whichever cells happen to cross 0.05 (Task 26).

THE DEPENDENCE, AND HOW IT IS HANDLED. Two structures break the independence
that every textbook interval assumes, and they push the same way - both make
the effective sample smaller than n:

  TEMPORAL   rain persists. Today's forecast error and tomorrow's are not two
             independent draws; a wet week is one synoptic event, not seven.
  SPATIAL    the sample is 15 European capitals. A trough over the continent
             is scored fifteen times on the same day, and a day on which the
             vendor's dry bias hurts in Vienna is a day it hurts in Bratislava.

The resampling unit is therefore the CALENDAR DAY, carrying every city scored
on it, drawn in contiguous blocks of `BOOTSTRAP_BLOCK_DAYS` days. Blocks handle
the temporal structure the way `calibration.block_bootstrap_ci` already does;
keeping each day's cities together handles the spatial structure without having
to model it - no covariance function, no assumed decay length, no claim about
how far a front reaches. A day either enters a replicate whole or not at all.

This matters quantitatively, not just in principle: the correctness checks
below construct data with exactly this dependence and show the naive
independent-sample interval covering the truth barely three quarters of the
time at a nominal 95%, while the block-day bootstrap covers correctly.

WHAT IS TESTED. Everything is a PAIRED difference, vendor minus member-derived,
on the identical city-days `triangulation.check_paired` already guarantees:

  brier_diff            negative = the served probability is better
  brier_diff_anystep    the same against the ensemble's any-3h-step event,
                        which is the event the vendor's max-over-hours answers
  reliability_diff      negative = the served probability is more honest
  resolution_diff       positive = the served probability discriminates more
  auc_diff              the same, rank-based
  value_diff_aNN        relative economic value at cost-loss ratio 0.NN, under
                        the acting rule - the E4a claim, and the one the paper
                        now leads with
  bias_vendor_minus_gefs  the dry bias itself

Reliability and resolution are tested because D10 makes them the mechanism: the
claim is that post-processing trades resolution for reliability and the two
cancel. A mechanism nobody tested is a story.

THREE VARIANCES, NOT ONE. For the statistics that are means of per-day
quantities, the bootstrap standard error is reported beside the naive
independent-sample one, a day-clustered one, and a Newey-West one at the block
length. They are not alternatives to choose between - printing the ratios is
the evidence that the dependence handling is doing something, and the size of
the ratio is itself reportable.

EQUIVALENCE. The margin is not chosen by taste: it is the largest swing the
ENSEMBLE'S OWN Brier shows between the two day-boundary rules - an arbitrary
methodological choice that moves the number by 0.0015. A difference smaller
than that cannot be claimed as an effect by anyone, so it is the natural
threshold below which two forecasts are the same forecast for this study's
purposes. TOST at that margin turns the tie into a claim that can fail.

Usage:  python src/significance.py            # checks, then the full run
        python src/significance.py check      # correctness checks only
        python src/significance.py quick      # fewer replicates, for iterating
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import triangulation
from calibration import brier_decomposition
from config import (
    BOOTSTRAP_BLOCK_DAYS,
    BOOTSTRAP_N,
    GEFS_BOUNDARY_MODES,
    GEFS_BOUNDARY_PRIMARY,
    LEAD_DAYS,
    N_PROB_BINS,
    PROCESSED,
    RANDOM_SEED,
)
from metrics import HEADLINE_ALPHAS, roc_auc, value_curve
from triangulation import LIKE_FOR_LIKE_MODEL, die

HEADLINE_TABLE = PROCESSED / "significance_headline.parquet"
CELL_TABLE = PROCESSED / "significance_cells.parquet"
BLOCK_TABLE = PROCESSED / "significance_block_sensitivity.parquet"

# The conventional levels, named once so they are not scattered as magic
# numbers. ALPHA_TEST is the size of every test; Q_FDR is the false discovery
# rate tolerated across the per-city panel - 0.10 rather than 0.05 because the
# panel is descriptive support for a pooled claim, not 105 separate findings.
ALPHA_TEST = 0.05
Q_FDR = 0.10

# A city x lead cell below this many days cannot support its own test.
MIN_CELL_DAYS = 200

# Candidate block lengths, in days. The pipeline's default of 7 (used by
# calibration.block_bootstrap_ci for per-bin reliability intervals) is NOT
# assumed here: measured against a known truth it recovers only ~90% of the
# real standard error when the daily series decorrelates over more than a
# week, and a 10% narrow interval is a test that rejects true nulls three
# times too often. `choose_block` picks from these by measurement, and the
# whole ladder is reported as a sensitivity so the choice is visible.
BLOCK_CANDIDATES = (7, 14, 21, 28)
BLOCK_MIN, BLOCK_MAX = 7, 28

# Statistics that are means of a per-day quantity. Only these admit the
# closed-form cluster and Newey-West variances: both formulas are statements
# about the variance of a sum, and neither AUC nor relative economic value is
# one. For the rest the bootstrap is the only route offered, which is the
# honest position rather than a limitation quietly worked around.
MEAN_STATISTICS = ("brier_diff", "brier_diff_anystep", "bias_vendor_minus_gefs")


# ---------------------------------------------------------------------------
# Per-day quantities: the paired differences, one row per city-day
# ---------------------------------------------------------------------------
def cell(paired: pd.DataFrame, mode: str, lead: int) -> pd.DataFrame:
    """One (boundary mode, lead) cell, sorted so each day's rows are contiguous.

    Contiguity is what makes the block bootstrap cheap: a block of consecutive
    DAYS is then a single row slice, so a replicate is a concatenation of
    slices rather than a membership test over the whole frame.
    """
    g = paired[(paired.boundary_mode == mode) & (paired.lead_days == lead)].copy()
    if g.empty:
        die(f"no paired rows at boundary_mode={mode}, lead={lead}")
    g["date"] = pd.to_datetime(g.local_date)
    g = g.sort_values(["date", "city"]).reset_index(drop=True)
    g["d_brier"] = ((g.vendor_pop - g.event) ** 2
                    - (g.pop_daytotal - g.event) ** 2)
    g["d_brier_anystep"] = ((g.vendor_pop - g.event) ** 2
                            - (g.pop_anystep - g.event) ** 2)
    g["d_bias"] = g.vendor_pop - g.pop_daytotal
    return g


def day_offsets(g: pd.DataFrame) -> np.ndarray:
    """Row index where each calendar day starts, plus the end. len = n_days + 1."""
    first = np.flatnonzero(np.r_[True, g.date.values[1:] != g.date.values[:-1]])
    return np.r_[first, len(g)]


# ---------------------------------------------------------------------------
# The resampling itself
# ---------------------------------------------------------------------------
def block_starts(rng, n_days: int, block: int, n_boot: int) -> np.ndarray:
    """(n_boot, n_blocks) start days for a moving block bootstrap over days.

    Blocks are used at full length, so a replicate carries ceil(J/L)*L days
    rather than exactly J - at most L-1 days too many. That is the standard
    construction and it matters not at all for a ratio-of-sums statistic, which
    every statistic here is; what would matter is truncating blocks, which
    would systematically under-weight the dependence at block ends.
    """
    block = int(min(block, n_days))
    n_blocks = int(np.ceil(n_days / block))
    return rng.integers(0, n_days - block + 1, size=(n_boot, n_blocks)), block


def replicate_rows(starts_row: np.ndarray, off: np.ndarray, block: int) -> np.ndarray:
    """Row indices of one replicate: the rows of every city on the drawn days."""
    return np.concatenate([np.arange(off[a], off[a + block]) for a in starts_row])


def integrated_autocorr_time(x, max_lag: int = 40) -> float:
    """tau = 1 + 2 * sum of Bartlett-weighted autocorrelations.

    How many days of this series carry the information of one independent day.
    For white noise tau = 1; for an AR(1) with coefficient r it converges to
    (1 + r) / (1 - r), so r = 0.6 gives 4. The Bartlett weights truncate the
    sum smoothly, which matters because raw autocorrelations at long lag are
    mostly noise and summing them unweighted lets that noise dominate.
    """
    x = np.asarray(x, float)
    x = x - x.mean()
    n = len(x)
    if n < 8 or not np.any(x):
        return 1.0
    denom = float(np.sum(x * x))
    if denom <= 0:
        return 1.0
    k = int(min(max_lag, n // 4))
    tau = 1.0
    for lag in range(1, k + 1):
        r = float(np.sum(x[:-lag] * x[lag:])) / denom
        tau += 2.0 * (1.0 - lag / (k + 1.0)) * r
    return float(max(tau, 1.0))


def choose_block(daily: np.ndarray, candidates=BLOCK_CANDIDATES) -> int:
    """Block length from the measured decorrelation time of the daily series.

    Blocks must be long enough to contain the dependence they are meant to
    preserve. The rule is L >= 4 tau - four decorrelation times, the usual
    rule of thumb - rounded up to the next candidate and clipped to the ladder
    so the reported sensitivity always brackets the choice. Validated in the
    checks against an AR(1) panel whose true standard error is known: the rule
    picks a block that recovers it, and the pipeline default of 7 does not.
    """
    tau = integrated_autocorr_time(daily)
    want = int(np.clip(np.ceil(4.0 * tau), BLOCK_MIN, BLOCK_MAX))
    for c in sorted(candidates):
        if c >= want:
            return int(c)
    return int(max(candidates))


def daily_series(values: np.ndarray, day_index: np.ndarray,
                 n_days: int) -> np.ndarray:
    """Per-day mean of a per-row quantity - the series whose memory sets L."""
    s = np.bincount(day_index, weights=values, minlength=n_days)
    c = np.bincount(day_index, minlength=n_days).astype(float)
    return np.divide(s, c, out=np.zeros_like(s), where=c > 0)


def bootstrap_mean(values: np.ndarray, day_index: np.ndarray, n_days: int,
                   n_boot: int, block: int, rng) -> np.ndarray:
    """Block-bootstrap replicates of the MEAN of a per-row quantity, vectorised.

    A day contributes a sum and a count, so the replicate mean is a ratio of
    two sums over drawn blocks and the whole bootstrap collapses to two
    cumulative-sum lookups. No row is ever materialised. `replicate_rows` gives
    the same answer the slow way and the checks assert that it does.
    """
    s = np.bincount(day_index, weights=values, minlength=n_days)
    c = np.bincount(day_index, minlength=n_days).astype(float)
    S = np.r_[0.0, np.cumsum(s)]
    C = np.r_[0.0, np.cumsum(c)]
    starts, block = block_starts(rng, n_days, block, n_boot)
    tot_s = (S[starts + block] - S[starts]).sum(axis=1)
    tot_c = (C[starts + block] - C[starts]).sum(axis=1)
    return tot_s / tot_c


# ---------------------------------------------------------------------------
# Inference from a bootstrap distribution
# ---------------------------------------------------------------------------
def boot_pvalue(reps: np.ndarray, theta: float, null: float = 0.0) -> float:
    """Two-sided bootstrap p-value for H0: theta == null.

    The replicates are centred first, so what is compared against the observed
    deviation is the bootstrap's estimate of the SAMPLING distribution rather
    than the distribution of the estimate itself. The (1 + count) / (B + 1)
    form is deliberate: a p-value of exactly zero is not a thing a bootstrap
    with B replicates can report, and writing 0.0 would invite someone to quote
    it.
    """
    z = np.asarray(reps, float) - theta
    b = len(z)
    dev = theta - null
    p_hi = (1 + np.sum(z >= dev)) / (b + 1)
    p_lo = (1 + np.sum(z <= dev)) / (b + 1)
    return float(min(1.0, 2 * min(p_hi, p_lo)))


def tost_pvalue(reps: np.ndarray, theta: float, margin: float) -> float:
    """Two one-sided tests: can |theta| < margin be asserted rather than assumed?

    Returns max(p_lower, p_upper). Below ALPHA_TEST, the difference is
    demonstrably smaller than the margin and the two forecasts may be called
    equivalent AT THAT MARGIN. Above it, the data are simply consistent with
    both a null effect and an effect larger than the margin - which is what
    "not significant" usually actually means, and why it is not reported alone.
    """
    if not np.isfinite(margin) or margin <= 0:
        return float("nan")
    z = np.asarray(reps, float) - theta
    b = len(z)
    p_upper = (1 + np.sum(z <= theta - margin)) / (b + 1)   # H0: theta >= +margin
    p_lower = (1 + np.sum(z >= theta + margin)) / (b + 1)   # H0: theta <= -margin
    return float(max(p_lower, p_upper))


def summarise(reps: np.ndarray, theta: float, margin: float | None = None,
              alpha: float = ALPHA_TEST) -> dict:
    reps = np.asarray(reps, float)
    se = float(np.std(reps, ddof=1))
    out = {
        "estimate": float(theta),
        "se_boot": se,
        "ci_lo": float(np.quantile(reps, alpha / 2)),
        "ci_hi": float(np.quantile(reps, 1 - alpha / 2)),
        "p_boot": boot_pvalue(reps, theta),
        # What size of difference this sample could have detected at 80% power.
        # Printed beside every null result: a null with an MDE larger than the
        # effect anyone cares about is not evidence of absence.
        "mde_80": float(2.80 * se),
        "n_boot": int(len(reps)),
    }
    if margin is not None:
        out["equivalence_margin"] = float(margin)
        out["p_tost"] = tost_pvalue(reps, theta, margin)
        out["equivalent"] = bool(out["p_tost"] < alpha)
    return out


# ---------------------------------------------------------------------------
# Closed-form variances, for comparison with the bootstrap
# ---------------------------------------------------------------------------
def analytic_ses(values: np.ndarray, day_index: np.ndarray, n_days: int,
                 lag: int = BOOTSTRAP_BLOCK_DAYS) -> dict:
    """Naive, day-clustered and Newey-West standard errors of a mean.

    The three differ only in what they are willing to assume:

      naive     every row independent. Wrong twice over here, and the point of
                computing it is to show by how much.
      cluster   rows within a day may be arbitrarily dependent, days
                independent. Handles the spatial structure, not the temporal.
      nw        days correlated up to `lag`, Bartlett-weighted. Handles both.
    """
    v = np.asarray(values, float)
    n = len(v)
    theta = float(v.mean())
    s = np.bincount(day_index, weights=v, minlength=n_days)
    c = np.bincount(day_index, minlength=n_days).astype(float)

    naive = float(np.std(v, ddof=1) / np.sqrt(n))
    u = s - c * theta                       # day-level residual contributions
    cluster = float(np.sqrt(np.sum(u ** 2)) / n)

    gamma0 = float(np.sum(u ** 2))
    acc = gamma0
    for l in range(1, min(lag, n_days - 1) + 1):
        w = 1.0 - l / (lag + 1.0)
        acc += 2.0 * w * float(np.sum(u[:-l] * u[l:]))
    nw = float(np.sqrt(max(acc, 0.0)) / n)
    return {"se_naive": naive, "se_cluster": cluster, "se_nw": nw,
            "p_naive": _z_p(theta, naive), "p_cluster": _z_p(theta, cluster),
            "p_nw": _z_p(theta, nw)}


def _z_p(theta: float, se: float) -> float:
    """Two-sided normal-approximation p-value."""
    if not np.isfinite(se) or se <= 0:
        return float("nan")
    from math import erfc, sqrt
    return float(erfc(abs(theta / se) / sqrt(2.0)))


# ---------------------------------------------------------------------------
# Task 26: false discovery rate
# ---------------------------------------------------------------------------
def benjamini_hochberg(pvals) -> np.ndarray:
    """BH-adjusted p-values (q-values). Reject where the result is <= q.

    Step-up with the running-minimum enforced from the top, so the adjusted
    values are monotone in the raw ones - without that, a test can be rejected
    while a smaller p-value beside it is not, which is not a thing anyone can
    defend in a table.
    """
    p = np.asarray(pvals, float)
    m = len(p)
    order = np.argsort(p)
    ranked = p[order] * m / np.arange(1, m + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(ranked, 1.0)
    return out


def benjamini_yekutieli(pvals) -> np.ndarray:
    """BH scaled by the harmonic number: valid under ARBITRARY dependence.

    Reported beside BH because the cells here are anything but independent -
    neighbouring capitals share weather and the seven leads share days. BH is
    valid under positive regression dependence, which these plausibly satisfy;
    BY needs no such argument and costs a factor of ~5.2 at m = 105. Quoting
    both means the reader does not have to take the dependence argument on
    trust.
    """
    p = np.asarray(pvals, float)
    m = len(p)
    c = float(np.sum(1.0 / np.arange(1, m + 1)))
    return np.minimum(benjamini_hochberg(p) * c, 1.0)


# ---------------------------------------------------------------------------
# The headline battery
# ---------------------------------------------------------------------------
def statistics(g: pd.DataFrame, rows: np.ndarray | None = None) -> dict:
    """Every paired difference, vendor minus member-derived, on one sample.

    `rows` selects a bootstrap replicate; None scores the sample itself. The
    same function computes the point estimate and every replicate, so no
    statistic can be defined one way for the estimate and another way for its
    interval.
    """
    v = g.vendor_pop.to_numpy(float)
    e = g.event.to_numpy(float)
    d = g.pop_daytotal.to_numpy(float)
    a = g.pop_anystep.to_numpy(float)
    if rows is not None:
        v, e, d, a = v[rows], e[rows], d[rows], a[rows]

    dec_v = brier_decomposition(v, e, n_bins=N_PROB_BINS)
    dec_g = brier_decomposition(d, e, n_bins=N_PROB_BINS)
    out = {
        "brier_diff": dec_v["brier"] - dec_g["brier"],
        "brier_diff_anystep": float(np.mean((v - e) ** 2) - np.mean((a - e) ** 2)),
        "reliability_diff": dec_v["reliability"] - dec_g["reliability"],
        "resolution_diff": dec_v["resolution"] - dec_g["resolution"],
        "auc_diff": roc_auc(v, e) - roc_auc(d, e),
        "bias_vendor_minus_gefs": float(np.mean(v - d)),
    }
    alphas = np.asarray(HEADLINE_ALPHAS, float)
    cv = value_curve(v, e, alphas=alphas).v_calibrated.to_numpy(float)
    cg = value_curve(d, e, alphas=alphas).v_calibrated.to_numpy(float)
    for i, al in enumerate(HEADLINE_ALPHAS):
        out[f"value_diff_a{int(round(al * 100)):02d}"] = float(cv[i] - cg[i])
    return out


def test_cell(g: pd.DataFrame, margin: float, n_boot: int = BOOTSTRAP_N,
              block: int | None = None,
              seed: int = RANDOM_SEED) -> pd.DataFrame:
    """The full battery for one (mode, lead) cell, with its intervals.

    `block` defaults to the length `choose_block` measures from this cell's own
    daily Brier-difference series, so the resampling is fitted to the memory of
    the quantity being tested rather than to a constant set for a different
    purpose elsewhere in the pipeline.
    """
    rng = np.random.default_rng(seed)
    off = day_offsets(g)
    n_days = len(off) - 1
    day_index = np.repeat(np.arange(n_days), np.diff(off))
    daily = daily_series(g.d_brier.to_numpy(float), day_index, n_days)
    if block is None:
        block = choose_block(daily)

    point = statistics(g)
    starts, blk = block_starts(rng, n_days, block, n_boot)
    reps: dict[str, list[float]] = {k: [] for k in point}
    for b in range(n_boot):
        rows = replicate_rows(starts[b], off, blk)
        for k, val in statistics(g, rows).items():
            reps[k].append(val)

    rows_out = []
    for k, theta in point.items():
        # The equivalence margin is a statement about Brier, so it is applied
        # to the Brier statistics and to nothing else. A margin for AUC or for
        # economic value would have to be argued separately and is not.
        marg = margin if k in ("brier_diff", "brier_diff_anystep") else None
        r = {"statistic": k}
        r.update(summarise(np.asarray(reps[k], float), theta, margin=marg))
        if k in MEAN_STATISTICS:
            col = {"brier_diff": "d_brier",
                   "brier_diff_anystep": "d_brier_anystep",
                   "bias_vendor_minus_gefs": "d_bias"}[k]
            r.update(analytic_ses(g[col].to_numpy(float), day_index, n_days,
                                  lag=blk))
        r.update({"n": int(len(g)), "n_days": n_days,
                  "n_cities": int(g.city.nunique()), "block_days": int(blk),
                  "tau_days": float(integrated_autocorr_time(daily))})
        rows_out.append(r)
    return pd.DataFrame(rows_out)


def block_sensitivity(g: pd.DataFrame, margin: float,
                      n_boot: int = BOOTSTRAP_N,
                      candidates=BLOCK_CANDIDATES,
                      seed: int = RANDOM_SEED) -> pd.DataFrame:
    """The lead-1 Brier verdict at every candidate block length.

    The block length is the one free parameter in this whole module, and a
    verdict that flips with it is not a verdict. Cheap to run because the Brier
    difference is a mean, so the vectorised bootstrap applies.
    """
    off = day_offsets(g)
    n_days = len(off) - 1
    day_index = np.repeat(np.arange(n_days), np.diff(off))
    d = g.d_brier.to_numpy(float)
    theta = float(d.mean())
    chosen = choose_block(daily_series(d, day_index, n_days))
    rows = []
    for block in sorted(set(candidates) | {chosen}):
        reps = bootstrap_mean(d, day_index, n_days, n_boot, block,
                              np.random.default_rng(seed))
        r = {"block_days": int(block), "chosen": block == chosen}
        r.update(summarise(reps, theta, margin=margin))
        rows.append(r)
    return pd.DataFrame(rows)


def equivalence_margin(paired: pd.DataFrame) -> float:
    """The largest Brier swing the ENSEMBLE shows between day-boundary rules.

    Recomputed here from the paired sample rather than imported as a number, so
    it tracks the data: if a future sample makes the boundary rule matter more,
    the bar for calling two forecasts equivalent rises with it.
    """
    briers = {}
    for (mode, lead), g in paired.groupby(["boundary_mode", "lead_days"]):
        briers[(mode, lead)] = float(np.mean(
            (g.pop_daytotal.to_numpy(float) - g.event.to_numpy(float)) ** 2))
    swings = [abs(briers[("snap", lead)] - briers[("prorata", lead)])
              for lead in sorted({l for _, l in briers})
              if ("snap", lead) in briers and ("prorata", lead) in briers]
    if not swings:
        die("cannot compute the equivalence margin: both boundary rules must "
            "be present in the paired sample")
    return float(max(swings))


# ---------------------------------------------------------------------------
# Task 26: the per-city panel
# ---------------------------------------------------------------------------
def test_cells(paired: pd.DataFrame, mode: str = GEFS_BOUNDARY_PRIMARY,
               n_boot: int = BOOTSTRAP_N, block: int | None = None,
               seed: int = RANDOM_SEED) -> pd.DataFrame:
    """Paired Brier difference per city x lead, then FDR across the panel.

    Within a single city there is no spatial dimension left, so the day blocks
    here handle serial correlation only - and the vectorised mean bootstrap is
    exact for this statistic, which is why 105 cells cost about a second. Each
    cell measures its own block length: a maritime city's scores decorrelate on
    a different timescale from a Mediterranean one, and forcing one length on
    both would quietly mis-size half the panel.
    """
    rng = np.random.default_rng(seed)
    rows = []
    sel = paired[paired.boundary_mode == mode]
    for (city, lead), g in sel.groupby(["city", "lead_days"]):
        g = g.sort_values("local_date")
        if len(g) < MIN_CELL_DAYS:
            continue
        d = (((g.vendor_pop - g.event) ** 2)
             - ((g.pop_daytotal - g.event) ** 2)).to_numpy(float)
        n_days = len(d)
        idx = np.arange(n_days)
        blk = choose_block(d) if block is None else int(block)
        reps = bootstrap_mean(d, idx, n_days, n_boot, blk, rng)
        r = {"city": city, "lead_days": int(lead), "boundary_mode": mode,
             "n": n_days, "block_days": blk,
             "tau_days": float(integrated_autocorr_time(d))}
        r.update(summarise(reps, float(d.mean())))
        r.update(analytic_ses(d, idx, n_days, lag=blk))
        rows.append(r)
    out = pd.DataFrame(rows)
    if out.empty:
        die(f"no city x lead cell reached {MIN_CELL_DAYS} days")
    out["q_bh"] = benjamini_hochberg(out.p_boot.to_numpy(float))
    out["q_by"] = benjamini_yekutieli(out.p_boot.to_numpy(float))
    out["reject_bh"] = out.q_bh < Q_FDR
    out["reject_by"] = out.q_by < Q_FDR
    out["reject_uncorrected"] = out.p_boot < ALPHA_TEST
    return out


# ---------------------------------------------------------------------------
# Correctness checks
# ---------------------------------------------------------------------------
def _dependent_panel(rng, n_days: int = 600, n_cities: int = 15,
                     mu: float = 0.0, rho: float = 0.6,
                     day_sd: float = 1.0, row_sd: float = 1.0):
    """A panel with exactly the two dependencies this module claims to handle.

    A day effect shared by every city (spatial), itself AR(1) across days
    (temporal), plus independent per-row noise. The mean of the panel is `mu`
    by construction, so the truth an interval is supposed to cover is known.
    """
    shock = rng.normal(0.0, day_sd, n_days)
    a = np.empty(n_days)
    a[0] = shock[0]
    for t in range(1, n_days):
        a[t] = rho * a[t - 1] + np.sqrt(1 - rho ** 2) * shock[t]
    values = (mu + np.repeat(a, n_cities)
              + rng.normal(0.0, row_sd, n_days * n_cities))
    day_index = np.repeat(np.arange(n_days), n_cities)
    return values, day_index, n_days


def run_checks(verbose: bool = True, n_sim: int = 200) -> None:
    """Prove the machinery before believing any verdict it produces.

    The important check is the third: on data with the dependence this study's
    sample actually has, the naive interval fails to cover the truth about a
    quarter of the time at a nominal 95%. That is the size of the mistake this
    module exists to avoid, measured rather than asserted.
    """
    def say(msg):
        if verbose:
            print(f"  ok  {msg}")

    rng = np.random.default_rng(RANDOM_SEED)

    # --- 1. The two bootstrap routes must agree -----------------------------
    # `bootstrap_mean` never materialises a row; `replicate_rows` does. They
    # are the same estimator and must give the same replicates from the same
    # seed, or the fast path used for the 105-cell panel is not the method
    # documented for the headline.
    v, di, nd = _dependent_panel(rng, n_days=120, n_cities=4)
    fast = bootstrap_mean(v, di, nd, 200, BOOTSTRAP_BLOCK_DAYS,
                          np.random.default_rng(1))
    off = np.r_[np.arange(0, len(v), 4), len(v)]
    starts, blk = block_starts(np.random.default_rng(1), nd,
                               BOOTSTRAP_BLOCK_DAYS, 200)
    slow = np.array([v[replicate_rows(s, off, blk)].mean() for s in starts])
    assert np.allclose(fast, slow), "the vectorised and explicit bootstraps differ"
    say("the vectorised bootstrap reproduces the explicit row-resampling one")

    # --- 2. Standard errors against the Monte Carlo truth -------------------
    # The panel's day effect is AR(1) with rho = 0.6, so its decorrelation time
    # is (1 + rho) / (1 - rho) = 4 days and blocks must run to several times
    # that. This is where the pipeline's 7-day default is shown to be too short
    # for THIS statistic: it is not wrong elsewhere, it is wrong here.
    thetas = np.array([_dependent_panel(rng)[0].mean() for _ in range(n_sim)])
    true_sd = float(thetas.std(ddof=1))
    v, di, nd = _dependent_panel(rng)
    daily = daily_series(v, di, nd)
    tau = integrated_autocorr_time(daily)
    chosen = choose_block(daily)
    assert 3.0 < tau < 6.0, f"tau {tau:.2f} should recover the AR(1) truth of 4"
    assert chosen >= 14, f"the rule chose {chosen} days for a 4-day memory"
    se_boot = float(bootstrap_mean(v, di, nd, 1000, chosen, rng).std(ddof=1))
    se_short = float(bootstrap_mean(v, di, nd, 1000, BOOTSTRAP_BLOCK_DAYS,
                                    rng).std(ddof=1))
    an = analytic_ses(v, di, nd, lag=chosen)
    assert 0.85 < se_boot / true_sd < 1.25, \
        f"block bootstrap SE {se_boot:.4f} vs truth {true_sd:.4f}"
    assert se_short / true_sd < 0.95, \
        ("the 7-day default should be measurably too narrow here; if it is "
         "not, the block-choice rule is solving a problem that does not exist")
    assert an["se_naive"] / true_sd < 0.7, \
        f"the naive SE should be badly too small, got {an['se_naive'] / true_sd:.2f}"
    assert an["se_cluster"] / true_sd < 0.95, \
        "clustering by day alone cannot capture the serial correlation"
    assert 0.8 < an["se_nw"] / true_sd < 1.3, "Newey-West SE off the truth"
    say(f"the block rule measures tau = {tau:.1f} days and picks L = {chosen}; "
        f"against the Monte Carlo\n      truth the SE ratios are: bootstrap "
        f"{se_boot / true_sd:.2f}, Newey-West {an['se_nw'] / true_sd:.2f}, "
        f"day-cluster {an['se_cluster'] / true_sd:.2f}, naive "
        f"{an['se_naive'] / true_sd:.2f}\n      - and the pipeline's 7-day "
        f"default would have given {se_short / true_sd:.2f}, which is why the "
        f"length is measured rather than inherited")

    # --- 3. Coverage, which is what an interval is actually promising -------
    cov_boot = cov_naive = 0
    false_pos_boot = false_pos_naive = 0
    for _ in range(n_sim // 2):
        v, di, nd = _dependent_panel(rng, mu=0.0)
        blk = choose_block(daily_series(v, di, nd))
        reps = bootstrap_mean(v, di, nd, 400, blk, rng)
        lo, hi = np.quantile(reps, [0.025, 0.975])
        cov_boot += lo <= 0.0 <= hi
        false_pos_boot += boot_pvalue(reps, float(v.mean())) < 0.05
        a = analytic_ses(v, di, nd, lag=blk)
        m = float(v.mean())
        cov_naive += abs(m) <= 1.96 * a["se_naive"]
        false_pos_naive += a["p_naive"] < 0.05
    n = n_sim // 2
    # The coverage estimate is itself a Monte Carlo quantity, so the bar has to
    # carry its own error bar - otherwise the check fails at random whenever it
    # runs with fewer simulations. Two and a half binomial standard errors.
    tol = 2.5 * float(np.sqrt(0.9 * 0.1 / n))
    assert cov_boot / n > 0.90 - tol, f"bootstrap coverage {cov_boot / n:.2f}"
    assert cov_naive / n < 0.85, \
        f"the naive interval should under-cover badly here, got {cov_naive / n:.2f}"
    assert false_pos_boot / n < 0.12 + tol, \
        f"bootstrap false-positive rate {false_pos_boot / n:.2f}"
    say(f"on dependent data the block-day bootstrap covers "
        f"{cov_boot / n:.0%} of the time at a nominal 95% and rejects a true "
        f"null {false_pos_boot / n:.0%} of the time;\n      the naive interval "
        f"covers {cov_naive / n:.0%} and rejects {false_pos_naive / n:.0%} - "
        f"this is the error the day-block resampling exists to avoid")

    # --- 4. Equivalence testing ---------------------------------------------
    # A genuinely null difference with a generous margin must be DECLARED
    # equivalent, and a difference of three times the margin must not be -
    # otherwise TOST would be a rubber stamp rather than a test that can fail.
    v, di, nd = _dependent_panel(rng, mu=0.0, day_sd=0.2, row_sd=0.2)
    reps = bootstrap_mean(v, di, nd, 1000, choose_block(daily_series(v, di, nd)),
                          rng)
    margin = 0.25
    assert tost_pvalue(reps, float(v.mean()), margin) < 0.05, \
        "a null difference well inside the margin must be declared equivalent"
    v2, di2, nd2 = _dependent_panel(rng, mu=3 * margin, day_sd=0.2, row_sd=0.2)
    reps2 = bootstrap_mean(v2, di2, nd2, 1000,
                           choose_block(daily_series(v2, di2, nd2)), rng)
    assert tost_pvalue(reps2, float(v2.mean()), margin) > 0.05, \
        "a difference of three margins must not be declared equivalent"
    assert boot_pvalue(reps2, float(v2.mean())) < 0.05, \
        "a difference of three margins must be significant"
    # And the distinction TOST exists to make: a tiny, noisy sample is not
    # significant AND not equivalent. Asserted over many draws rather than one,
    # because a single underpowered panel is significant about 5% of the time
    # by definition and a check that can fail on the roll of a die is worse
    # than no check. These panels are deliberately short - 60 days - which is
    # also the regime where the bootstrap itself is least trustworthy, so the
    # rejection bar is loose and the equivalence bar is the one that matters.
    n_small = 60
    sig = eq = 0
    for _ in range(n_small):
        v3, di3, nd3 = _dependent_panel(rng, n_days=60, mu=0.0, day_sd=2.0)
        blk3 = choose_block(daily_series(v3, di3, nd3))
        reps3 = bootstrap_mean(v3, di3, nd3, 400, blk3, rng)
        sig += boot_pvalue(reps3, float(v3.mean())) < 0.05
        eq += tost_pvalue(reps3, float(v3.mean()), 0.05) < 0.05
    assert sig / n_small < 0.20, f"underpowered panels rejected {sig / n_small:.0%}"
    assert eq / n_small < 0.05, \
        ("an underpowered sample must fail BOTH tests; equivalence was "
         f"declared on {eq / n_small:.0%} of them")
    say(f"TOST declares a null difference equivalent, refuses a difference of "
        f"three margins,\n      and on {n_small} underpowered panels declares "
        f"equivalence {eq / n_small:.0%} of the time while rejecting "
        f"{sig / n_small:.0%}")

    # --- 5. Task 26: the FDR procedures -------------------------------------
    m = 400
    null_p = rng.random(m)
    q = benjamini_hochberg(null_p)
    assert np.all(np.diff(q[np.argsort(null_p)]) >= -1e-12), \
        "adjusted p-values must be monotone in the raw ones"
    assert (q >= null_p - 1e-12).all(), "adjustment can only increase a p-value"
    assert (benjamini_yekutieli(null_p) >= q - 1e-12).all(), \
        "BY must be at least as conservative as BH"
    assert (q <= np.minimum(null_p * m, 1.0) + 1e-12).all(), \
        "BH must reject at least as much as Bonferroni"
    # Under the global null BH controls the chance of ANY discovery at q.
    hits = 0
    for _ in range(200):
        hits += (benjamini_hochberg(rng.random(100)) < 0.10).any()
    assert hits / 200 < 0.20, f"BH fired on {hits / 200:.0%} of null panels"
    # ...and it must still find real signal, or control would be worthless.
    mixed = np.r_[rng.random(90), rng.random(10) * 1e-4]
    found = (benjamini_hochberg(mixed) < 0.10)[-10:].sum()
    assert found >= 8, f"BH recovered only {found}/10 planted signals"
    say(f"BH is monotone, no less powerful than Bonferroni, fires on "
        f"{hits / 200:.0%} of pure-null panels at q=0.10,\n      and recovers "
        f"{found}/10 planted signals; BY is uniformly more conservative")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _stars(p: float) -> str:
    if not np.isfinite(p):
        return "    "
    return "*** " if p < 0.001 else ("**  " if p < 0.01 else
                                     ("*   " if p < ALPHA_TEST else "n.s."))


def report(head: pd.DataFrame, cells: pd.DataFrame, sens: pd.DataFrame,
           margin: float) -> None:
    mode = GEFS_BOUNDARY_PRIMARY
    print("\n" + "=" * 78)
    print("SIGNIFICANCE: is the served-vs-ensemble verdict a result or noise?")
    print("=" * 78)
    h1 = head[(head.boundary_mode == mode) & (head.lead_days == 1)]
    n = int(h1.n.iloc[0])
    print(f"  Lead 1, boundary rule '{mode}': {n} paired city-days over "
          f"{int(h1.n_days.iloc[0])} calendar days\n  and "
          f"{int(h1.n_cities.iloc[0])} cities. Resampling unit: the calendar "
          f"day, in blocks of {int(h1.block_days.iloc[0])},\n  so every city "
          f"scored on a day enters or leaves a replicate together.")
    print("  Sign convention: every statistic is VENDOR MINUS MEMBER-DERIVED. "
          "Negative favours\n  the vendor on Brier, reliability and the "
          "dry-bias line (all lower-is-better), and\n  favours the ENSEMBLE "
          "on resolution, AUC and economic value (higher-is-better).")
    print(f"  Equivalence margin: {margin:.4f}, the largest Brier swing the "
          f"ensemble itself shows\n  between the two day-boundary rules.")

    print("\n--- 1. Lead 1, the only same-lead comparison ---")
    print(f"   {'statistic':<24} {'estimate':>9} {'95% CI':>19} "
          f"{'p':>8}      {'MDE(80%)':>9}")
    for _, r in h1.iterrows():
        print(f"   {r.statistic:<24} {r.estimate:+9.4f} "
              f"[{r.ci_lo:+8.4f},{r.ci_hi:+8.4f}] {r.p_boot:8.4f} "
              f"{_stars(r.p_boot)} {r.mde_80:9.4f}")

    b = h1[h1.statistic == "brier_diff"].iloc[0]
    print(f"\n  VERDICT ON THE TIE. The lead-1 Brier difference is "
          f"{b.estimate:+.4f} "
          f"(95% CI {b.ci_lo:+.4f} to {b.ci_hi:+.4f}), p = {b.p_boot:.3f}.")
    if b.p_boot >= ALPHA_TEST and bool(b.equivalent):
        print(f"  Not significant AND demonstrably within the margin "
              f"(TOST p = {b.p_tost:.4f}): the two\n  probabilities are "
              f"EQUIVALENT at {margin:.4f} Brier. The tie is now a result "
              f"rather than a\n  failure to find something.")
    elif b.p_boot >= ALPHA_TEST:
        print(f"  Not significant, but NOT shown equivalent either "
              f"(TOST p = {b.p_tost:.4f}). The sample is\n  consistent both "
              f"with no difference and with one larger than the "
              f"{margin:.4f} margin;\n  the honest statement is that this "
              f"study cannot resolve it, and the smallest\n  difference it "
              f"could have detected is {b.mde_80:.4f}.")
        if b.mde_80 > margin:
            print(f"  READ THAT AGAIN BEFORE QUOTING THE TIE. The detectable "
                  f"difference, {b.mde_80:.4f}, is\n  {b.mde_80 / margin:.0f} "
                  f"times the margin, so NO sample of this size and "
                  f"correlation could ever have\n  demonstrated equivalence "
                  f"at it. 'The vendor is as well calibrated as the ensemble' "
                  f"is\n  therefore not a finding of this study - what is a "
                  f"finding is that the difference, if\n  any, is smaller "
                  f"than {abs(b.ci_lo):.3f} Brier, alongside the decision-value "
                  f"gap in section 3\n  which the same sample resolves "
                  f"comfortably. Closing this needs cities (G1), not days.")
    else:
        better = "vendor" if b.estimate < 0 else "member-derived"
        print(f"  SIGNIFICANT: the {better} probability is better calibrated "
              f"at lead 1, and the\n  difference survives both the spatial "
              f"and the serial dependence. This overturns\n  the dead-heat "
              f"reading in triangulation.py.")

    print("\n--- 2. What the dependence handling costs, per standard error ---")
    m = h1[h1.statistic.isin(MEAN_STATISTICS)]
    print(f"   {'statistic':<24} {'boot':>9} {'Newey-W':>9} {'cluster':>9} "
          f"{'naive':>9}   naive too narrow by")
    for _, r in m.iterrows():
        print(f"   {r.statistic:<24} {r.se_boot:9.5f} {r.se_nw:9.5f} "
              f"{r.se_cluster:9.5f} {r.se_naive:9.5f}   "
              f"{r.se_boot / r.se_naive:.1f}x")
    print("  A test run on the naive standard error would be that many times "
          "too confident.\n  Any published verdict on these data that does "
          "not resample whole days is wrong by\n  about that factor.")

    print("\n--- 3. Decision value: the claim the Brier tie conceals (E4a) ---")
    for _, r in h1[h1.statistic.str.startswith("value_diff")].iterrows():
        al = int(r.statistic[-2:])
        print(f"   alpha = 0.{al:02d}   V(vendor) - V(GEFS) = {r.estimate:+.3f} "
              f"[{r.ci_lo:+.3f}, {r.ci_hi:+.3f}]  p = {r.p_boot:.4f} "
              f"{_stars(r.p_boot)}")
    lo = h1[h1.statistic == "value_diff_a05"].iloc[0]
    if lo.p_boot < ALPHA_TEST and lo.estimate < 0:
        print(f"  The low-cost-ratio user's loss IS resolved by this sample "
              f"even though the average\n  score is not: the same days that "
              f"cannot separate two Brier scores separate these\n  value "
              f"curves by {abs(lo.estimate):.2f} with a p of {lo.p_boot:.4f}. "
              f"Unresolved on the mean score, clearly\n  worse for the cheap-"
              f"action user - and it is the metric choice, not the data, that "
              f"decides\n  whether a reader ever sees it.")

    print("\n--- 4. The mechanism (D10): reliability against resolution ---")
    rel = h1[h1.statistic == "reliability_diff"].iloc[0]
    res = h1[h1.statistic == "resolution_diff"].iloc[0]
    both = rel.p_boot < ALPHA_TEST and res.p_boot < ALPHA_TEST
    print(f"   reliability {rel.estimate:+.4f} (p = {rel.p_boot:.4f}), "
          f"resolution {res.estimate:+.4f} (p = {res.p_boot:.4f})")
    if both and rel.estimate < 0 and res.estimate < 0:
        print("  Both significant and both favouring the vendor on honesty "
              "while costing it\n  resolution: the trade is real, measured, "
              "and not an artefact of one cancelling\n  Brier difference. "
              "This is the finding, not the tie.")
    elif not both:
        print("  At least one leg of the trade is not resolved by this "
              "sample, so D10 is stated as\n  a mechanism consistent with "
              "the data rather than as a demonstrated one.")

    print("\n--- 5. Across leads and both boundary rules ---")
    print(f"   {'rule':<9} {'lead':>4} {'Brier diff':>11} {'95% CI':>20} "
          f"{'p':>8}   equivalent?")
    for _, r in head[head.statistic == "brier_diff"].sort_values(
            ["boundary_mode", "lead_days"]).iterrows():
        eq = "yes" if bool(r.equivalent) else "no"
        print(f"   {r.boundary_mode:<9} {int(r.lead_days):>4} "
              f"{r.estimate:+11.4f} [{r.ci_lo:+8.4f},{r.ci_hi:+8.4f}] "
              f"{r.p_boot:8.4f}   {eq}")
    print("  Leads beyond 1 are NOT same-lead comparisons (the vendor archive "
          "carries no lead\n  axis at all), so a significant difference there "
          "measures how fast the ensemble\n  loses what the served number "
          "still has - not who forecasts better.")

    print("\n--- 6. Task 26: the per-city panel under FDR control ---")
    c = cells
    print(f"   {len(c)} city x lead cells, {int(c.n.min())}-{int(c.n.max())} "
          f"days each, paired Brier difference per cell.")
    print(f"   Uncorrected, p < {ALPHA_TEST}:      "
          f"{int(c.reject_uncorrected.sum()):>3} cells")
    print(f"   Benjamini-Hochberg, q < {Q_FDR}:  "
          f"{int(c.reject_bh.sum()):>3} cells")
    print(f"   Benjamini-Yekutieli, q < {Q_FDR}: "
          f"{int(c.reject_by.sum()):>3} cells  (valid under arbitrary "
          f"dependence)")
    won = int((c.estimate < 0).sum())
    print(f"   The vendor has the lower Brier in {won} of {len(c)} cells "
          f"({won / len(c):.0%}) - but only\n   {int(c.reject_bh.sum())} of "
          f"those cells survive FDR control, so the '63% of cells' figure\n"
          f"   quoted from the per-city panel is a direction, not {len(c)} "
          f"findings.")
    if c.reject_bh.any():
        top = c[c.reject_bh].reindex(
            c[c.reject_bh].estimate.abs().sort_values(ascending=False).index)
        print("   Surviving cells:")
        for _, r in top.head(10).iterrows():
            print(f"     {r.city:<14} lead {int(r.lead_days)}  "
                  f"{r.estimate:+.4f}  q_BH = {r.q_bh:.4f}")

    print("\n--- 7. Sensitivity to the one free parameter: block length ---")
    print(f"   {'block':>6} {'Brier diff':>11} {'95% CI':>20} {'p':>8} "
          f"{'TOST p':>8}  equivalent?")
    for _, r in sens.iterrows():
        mark = " <- chosen" if bool(r.chosen) else ""
        print(f"   {int(r.block_days):>6} {r.estimate:+11.4f} "
              f"[{r.ci_lo:+8.4f},{r.ci_hi:+8.4f}] {r.p_boot:8.4f} "
              f"{r.p_tost:8.4f}  {'yes' if bool(r.equivalent) else 'no':<3}"
              f"{mark}")
    stable = len(set(sens.equivalent)) == 1 and len(
        {r.p_boot < ALPHA_TEST for _, r in sens.iterrows()}) == 1
    print(f"  The verdict is {'UNCHANGED' if stable else 'NOT STABLE'} across "
          f"every block length tried.\n  The chosen one is not a preference: "
          f"it is {int(head.block_days.iloc[0])} days because the daily score "
          f"difference decorrelates\n  in tau = {head.tau_days.iloc[0]:.1f} "
          f"days and a block has to hold several of those.")

    print("\n--- Caveats ---")
    print(f"  1. Block bootstraps are asymptotic in the number of BLOCKS, not "
          f"rows. At "
          f"{int(h1.n_days.iloc[0]) // int(h1.block_days.iloc[0])} blocks of "
          f"{int(h1.block_days.iloc[0])} days\n     the intervals are usable "
          f"but not exact, and a rain regime with a longer memory than\n     "
          f"the measured tau = {h1.tau_days.iloc[0]:.1f} days would widen "
          f"them further.")
    print("  2. Resampling whole days handles spatial dependence WITHIN a "
          "day. It does not model\n     dependence between neighbouring "
          "cities on different days, which for capitals\n     hundreds of km "
          "apart is the smaller term but is not zero.")
    print("  3. Equivalence is asserted at one margin, derived from one "
          "methodological\n     sensitivity. A reader who thinks a smaller "
          "Brier difference matters is entitled\n     to, and the CI is "
          "printed so they can apply their own margin.")
    print("  4. Every number here rests on 15 European capitals over 21 "
          "months. These tests say\n     what this sample can and cannot "
          "resolve; they say nothing about any other\n     region, and the "
          "unresolved Brier comparison stays unresolved until the city\n     "
          "count rises (G1, G20).")


# ---------------------------------------------------------------------------
def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    n_boot = 300 if arg == "quick" else BOOTSTRAP_N

    print("=== correctness checks ===")
    run_checks(n_sim=60 if arg == "quick" else 200)
    if arg in ("check", "selftest"):
        print("all checks passed")
        return

    paired = triangulation.build_paired(LIKE_FOR_LIKE_MODEL)
    triangulation.check_paired(paired)
    margin = equivalence_margin(paired)

    # The full battery at lead 1 under BOTH boundary rules - the headline
    # comparison and its methodological sensitivity - plus every lead under the
    # primary rule. Not the whole grid: leads beyond 1 under the secondary rule
    # would be a sensitivity of a comparison that is not like-for-like anyway.
    cells_to_test = ([(m, 1) for m in GEFS_BOUNDARY_MODES]
                     + [(GEFS_BOUNDARY_PRIMARY, l) for l in LEAD_DAYS if l != 1])
    print(f"\n=== testing {len(cells_to_test)} (rule, lead) cells x "
          f"{n_boot} block-day replicates ===")
    parts = []
    for mode, lead in cells_to_test:
        g = cell(paired, mode, lead)
        out = test_cell(g, margin, n_boot=n_boot)
        out["boundary_mode"] = mode
        out["lead_days"] = int(lead)
        out["model"] = LIKE_FOR_LIKE_MODEL
        parts.append(out)
        b = out[out.statistic == "brier_diff"].iloc[0]
        print(f"  {mode:<8} lead {lead}: Brier diff {b.estimate:+.4f} "
              f"[{b.ci_lo:+.4f}, {b.ci_hi:+.4f}], p = {b.p_boot:.4f}")
    head = pd.concat(parts, ignore_index=True)

    print(f"\n=== per-city panel, {GEFS_BOUNDARY_PRIMARY} rule ===")
    cells = test_cells(paired, n_boot=n_boot)
    print(f"  {len(cells)} cells tested, {int(cells.reject_bh.sum())} survive "
          f"BH at q = {Q_FDR}")

    sens = block_sensitivity(cell(paired, GEFS_BOUNDARY_PRIMARY, 1), margin,
                             n_boot=max(n_boot, 1000))

    HEADLINE_TABLE.parent.mkdir(parents=True, exist_ok=True)
    head.to_parquet(HEADLINE_TABLE, index=False)
    cells.to_parquet(CELL_TABLE, index=False)
    sens.to_parquet(BLOCK_TABLE, index=False)
    report(head, cells, sens, margin)
    print(f"\nwrote {HEADLINE_TABLE}, {CELL_TABLE.name} and {BLOCK_TABLE.name}")


if __name__ == "__main__":
    main()
