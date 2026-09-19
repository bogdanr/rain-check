"""Verification metrics beyond the Brier family (Tasks 22-23).

Nothing here plots and nothing here reads the disk; `decision_metrics.py` does
both. Keeping the maths in one dependency-free module is what makes the
correctness checks at the bottom worth anything: every function is exercised
against a case whose answer is known analytically rather than against last
week's output.

Three families live here:

  Task 23  RELATIVE ECONOMIC VALUE (Richardson). The direct operationalisation
           of "how much can this forecast be trusted for a decision": a user
           facing cost C of protecting and loss L of being caught out has a
           cost-loss ratio alpha = C/L, and V(alpha) is the fraction of the
           distance from climatology to a perfect forecast that acting on this
           forecast recovers. V = 0 means the forecast is worth no more than
           knowing the long-run rain frequency; V = 1 means it is worth as much
           as knowing the future.
  Task 22  CRPS, ROC/AUC and sharpness. CRPS is the comparator WeatherBench 2
           uses, so reporting it makes these results legible to the ML weather
           community, which does not use Brier. ROC/AUC is invariant to any
           monotone relabelling of the probabilities, which is precisely why it
           is worth reporting beside reliability: it measures discrimination
           only, and a forecast can discriminate perfectly while being grossly
           miscalibrated. That separation is this study's whole argument.

Usage:  python src/metrics.py        # run the correctness checks
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Cost-loss ratios evaluated. 0 and 1 are excluded because V is undefined
# there: a user who pays nothing to protect always protects, and one whose
# protection costs as much as the loss never does, so no forecast can help
# either of them.
ALPHA_GRID = np.round(np.arange(0.01, 1.00, 0.01), 10)

# Cost-loss ratios quoted in the headline tables. These bracket the range real
# users sit in: 0.05 is a cheap precaution against an expensive loss (covering
# a crop, postponing a pour), 0.5 is protection that costs about as much as the
# damage it avoids.
HEADLINE_ALPHAS = (0.05, 0.10, 0.20, 0.30, 0.50)


def _pair(prob, event) -> tuple[np.ndarray, np.ndarray]:
    """Validate and coerce a (probability, binary outcome) pair.

    Fails loudly: a NaN or an out-of-range probability silently poisons every
    metric downstream, and each of these has actually reached this layer at
    least once via an incomplete forecast day.
    """
    p = np.asarray(prob, dtype=float)
    e = np.asarray(event, dtype=float)
    if p.shape != e.shape:
        raise ValueError(f"prob/event length mismatch: {p.shape} vs {e.shape}")
    if p.size == 0:
        raise ValueError("empty series")
    if not np.isfinite(p).all() or not np.isfinite(e).all():
        raise ValueError("prob/event contain NaN or inf")
    if p.min() < 0.0 or p.max() > 1.0:
        raise ValueError(f"probabilities outside [0,1]: [{p.min()}, {p.max()}]")
    if not np.isin(e, (0.0, 1.0)).all():
        raise ValueError("event must be binary 0/1")
    return p, e


# ---------------------------------------------------------------------------
# Task 23: relative economic value
# ---------------------------------------------------------------------------
def contingency_rates(prob, event, thresholds) -> tuple[np.ndarray, ...]:
    """Hit / false-alarm / miss rates as fractions of n, for many thresholds.

    The decision rule is "act when the stated probability is at or above the
    trigger". Computed by one sort plus a searchsorted rather than a loop over
    thresholds, because the envelope in `value_curve` needs every candidate
    trigger against every cost-loss ratio and the naive form is O(n * t).
    """
    p, e = _pair(prob, event)
    t = np.asarray(thresholds, dtype=float)
    order = np.argsort(p, kind="mergesort")
    ps, es = p[order], e[order]
    cum = np.concatenate([[0.0], np.cumsum(es)])
    n = len(ps)
    total_events = cum[-1]

    idx = np.searchsorted(ps, t, side="left")   # first index with p >= t
    acted = n - idx
    hits = total_events - cum[idx]
    false_alarms = acted - hits
    misses = total_events - hits
    return hits / n, false_alarms / n, misses / n, total_events / n


def _value(alpha: np.ndarray, hit: np.ndarray, false_alarm: np.ndarray,
           miss: np.ndarray, base: float) -> np.ndarray:
    """Richardson's V for each (threshold, alpha) pair. Shapes: (t,) x (a,).

    Expenses are expressed per unit loss L, so C/L = alpha is the only free
    parameter: acting costs alpha whether or not the event happens, and being
    caught out unprotected costs 1. The climatological user acts every day if
    alpha < s and never if alpha > s, so their expense is min(alpha, s); the
    perfect forecast pays alpha only on the s days the event occurs.
    """
    a = np.asarray(alpha, dtype=float)[None, :]
    expense = a * (hit[:, None] + false_alarm[:, None]) + miss[:, None]
    clim = np.minimum(a, base)
    perfect = a * base
    denom = clim - perfect
    # denom > 0 strictly for 0 < alpha < 1 and 0 < s < 1; a degenerate base
    # rate makes every user's decision forecast-independent, and V undefined.
    with np.errstate(divide="ignore", invalid="ignore"):
        v = (clim - expense) / denom
    return np.where(denom > 0, v, np.nan)


def value_curve(prob, event, alphas=ALPHA_GRID,
                thresholds=None) -> pd.DataFrame:
    """V(alpha), both as the envelope over triggers and at the calibrated one.

    Two curves, because they answer different questions:

      `v_envelope`   the best this forecast could do for that user if they
                     knew, from hindsight, the trigger to use. This is what the
                     literature usually plots, and it is an upper bound.
      `v_calibrated` what the user actually gets by following the textbook rule
                     "act when the stated probability exceeds your cost-loss
                     ratio". This rule is optimal only if the probability means
                     what it says, so the gap between the two curves is the
                     decision-currency price of miscalibration - which is the
                     quantity this study exists to measure.
    """
    p, e = _pair(prob, event)
    a = np.asarray(alphas, dtype=float)
    if thresholds is None:
        # Every distinct issued probability is a candidate trigger, plus 0 (act
        # always) and just above 1 (never act) so the envelope can fall back on
        # the two climatological strategies.
        thresholds = np.unique(np.concatenate([[0.0], np.unique(p), [1.0 + 1e-9]]))
    t = np.asarray(thresholds, dtype=float)

    h, f, m, base = contingency_rates(p, e, t)
    v = _value(a, h, f, m, base)                       # (n_thresholds, n_alpha)

    ha, fa, ma, _ = contingency_rates(p, e, a)
    v_cal = np.diagonal(_value(a, ha, fa, ma, base))   # trigger == alpha

    best = np.nanargmax(v, axis=0) if np.isfinite(v).any() else np.zeros(len(a), int)
    return pd.DataFrame({
        "alpha": a,
        "v_envelope": v[best, np.arange(len(a))],
        "envelope_threshold": t[best],
        "v_calibrated": v_cal,
        "base_rate": base,
        "n": len(p),
    })


def value_summary(curve: pd.DataFrame,
                  alphas=HEADLINE_ALPHAS) -> dict:
    """Headline numbers from a value curve: the peak, where it sits, and where
    the forecast is worth anything at all."""
    env, cal, a = curve.v_envelope.values, curve.v_calibrated.values, curve.alpha.values
    pos = a[cal > 0]
    out = {
        "n": int(curve.n.iloc[0]),
        "base_rate": float(curve.base_rate.iloc[0]),
        "v_max": float(np.nanmax(env)),
        "alpha_at_v_max": float(a[int(np.nanargmax(env))]),
        "threshold_at_v_max": float(curve.envelope_threshold.values[int(np.nanargmax(env))]),
        "v_max_calibrated": float(np.nanmax(cal)),
        "alpha_at_v_max_calibrated": float(a[int(np.nanargmax(cal))]),
        # Over how much of the user population is this forecast worth acting on
        # at all, under the rule a user would actually apply?
        "alpha_positive_lo": float(pos.min()) if len(pos) else np.nan,
        "alpha_positive_hi": float(pos.max()) if len(pos) else np.nan,
        "alpha_positive_share": float(len(pos) / len(a)),
    }
    for x in alphas:
        i = int(np.argmin(np.abs(a - x)))
        out[f"v_env_a{int(round(x * 100)):02d}"] = float(env[i])
        out[f"v_cal_a{int(round(x * 100)):02d}"] = float(cal[i])
    return out


# ---------------------------------------------------------------------------
# Task 22: CRPS
# ---------------------------------------------------------------------------
def crps_ensemble(members, obs) -> np.ndarray:
    """CRPS of an empirical predictive distribution, per observation.

    The energy-form estimator, CRPS = E|X - y| - 0.5 E|X - X'|, evaluated in
    O(k log k) per case via the sorted identity for the pairwise term rather
    than the O(k^2) double sum - the climatological distributions used as a
    reference here carry hundreds of members per day.

    `members` is (n, k) or (k,); this is the NRG (biased) estimator, which is
    the WeatherBench 2 convention, so the numbers are comparable to theirs.
    """
    x = np.atleast_2d(np.asarray(members, dtype=float))
    y = np.asarray(obs, dtype=float).reshape(-1)
    if x.shape[0] != len(y):
        x = x.reshape(len(y), -1)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("crps_ensemble: non-finite input")
    k = x.shape[1]
    xs = np.sort(x, axis=1)
    i = np.arange(1, k + 1)
    term1 = np.abs(xs - y[:, None]).mean(axis=1)
    term2 = (xs * (2 * i - k - 1)).sum(axis=1) / (k * k)
    return term1 - term2


def crps_deterministic(forecast, obs) -> np.ndarray:
    """CRPS of a point forecast, which is exactly the absolute error.

    Worth having explicitly: it is how a deterministic forecast is placed on
    the same axis as an ensemble, and the identity is asserted in the checks.
    """
    return np.abs(np.asarray(forecast, float) - np.asarray(obs, float))


def crps_binary(prob, event) -> np.ndarray:
    """CRPS of a probability forecast for a binary event - i.e. the Brier score.

    For a 0/1 outcome the predictive CDF is a single step, and the integral
    that defines CRPS collapses to (p - y)^2. This is not a trivial restatement
    but the bridge the paper needs: it says the Brier numbers already published
    ARE CRPS numbers for this event, so a reader from the CRPS community can
    read the table without conversion.
    """
    p, e = _pair(prob, event)
    return (p - e) ** 2


def crps_from_threshold_probs(thresholds, exceedance_prob, obs) -> np.ndarray:
    """CRPS as the integral of the Brier score over event thresholds.

    `exceedance_prob` is (n, t): the forecast probability that the observation
    exceeds each threshold. Used as an independent route to CRPS in the checks,
    and as the honest way to score a forecast that is only ever issued as a set
    of threshold probabilities.
    """
    t = np.asarray(thresholds, dtype=float)
    p = np.asarray(exceedance_prob, dtype=float)
    y = np.asarray(obs, dtype=float).reshape(-1, 1)
    bs = (p - (y > t[None, :]).astype(float)) ** 2
    return np.trapezoid(bs, t, axis=1)


def crps_skill_score(crps_forecast: float, crps_reference: float) -> float:
    return 1.0 - crps_forecast / crps_reference if crps_reference > 0 else np.nan


# ---------------------------------------------------------------------------
# Task 22: ROC and AUC
# ---------------------------------------------------------------------------
def roc_curve(prob, event) -> pd.DataFrame:
    """Hit rate against false-alarm rate over every trigger the data supports.

    Endpoints (0,0) and (1,1) are included so the trapezoidal area is the AUC
    and not a truncated version of it.
    """
    p, e = _pair(prob, event)
    order = np.argsort(-p, kind="mergesort")
    ps, es = p[order], e[order]
    n_pos, n_neg = es.sum(), (1 - es).sum()
    if n_pos == 0 or n_neg == 0:
        raise ValueError("ROC undefined: the event never happens or always does")

    tp = np.cumsum(es)
    fp = np.cumsum(1 - es)
    # One point per distinct probability: ties must be collapsed or the curve
    # walks through the interior of a tie block and understates the area.
    last = np.r_[np.diff(ps) != 0, True]
    return pd.DataFrame({
        "threshold": np.r_[np.inf, ps[last]],
        "false_alarm_rate": np.r_[0.0, fp[last] / n_neg],
        "hit_rate": np.r_[0.0, tp[last] / n_pos],
    })


def roc_auc(prob, event) -> float:
    """AUC by the Mann-Whitney identity, with ties at half credit.

    Equivalent to the trapezoidal area under `roc_curve` (asserted in the
    checks) but computed from ranks, so it is exact rather than accumulated.
    """
    p, e = _pair(prob, event)
    pos, neg = p[e == 1], p[e == 0]
    if len(pos) == 0 or len(neg) == 0:
        return np.nan
    ranks = pd.Series(p).rank(method="average").values
    r_pos = ranks[e == 1].sum()
    n1, n0 = len(pos), len(neg)
    return float((r_pos - n1 * (n1 + 1) / 2) / (n1 * n0))


# ---------------------------------------------------------------------------
# Task 22: sharpness
# ---------------------------------------------------------------------------
def sharpness(prob, n_bins: int = 20) -> tuple[pd.DataFrame, dict]:
    """How often each probability is issued, and two summaries of it.

    Sharpness is a property of the forecast alone - no observation enters -
    which is why it must be read next to reliability and never instead of it: a
    forecast that says 0% or 100% every day is maximally sharp and may be
    maximally wrong. `sharpness_score` = mean p(1-p) is the usual summary and
    is LOWER for sharper forecasts; a forecast issuing only the base rate
    scores s(1-s), a forecast issuing only 0 and 1 scores 0.
    """
    p = np.asarray(prob, dtype=float)
    if not np.isfinite(p).all():
        raise ValueError("sharpness: non-finite probabilities")
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, n_bins - 1)
    counts = np.bincount(idx, minlength=n_bins)
    hist = pd.DataFrame({
        "lo": edges[:-1], "hi": edges[1:],
        "mid": (edges[:-1] + edges[1:]) / 2,
        "n": counts, "share": counts / len(p),
    })
    summary = {
        "sharpness_score": float(np.mean(p * (1 - p))),
        "prob_variance": float(np.var(p)),
        "prob_sd": float(np.std(p)),
        "mean_prob": float(np.mean(p)),
        # What fraction of days does the forecast commit to a near-certain
        # answer? A decision-maker feels this more than any variance.
        "share_confident": float(np.mean((p <= 0.05) | (p >= 0.95))),
        "share_hedged": float(np.mean((p > 0.2) & (p < 0.8))),
        "n_distinct": int(len(np.unique(p))),
    }
    return hist, summary


# ---------------------------------------------------------------------------
# Correctness checks
# ---------------------------------------------------------------------------
def _synthetic_calibrated(n: int, seed: int = 11) -> tuple[np.ndarray, np.ndarray]:
    """A genuinely calibrated forecast: outcomes drawn from the stated odds."""
    rng = np.random.default_rng(seed)
    p = rng.beta(1.2, 2.4, size=n)
    e = (rng.random(n) < p).astype(float)
    return p, e


def run_checks(verbose: bool = True) -> None:
    """Every metric against a case whose answer is known before we compute it.

    These are assertions, not prose: if the economic value of a climatological
    forecast ever stops being zero, the run stops.
    """
    def say(msg):
        if verbose:
            print(f"  ok  {msg}")

    rng = np.random.default_rng(7)
    n = 4000
    e = (rng.random(n) < 0.3).astype(float)
    s = e.mean()

    # --- Task 23: the three analytically known economic-value cases ---------
    perfect = value_curve(e, e)
    assert np.allclose(perfect.v_envelope, 1.0), "perfect forecast must have V = 1"
    assert np.allclose(perfect.v_calibrated, 1.0), "perfect forecast, acting rule"
    say("economic value of a perfect forecast is 1 at every cost-loss ratio")

    clim = value_curve(np.full(n, s), e)
    assert np.allclose(clim.v_envelope, 0.0, atol=1e-12), "climatology must have V = 0"
    assert np.allclose(clim.v_calibrated, 0.0, atol=1e-12), "climatology, acting rule"
    say("economic value of a constant-climatology forecast is 0 everywhere")

    rand = value_curve(rng.random(n), e)
    # A random forecast carries no information, so its envelope can only beat
    # climatology by the hindsight in picking the trigger - a vanishing amount
    # at this sample size - and the honest acting rule cannot beat it at all.
    assert rand.v_envelope.max() < 0.05, f"random forecast V={rand.v_envelope.max():.3f}"
    assert rand.v_calibrated.max() < 0.05, "random forecast, acting rule"
    say("economic value of a random forecast is ~0")

    p_cal, e_cal = _synthetic_calibrated(20000)
    cal = value_curve(p_cal, e_cal)
    assert (cal.v_envelope >= cal.v_calibrated - 1e-12).all(), \
        "the envelope is a maximum over triggers and cannot fall below any one"
    gap = float((cal.v_envelope - cal.v_calibrated).max())
    # For a calibrated forecast the optimal trigger IS the cost-loss ratio, so
    # the two curves must coincide up to sampling noise. If this ever fails
    # loudly, either the value formula or the calibration test data is wrong.
    assert gap < 0.05, f"calibrated forecast: envelope-vs-rule gap {gap:.3f}"
    assert cal.v_envelope.max() <= 1.0 + 1e-12, "V cannot exceed 1"
    say(f"for a calibrated forecast the acting rule attains the envelope "
        f"(max gap {gap:.3f})")

    # --- Task 22: CRPS ------------------------------------------------------
    x = rng.normal(0.0, 1.0, size=(1, 40000))
    y = np.array([0.5])
    from math import erf, exp, pi, sqrt
    z = 0.5
    cdf = 0.5 * (1 + erf(z / sqrt(2)))
    pdf = exp(-z * z / 2) / sqrt(2 * pi)
    analytic = z * (2 * cdf - 1) + 2 * pdf - 1 / sqrt(pi)
    got = float(crps_ensemble(x, y)[0])
    assert abs(got - analytic) < 0.01, f"Gaussian CRPS {got:.4f} vs {analytic:.4f}"
    say(f"CRPS of a Gaussian sample matches the closed form ({got:.4f} vs "
        f"{analytic:.4f})")

    pt = np.array([[2.0], [5.0]])
    assert np.allclose(crps_ensemble(pt, [1.0, 1.0]),
                       crps_deterministic([2.0, 5.0], [1.0, 1.0])), \
        "CRPS of a one-member ensemble must be the absolute error"
    say("CRPS of a point forecast is its absolute error")

    p_b, e_b = _synthetic_calibrated(2000, seed=3)
    assert np.allclose(crps_binary(p_b, e_b).mean(),
                       np.mean((p_b - e_b) ** 2)), \
        "binary CRPS must equal the Brier score"
    say("CRPS of a binary-event probability equals its Brier score")

    # The threshold-integral route must agree with the energy form.
    members = rng.normal(1.0, 1.0, size=(200, 300))
    obs = rng.normal(1.0, 1.0, size=200)
    grid = np.linspace(-6, 8, 1400)
    exc = (members[:, :, None] > grid[None, None, :]).mean(axis=1)
    a = crps_ensemble(members, obs).mean()
    b = crps_from_threshold_probs(grid, exc, obs).mean()
    assert abs(a - b) < 0.01, f"CRPS routes disagree: {a:.4f} vs {b:.4f}"
    say(f"CRPS as an integral of Brier scores agrees with the energy form "
        f"({a:.4f} vs {b:.4f})")

    # --- Task 22: ROC / AUC -------------------------------------------------
    auc_rand = roc_auc(rng.random(n), e)
    assert abs(auc_rand - 0.5) < 0.03, f"random AUC {auc_rand:.3f}"
    say(f"AUC of a random forecast is ~0.5 ({auc_rand:.3f})")
    assert roc_auc(e, e) == 1.0, "perfect forecast AUC"
    assert roc_auc(1 - e, e) == 0.0, "inverted forecast AUC"
    say("AUC of a perfect forecast is 1 and of an inverted one is 0")

    curve = roc_curve(p_cal, e_cal)
    area = float(np.trapezoid(curve.hit_rate, curve.false_alarm_rate))
    assert abs(area - roc_auc(p_cal, e_cal)) < 1e-9, "curve area vs rank AUC"
    say("the area under the plotted ROC curve equals the rank-based AUC")

    # Discrimination must be blind to calibration: squash the probabilities
    # into a badly biased but strictly monotone relabelling and the AUC cannot
    # move. This is the property that makes AUC worth reporting beside
    # reliability rather than instead of it.
    squashed = 0.5 * p_cal ** 3 + 0.01
    assert abs(roc_auc(squashed, e_cal) - roc_auc(p_cal, e_cal)) < 1e-12, \
        "AUC must be invariant to monotone recalibration"
    assert np.mean((squashed - e_cal) ** 2) > np.mean((p_cal - e_cal) ** 2), \
        "the squashed forecast should be worse by Brier while equal by AUC"
    say("AUC is unchanged by a monotone miscalibration that worsens Brier")

    # --- Task 22: sharpness -------------------------------------------------
    _, flat = sharpness(np.full(n, s))
    assert flat["prob_variance"] < 1e-24, "a constant forecast has no sharpness"
    assert abs(flat["sharpness_score"] - s * (1 - s)) < 1e-12, \
        "a climatological forecast scores s(1-s)"
    _, sharp = sharpness(e)
    assert sharp["sharpness_score"] == 0.0, "a 0/1 forecast is maximally sharp"
    assert sharp["share_confident"] == 1.0, "a 0/1 forecast commits every day"
    assert abs(sharp["prob_variance"] - s * (1 - s)) < 1e-9, \
        "a 0/1 forecast has the maximal attainable forecast variance"
    hist, _ = sharpness(p_cal)
    assert abs(hist.share.sum() - 1.0) < 1e-12, "sharpness histogram must sum to 1"
    say("sharpness is 0 for a constant forecast and maximal for a 0/1 forecast")


if __name__ == "__main__":
    print("=== metrics.py correctness checks (Tasks 22-23) ===")
    run_checks()
    print("all checks passed")
