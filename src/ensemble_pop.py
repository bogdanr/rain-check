"""Member-derived probability of precipitation from an ensemble archive.

Closes plan Task 20d and gap G17; feeds Task 21 (the vendor-versus-ensemble
divergence), D5 (the lead-time axis) and Task 30 (physics versus ML).

What this computes, and why it is not what the vendor serves
------------------------------------------------------------
The vendor publishes `precipitation_probability` hourly, by an undisclosed
method. This module computes PoP the way a forecaster would, from the
ensemble itself:

    per-step accumulation  = rate [kg m-2 s-1] x step seconds  (mm per step)
    member local-day total = sum of the steps inside the local calendar day
    PoP                    = (# members at or above threshold) / n members

With GEFS's 31 members the achievable probabilities are multiples of
1/31 ~ 0.032 and with ECMWF's 51 members multiples of ~0.020, which matters
for sharpness and reliability comparisons against a vendor series whose basis
is unknown and effectively continuous. The member count is therefore read
from the data and carried on every row rather than assumed.

Three archives, one step ladder each
------------------------------------
GEFS publishes 3-hourly steps, AIFS ENS 6-hourly, IFS ENS 3-hourly to 144 h
and 6-hourly beyond. Nothing below assumes a step length: the ladder comes
from the `lead_hours` column that `src/collect_members.py` stores beside
`lead_index` for exactly this reason, and every interval width in the
accumulation maths is read off it. A module that assumed 3 h would understate
AIFS day totals by half while producing entirely plausible numbers.

G17: the sub-daily boundary problem, stated plainly
---------------------------------------------------
Accumulation steps are referenced to a 00Z initialisation, so a local calendar
day can only be expressed exactly where the UTC offset is a multiple of the
step length - 3 hours for GEFS, and 6 for AIFS ENS, which makes the problem
strictly worse there. UTC+1, +2, +4, +5:30, +5:45 and +9:30 - which between
them cover most of Europe, South Asia and Australia - cannot be. The day
boundary therefore has to be approximated, and the choice is a real
methodological decision, not an implementation detail: at a 0.2 mm threshold
a single misattributed 3-hour step can flip a member from dry to wet and move
PoP by 1/31.

Two handlings are implemented, and BOTH can be run, so the sensitivity is a
measured number in the paper rather than an assertion:

  `prorata` (PRIMARY).  A step that straddles local midnight contributes the
      fraction of its accumulation equal to the fraction of its 3 hours that
      falls inside the day. Chosen as primary because it is unbiased in the
      mean - the day totals of consecutive days sum exactly to the total of
      the two-day period, so no water is created or destroyed at the boundary
      - and because it degenerates to the exact answer whenever the offset IS
      a multiple of 3 h. Its assumption, that precipitation is uniform within
      a 3-hour step, is wrong for convective rain and will slightly smear
      short intense events across midnight.

  `snap`.  The local day is snapped to the nearest step boundary on the
      archive's own ladder, giving a whole number of steps with no
      apportionment. Preserves the within-step intensity structure exactly, at
      the cost of verifying a window displaced by up to half a step from the
      day the gauge actually measured. Not unbiased: for a fixed UTC offset
      the displacement is systematic, so it can alias with the diurnal cycle
      of precipitation.

Neither is right. Reporting the spread between them is the honest treatment,
and `compare_boundary_modes` produces exactly that.

The vendor control - without which the divergence result is confounded
----------------------------------------------------------------------
The vendor PoP under comparison is HOURLY, so any divergence between it and
the ensemble mixes two different things: a genuine difference in how the
probability was derived (the provenance signal this study is about), and a
pure temporal-resolution artefact at the day boundary. `vendor_pop_control`
separates them by re-deriving the vendor's daily PoP at the ensemble's
3-hourly quantisation, using the same boundary rule, and reporting it
alongside the exact-hourly value. The difference between those two columns is
the resolution artefact; whatever remains after subtracting it is provenance.

Two further confounds are measured rather than assumed, because leaving them
inside the headline number would be the same mistake:

  * Event definition. The existing pipeline's daily vendor probability is the
    MAX of the hourly PoP over the day (src/capitals.py:141), i.e. "will it
    rain in some hour", whereas the study's observed event is a daily TOTAL
    at or above the threshold (src/build_dataset.py:74). Those are different
    events. `pop_anystep` here computes the ensemble's matching "any 3-hour
    step at or above threshold" probability so the comparison can be made
    like for like; `pop_daytotal` is the one that matches the observation.
  * Hour labelling. Open-Meteo labels an hourly value with the START of the
    hour but documents the value as covering the PRECEDING hour; the existing
    pipeline assigns hours to days by that label. `vendor_pop_control`
    reports both that convention and the interval-consistent one, so the
    one-hour shift is visible. Nothing existing is changed.

Usage:
    python src/ensemble_pop.py compute [src] [--mode prorata|snap|all]
    python src/ensemble_pop.py compare [src]    # boundary-mode sensitivity
    python src/ensemble_pop.py vendor-control [model]
    python src/ensemble_pop.py selftest         # boundary maths, no network

`src` is a key of config.ENSEMBLE_SOURCES and defaults to `gefs`.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

from config import (
    ENSEMBLE_SOURCES,
    GEFS_BOUNDARY_MODES,
    GEFS_BOUNDARY_PRIMARY,
    GEFS_STEP_SECONDS,
    LEAD_DAYS,
    PRIMARY_MODEL,
    PROCESSED,
    RAIN_THRESHOLD_MM,
    RAIN_THRESHOLD_VARIANTS_MM,
    RAW,
    City,
    load_capitals,
)

STEP_H = GEFS_STEP_SECONDS / 3600.0

# The vendor control is a single-archive product (it compares the vendor
# against GEFS), so its path stays fixed; the PoP and boundary tables are per
# source and are named from the registry, so adding an archive cannot
# overwrite another's results.
VENDOR_CONTROL_TABLE = PROCESSED / "vendor_pop_quantisation.parquet"


def boundary_table(src) -> object:
    return PROCESSED / f"{src.key}_boundary_sensitivity.parquet"


# The ladder GEFS publishes, as a fallback for the handful of callers that
# predate the multi-archive registry and pass no data-derived ladder. It is
# built from the registered step length rather than written out, so the two
# cannot disagree.
def uniform_ladder(n_index: int, step_h: float = STEP_H) -> np.ndarray:
    return np.arange(n_index, dtype=np.float64) * step_h


# --------------------------------------------------------------------------
# Local-day geometry
# --------------------------------------------------------------------------
def local_day_window(local_date: date, tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """UTC half-open interval [start, end) of one local calendar day.

    Built from two local midnights rather than start + 24 h, so the 23- and
    25-hour days at daylight-saving transitions are handled exactly. That is
    not pedantry: a 23-hour day that is treated as 24 hours double-counts one
    hour of precipitation into the neighbouring day, and DST transitions are
    seasonal, so the error would correlate with the season it is being
    weighted by (design decision D2).

    `nonexistent="shift_forward"` covers the time zones whose DST jump lands
    exactly on midnight (Santiago, Havana, Beirut and others), where local
    midnight does not occur at all; `ambiguous=True` picks the first of the
    two midnights in the autumn case. Both are documented rather than left to
    raise at global scale-up.
    """
    start = pd.Timestamp(local_date).tz_localize(
        tz, nonexistent="shift_forward", ambiguous=True)
    end = pd.Timestamp(local_date + timedelta(days=1)).tz_localize(
        tz, nonexistent="shift_forward", ambiguous=True)
    return start.tz_convert("UTC"), end.tz_convert("UTC")


def lead_hours_window(init_time: pd.Timestamp, local_date: date, tz: str
                      ) -> tuple[float, float]:
    """The local day expressed as hours after the model initialisation."""
    start, end = local_day_window(local_date, tz)
    init = pd.Timestamp(init_time).tz_localize("UTC") \
        if pd.Timestamp(init_time).tzinfo is None else pd.Timestamp(init_time)
    return ((start - init).total_seconds() / 3600.0,
            (end - init).total_seconds() / 3600.0)


def required_lead_index(init_time: pd.Timestamp, tz: str,
                        lead_hours: np.ndarray,
                        lead_days=LEAD_DAYS) -> int:
    """Highest lead INDEX needed to cover local days `lead_days` from one init.

    Lead index i holds the mean rate over (lead_hours[i-1], lead_hours[i]], so
    the index needed to close a day ending at H hours is the first index whose
    lead hour reaches H. The ladder is passed in rather than assumed because
    the three archives do not share one, and an index computed on the wrong
    ladder is off by a factor of two without looking wrong.

    This is the number that decides the real cost of the whole campaign. A
    GEFS chunk carries lead indices 0-63 (0-189 h). Local day k ends at
    24(k+1) - offset hours after the 00Z init, so:

      * days 1-6 need at most 168 + 12 = 180 h -> index 60, inside ONE chunk
        for every time zone on Earth;
      * day 7 needs 192 - offset hours, i.e. index 64 for any offset below
        +3 h - a SECOND chunk, for the sake of one or two steps.

    The feasibility report's 79 GB projection assumed days 1-7 fit in one
    chunk; they do not, except east of UTC+3. The ECMWF stores keep every lead
    in one chunk and never pay the second read. Measured rather than argued by
    src/collect_members.py, which reports chunks per (tile, init).
    """
    init_date = pd.Timestamp(init_time).date()
    worst = 0.0
    for k in lead_days:
        _, end_h = lead_hours_window(init_time, init_date + timedelta(days=k), tz)
        worst = max(worst, end_h)
    idx = int(np.searchsorted(np.asarray(lead_hours, dtype=float),
                              worst - 1e-9, side="left"))
    return min(idx, len(lead_hours) - 1)


def step_weights(start_h: float, end_h: float, lead_hours: np.ndarray,
                 mode: str = GEFS_BOUNDARY_PRIMARY) -> np.ndarray:
    """Weight of each lead index in a local day, on the archive's own ladder.

    Index i covers (lead_hours[i-1], lead_hours[i]]. Returns an array as long
    as `lead_hours`; index 0 always weighs zero, being the initialisation
    instant, which carries no accumulation interval - which is also why the
    archives store NaN there.

    A weight is a FRACTION of its own step, not of a fixed 3 hours, so a
    ladder that coarsens with lead (IFS ENS at 144 h) stays correct: the
    fraction is converted back to millimetres by `day_totals`, which multiplies
    by each step's real duration.
    """
    lh = np.asarray(lead_hours, dtype=np.float64)
    n_index = len(lh)
    hi = lh                                  # interval end of index i
    lo = np.concatenate(([lh[0]], lh[:-1]))  # interval start of index i
    width = np.maximum(hi - lo, 1e-12)

    if mode == "prorata":
        overlap = np.minimum(hi, end_h) - np.maximum(lo, start_h)
        w = np.clip(overlap, 0.0, None) / width
    elif mode == "snap":
        # Snap to the nearest boundary the ladder actually offers. On a uniform
        # ladder this is rounding to a multiple of the step; on a coarsening
        # one it is still the nearest real boundary, which is the point.
        s = float(lh[int(np.abs(lh - start_h).argmin())])
        e = float(lh[int(np.abs(lh - end_h).argmin())])
        w = ((lo >= s - 1e-9) & (hi <= e + 1e-9)).astype(np.float64)
    else:
        raise ValueError(f"unknown boundary mode {mode!r}; "
                         f"expected one of {GEFS_BOUNDARY_MODES}")
    w = w[:n_index]
    w[0] = 0.0
    return w


# --------------------------------------------------------------------------
# PoP from member rates
# --------------------------------------------------------------------------
def day_totals(rates: np.ndarray, weights: np.ndarray,
               step_seconds: np.ndarray) -> np.ndarray:
    """Per-member local-day accumulation in mm, from rates in kg m-2 s-1.

    `rates` is (n_members, n_index) indexed by lead index; `step_seconds` is
    the real duration of each index's accumulation interval, taken from the
    archive's ladder. Passing durations rather than one constant is what keeps
    a 6-hourly archive from being scored as if it were 3-hourly.

    Only the weighted entries are allowed to be NaN-free; a NaN under a
    non-zero weight means the day is incomplete and the caller must drop it
    rather than silently treat missing rain as no rain. That check lives in
    `_day_row`.
    """
    # Zero-weight columns are masked out BEFORE the multiply. Lead index 0 is
    # all-NaN in the archive (there is no accumulation interval before t=0) and
    # 0.0 * NaN is NaN, not 0.0 -- multiplying first would poison every total.
    scale = (weights * np.asarray(step_seconds, dtype=np.float64))[None, :]
    contrib = np.where(weights[None, :] > 0, rates * scale, 0.0)
    return contrib.sum(axis=1)


def pop_from_totals(totals: np.ndarray, threshold: float) -> float:
    return float((totals >= threshold).mean())


def _day_row(rates: np.ndarray, weights: np.ndarray,
             step_seconds: np.ndarray, threshold: float) -> dict:
    used = weights > 0
    if not used.any():
        return {"usable": False, "why": "no steps cover the day"}
    if np.isnan(rates[:, used]).any():
        # Loud rather than quiet: a truncated member (the archive records
        # `ingested_forecast_length` per member for exactly this reason) would
        # otherwise contribute a spuriously dry total and bias PoP downward.
        return {"usable": False, "why": "NaN under a contributing step"}

    totals = day_totals(rates, weights, step_seconds)
    # Per-step accumulation for the "any step wet" event, which is what the
    # vendor's daily max-of-hourly-PoP actually answers. On a coarsening
    # ladder a late 6-hour step clears a fixed threshold more easily than an
    # early 3-hour one, so this quantity is not comparable across archives
    # with different ladders and is reported, never compared, as such.
    step_mm = rates * np.asarray(step_seconds, dtype=np.float64)[None, :]
    any_step = (np.where(used[None, :], step_mm, 0.0) >= threshold).any(axis=1)
    return {
        "usable": True,
        "pop_daytotal": pop_from_totals(totals, threshold),
        "pop_anystep": float(any_step.mean()),
        "ens_mean_mm": float(totals.mean()),
        "ens_median_mm": float(np.median(totals)),
        "ens_max_mm": float(totals.max()),
        "n_steps": int(used.sum()),
        # How much of the day's mass came from partially-weighted steps. This
        # is the exposure of the result to the G17 approximation: zero for
        # offsets that are whole multiples of the step length, and larger on a
        # coarser ladder.
        "boundary_weight": float(weights[(weights > 0) & (weights < 1)].sum()),
    }


# --------------------------------------------------------------------------
# Driving it over the collected archive
# --------------------------------------------------------------------------
def _slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def load_city_rates(city: City, src=None) -> pd.DataFrame:
    """Every collected (init_time, lead_index, lead_hours, member, rate)."""
    src = _source(src)
    files = sorted(src.raw.glob(f"*/{src.key}_{_slug(city.name)}.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    return df.drop_duplicates(subset=["init_time", "lead_index", "member"],
                              ignore_index=True)


def ladder_of(df: pd.DataFrame, n_index: int) -> np.ndarray:
    """The archive's lead ladder, recovered from the collected data itself.

    Taken from the stored `lead_hours` rather than from the registry, so the
    accumulation maths is tied to the file it is reading: a table collected
    before a ladder change is still scored on the ladder it was collected on.
    Files written before `lead_hours` existed fall back to the GEFS step, with
    the assumption stated rather than hidden.
    """
    if "lead_hours" not in df.columns:
        return uniform_ladder(n_index)
    pair = (df[["lead_index", "lead_hours"]].drop_duplicates()
            .sort_values("lead_index"))
    if len(pair) != pair["lead_index"].nunique():
        raise RuntimeError("one lead_index maps to several lead_hours - the "
                           "files on disk come from different ladders and "
                           "must not be concatenated")
    lad = np.full(n_index, np.nan)
    lad[pair["lead_index"].to_numpy()] = pair["lead_hours"].to_numpy()
    if np.isnan(lad).any():
        raise RuntimeError("gaps in the lead ladder; refusing to interpolate "
                           "step durations")
    return lad.astype(np.float64)


def _rate_cube(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, int]:
    """Long frame -> (init_times, cube[n_init, n_member, n_index])."""
    inits = np.sort(df["init_time"].unique())
    n_index = int(df["lead_index"].max()) + 1
    members = np.sort(df["member"].unique())
    cube = np.full((len(inits), len(members), n_index), np.nan, dtype=np.float32)
    ii = pd.Index(inits).get_indexer(df["init_time"])
    mi = pd.Index(members).get_indexer(df["member"])
    cube[ii, mi, df["lead_index"].to_numpy()] = df["precip_rate"].to_numpy()
    return inits, cube, len(members)


def _source(src):
    from ens_archive import source as _s
    return _s(src)


def compute(src=None, modes=(GEFS_BOUNDARY_PRIMARY,),
            thresholds=RAIN_THRESHOLD_VARIANTS_MM,
            lead_days=LEAD_DAYS, cities=None,
            step_hours: float | None | tuple = None) -> pd.DataFrame:
    """Member-derived PoP for every collected city-day. Long form.

    Long over (boundary mode x threshold) rather than wide, for the same
    reason src/collect_ensemble.py is long over members: the set of modes and
    thresholds is a study parameter that will grow, and a long table gains
    rows where a wide one would need a migration.

    `step_hours` coarsens the ladder before accumulating. Task 30 needs it:
    IFS ENS publishes 3-hourly steps and AIFS ENS 6-hourly ones, so scoring
    each on its own ladder would give the physics system a finer day boundary
    than the ML system and hand it an advantage that has nothing to do with
    forecast quality. The native run is kept as the sensitivity.

    Several ladders may be asked for at once (`step_hours=(None, 6.0)`), in
    which case they land in ONE table distinguished by the `step_hours`
    column. They have to share a file: the sensitivity is a comparison
    between them, and two files would let a later run leave the matched and
    native halves derived from different collections without anything
    noticing.
    """
    src = _source(src)
    cities = cities or load_capitals()
    ladders = (step_hours if isinstance(step_hours, (tuple, list))
               else (step_hours,))
    rows = []
    for name, city in sorted(cities.items()):
        df = load_city_rates(city, src)
        if df.empty:
            print(f"  {name}: no {src.key} extraction on disk - skipping")
            continue
        inits, cube, n_members = _rate_cube(df)
        if n_members != src.members:
            raise RuntimeError(
                f"{name}: {n_members} members on disk, registry says "
                f"{src.members} - PoP resolution and every sharpness "
                f"comparison depend on the member count")
        native = ladder_of(df, cube.shape[2])
        for want in ladders:
            if want is None:
                cube_l, ladder = cube, native
            else:
                cube_l, ladder = coarsen(cube, native, want)
            _accumulate(rows, src, name, city, inits, cube_l, ladder,
                        n_members, modes, thresholds, lead_days)
        print(f"  {name}: {len(inits)} inits x {len(lead_days)} leads"
              f"{'' if len(ladders) == 1 else f' x {len(ladders)} ladders'}")
    out = pd.DataFrame(rows)
    if out.empty:
        raise SystemExit(f"nothing computed - run `python "
                         f"src/collect_members.py collect {src.key}` first")
    out["usable"] = out["usable"].astype(bool)
    table = src.pop_table
    table.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(table, index=False)
    bad = int((~out.usable).sum())
    print(f"wrote {table}  rows={len(out)}  cities={out.city.nunique()}  "
          f"ladders={sorted(out.step_hours.unique())}  "
          f"unusable={bad} ({bad / len(out):.2%})")
    return out


def _accumulate(rows: list, src, name: str, city, inits, cube: np.ndarray,
                ladder: np.ndarray, n_members: int, modes, thresholds,
                lead_days) -> None:
    """One city on one step ladder, appended to `rows` in place."""
    steps_s = np.diff(ladder, prepend=ladder[0])
    steps_s[0] = 0.0
    steps_s = steps_s * 3600.0
    step_h = float(np.median(np.diff(ladder)))
    for t, init in enumerate(inits):
        init_ts = pd.Timestamp(init)
        init_date = init_ts.date()
        for k in lead_days:
            local_date = init_date + timedelta(days=k)
            start_h, end_h = lead_hours_window(init_ts, local_date,
                                               city.timezone)
            for mode in modes:
                w = step_weights(start_h, end_h, ladder, mode)
                for thr in thresholds:
                    r = _day_row(cube[t], w, steps_s, thr)
                    rows.append({
                        "source": src.key, "method": src.method,
                        "city": name, "country": city.country,
                        "timezone": city.timezone,
                        "init_time": init_ts, "local_date": local_date,
                        "lead_days": k, "boundary_mode": mode,
                        "threshold_mm": thr, "n_members": n_members,
                        "step_hours": step_h,
                        # start_h is the lead hour of local midnight for a
                        # 00Z init, i.e. 24*k - offset; invert for a signed
                        # offset that reads the conventional way (UTC+2 ->
                        # +2), which is what the G17 sensitivity table keys on.
                        "utc_offset_h": round(24 * k - start_h, 2),
                        **r,
                    })


def coarsen(cube: np.ndarray, ladder: np.ndarray, step_h: float
            ) -> tuple[np.ndarray, np.ndarray]:
    """Re-express rates on a coarser uniform ladder, conserving accumulation.

    Task 30 compares a 3-hourly archive with a 6-hourly one. Coarsening is
    done by summing each fine step's ACCUMULATION into its containing coarse
    step and dividing by the coarse duration, so total rainfall is preserved
    exactly; averaging the rates would be right only where the fine steps are
    equal in length, which on IFS ENS they are not.

    A fine step that straddles a coarse boundary would make the operation
    ambiguous, so it is refused rather than apportioned: every archive in the
    registry nests cleanly, and a future one that does not should be noticed.

    The tail is truncated rather than part-filled. If the fine ladder ends at
    207 h and the coarse step is 6 h, the last coarse step would cover only
    3 h of forecast and read as half as wet as it is; dropping it costs leads
    the study does not use and keeps every surviving step a true total.
    """
    fine_w = np.diff(ladder, prepend=ladder[0])
    fine_w[0] = 0.0
    end = ladder
    start = np.concatenate(([ladder[0]], ladder[:-1]))
    n_coarse = int(np.floor(ladder[-1] / step_h)) + 1
    new_ladder = np.arange(n_coarse, dtype=np.float64) * step_h
    bin_of = np.ceil(end / step_h - 1e-9).astype(int)
    straddles = (np.floor(start / step_h + 1e-9).astype(int)
                 != np.maximum(bin_of - 1, 0))
    if straddles[1:].any():
        bad = float(end[1:][straddles[1:]][0])
        raise RuntimeError(f"a {ladder[1] - ladder[0]:g} h step ending at "
                           f"{bad:g} h straddles a {step_h:g} h boundary; "
                           f"this ladder does not nest and must not be "
                           f"coarsened silently")
    acc = cube * (fine_w * 3600.0)[None, None, :]
    out = np.zeros(cube.shape[:2] + (n_coarse,), dtype=np.float64)
    keep = (bin_of > 0) & (bin_of < n_coarse)
    # A NaN in any contributing fine step propagates through the sum, so a
    # truncated member stays visibly missing instead of being summed as a dry
    # one - which is the failure `_day_row` exists to catch.
    np.add.at(out, (slice(None), slice(None), bin_of[keep]), acc[:, :, keep])
    out[:, :, 0] = np.nan
    return (out / (step_h * 3600.0)).astype(np.float32), new_ladder


def compare_boundary_modes(src=None, thresholds=(RAIN_THRESHOLD_MM,)
                           ) -> pd.DataFrame:
    """G17 sensitivity: how much does the boundary handling move PoP?

    Reported per city because the answer is a property of the UTC offset:
    zero for offsets that are whole multiples of the step, largest at half a
    step from one. A study that reported only the primary mode would be
    asserting that this table is small; it is cheaper to publish it.
    """
    src = _source(src)
    df = compute(src, modes=GEFS_BOUNDARY_MODES, thresholds=thresholds)
    use = df[df.usable]
    wide = use.pivot_table(
        index=["city", "timezone", "utc_offset_h", "init_time", "local_date",
               "lead_days", "threshold_mm"],
        columns="boundary_mode", values=["pop_daytotal", "ens_mean_mm"]).dropna()
    d_pop = (wide[("pop_daytotal", "snap")] - wide[("pop_daytotal", "prorata")])
    d_mm = (wide[("ens_mean_mm", "snap")] - wide[("ens_mean_mm", "prorata")])
    out = pd.DataFrame({"d_pop": d_pop, "d_mm": d_mm}).reset_index()
    summary = out.groupby(["city", "utc_offset_h"]).agg(
        n=("d_pop", "size"),
        mean_abs_d_pop=("d_pop", lambda s: float(np.abs(s).mean())),
        max_abs_d_pop=("d_pop", lambda s: float(np.abs(s).max())),
        frac_changed=("d_pop", lambda s: float((np.abs(s) > 1e-9).mean())),
        mean_abs_d_mm=("d_mm", lambda s: float(np.abs(s).mean())),
    ).reset_index().sort_values("mean_abs_d_pop", ascending=False)
    table = boundary_table(src)
    table.parent.mkdir(parents=True, exist_ok=True)
    summary.to_parquet(table, index=False)
    print(summary.to_string(index=False))
    print(f"\nwrote {table}")
    return summary


# --------------------------------------------------------------------------
# The vendor quantisation control (G17, second half)
# --------------------------------------------------------------------------
def vendor_pop_control(model: str = PRIMARY_MODEL, modes=GEFS_BOUNDARY_MODES,
                       cities=None) -> pd.DataFrame:
    """Re-derive the vendor's daily PoP at the ensemble's 3-hourly resolution.

    Reads the hourly vendor series already on disk
    (data/raw/provider_pop_<city>_<model>.parquet) and produces, per local
    day, three numbers:

      `vendor_pop_label`    the existing pipeline's convention - max of the
                            hourly PoP over the hours whose START label falls
                            in the local day (src/capitals.py:139-147).
      `vendor_pop_interval` the same, but assigning each hourly value to the
                            day containing the hour it actually describes.
                            Open-Meteo documents an hourly value stamped H as
                            covering the PRECEDING hour (H-1, H], so the two
                            conventions differ by one hour at each boundary.
                            Reported, not applied: nothing in the existing
                            analysis chain is touched.
      `vendor_pop_3h_<m>`   the vendor series first collapsed onto the
                            ensemble's 3-hourly step grid (max within each
                            step, interval-consistent), then reduced over the
                            same steps the ensemble uses under boundary mode
                            <m>.

    `vendor_pop_3h_* - vendor_pop_interval` is the pure temporal-resolution
    artefact. Subtracting it from the vendor-versus-ensemble gap leaves the
    provenance signal, which is the result Task 21 publishes. Without this
    column the headline divergence cannot be attributed.

    Note on the two modes here: the daily vendor statistic is a MAX, not a
    sum, so pro-rata apportionment has no meaning for it -- a partially
    covered step is either looked at or not, and `prorata` (weight > 0)
    degenerates to "look at it", which can only raise a max. That is why
    `prorata` shows a one-sided positive bias below while `snap` is
    near-unbiased. Report `snap` as the control for the vendor series and
    keep `prorata` as the ensemble primary, where the reduction is a sum and
    apportionment is exactly right.
    """
    cities = cities or load_capitals()
    rows = []
    for name, city in sorted(cities.items()):
        path = RAW / f"provider_pop_{_slug(name)}_{model}.parquet"
        if not path.exists():
            print(f"  {name}: no vendor series for {model} - skipping")
            continue
        v = pd.read_parquet(path)
        if v.empty or "precipitation_probability" not in v:
            print(f"  {name}: vendor series empty - skipping")
            continue
        v = v.dropna(subset=["precipitation_probability"]).copy()
        v["time"] = pd.to_datetime(v["time"], utc=True)
        v = v.sort_values("time")
        pop = v.set_index("time")["precipitation_probability"].astype(float) / 100.0

        # Hour stamped H describes (H-1, H], so the stamp IS the interval end
        # and no shift is needed: `pop_interval` and `pop` share an index and
        # differ only in how each day's membership is selected below. (An
        # earlier version shifted by +1h, which made the two conventions
        # select identical hour sets and reported a spurious zero difference.)
        pop_interval = pop

        # 3-hourly blocks ending at 00/03/06...Z, matching the GEFS steps.
        # Max within the block, because the daily statistic is a max and a
        # mean would change the event as well as the resolution.
        block = pop_interval.resample("3h", label="right", closed="right").max()

        for local_date, grp in pop.groupby(pop.index.tz_convert(city.timezone).date):
            start, end = local_day_window(local_date, city.timezone)
            # label convention: stamps inside [start, end)  -- what capitals.py
            #   :139-147 does today.
            # interval convention: intervals ending inside (start, end], i.e.
            #   stamps in [start+1h, end]. The two sets differ by exactly one
            #   hour at each end of the day.
            hrs_label = grp
            hrs_interval = pop_interval[(pop_interval.index > start)
                                        & (pop_interval.index <= end)]
            if len(hrs_label) < 23 or len(hrs_interval) < 23:
                continue  # incomplete day; the existing pipeline drops these too
            row = {"city": name, "model": model, "local_date": local_date,
                   "vendor_pop_label": float(hrs_label.max()),
                   "vendor_pop_interval": float(hrs_interval.max()),
                   "n_hours": int(len(hrs_label))}
            for mode in modes:
                # Express the day in "hours after 00Z of its own UTC date" so
                # the same step_weights() used for the ensemble applies.
                day0 = pd.Timestamp(local_date, tz="UTC") - pd.Timedelta(days=1)
                start_h = (start - day0).total_seconds() / 3600.0
                end_h = (end - day0).total_seconds() / 3600.0
                w = step_weights(start_h, end_h,
                                 uniform_ladder(int(np.ceil(end_h / STEP_H)) + 1),
                                 mode)
                ends = day0 + pd.to_timedelta(np.arange(len(w)) * STEP_H, unit="h")
                sel = [e for e, ww in zip(ends, w) if ww > 0]
                vals = block.reindex(sel).dropna()
                row[f"vendor_pop_3h_{mode}"] = (float(vals.max())
                                                if len(vals) else np.nan)
                row[f"n_steps_{mode}"] = int(len(vals))
            rows.append(row)
        print(f"  {name}: {sum(r['city'] == name for r in rows)} days")

    out = pd.DataFrame(rows)
    if out.empty:
        raise SystemExit("no vendor series found - run collect_archive.py "
                         "all-providers first")
    for mode in modes:
        out[f"d_quantisation_{mode}"] = (out[f"vendor_pop_3h_{mode}"]
                                         - out["vendor_pop_interval"])
    out["d_hour_labelling"] = out["vendor_pop_label"] - out["vendor_pop_interval"]
    VENDOR_CONTROL_TABLE.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(VENDOR_CONTROL_TABLE, index=False)

    print(f"\nwrote {VENDOR_CONTROL_TABLE}  rows={len(out)}")
    print("\nresolution artefact, vendor PoP (3-hourly minus exact hourly):")
    for mode in modes:
        d = out[f"d_quantisation_{mode}"].dropna()
        print(f"  {mode:8s} mean {d.mean():+.4f}  mean|d| {d.abs().mean():.4f}  "
              f"max|d| {d.abs().max():.4f}  changed {100 * (d.abs() > 1e-9).mean():.1f}%")
    d = out["d_hour_labelling"]
    print(f"  hour-labelling convention: mean {d.mean():+.4f}  "
          f"mean|d| {d.abs().mean():.4f}  changed {100 * (d.abs() > 1e-9).mean():.1f}%")
    return out


# --------------------------------------------------------------------------
# Self-test of the boundary maths (no network, no data)
# --------------------------------------------------------------------------
def selftest() -> None:
    """The G17 maths is easy to get wrong, so it is checked, not trusted."""
    init = pd.Timestamp("2024-09-01T00:00:00Z")
    g3 = uniform_ladder(70)                  # GEFS: 3-hourly
    g6 = uniform_ladder(40, 6.0)             # AIFS ENS: 6-hourly

    # UTC+3 (Bucharest, summer): local day 1 is 21..45 h, a whole number of
    # steps, so both modes must agree exactly and no step is partial.
    s, e = lead_hours_window(init, date(2024, 9, 2), "Europe/Bucharest")
    assert (s, e) == (21.0, 45.0), (s, e)
    wp = step_weights(s, e, g3, "prorata")
    ws = step_weights(s, e, g3, "snap")
    assert np.allclose(wp, ws) and wp.sum() == 8.0
    assert not ((wp > 0) & (wp < 1)).any(), "no partial step expected at UTC+3"

    # The same day on a 6-hourly ladder is four steps, not eight: a weight is
    # a fraction of its OWN step. If this ever read 8, every AIFS day total
    # would be double.
    w6 = step_weights(s, e, g6, "prorata")
    assert w6.sum() == 4.0, w6.sum()
    # ... and UTC+3 is no longer a whole number of steps at 6-hourly, which is
    # the G17 problem getting worse on the ML archive rather than going away.
    s3, e3 = lead_hours_window(init, date(2024, 9, 2), "Europe/Bucharest")
    assert ((step_weights(s3, e3, g6, "prorata") % 1) != 0).sum() == 2

    # UTC+1 (Berlin, winter): 23..47 h. Two partial steps under prorata; snap
    # displaces the window by 1 h.
    s, e = lead_hours_window(pd.Timestamp("2024-12-01T00:00:00Z"),
                             date(2024, 12, 2), "Europe/Berlin")
    assert (s, e) == (23.0, 47.0), (s, e)
    wp = step_weights(s, e, g3, "prorata")
    assert abs(wp.sum() - 8.0) < 1e-9, wp.sum()
    assert ((wp > 0) & (wp < 1)).sum() == 2
    ws = step_weights(s, e, g3, "snap")
    assert ws.sum() == 8.0 and not ((ws > 0) & (ws < 1)).any()

    # UTC+5:45 (Kathmandu): the pathological offset. prorata still conserves.
    s, e = lead_hours_window(init, date(2024, 9, 2), "Asia/Kathmandu")
    assert abs(s - 18.25) < 1e-9, s
    wp = step_weights(s, e, g3, "prorata")
    assert abs(wp.sum() - 8.0) < 1e-9

    # Conservation: consecutive days must partition the steps exactly, or the
    # boundary handling is creating or destroying precipitation.
    tot = np.zeros(70)
    for k in (1, 2, 3):
        s, e = lead_hours_window(init, date(2024, 9, 1) + timedelta(days=k),
                                 "Asia/Kathmandu")
        tot += step_weights(s, e, g3, "prorata")
    # Steps strictly inside the three-day span. The first and last steps are
    # clipped by the 5:45 offset and are partial by construction.
    inner = tot[8:31]
    assert np.allclose(inner, 1.0), inner

    # A coarsening ladder, as IFS ENS publishes: 3-hourly to 144 h, 6-hourly
    # after. A day that spans the change must still weigh one day.
    mixed = np.concatenate([np.arange(0, 145, 3.0), np.arange(150, 361, 6.0)])
    s, e = 138.0, 162.0
    wm = step_weights(s, e, mixed, "prorata")
    dur = np.diff(mixed, prepend=mixed[0])
    dur[0] = 0.0
    assert abs(float((wm * dur).sum()) - 24.0) < 1e-9, (wm * dur).sum()

    # DST: a 23-hour local day must be 23 hours, not 24.
    s, e = lead_hours_window(pd.Timestamp("2025-03-29T00:00:00Z"),
                             date(2025, 3, 30), "Europe/Berlin")
    assert abs((e - s) - 23.0) < 1e-9, e - s

    # Chunk economics (see required_lead_index): days 1-6 fit one GEFS chunk
    # everywhere; day 7 does not west of UTC+3.
    assert required_lead_index(init, "Europe/Bucharest", g3, [1, 2, 3, 4, 5, 6]) <= 63
    assert required_lead_index(init, "Pacific/Midway", g3, [1, 2, 3, 4, 5, 6]) <= 63
    assert required_lead_index(init, "Atlantic/Reykjavik", g3, LEAD_DAYS) == 64
    assert required_lead_index(init, "Europe/Bucharest", g3, LEAD_DAYS) == 63

    # Day totals: a constant 1 mm per step over a whole day gives 8 mm on a
    # 3-hourly ladder and, for the same RATE, the same 8 mm on a 6-hourly one
    # - the rate is per second, so the day total cannot depend on how the day
    # is sliced.
    dur3 = np.full(70, 3 * 3600.0)
    dur3[0] = 0.0
    rate = np.full((3, 70), 1.0 / GEFS_STEP_SECONDS, dtype=np.float64)
    s, e = lead_hours_window(init, date(2024, 9, 2), "Europe/Bucharest")
    w = step_weights(s, e, g3, "prorata")
    assert np.allclose(day_totals(rate, w, dur3), 8.0)
    dur6 = np.full(40, 6 * 3600.0)
    dur6[0] = 0.0
    rate6 = np.full((3, 40), 1.0 / GEFS_STEP_SECONDS, dtype=np.float64)
    assert np.allclose(day_totals(rate6, step_weights(s, e, g6, "prorata"),
                                  dur6), 8.0)

    # Coarsening conserves accumulation exactly, which is the property that
    # lets Task 30 put a 3-hourly and a 6-hourly archive on one ladder.
    rng = np.random.default_rng(0)
    fine = rng.gamma(0.4, 2e-5, size=(2, 5, 70)).astype(np.float32)
    fine[:, :, 0] = np.nan
    coarse, lad6 = coarsen(fine, g3, 6.0)
    assert np.allclose(lad6, uniform_ladder(len(lad6), 6.0))
    within = g3 <= lad6[-1] + 1e-9
    tot_fine = np.nansum(fine[:, :, 1:] * 3 * 3600.0 * within[1:], axis=2)
    tot_coarse = np.nansum(coarse[:, :, 1:] * 6 * 3600.0, axis=2)
    assert np.allclose(tot_fine, tot_coarse, rtol=1e-5), \
        float(np.abs(tot_fine - tot_coarse).max())
    # A missing fine step must poison its coarse step rather than read as dry.
    holed = fine.copy()
    holed[0, 0, 3] = np.nan
    assert np.isnan(coarsen(holed, g3, 6.0)[0][0, 0, 2])
    # A ladder that does not nest is refused, not silently apportioned.
    try:
        coarsen(fine, uniform_ladder(70, 4.0), 6.0)
    except RuntimeError:
        pass
    else:
        raise AssertionError("a non-nesting ladder was coarsened silently")

    print("ensemble_pop selftest: boundary, ladder, DST, conservation and "
          "coarsening assertions passed")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "compute"
    args = sys.argv[2:]
    key = next((a for a in args if a in ENSEMBLE_SOURCES), None)
    if cmd == "compute":
        mode = next((a.split("=")[1] for a in args if a.startswith("--mode=")),
                    GEFS_BOUNDARY_PRIMARY)
        # --step-hours=6 coarsens; --step-hours=native,6 writes both ladders
        # into the one table so the Task 30 sensitivity compares halves that
        # provably came from the same collection.
        raw = next((a.split("=")[1] for a in args
                    if a.startswith("--step-hours=")), None)
        step = None if raw is None else tuple(
            None if p in ("native", "none") else float(p)
            for p in raw.split(","))
        compute(key, modes=GEFS_BOUNDARY_MODES if mode == "all" else (mode,),
                step_hours=step)
    elif cmd == "compare":
        compare_boundary_modes(key)
    elif cmd == "vendor-control":
        rest = [a for a in args if a not in ENSEMBLE_SOURCES]
        vendor_pop_control(rest[0] if rest else PRIMARY_MODEL)
    elif cmd == "selftest":
        selftest()
    else:
        raise SystemExit(f"unknown command: {cmd}")
