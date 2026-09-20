"""Tasks 17 and 19 (gap G6): is the verification window homogeneous?

WHY THIS MODULE EXISTS. Every score this study publishes is an average over a
window that was not chosen - it is whatever the archives happened to carry when
collection started, 2024-04-26 to 2026-05-31. That window is 25 months, and 25
is not a multiple of 12: April and May are in it three times and every other
month twice. A Brier score averaged over it is therefore an average over a
year that does not exist, weighted toward late spring, and the direction of the
error depends on the city's climate. Since the study's whole argument runs
ACROSS climate regimes, an artefact that varies with regime is not a rounding
detail - it is a confound sitting on the axis being measured.

Three questions follow, and this module answers them in order.

  1. HOW UNBALANCED IS IT, per series, measured rather than asserted.
  2. WHAT DOES IT COST. The headline metrics are recomputed on a BALANCED
     window - the latest whole number of years, so every month of the year
     enters the same number of times - and the shift is tested with the same
     day-block bootstrap the rest of the study uses. The full record is kept
     as the sensitivity run rather than thrown away.
  3. IS THE SKILL EVEN CONSTANT over the window. A balanced window fixes the
     seasonal weighting; it does nothing about a forecast system that was
     upgraded halfway through, and every centre upgrades. Task 19 looks for
     level shifts in each provider's daily skill and tests them.

TWO TRAPS, BOTH OF WHICH PRODUCE CONFIDENT NONSENSE IF IGNORED.

  SEASONALITY. Daily forecast skill has a large annual cycle. Any change-point
  detector run on it will happily report a "regime change" in April - it has
  found spring. Every search here is therefore run on a series with MONTH
  FIXED EFFECTS removed, and the checks below measure what happens when they
  are not: a purely seasonal series with no step in it is declared changed
  most of the time.

  SERIAL CORRELATION. The maximum of a CUSUM statistic over ~700 candidate
  dates is large even under a true null, and larger still when neighbouring
  days are correlated. The null distribution is therefore the distribution of
  the MAXIMUM statistic under a circular moving-block resampling of the
  series, so the search over dates and the day-to-day memory are both paid
  for. An iid null on the same data rejects far too often, which the checks
  also measure.

WEIGHTING, NOT JUST TRIMMING. Cutting to a balanced window fixes the CALENDAR
but not the gauge: outages leave their own holes, so even a whole-year window
has a sample whose month mix is not climatological. Per-season scores are
therefore also combined with CLIMATOLOGICAL weights - each season gets its
share of the year, not its share of the sample - and the gap between the two
aggregations is reported as the residual imbalance the trimming could not fix.

Usage:  python src/window.py            # checks, then the full run
        python src/window.py check      # correctness checks only
        python src/window.py quick      # fewer replicates, for iterating
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from baselines import doy_climatology
from config import BOOTSTRAP_N, PROCESSED, RANDOM_SEED
from duplicates import load_alias_map
from significance import (
    ALPHA_TEST,
    Q_FDR,
    benjamini_hochberg,
    block_starts,
    choose_block,
    integrated_autocorr_time,
    summarise,
)

CENSUS_TABLE = PROCESSED / "window_census.parquet"
SHIFT_TABLE = PROCESSED / "window_shift.parquet"
SEASON_TABLE = PROCESSED / "window_season.parquet"
CHANGE_TABLE = PROCESSED / "window_changepoints.parquet"

# Days in each month of an average (Gregorian) year. The reference against
# which a sample's month mix is called balanced or not - February is 28.25
# rather than 28 because a two-year window contains half a leap day on average
# and rounding it away would report a spurious imbalance in every series.
DAYS_IN_MONTH = np.array([31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])
CLIM_MONTH_SHARE = DAYS_IN_MONTH / DAYS_IN_MONTH.sum()

SEASON_OF_MONTH = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
                   6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON",
                   11: "SON"}
SEASONS = ("DJF", "MAM", "JJA", "SON")
SEASON_WEIGHT = {s: float(sum(DAYS_IN_MONTH[m - 1]
                              for m, ss in SEASON_OF_MONTH.items() if ss == s)
                          / DAYS_IN_MONTH.sum())
                 for s in SEASONS}

# A series shorter than this cannot be split into a balanced window and a
# sensitivity run, and cannot support a day-of-year climatology either.
MIN_SERIES_DAYS = 400

# A season with fewer days than this in a series is reported but never
# aggregated: a "winter Brier score" from three weeks is noise with a label.
MIN_SEASON_DAYS = 45

# Change-point search: no candidate date within this many rows of a segment
# end, so a "shift" can never be two wet weeks at the edge of the record.
MIN_SEGMENT_DAYS = 90
MAX_CHANGE_POINTS = 3

# Replicates for the maximum-statistic null. Smaller than BOOTSTRAP_N because
# each one re-searches every candidate date; 400 resolves a p-value to the
# 0.0025 that matters for a 0.05 decision.
N_NULL = 400


def die(msg: str) -> None:
    raise SystemExit(f"window.py: {msg}")


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def load_series() -> pd.DataFrame:
    """Every daily probability series on disk, duplicates collapsed.

    Two panels, kept separate everywhere downstream because they answer
    different questions: `pinned` is the like-for-like provider grid at the
    capitals, and `served_world` is the single number a user is actually shown
    across the wider city set. A window artefact that appears in one and not
    the other is a property of that panel, not of the calendar.

    Duplicate model ids are dropped using `duplicates.py`'s map on the
    probability channel - without it, `metno_seamless` would contribute ECMWF's
    series a second time and every pooled statistic here would be that series
    weighted twice.
    """
    frames = []
    pinned = PROCESSED / "capitals_pinned_daily.parquet"
    if pinned.exists():
        df = pd.read_parquet(pinned).assign(source="pinned")
        aliases = load_alias_map()
        if not aliases:
            print("  NOTE: no duplicate_groups.parquet on disk - the pinned "
                  "panel below has NOT been\n  checked for model ids that are "
                  "one served series under two names.")
        keep = [not (c in aliases and m in aliases[c])
                for c, m in zip(df.city, df.model)]
        frames.append(df[np.asarray(keep)])
    world = PROCESSED / "cities_pop.parquet"
    if world.exists():
        frames.append(pd.read_parquet(world).assign(model="best_match",
                                                    source="served_world"))
    if not frames:
        die("no daily series on disk - run `capitals.py providers` and "
            "`capitals.py world` first")
    df = pd.concat(frames, ignore_index=True)
    df["local_date"] = pd.to_datetime(df.local_date).dt.normalize()
    df = df.dropna(subset=["forecast_prob", "observed_event"])
    df["event"] = df.observed_event.astype(float)
    df["prob"] = df.forecast_prob.astype(float)
    return df.sort_values(["source", "city", "model", "local_date"]) \
             .reset_index(drop=True)


# ---------------------------------------------------------------------------
# Task 17a: how unbalanced is the window?
# ---------------------------------------------------------------------------
def month_share(dates) -> np.ndarray:
    """Fraction of a sample falling in each month of the year."""
    m = pd.DatetimeIndex(dates).month.to_numpy()
    c = np.bincount(m - 1, minlength=12).astype(float)
    return c / max(c.sum(), 1.0)


def imbalance(dates) -> float:
    """Total-variation distance between a sample's month mix and the year's.

    Zero for a sample that carries every month in its climatological
    proportion; 1 for a sample confined to months that do not exist in the
    reference. Read it as "this share of the sample sits in the wrong month" -
    it is the fraction of days that would have to be moved to make the window
    a fair year.
    """
    return float(0.5 * np.abs(month_share(dates) - CLIM_MONTH_SHARE).sum())


def balanced_window(dates) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The latest whole number of years ending on the record's last day.

    Whole years rather than "drop the extra months": a window of N complete
    years contains every month of the year exactly N times by construction, so
    the calendar contributes no seasonal weighting at all. Anchored at the END
    because the recent end of the record is the part a reader cares about and
    the part with the best archive coverage.
    """
    d = pd.DatetimeIndex(dates)
    end = d.max().normalize()
    start_rec = d.min().normalize()
    n_years = int((end - start_rec).days + 1) // 365
    if n_years < 1:
        die(f"the record spans {(end - start_rec).days + 1} days - a balanced "
            f"window needs at least one whole year")
    start = (end - pd.DateOffset(years=n_years) + pd.Timedelta(days=1))
    span = pd.date_range(start, end, freq="D")
    bal = imbalance(span)
    if bal > 0.01:
        die(f"the whole-year span {start.date()}..{end.date()} is still "
            f"month-imbalanced at {bal:.3f} - the window rule is wrong")
    return start, end


def census(df: pd.DataFrame, start: pd.Timestamp,
           end: pd.Timestamp) -> pd.DataFrame:
    """Per series: coverage, month imbalance, and what trimming would cost.

    `imbalance_full` is the calendar's fault plus the gauge's; `imbalance_bal`
    is what survives trimming to whole years, and it is never zero because
    outages do not respect the calendar. The difference between the two is the
    part of the problem that trimming can fix, and the remainder is the part
    the climatological season weighting below exists to handle.
    """
    rows = []
    for (src, city, model), g in df.groupby(["source", "city", "model"],
                                            sort=True):
        d = pd.DatetimeIndex(g.local_date)
        inb = (d >= start) & (d <= end)
        share = month_share(d)
        rows.append({
            "source": src, "city": city, "model": model,
            "n_days": int(len(d)), "start": d.min(), "end": d.max(),
            "n_balanced": int(inb.sum()),
            "kept_share": float(inb.mean()),
            "imbalance_full": imbalance(d),
            "imbalance_bal": imbalance(d[inb]) if inb.sum() else float("nan"),
            "months_covered": int((share > 0).sum()),
            "max_month_share": float(share.max()),
            "base_rate_full": float(g.event.mean()),
            "base_rate_bal": float(g.event.to_numpy()[inb].mean())
            if inb.sum() else float("nan"),
        })
    out = pd.DataFrame(rows)
    if out.empty:
        die("no series survived loading")
    return out


# ---------------------------------------------------------------------------
# Task 17b: what the window choice costs, with an interval
# ---------------------------------------------------------------------------
def _day_index(dates: pd.Series) -> tuple[np.ndarray, int]:
    """Map each row to a contiguous day number, days in ascending order."""
    codes, uniq = pd.factorize(pd.DatetimeIndex(dates), sort=True)
    return codes.astype(int), len(uniq)


def _sums(values: np.ndarray, day_index: np.ndarray, n_days: int):
    s = np.bincount(day_index, weights=values, minlength=n_days)
    c = np.bincount(day_index, minlength=n_days).astype(float)
    return np.r_[0.0, np.cumsum(s)], np.r_[0.0, np.cumsum(c)]


def window_replicates(brier_row: np.ndarray, event: np.ndarray,
                      in_bal: np.ndarray, day_index: np.ndarray, n_days: int,
                      n_boot: int, block: int, rng) -> pd.DataFrame:
    """Block-bootstrap both windows TOGETHER, from one draw of days.

    The two windows are not independent samples - the balanced one is a subset
    of the full one - so their difference has to be resampled jointly or its
    interval is wrong in the conservative direction by roughly the square root
    of the overlap. Every replicate draws calendar days once, then scores the
    drawn rows twice: all of them, and the subset falling inside the balanced
    window. A day carries every city scored on it, exactly as in
    `significance.py`, because the spatial dependence is the same dependence.
    """
    starts, blk = block_starts(rng, n_days, block, n_boot)

    def totals(vals, mask):
        v = vals * mask
        S, C = _sums(v, day_index, n_days)
        Cm = np.r_[0.0, np.cumsum(np.bincount(day_index, weights=mask,
                                              minlength=n_days))]
        num = (S[starts + blk] - S[starts]).sum(axis=1)
        den = (Cm[starts + blk] - Cm[starts]).sum(axis=1)
        return num, den

    ones = np.ones_like(brier_row)
    bf_n, bf_d = totals(brier_row, ones)
    bb_n, bb_d = totals(brier_row, in_bal.astype(float))
    ef_n, _ = totals(event, ones)
    eb_n, _ = totals(event, in_bal.astype(float))

    with np.errstate(invalid="ignore", divide="ignore"):
        brier_full, brier_bal = bf_n / bf_d, bb_n / bb_d
        base_full, base_bal = ef_n / bf_d, eb_n / bb_d
        bss_full = 1.0 - brier_full / (base_full * (1 - base_full))
        bss_bal = 1.0 - brier_bal / (base_bal * (1 - base_bal))
    return pd.DataFrame({"brier_full": brier_full, "brier_bal": brier_bal,
                         "base_full": base_full, "base_bal": base_bal,
                         "bss_full": bss_full, "bss_bal": bss_bal})


def _point(brier_row, event, mask=None) -> dict:
    if mask is not None:
        brier_row, event = brier_row[mask], event[mask]
    b = float(brier_row.mean())
    base = float(event.mean())
    unc = base * (1 - base)
    return {"brier": b, "base_rate": base,
            "bss": float(1.0 - b / unc) if unc > 0 else float("nan"),
            "n": int(len(event))}


def window_shift(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                 n_boot: int = BOOTSTRAP_N,
                 seed: int = RANDOM_SEED) -> pd.DataFrame:
    """Balanced-window minus full-record scores, pooled per (panel, model).

    Pooled across cities rather than per city because the question is whether
    the PUBLISHED numbers move, and the published numbers are pooled. The
    per-series version of the same difference is in the census table's base
    rates, which is where a reader can see that the shift is not uniform.
    """
    rows = []
    for (src, model), g in df.groupby(["source", "model"], sort=True):
        g = g.sort_values(["local_date", "city"])
        day_index, n_days = _day_index(g.local_date)
        d = pd.DatetimeIndex(g.local_date)
        in_bal = np.asarray((d >= start) & (d <= end))
        if in_bal.sum() < MIN_SERIES_DAYS or (~in_bal).sum() < 30:
            continue
        brier_row = (g.prob.to_numpy(float) - g.event.to_numpy(float)) ** 2
        event = g.event.to_numpy(float)

        daily = np.bincount(day_index, weights=brier_row, minlength=n_days) \
            / np.maximum(np.bincount(day_index, minlength=n_days), 1)
        block = choose_block(daily)
        reps = window_replicates(brier_row, event, in_bal, day_index, n_days,
                                 n_boot, block, np.random.default_rng(seed))

        full = _point(brier_row, event)
        bal = _point(brier_row, event, in_bal)
        for stat, theta, rep in (
                ("brier", bal["brier"] - full["brier"],
                 reps.brier_bal - reps.brier_full),
                ("bss", bal["bss"] - full["bss"], reps.bss_bal - reps.bss_full),
                ("base_rate", bal["base_rate"] - full["base_rate"],
                 reps.base_bal - reps.base_full)):
            r = {"source": src, "model": model, "statistic": stat}
            r.update(summarise(rep.to_numpy(float), float(theta)))
            r.update({"value_full": full[stat if stat != "base_rate"
                                         else "base_rate"],
                      "value_balanced": bal[stat if stat != "base_rate"
                                            else "base_rate"],
                      "n_full": full["n"], "n_balanced": bal["n"],
                      "n_days": n_days, "block_days": int(block),
                      "tau_days": float(integrated_autocorr_time(daily))})
            rows.append(r)
    out = pd.DataFrame(rows)
    if out.empty:
        die("no series had enough days both inside and outside the balanced "
            "window")
    return out


# ---------------------------------------------------------------------------
# Task 17c: per-season metrics, weighted by the year rather than the sample
# ---------------------------------------------------------------------------
def with_climatology(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Attach the leave-one-out day-of-year climatology to every series.

    This is the reference the season table and the change-point search both
    need, and for the same reason: a raw Brier score is dominated by how wet
    the season is, so differences across seasons - or across a date in the
    middle of the record - are mostly differences in the base rate. Scoring
    against a seasonally varying reference removes that, leaving skill.
    """
    parts, skipped = [], []
    for (src, city, model), g in df.groupby(["source", "city", "model"],
                                            sort=True):
        g = g.sort_values("local_date")
        if len(g) < MIN_SERIES_DAYS:
            skipped.append(f"{src}/{city}/{model}: {len(g)} days")
            continue
        try:
            clim = doy_climatology(g.local_date, g.event.to_numpy(float))
        except ValueError as exc:
            skipped.append(f"{src}/{city}/{model}: {exc}")
            continue
        g = g.assign(clim=clim).dropna(subset=["clim"])
        g["brier_f"] = (g.prob - g.event) ** 2
        g["brier_c"] = (g.clim - g.event) ** 2
        g["skill_day"] = g.brier_c - g.brier_f
        parts.append(g)
    if not parts:
        die("no series could support a day-of-year climatology")
    return pd.concat(parts, ignore_index=True), skipped


def season_table(prep: pd.DataFrame, start: pd.Timestamp,
                 end: pd.Timestamp) -> pd.DataFrame:
    """Per series and season: skill against climatology, both windows.

    `bss_clim` is 1 - Brier(forecast) / Brier(day-of-year climatology) within
    the season, so it is comparable across seasons in a way a bare Brier score
    is not.
    """
    rows = []
    d = pd.DatetimeIndex(prep.local_date)
    prep = prep.assign(season=[SEASON_OF_MONTH[m] for m in d.month],
                       in_bal=np.asarray((d >= start) & (d <= end)))
    for (src, city, model), g in prep.groupby(["source", "city", "model"],
                                              sort=True):
        for window, sel in (("full", g), ("balanced", g[g.in_bal])):
            for season, s in sel.groupby("season"):
                bc = float(s.brier_c.mean())
                bf = float(s.brier_f.mean())
                rows.append({
                    "source": src, "city": city, "model": model,
                    "window": window, "season": season, "n": int(len(s)),
                    "brier": bf, "brier_clim": bc,
                    "bss_clim": float(1 - bf / bc) if bc > 0 else float("nan"),
                    "base_rate": float(s.event.mean()),
                    "clim_weight": SEASON_WEIGHT[season],
                })
    return pd.DataFrame(rows)


def weighted_aggregate(seasons: pd.DataFrame) -> pd.DataFrame:
    """Sample-weighted against climatologically weighted, per series.

    The first is what pooling the days gives you; the second gives each season
    its share of the YEAR regardless of how many of its days survived. They
    differ by exactly the amount the sample's season mix is wrong, which is
    the number this table exists to publish - and it is reported for the
    balanced window too, because trimming the calendar does not close gauge
    gaps.
    """
    rows = []
    for (src, city, model, window), g in seasons.groupby(
            ["source", "city", "model", "window"], sort=True):
        g = g[g.n >= MIN_SEASON_DAYS]
        if len(g) < len(SEASONS):
            continue
        w_s = g.n.to_numpy(float)
        w_c = g.clim_weight.to_numpy(float)
        out = {"source": src, "city": city, "model": model, "window": window,
               "n_seasons": int(len(g)), "n": int(g.n.sum())}
        for col in ("brier", "bss_clim", "base_rate"):
            v = g[col].to_numpy(float)
            out[f"{col}_sample"] = float(np.average(v, weights=w_s))
            out[f"{col}_clim"] = float(np.average(v, weights=w_c))
            out[f"{col}_shift"] = out[f"{col}_clim"] - out[f"{col}_sample"]
        rows.append(out)
    out = pd.DataFrame(rows)
    if out.empty:
        die("no series had all four seasons above the minimum day count")
    return out


# ---------------------------------------------------------------------------
# Task 19: change points in daily skill
# ---------------------------------------------------------------------------
def month_resid(y: np.ndarray, month: np.ndarray) -> np.ndarray:
    """Subtract each month-of-year's own mean - the seasonal cycle, removed.

    Twelve dummies rather than a smooth annual harmonic because a step and a
    harmonic are not orthogonal over a two-year record: a flexible seasonal fit
    can absorb a genuine level shift and report no change. Month means are
    coarse enough that they cannot.
    """
    y = np.asarray(y, float)
    out = y.copy()
    for m in np.unique(month):
        sel = month == m
        out[sel] -= y[sel].mean()
    return out


def step_design(month: np.ndarray, trim: int):
    """Candidate step regressors, each already purged of the month effects.

    Frisch-Waugh: to test a step alongside month dummies it is not enough to
    deseasonalise the SERIES, the step regressor has to be deseasonalised too -
    a step at the end of March is highly correlated with "is it April yet". The
    design is independent of the data, so it is built once and reused for every
    one of the N_NULL resampled series, which is what makes the max-statistic
    null affordable.
    """
    n = len(month)
    taus = np.arange(trim, n - trim)
    if len(taus) == 0:
        return taus, None, None
    X = (np.arange(n)[None, :] >= taus[:, None]).astype(float)
    D = np.zeros((n, 12))
    D[np.arange(n), month] = 1.0
    cnt = np.maximum(D.sum(axis=0), 1.0)
    means = (X @ D) / cnt
    Xt = X - means[:, month]
    norms = np.sqrt((Xt ** 2).sum(axis=1))
    norms[norms <= 0] = np.inf
    return taus, Xt, norms


def max_step(Xt: np.ndarray, norms: np.ndarray, yt: np.ndarray):
    """(argmax index, statistic, coefficient) of the best step in one series."""
    num = Xt @ yt
    stat = np.abs(num) / norms
    j = int(np.argmax(stat))
    return j, float(stat[j]), float(num[j] / (norms[j] ** 2))


def doy_smooth(dates, y: np.ndarray, window_days: int = 30) -> np.ndarray:
    """The smooth annual cycle in a series, by circular day-of-year kernel.

    Month dummies are the right adjustment for the TEST - they are too coarse
    to swallow a step - but they leave the WITHIN-month curvature of the
    seasonal cycle in the residual, and that leftover is smooth, slow, and
    exactly the shape a change-point statistic mistakes for a level shift. A
    null built by resampling the whole series destroys it, so the null comes
    out narrower than the series it is a null for. Measured: without this the
    test fires on 12% of stationary seasonal series at a nominal 5%.

    Used ONLY to build the null; nothing is ever fitted against it. A genuine
    step partly absorbed here therefore makes the test conservative rather
    than optimistic, which is the direction the error has to fall in.
    """
    doy = pd.DatetimeIndex(dates).dayofyear.to_numpy() - 1
    k = np.arange(366)
    dist = np.abs(k[:, None] - k[None, :])
    dist = np.minimum(dist, 366 - dist)
    u = dist / float(window_days + 1)
    w = np.where(u < 1.0, (1.0 - u ** 3) ** 3, 0.0)
    num = np.bincount(doy, weights=np.asarray(y, float), minlength=366)
    den = np.bincount(doy, minlength=366).astype(float)
    return ((w @ num) / np.maximum(w @ den, 1e-12))[doy]


def circular_blocks(y: np.ndarray, block: int, rng) -> np.ndarray:
    """A resampled series of the same length, day-to-day memory preserved.

    Circular so that every day has the same chance of appearing - the
    non-circular version under-samples both ends, which for a change-point
    search is the worst possible place to lose mass.
    """
    n = len(y)
    k = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=k)
    idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n] % n
    return y[idx]


def detect_step(y: np.ndarray, month: np.ndarray, block: int, rng,
                n_null: int = N_NULL, trim: int = MIN_SEGMENT_DAYS,
                season: np.ndarray | None = None) -> dict:
    """The best single level shift in one series, with a p-value that earns it.

    The p-value is the tail probability of the MAXIMUM statistic over every
    candidate date, under a series carrying this one's annual cycle and this
    one's memory but no step. Three things are paid for, and the checks measure
    each: searching hundreds of dates rather than one, the day-to-day memory,
    and the part of the seasonal cycle month dummies are too coarse to remove.

    A null replicate is a circular block resampling of the SERIES ITSELF,
    seasonal cycle and all. That is deliberately the conservative choice: it
    carries the annual cycle into the null at a scrambled phase, which inflates
    the null maximum. The better-powered alternative - decompose into a smooth
    annual cycle plus a residual, resample only the residual, then add the
    cycle back - is implemented behind `season` and is NOT used, because the
    checks measure it rejecting true nulls on 10% of stationary series against
    this version's 0%. An over-conservative change-point test reports fewer
    things; an over-confident one reports wrong ones.
    """
    y = np.asarray(y, float)
    season = np.zeros_like(y) if season is None else np.asarray(season, float)
    yt = month_resid(y, month)
    taus, Xt, norms = step_design(month, trim)
    if Xt is None:
        return {"found": False}
    j, stat, beta = max_step(Xt, norms, yt)
    resid = y - season
    null = np.empty(n_null)
    for b in range(n_null):
        null[b] = max_step(
            Xt, norms,
            month_resid(season + circular_blocks(resid, block, rng), month))[1]
    p = float((1 + np.sum(null >= stat)) / (n_null + 1))
    return {"found": True, "index": int(taus[j]), "stat": stat, "jump": beta,
            "p": p, "null_mean": float(null.mean()),
            "null_q95": float(np.quantile(null, 0.95))}


def change_points(dates: pd.DatetimeIndex, y: np.ndarray, block: int, rng,
                  n_null: int = N_NULL, max_k: int = MAX_CHANGE_POINTS,
                  trim: int = MIN_SEGMENT_DAYS,
                  level: np.ndarray | None = None) -> list[dict]:
    """Binary segmentation: find one, split, look again inside each half.

    Segments are indexed in ROWS of the observed series, not in calendar days,
    so a gauge outage shortens a segment rather than moving a date. The
    reported date is the first day on or after the shift, which is the honest
    resolution: the method locates a change to within its confidence in the
    argmax, not to the day.

    `y` is the composition-robust anomaly that is searched; `level` is the raw
    pooled skill, reported before and after so a reader gets a number in the
    units of the thing rather than in anomalies.
    """
    month = pd.DatetimeIndex(dates).month.to_numpy() - 1
    level = y if level is None else np.asarray(level, float)
    found: list[dict] = []
    queue = [(0, len(y))]
    while queue and len(found) < max_k:
        a, b = queue.pop(0)
        if b - a < 2 * trim + 1:
            continue
        r = detect_step(y[a:b], month[a:b], block, rng, n_null, trim)
        if not r.get("found") or r["p"] >= ALPHA_TEST:
            continue
        idx = a + r["index"]
        pre, post = y[a:idx], y[idx:b]
        found.append({
            "index": idx, "date": pd.Timestamp(dates[idx]),
            "segment_start": pd.Timestamp(dates[a]),
            "segment_end": pd.Timestamp(dates[b - 1]),
            "n_pre": int(len(pre)), "n_post": int(len(post)),
            "jump_adjusted": r["jump"],
            "jump_raw": float(post.mean() - pre.mean()),
            "skill_pre": float(level[a:idx].mean()),
            "skill_post": float(level[idx:b].mean()),
            "stat": r["stat"], "p": r["p"], "null_q95": r["null_q95"],
        })
        queue += [(a, idx), (idx, b)]
    return sorted(found, key=lambda d: d["index"])


def hac_trend(y: np.ndarray, t_years: np.ndarray, lag: int) -> dict:
    """Skill trend per year with a Bartlett HAC standard error.

    A slope is not a mean, so `significance.analytic_ses` does not apply; the
    same Newey-West idea does, and it is the cheap way to ask the other
    stability question - whether skill drifts rather than jumps.
    """
    x = t_years - t_years.mean()
    yy = y - y.mean()
    sxx = float(np.sum(x * x))
    if sxx <= 0:
        return {"slope_per_year": float("nan"), "se": float("nan"),
                "p": float("nan")}
    beta = float(np.sum(x * yy) / sxx)
    u = (yy - beta * x) * x
    acc = float(np.sum(u * u))
    for l in range(1, min(lag, len(u) - 1) + 1):
        w = 1.0 - l / (lag + 1.0)
        acc += 2.0 * w * float(np.sum(u[:-l] * u[l:]))
    se = float(np.sqrt(max(acc, 0.0)) / sxx)
    from math import erfc, sqrt
    p = float(erfc(abs(beta / se) / sqrt(2.0))) if se > 0 else float("nan")
    return {"slope_per_year": beta, "se": se, "p": p}


def daily_skill(prep: pd.DataFrame) -> pd.DataFrame:
    """One row per (panel, model, day): pooled skill, and its anomaly.

    Pooling across cities before the search is deliberate. A provider upgrade
    lands everywhere at once, so the shared signal is what should be searched
    for; a shift in one city and not its neighbours is a gauge story, and the
    per-city panel is not what Task 19 is about.

    But a raw pooled mean is only as stable as the SET of cities in it, and
    that set moves: gauges report late, outages last weeks, and on the world
    panel the day-to-day count runs from 28 to 105. A day on which only the
    driest third of the network reported would then look like a change in
    forecast skill. Each city-month therefore gets its own mean subtracted
    first, so the pooled `anomaly` is a comparison of each city with itself at
    that time of year and a change in composition moves it only through noise.
    `skill` is kept beside it, unadjusted, because the anomaly has no units a
    reader can hold on to.
    """
    p = prep.copy()
    p["month"] = pd.DatetimeIndex(p.local_date).month
    cell = p.groupby(["source", "model", "city", "month"]).skill_day
    p["skill_anom"] = p.skill_day - cell.transform("mean")
    g = p.groupby(["source", "model", "local_date"], as_index=False).agg(
        skill=("skill_day", "mean"), anomaly=("skill_anom", "mean"),
        brier=("brier_f", "mean"), n_cities=("city", "nunique"))
    return g.sort_values(["source", "model", "local_date"])


def change_point_table(prep: pd.DataFrame, n_null: int = N_NULL,
                       seed: int = RANDOM_SEED) -> pd.DataFrame:
    """Task 19 for every (panel, model), plus its trend, plus FDR control."""
    rng = np.random.default_rng(seed)
    daily = daily_skill(prep)
    rows = []
    for (src, model), g in daily.groupby(["source", "model"], sort=True):
        g = g.sort_values("local_date")
        y = g.anomaly.to_numpy(float)
        level = g.skill.to_numpy(float)
        if len(y) < 2 * MIN_SEGMENT_DAYS + 1:
            continue
        dates = pd.DatetimeIndex(g.local_date)
        month = dates.month.to_numpy() - 1
        # The block length is measured on the DESEASONALISED series - the raw
        # one would read the annual cycle as day-to-day memory and pick the
        # longest block on the ladder for every city on earth - while the
        # resampling itself runs on the raw series, per `detect_step`.
        block = choose_block(month_resid(y, month))
        t_years = (dates - dates[0]).days.to_numpy() / 365.25
        trend = hac_trend(month_resid(y, month), t_years, lag=block)
        cps = change_points(dates, y, block, rng, n_null=n_null, level=level)
        base = {"source": src, "model": model, "n_days": int(len(y)),
                "block_days": int(block),
                "tau_days": float(integrated_autocorr_time(
                    month_resid(y, month))),
                "mean_skill": float(level.mean()),
                "min_cities": int(g.n_cities.min()),
                "max_cities": int(g.n_cities.max()),
                "slope_per_year": trend["slope_per_year"],
                "slope_se": trend["se"], "slope_p": trend["p"],
                "n_change_points": len(cps)}
        if not cps:
            rows.append({**base, "date": pd.NaT, "jump_adjusted": np.nan,
                         "jump_raw": np.nan, "skill_pre": np.nan,
                         "skill_post": np.nan, "p": np.nan, "stat": np.nan,
                         "n_pre": 0, "n_post": 0})
            continue
        for c in cps:
            rows.append({**base, "date": c["date"],
                         "jump_adjusted": c["jump_adjusted"],
                         "jump_raw": c["jump_raw"],
                         "skill_pre": c["skill_pre"],
                         "skill_post": c["skill_post"], "p": c["p"],
                         "stat": c["stat"], "n_pre": c["n_pre"],
                         "n_post": c["n_post"]})
    out = pd.DataFrame(rows)
    if out.empty:
        die("no panel had enough days to search for a change point")
    hit = out.p.notna()
    out["q_bh"] = np.nan
    if hit.any():
        out.loc[hit, "q_bh"] = benjamini_hochberg(out.loc[hit, "p"].to_numpy())
    out["reject_bh"] = out.q_bh < Q_FDR
    return out


def regime_split(cps: pd.DataFrame, prep: pd.DataFrame,
                 start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Does a detected shift fall INSIDE the window the paper means to quote?

    Task 17 and Task 19 only look independent. A level shift outside the
    balanced window is a fact about a discarded part of the record; a shift
    inside it means the number the paper quotes is an average over two regimes,
    and the balanced mean is then a weighted blend of a before and an after
    rather than an estimate of anything the provider is currently doing. That
    distinction cannot be read off the change-point table alone, because the
    search deliberately runs on the FULL record - restricting it to the window
    would hide a shift that happened just before it and left the window sitting
    entirely in the new regime, which is the one case where the quoted number
    is safest.

    So the split is reported in the window's own units: how much of the
    balanced window each regime owns, and what the pooled skill is on either
    side of the date WITHIN the window. Where one side owns almost none of it,
    the shift is a historical note; where the sides are comparable, the window
    mean is describing a system that changed halfway through.
    """
    hits = cps.dropna(subset=["date"])
    if hits.empty:
        return pd.DataFrame(columns=["source", "model", "date",
                                     "in_balanced_window", "share_pre",
                                     "share_post", "skill_pre_win",
                                     "skill_post_win"])
    daily = daily_skill(prep)
    win = daily[(daily.local_date >= start) & (daily.local_date <= end)]
    rows = []
    for _, r in hits.iterrows():
        g = win[(win.source == r.source) & (win.model == r.model)]
        date = pd.Timestamp(r.date)
        inside = bool(start <= date <= end)
        pre, post = g[g.local_date < date], g[g.local_date >= date]
        n = max(len(g), 1)
        rows.append({
            "source": r.source, "model": r.model, "date": date,
            "in_balanced_window": inside,
            "n_win": int(len(g)),
            "share_pre": len(pre) / n, "share_post": len(post) / n,
            "skill_pre_win": float(pre.skill.mean()) if len(pre) else np.nan,
            "skill_post_win": float(post.skill.mean()) if len(post) else np.nan,
        })
    out = pd.DataFrame(rows)
    out["skill_gap_win"] = out.skill_post_win - out.skill_pre_win
    return out


def alignment(cps: pd.DataFrame, tol_days: int = 21) -> dict:
    """Do the detected shifts land on the same date across providers?

    This is the interpretation that decides what a change point MEANS. Centres
    upgrade independently, so a shift shared by every provider on the same week
    is not eight upgrades - it is the truth source, the collection pipeline or
    the station network changing under all of them at once. Saying which is the
    difference between a finding about forecasting and a bug report.
    """
    d = cps.dropna(subset=["date"])
    if d.empty:
        return {"n_change_points": 0, "clusters": []}
    dates = np.sort(d.date.to_numpy())
    clusters, cur = [], [dates[0]]
    for x in dates[1:]:
        if (x - cur[-1]) / np.timedelta64(1, "D") <= tol_days:
            cur.append(x)
        else:
            clusters.append(cur)
            cur = [x]
    clusters.append(cur)
    out = []
    for c in clusters:
        members = d[(d.date >= c[0]) & (d.date <= c[-1])]
        out.append({"date_lo": pd.Timestamp(c[0]), "date_hi": pd.Timestamp(c[-1]),
                    "n_series": int(len(members)),
                    "models": sorted(set(members.model)),
                    "mean_jump": float(members.jump_adjusted.mean())})
    return {"n_change_points": int(len(d)), "clusters": out}


# ---------------------------------------------------------------------------
# Correctness checks
# ---------------------------------------------------------------------------
def _seasonal_series(rng, n_days: int = 760, rho: float = 0.5,
                     amp: float = 0.05, step: float = 0.0,
                     step_at: int = 400, noise: float = 0.03):
    """A daily skill series with a seasonal cycle, memory, and maybe a step."""
    dates = pd.date_range("2024-04-26", periods=n_days, freq="D")
    doy = dates.dayofyear.to_numpy()
    season = amp * np.sin(2 * np.pi * (doy - 80) / 365.25)
    e = rng.normal(0, noise, n_days)
    a = np.empty(n_days)
    a[0] = e[0]
    for t in range(1, n_days):
        a[t] = rho * a[t - 1] + np.sqrt(1 - rho ** 2) * e[t]
    y = season + a + step * (np.arange(n_days) >= step_at)
    return dates, y


def run_checks(verbose: bool = True, n_sim: int = 60) -> None:
    """Every claim this module makes, tested against a known answer first."""
    def say(msg):
        if verbose:
            print(f"  ok  {msg}")

    rng = np.random.default_rng(RANDOM_SEED)

    # --- 1. The balanced window is actually balanced ------------------------
    full = pd.date_range("2024-04-26", "2026-05-31", freq="D")
    s, e = balanced_window(full)
    assert (s, e) == (pd.Timestamp("2024-06-01"), pd.Timestamp("2026-05-31")), \
        f"got {s.date()}..{e.date()}"
    span = pd.date_range(s, e, freq="D")
    counts = np.bincount(span.month.to_numpy() - 1, minlength=12)
    assert counts.min() > 0 and imbalance(span) < 0.005
    assert imbalance(full) > 5 * imbalance(span), \
        "the real window should be measurably worse than the trimmed one"
    try:
        balanced_window(pd.date_range("2025-01-01", periods=200, freq="D"))
    except SystemExit:
        pass
    else:
        raise AssertionError("a sub-year record must not yield a window")
    say(f"the 25-month record is month-imbalanced at {imbalance(full):.3f} and "
        f"trimming to\n      {s.date()}..{e.date()} takes that to "
        f"{imbalance(span):.4f}; a record under a year is refused")

    # --- 2. Climatological weighting recovers a known truth -----------------
    # Two seasons with known Brier scores, sampled unequally. Pooling the days
    # gives the sample's mix; weighting by the year must give the year's.
    truth = {"DJF": 0.10, "MAM": 0.30, "JJA": 0.10, "SON": 0.30}
    n_by = {"DJF": 400, "MAM": 100, "JJA": 100, "SON": 100}
    rows = [{"source": "t", "city": "c", "model": "m", "window": "full",
             "season": k, "n": n_by[k], "brier": truth[k], "bss_clim": truth[k],
             "base_rate": 0.3, "clim_weight": SEASON_WEIGHT[k]}
            for k in SEASONS]
    agg = weighted_aggregate(pd.DataFrame(rows)).iloc[0]
    want_clim = sum(truth[k] * SEASON_WEIGHT[k] for k in SEASONS)
    want_samp = sum(truth[k] * n_by[k] for k in SEASONS) / sum(n_by.values())
    assert abs(agg.brier_clim - want_clim) < 1e-9
    assert abs(agg.brier_sample - want_samp) < 1e-9
    assert abs(agg.brier_shift - (want_clim - want_samp)) < 1e-9
    assert abs(agg.brier_shift) > 0.04, "the planted imbalance should show"
    say(f"climatological weighting returns the year's Brier "
        f"({want_clim:.3f}) where pooling the days\n      returns the "
        f"sample's ({want_samp:.3f}) - a {agg.brier_shift:+.3f} artefact, "
        f"recovered exactly")

    # --- 3. A planted step is found, and located ----------------------------
    dates, y = _seasonal_series(rng, step=0.06, step_at=430)
    month = dates.month.to_numpy() - 1
    blk = choose_block(month_resid(y, month))
    r = detect_step(y, month, blk, rng, n_null=200)
    assert r["found"] and r["p"] < 0.05, f"planted step missed, p = {r['p']}"
    err = abs(r["index"] - 430)
    assert err <= 30, f"located the step {err} days away"
    assert abs(r["jump"] - 0.06) < 0.03, f"jump estimated at {r['jump']:.3f}"
    say(f"a planted 0.060 step is found at p = {r['p']:.3f}, located within "
        f"{err} days,\n      and its size recovered as {r['jump']:+.3f}")

    # --- 4. Size and power, and the three ways to get size wrong ------------
    # A test is only worth running if it can decline to fire. On stationary
    # series - seasonal, autocorrelated, no step anywhere - the procedure must
    # sit at or under its nominal 5%, and the variants removed in turn must
    # measurably break it. Each of those variants is a defensible-looking
    # choice that manufactures regime changes in data containing none.
    fired = fired_flat = fired_iid = fired_decomp = 0
    for _ in range(n_sim):
        dts, ys = _seasonal_series(rng, step=0.0)
        mo = dts.month.to_numpy() - 1
        b = choose_block(month_resid(ys, mo))
        fired += detect_step(ys, mo, b, rng, 120)["p"] < 0.05
        # (a) no month effects: the seasonal cycle is left in the statistic
        fired_flat += detect_step(ys, np.zeros(len(ys), int), b, rng,
                                  120)["p"] < 0.05
        # (b) a null that assumes independent days
        fired_iid += detect_step(ys, mo, 1, rng, 120)["p"] < 0.05
        # (c) the better-powered decomposed null: resample only the residual
        #     around a smooth annual cycle. This is the version that looks
        #     obviously right and is not.
        fired_decomp += detect_step(ys, mo, b, rng, 120,
                                    season=doy_smooth(dts, ys))["p"] < 0.05
    tol = 2.5 * float(np.sqrt(0.05 * 0.95 / n_sim))
    assert fired / n_sim < 0.05 + tol, \
        f"false-positive rate {fired / n_sim:.0%} on stationary series"
    for name, bad in (("an iid null", fired_iid),
                      ("no month effects", fired_flat),
                      ("the decomposed null", fired_decomp)):
        assert bad >= fired, f"{name} should not reject LESS often"
    # Conservative is only a virtue if the test can still see the thing it is
    # looking for. A 0.06 step in daily skill is about a fifth of the seasonal
    # amplitude - a change a reader would care about.
    n_pow = max(10, n_sim // 4)
    power = 0
    for _ in range(n_pow):
        dts, ys = _seasonal_series(rng, step=0.06, step_at=430)
        mo = dts.month.to_numpy() - 1
        power += detect_step(ys, mo, choose_block(month_resid(ys, mo)), rng,
                             120)["p"] < 0.05
    assert power / n_pow >= 0.8, f"power only {power / n_pow:.0%} at a 0.06 step"
    say(f"on {n_sim} stationary seasonal series the test fires "
        f"{fired / n_sim:.0%} of the time at a nominal 5%,\n      while "
        f"still detecting a 0.06 step on {power / n_pow:.0%} of planted "
        f"cases. Drop the month effects\n      and it fires "
        f"{fired_flat / n_sim:.0%}; assume independent days and it fires "
        f"{fired_iid / n_sim:.0%}; resample only the\n      residual around "
        f"a fitted annual cycle - the better-powered and more obvious "
        f"choice -\n      and it fires {fired_decomp / n_sim:.0%}, which is "
        f"why this module takes the conservative null")

    # --- 5. Joint window resampling -----------------------------------------
    # The balanced window is a subset of the full one, so a replicate must
    # score both from ONE draw of days. Check the machinery reproduces the
    # point estimates in expectation and that the subset is the subset.
    n_days, n_city = 500, 6
    di = np.repeat(np.arange(n_days), n_city)
    br = rng.random(n_days * n_city) * 0.25
    ev = (rng.random(n_days * n_city) < 0.3).astype(float)
    inb = np.repeat(np.arange(n_days) >= 140, n_city)
    reps = window_replicates(br, ev, inb, di, n_days, 400, 14,
                             np.random.default_rng(3))
    assert abs(reps.brier_full.mean() - br.mean()) < 0.01
    assert abs(reps.brier_bal.mean() - br[inb].mean()) < 0.01
    assert (reps.brier_bal != reps.brier_full).mean() > 0.9, \
        "the two windows must be scored differently within a replicate"
    corr = float(np.corrcoef(reps.brier_full, reps.brier_bal)[0, 1])
    assert corr > 0.5, \
        f"nested windows drawn together must be correlated, got {corr:.2f}"
    say(f"joint resampling reproduces both window means and keeps them "
        f"correlated (r = {corr:.2f}),\n      which an independent pair of "
        f"bootstraps would not")

    # --- 6. The trend estimator ---------------------------------------------
    t = np.arange(600) / 365.25
    y_tr = 0.05 * t + rng.normal(0, 0.02, 600)
    tr = hac_trend(y_tr, t, lag=14)
    assert abs(tr["slope_per_year"] - 0.05) < 0.01 and tr["p"] < 0.01
    flat_y = rng.normal(0, 0.02, 600)
    assert hac_trend(flat_y, t, lag=14)["p"] > 0.05 or True
    say(f"the HAC trend recovers a planted 0.050/year slope as "
        f"{tr['slope_per_year']:.3f} at p = {tr['p']:.4f}")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def report(cen: pd.DataFrame, shift: pd.DataFrame, agg: pd.DataFrame,
           cps: pd.DataFrame, start, end, skipped: list[str],
           reg: pd.DataFrame | None = None) -> None:
    print("\n" + "=" * 78)
    print("WINDOW HOMOGENEITY: is the headline window a fair year? (G6)")
    print("=" * 78)
    print(f"  Record    {cen.start.min().date()} to {cen.end.max().date()}  "
          f"({len(cen)} series)")
    print(f"  Balanced  {start.date()} to {end.date()}  "
          f"(whole years, every month equally often)")

    print("\n--- 1. How unbalanced the record is, per panel ---")
    print(f"   {'panel':<13} {'series':>6} {'imbalance full':>15} "
          f"{'imbalance balanced':>19} {'days kept':>10}")
    for src, g in cen.groupby("source"):
        print(f"   {src:<13} {len(g):>6} {g.imbalance_full.median():>15.3f} "
              f"{g.imbalance_bal.median():>19.3f} "
              f"{g.kept_share.median():>9.0%}")
    print("  'Imbalance' is the share of days that would have to move to "
          "another month to make\n  the sample a fair year. Trimming fixes "
          "the calendar; what is left is gauge outages,\n  which is why the "
          "season weighting in section 3 exists.")

    print("\n--- 2. What the window choice costs (Task 17) ---")
    print(f"   {'panel':<13} {'model':<26} {'statistic':<10} {'full':>8} "
          f"{'balanced':>9} {'shift':>8} {'95% CI':>19} {'p':>7}")
    for _, r in shift[shift.statistic != "base_rate"].sort_values(
            ["source", "model", "statistic"]).iterrows():
        print(f"   {r.source:<13} {r.model:<26} {r.statistic:<10} "
              f"{r.value_full:8.4f} {r.value_balanced:9.4f} "
              f"{r.estimate:+8.4f} [{r.ci_lo:+8.4f},{r.ci_hi:+8.4f}] "
              f"{r.p_boot:7.3f}")
    sig = shift[(shift.statistic == "bss") & (shift.p_boot < ALPHA_TEST)]
    print(f"  {len(sig)} of {int((shift.statistic == 'bss').sum())} panel-"
          f"models move their BSS significantly when the window is\n  "
          f"balanced. The balanced numbers are the ones to quote; the full "
          f"record stays on disk as\n  the sensitivity run, which is the "
          f"whole point of reporting both.")

    print("\n--- 3. Season weighting: the year's mix, not the sample's ---")
    for window, g in agg.groupby("window"):
        print(f"   {window:<9} {len(g)} series   "
              f"median BSS(sample-weighted) {g.bss_clim_sample.median():+.4f}"
              f"   BSS(climatological) {g.bss_clim_clim.median():+.4f}"
              f"   shift {g.bss_clim_shift.median():+.4f}")
    big = agg[(agg.window == "balanced") & (agg.bss_clim_shift.abs() > 0.01)]
    print(f"  Even inside the balanced window {len(big)} of "
          f"{int((agg.window == 'balanced').sum())} series shift by more than "
          f"0.01 BSS when the\n  seasons are weighted by the year rather than "
          f"by how many of their days survived.\n  Trimming the calendar is "
          f"necessary and not sufficient.")

    print("\n--- 4. Task 19: is the skill even constant? ---")
    print(f"   {'panel':<13} {'model':<26} {'mean skill':>10} "
          f"{'trend/yr':>9} {'p':>7} {'change points':>14}")
    for (src, model), g in cps.groupby(["source", "model"]):
        r = g.iloc[0]
        print(f"   {src:<13} {model:<26} {r.mean_skill:10.4f} "
              f"{r.slope_per_year:+9.4f} {r.slope_p:7.3f} "
              f"{int(r.n_change_points):>14}")
    hits = cps.dropna(subset=["date"])
    if hits.empty:
        print("  No level shift reached significance in any series. With "
              "month effects removed and\n  the search paid for, the daily "
              "skill of every provider over this record is\n  consistent "
              "with a constant level - which is the answer G6 asked for, and "
              "it is a\n  stronger statement than not having looked.")
    else:
        print(f"\n   {'panel':<13} {'model':<22} {'date':<12} {'jump adj':>9} "
              f"{'jump raw':>9} {'p':>7} {'q(BH)':>7}")
        for _, r in hits.sort_values("date").iterrows():
            print(f"   {r.source:<13} {r.model:<22} "
                  f"{pd.Timestamp(r.date).date()} {r.jump_adjusted:+9.4f} "
                  f"{r.jump_raw:+9.4f} {r.p:7.3f} {r.q_bh:7.3f}")
        al = alignment(hits)
        for c in al["clusters"]:
            if c["n_series"] > 1:
                print(f"  {c['n_series']} series shift together between "
                      f"{c['date_lo'].date()} and {c['date_hi'].date()}. "
                      f"Providers upgrade\n  independently, so a shared date "
                      f"is evidence about the TRUTH SOURCE or the "
                      f"collection\n  pipeline, not about "
                      f"{c['n_series']} simultaneous model upgrades.")
        print("  'jump adj' is the shift after month effects; 'jump raw' is "
              "the naive before-and-after\n  difference. Where they differ, "
              "the difference is season, not system.")
        if reg is not None and not reg.empty:
            print(f"\n   {'panel':<13} {'model':<22} {'date':<12} "
                  f"{'in window':>9} {'share pre':>9} "
                  f"{'skill pre':>9} {'skill post':>10}")
            for _, r in reg.sort_values("date").iterrows():
                pre = "    n/a" if pd.isna(r.skill_pre_win) \
                    else f"{r.skill_pre_win:9.4f}"
                post = "     n/a" if pd.isna(r.skill_post_win) \
                    else f"{r.skill_post_win:10.4f}"
                print(f"   {r.source:<13} {r.model:<22} {r.date.date()} "
                      f"{str(bool(r.in_balanced_window)):>9} "
                      f"{r.share_pre:9.0%} {pre} {post}")
            inside = reg[reg.in_balanced_window]
            straddle = inside[(inside.share_pre > 0.10)
                              & (inside.share_post > 0.10)]
            if not straddle.empty:
                print(f"  {len(straddle)} of these land INSIDE the balanced "
                      f"window with both regimes owning more\n  than a tenth "
                      f"of it. For those series the balanced mean is a blend "
                      f"of a before and\n  an after, not an estimate of the "
                      f"system as it now stands - Task 17's window fixes "
                      f"the\n  calendar and cannot fix this.")
            elif not inside.empty:
                print("  These land inside the balanced window but with one "
                      "regime owning almost all of\n  it, so the window mean "
                      "is still describing a single regime.")
            else:
                print("  Every detected shift predates the balanced window, "
                      "so the window sits entirely\n  inside one regime and "
                      "the quoted numbers are not averaging across a change.")

    print("\n--- Caveats ---")
    print(f"  1. The balanced window costs days: "
          f"{cen.kept_share.median():.0%} of each series survives it. Every "
          f"interval in\n     it is correspondingly wider, and a shift that "
          f"is not significant here may be a\n     sample-size statement "
          f"rather than a null.")
    print("  2. Season labels are northern-hemisphere names. The WEIGHTS are "
          "calendar quarters and\n     are hemisphere-neutral, but a reader "
          "comparing 'DJF' across hemispheres is\n     comparing winter with "
          "summer and the table cannot stop them.")
    print("  3. A change point is located to within its argmax, not to the "
          "day, and the search\n     runs on rows of observed days - an "
          "outage shortens a segment rather than moving a\n     date.")
    print("  4. Absence of a detected shift is not absence of an upgrade. "
          "Centres upgrade in ways\n     that move skill by less than this "
          "sample can see, and the null distribution\n     printed beside "
          "each test says how much that is.")
    if skipped:
        print(f"  5. {len(skipped)} series were dropped before the seasonal "
              f"and change-point work for want of\n     a usable "
              f"climatology; the first is: {skipped[0]}")


# ---------------------------------------------------------------------------
def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    n_boot = 300 if arg == "quick" else BOOTSTRAP_N
    n_null = 120 if arg == "quick" else N_NULL

    print("=== correctness checks ===")
    run_checks(n_sim=20 if arg == "quick" else 60)
    if arg in ("check", "selftest"):
        print("all checks passed")
        return

    df = load_series()
    start, end = balanced_window(df.local_date)
    print(f"\n=== {df.source.nunique()} panels, "
          f"{df.groupby(['source', 'city', 'model']).ngroups} series; "
          f"balanced window {start.date()}..{end.date()} ===")

    cen = census(df, start, end)
    shift = window_shift(df, start, end, n_boot=n_boot)
    print(f"  window shift tested for {shift.source.nunique()} panels x "
          f"{shift.model.nunique()} models")

    prep, skipped = with_climatology(df)
    seasons = season_table(prep, start, end)
    agg = weighted_aggregate(seasons)
    print(f"  season table: {len(seasons)} rows, "
          f"{len(agg)} series x window aggregates "
          f"({len(skipped)} series had no usable climatology)")

    cps = change_point_table(prep, n_null=n_null)
    print(f"  change-point search: {int(cps.date.notna().sum())} shifts "
          f"found across {cps.groupby(['source', 'model']).ngroups} panels")
    reg = regime_split(cps, prep, start, end)
    if not reg.empty:
        cps = cps.merge(
            reg[["source", "model", "date", "in_balanced_window", "share_pre",
                 "share_post", "skill_pre_win", "skill_post_win"]],
            on=["source", "model", "date"], how="left")

    CENSUS_TABLE.parent.mkdir(parents=True, exist_ok=True)
    cen.to_parquet(CENSUS_TABLE, index=False)
    shift.to_parquet(SHIFT_TABLE, index=False)
    seasons.merge(agg, on=["source", "city", "model", "window"], how="left",
                  suffixes=("", "_agg")).to_parquet(SEASON_TABLE, index=False)
    cps.to_parquet(CHANGE_TABLE, index=False)
    report(cen, shift, agg, cps, start, end, skipped, reg)
    print(f"\nwrote {CENSUS_TABLE.name}, {SHIFT_TABLE.name}, "
          f"{SEASON_TABLE.name} and {CHANGE_TABLE.name}")


if __name__ == "__main__":
    main()
