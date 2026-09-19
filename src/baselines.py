"""Reference forecasts: persistence and smoothed day-of-year climatology (Task 24).

The study has so far benchmarked everything against a FLAT sample base rate -
the rain frequency over the whole verification window. That is the weakest
reference in the book, and it is weak in a way that matters here: with an
unequal number of summers and winters in a 25-month window, the sample base
rate is not the climatological base rate, and the sign of the error depends on
the climate regime (a Mediterranean window over-weighted toward spring raises
the reference; a monsoon window over-weighted outside the wet season lowers
it). Since this study's central axis is comparison ACROSS regimes, that bias
runs along exactly the axis being measured.

Two references are built here, both from observations already on disk:

  PERSISTENCE       "tomorrow is like today". Two forms, and the choice is
                    reported rather than hidden - see `persistence_binary` and
                    `persistence_frequency`.
  DOY CLIMATOLOGY   the seasonally varying rain frequency for this station,
                    estimated with a circular day-of-year kernel.

Every estimate is LEAVE-ONE-OUT: the day being forecast never contributes to
its own reference. Without that, the climatology is partly a forecast of
itself, which flatters the reference and deflates every skill score computed
against it - and with a record this short the effect is not small.

Nothing here plots or reads the disk. `python src/baselines.py` runs the
correctness checks.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import CLIM_PSEUDO_COUNT, CLIM_WINDOW_DAYS, PERSISTENCE_WINDOW_DAYS

# Day-of-year distances are computed modulo this. 366 rather than 365 so that
# leap-year day numbering does not fold 31 December onto 1 January twice.
DOY_PERIOD = 366

# Minimum effective number of other-year observations behind any single
# day-of-year estimate. Below this the "climatology" is one or two remembered
# days, and a skill score against it means nothing - so it fails rather than
# returning a plausible-looking number.
MIN_CLIM_SUPPORT = 5.0

# Share of days allowed to fall below that support before the whole series is
# rejected. One gauge outage is a handful of dropped days; a record where most
# days are unsupported is not a climatology at all.
MAX_UNSUPPORTED_SHARE = 0.10


def _dates(dates) -> pd.DatetimeIndex:
    d = pd.DatetimeIndex(pd.to_datetime(pd.Series(np.asarray(dates)))).normalize()
    if d.hasnans:
        raise ValueError("baselines: unparseable dates")
    if d.duplicated().any():
        raise ValueError("baselines: duplicate dates in a single series")
    return d


def _events(event) -> np.ndarray:
    e = np.asarray(event, dtype=float)
    if not np.isfinite(e).all():
        raise ValueError("baselines: non-finite event values")
    if not np.isin(e, (0.0, 1.0)).all():
        raise ValueError("baselines: event must be binary 0/1")
    return e


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def persistence_binary(dates, event) -> np.ndarray:
    """Yesterday's observed wet/dry state, issued as a probability of 0 or 1.

    The classical persistence reference. It is deliberately the harsher of the
    two forms to score with Brier - a 0/1 forecast pays the full penalty on
    every day it gets wrong - which is why the frequency form below is reported
    beside it rather than instead of it.

    NaN where yesterday was not observed, so a gap in the gauge record becomes
    a dropped day rather than a silently invented dry one.
    """
    d = _dates(dates)
    e = _events(event)
    s = pd.Series(e, index=d)
    return s.reindex(d - pd.Timedelta(days=1)).to_numpy(dtype=float)


def persistence_frequency(dates, event, window: int = PERSISTENCE_WINDOW_DAYS,
                          min_obs: int | None = None) -> np.ndarray:
    """Fraction of the preceding `window` observed days that were wet.

    WHY BOTH FORMS ARE IMPLEMENTED. "Persistence" for a binary event is
    ambiguous, and the two readings answer different questions. The binary form
    is the textbook reference and is what a reviewer will expect; the frequency
    form is what a user without a forecast would actually do - judge from the
    recent run of weather - and is a genuinely harder reference to beat because
    it is a probability rather than a commitment. Both are reported; neither is
    presented as the definition of persistence.

    Strictly trailing: the day being forecast is never in its own window.
    """
    d = _dates(dates)
    e = _events(event)
    min_obs = min_obs if min_obs is not None else max(5, window // 3)

    # Reindex onto a gapless calendar first: a rolling window counted in ROWS
    # would silently stretch across a month-long gauge outage and call it 30
    # days of recent weather.
    full = pd.date_range(d.min(), d.max(), freq="D")
    s = pd.Series(e, index=d).reindex(full)
    roll = s.rolling(f"{window}D", min_periods=min_obs).mean().shift(1)
    return roll.reindex(d).to_numpy(dtype=float)


# ---------------------------------------------------------------------------
# Smoothed day-of-year climatology
# ---------------------------------------------------------------------------
def _tricube_weights(window_days: int) -> np.ndarray:
    """Kernel weight by circular day-of-year distance, DOY_PERIOD x DOY_PERIOD.

    Tricube rather than a flat window: a box kernel gives a day 15 days away
    the same say as the day itself and then drops it to nothing, which puts
    step discontinuities into the seasonal cycle at every window edge. Weight 1
    at zero distance is relied upon by the leave-one-out correction below.
    """
    k = np.arange(DOY_PERIOD)
    dist = np.abs(k[:, None] - k[None, :])
    dist = np.minimum(dist, DOY_PERIOD - dist)
    u = dist / float(window_days + 1)
    return np.where(u < 1.0, (1.0 - u ** 3) ** 3, 0.0)


def _near_in_time(d: pd.DatetimeIndex, exclude_days: int) -> list[np.ndarray]:
    """Indices of observations within `exclude_days` calendar days of each day.

    These are the observations a day-of-year climatology must NOT use, and the
    reason is specific to a short record. With only two years on disk, a window
    of +/-15 days around a day of year contains that day's own synoptic
    neighbours from its own year - days that are strongly autocorrelated with
    it, and half of which lie in its future. Including them turns the
    "climatology" into a two-sided persistence forecast and makes the reference
    unbeatable for reasons that have nothing to do with the seasonal cycle.
    Dropping them leaves only other years, which is what a climatology means.
    """
    order = np.argsort(d.to_numpy())
    sorted_days = d.to_numpy()[order]
    span = np.timedelta64(exclude_days, "D")
    lo = np.searchsorted(sorted_days, d.to_numpy() - span, side="left")
    hi = np.searchsorted(sorted_days, d.to_numpy() + span, side="right")
    return [order[a:b] for a, b in zip(lo, hi)]


def doy_climatology(dates, event, window_days: int = CLIM_WINDOW_DAYS,
                    pseudo_count: float = CLIM_PSEUDO_COUNT,
                    exclude_within_days: int | None = None) -> np.ndarray:
    """P(wet) for each day from a smoothed, circular day-of-year fit.

    Returns one probability per input day. The estimate for day i is the
    kernel-weighted rain frequency over every year's observations near that day
    of year, excluding everything within `exclude_within_days` calendar days of
    day i itself (default: the full window half-width, so only other years
    contribute), and shrunk toward the station's own annual frequency by
    `pseudo_count` pseudo-observations.

    The exclusion is what makes this usable as a SKILL REFERENCE rather than
    merely as a description. A reference that has seen the day it scores - or
    the week either side of it - is partly forecasting from the answer, and
    over a 25-month record that leakage is worth several points of skill and
    would be pure artefact. Measured here: without it, the reference improves
    as the window NARROWS, which is the signature of persistence leaking in.
    """
    d = _dates(dates)
    e = _events(event)
    n = len(e)
    if n < 2:
        raise ValueError("doy_climatology: need at least two days")
    excl = window_days if exclude_within_days is None else exclude_within_days

    doy = d.dayofyear.to_numpy() - 1                     # 0-based, 0..365
    counts = np.bincount(doy, minlength=DOY_PERIOD).astype(float)
    wet = np.bincount(doy, weights=e, minlength=DOY_PERIOD)

    w = _tricube_weights(window_days)
    num = (w @ wet)[doy]
    den = (w @ counts)[doy]

    # Remove the temporal neighbourhood of each day, itself included.
    for i, near in enumerate(_near_in_time(d, excl)):
        wi = w[doy[i], doy[near]]
        num[i] -= float((wi * e[near]).sum())
        den[i] -= float(wi.sum())

    support = den.copy()
    base_loo = (e.sum() - e) / (n - 1)
    num = num + pseudo_count * base_loo
    den = den + pseudo_count

    # A single thin day-of-year is a dropped day, not a dead series: gauge
    # outages are routine. A series where thin days are the norm is a dead
    # series, and saying so loudly is better than returning a reference built
    # from two remembered afternoons.
    thin = support < MIN_CLIM_SUPPORT
    if thin.mean() > MAX_UNSUPPORTED_SHARE:
        raise ValueError(
            f"doy_climatology: {thin.mean():.0%} of days have under "
            f"{MIN_CLIM_SUPPORT} effective observations from other years - "
            f"the record is too short or too gappy for a "
            f"+/-{window_days}-day window")
    # `where` rather than a bare divide: with no prior, a thin day can have
    # den == 0, and 0/0 is a NaN that arrives with a warning attached. The day
    # is already condemned by `thin`; it should not also emit noise.
    out = np.divide(num, den, out=np.full(n, np.nan), where=~thin & (den > 0))
    finite = out[np.isfinite(out)]
    if finite.min() < 0 or finite.max() > 1:
        raise ValueError("doy_climatology: produced a non-probability")
    return out


def doy_climatology_members(dates, values, window_days: int = CLIM_WINDOW_DAYS,
                           exclude_within_days: int | None = None
                            ) -> list[np.ndarray]:
    """Climatological SAMPLE of the observed quantity for each day.

    The distributional counterpart of `doy_climatology`, used as the reference
    distribution for CRPS on precipitation amount, and excluding the same
    temporal neighbourhood for the same reason. Returned as a list because the
    member count varies with gauge coverage, and padding it to a rectangle
    would fabricate members.
    """
    d = _dates(dates)
    v = np.asarray(values, dtype=float)
    if not np.isfinite(v).all():
        raise ValueError("doy_climatology_members: non-finite values")
    doy = d.dayofyear.to_numpy() - 1
    excl = window_days if exclude_within_days is None else exclude_within_days

    near = _tricube_weights(window_days) > 0             # boolean neighbourhood
    by_doy = {k: np.flatnonzero(doy == k) for k in range(DOY_PERIOD)}
    pool = {k: np.concatenate([by_doy[j] for j in np.flatnonzero(near[k])] or
                              [np.array([], dtype=int)])
            for k in np.unique(doy)}
    out = [v[np.setdiff1d(pool[doy[i]], drop, assume_unique=False)]
           for i, drop in enumerate(_near_in_time(d, excl))]
    thin = np.mean([len(m) < MIN_CLIM_SUPPORT for m in out])
    if thin > MAX_UNSUPPORTED_SHARE:
        raise ValueError(
            f"doy_climatology_members: {thin:.0%} of days have fewer than "
            f"{MIN_CLIM_SUPPORT:.0f} members from other years - the record is "
            f"too short or too gappy")
    return out


# ---------------------------------------------------------------------------
# Correctness checks
# ---------------------------------------------------------------------------
def run_checks(verbose: bool = True) -> None:
    """Known-answer checks for both baselines."""
    def say(msg):
        if verbose:
            print(f"  ok  {msg}")

    rng = np.random.default_rng(19)

    # --- persistence --------------------------------------------------------
    d = pd.date_range("2024-01-01", periods=200, freq="D")
    e = (rng.random(200) < 0.4).astype(float)
    pb = persistence_binary(d, e)
    assert np.isnan(pb[0]), "the first day has no yesterday"
    assert np.array_equal(pb[1:], e[:-1]), "persistence must reproduce yesterday"
    say("binary persistence is exactly yesterday's outcome, NaN on day one")

    gap = d.delete(range(50, 60))
    pg = persistence_binary(gap, np.delete(e, range(50, 60)))
    assert np.isnan(pg[50]), "the day after a gauge outage has no yesterday"
    say("persistence returns NaN across a gap instead of inventing a dry day")

    pf = persistence_frequency(d, e, window=30)
    assert np.isnan(pf[:9]).all(), "the frequency form needs history first"
    i = 100
    manual = e[i - 30:i].mean()
    assert abs(pf[i] - manual) < 1e-12, f"{pf[i]} vs {manual}"
    assert np.nanmax(pf) <= 1.0 and np.nanmin(pf) >= 0.0
    say("frequency persistence equals the trailing 30-day observed wet fraction")

    # --- day-of-year climatology -------------------------------------------
    # Ten years of a known seasonal cycle: the fit must recover it.
    dd = pd.date_range("2010-01-01", "2019-12-31", freq="D")
    truth = 0.35 + 0.25 * np.sin(2 * np.pi * (dd.dayofyear.to_numpy() - 80) / 365.25)
    ev = (rng.random(len(dd)) < truth).astype(float)
    est = doy_climatology(dd, ev, window_days=15, pseudo_count=0.0)
    mae = float(np.abs(est - truth).mean())
    assert mae < 0.03, f"seasonal cycle not recovered: MAE {mae:.3f}"
    say(f"a known seasonal cycle is recovered to MAE {mae:.3f} over 10 years")

    # The no-leakage property, asserted directly rather than trusted: change a
    # day's own outcome, or any outcome inside the excluded temporal
    # neighbourhood, and its climatology must not move at all.
    for offset, what in ((0, "its own outcome"), (3, "a neighbour three days away"),
                         (-14, "a neighbour a fortnight earlier")):
        flipped = ev.copy()
        flipped[500 + offset] = 1.0 - flipped[500 + offset]
        est2 = doy_climatology(dd, flipped, window_days=15, pseudo_count=0.0)
        assert abs(est2[500] - est[500]) < 1e-12, \
            f"the climatology for a day leaked {what}"
    # ... but an observation from another year, at the same time of year, must.
    flipped = ev.copy()
    flipped[500 + 365] = 1.0 - flipped[500 + 365]
    est3 = doy_climatology(dd, flipped, window_days=15, pseudo_count=0.0)
    assert abs(est3[500] - est[500]) > 1e-9, \
        "the climatology ignored the same day of year in another year"
    say("each day's climatology uses other years only, never its own fortnight")

    # A single year cannot support a climatology under that rule, and saying so
    # is the point: silently returning a persistence forecast dressed as a
    # climatology is exactly the failure this guard exists to prevent.
    one_year = pd.date_range("2024-01-01", "2024-12-31", freq="D")
    try:
        doy_climatology(one_year, (rng.random(366) < 0.3).astype(float))
    except ValueError as exc:
        assert "too short" in str(exc)
    else:
        raise AssertionError("a one-year record must not yield a climatology")
    say("a single-year record is rejected instead of silently becoming persistence")

    # Degenerate records: all-wet and all-dry must not produce probabilities
    # outside [0,1] or NaNs, which is where a naive shrinkage formula breaks.
    short = pd.date_range("2024-04-26", "2026-05-31", freq="D")
    for const in (0.0, 1.0):
        c = doy_climatology(short, np.full(len(short), const))
        assert np.isfinite(c).all() and 0.0 <= c.min() and c.max() <= 1.0
        assert abs(c.mean() - const) < 1e-9, "a constant record has constant climatology"
    say("all-wet and all-dry records give a valid degenerate climatology")

    # A flat reference is the special case of an infinitely wide window with no
    # seasonality, and the shrinkage limit must reproduce it exactly.
    e_s = (rng.random(len(short)) < 0.3).astype(float)
    huge = doy_climatology(short, e_s, pseudo_count=1e9)
    loo_base = (e_s.sum() - e_s) / (len(e_s) - 1)
    assert np.allclose(huge, loo_base, atol=1e-6), \
        "infinite shrinkage must collapse to the (leave-one-out) base rate"
    say("infinite shrinkage collapses the climatology to the flat base rate")

    # Window sensitivity must be monotone in smoothness: a wider window cannot
    # produce a more variable seasonal cycle than a narrower one.
    sd = [float(np.std(doy_climatology(dd, ev, window_days=w, pseudo_count=0.0)))
          for w in (5, 15, 45)]
    assert sd[0] >= sd[1] >= sd[2], f"wider windows must smooth more: {sd}"
    say("widening the day-of-year window monotonically smooths the cycle")

    # --- climatological amount sample --------------------------------------
    amounts = rng.gamma(0.4, 6.0, size=len(short))
    members = doy_climatology_members(short, amounts, window_days=15)
    assert len(members) == len(short)
    assert all(len(m) >= 20 for m in members), "too few climatological members"
    i = 300
    assert not np.any(members[i] == amounts[i]), "a day must not be its own member"
    near = np.abs((short - short[i]).days) <= 15
    assert not np.isin(amounts[near], members[i]).any(), \
        "the amount sample leaked the day's own fortnight"
    say("the climatological amount sample excludes the fortnight it scores")


if __name__ == "__main__":
    print("=== baselines.py correctness checks (Task 24) ===")
    run_checks()
    print("all checks passed")
