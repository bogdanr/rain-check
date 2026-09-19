"""Member-derived probability of precipitation from the GEFS ensemble.

Closes plan Task 20d and gap G17; feeds Task 21 (the vendor-versus-ensemble
divergence) and D5 (the lead-time axis).

What this computes, and why it is not what the vendor serves
------------------------------------------------------------
The vendor publishes `precipitation_probability` hourly, by an undisclosed
method. This module computes PoP the way a forecaster would, from the
ensemble itself:

    per-step accumulation  = rate [kg m-2 s-1] x 10800 s      (mm per 3 h)
    member local-day total = sum of the steps inside the local calendar day
    PoP                    = (# members with total >= threshold) / 31

With 31 members the achievable probabilities are multiples of 1/31 ~ 0.032,
which matters for sharpness and reliability comparisons against a vendor
series whose basis is unknown and effectively continuous.

G17: the 3-hourly boundary problem, stated plainly
--------------------------------------------------
GEFS accumulation steps are 3-hourly and referenced to a 00Z initialisation,
so a local calendar day can only be expressed exactly where the UTC offset is
a multiple of 3 hours. UTC+1, +2, +4, +5:30, +5:45 and +9:30 - which between
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

  `snap`.  The local day is snapped to the nearest 3-hourly step boundary,
      giving a whole number of steps with no apportionment. Preserves the
      within-step intensity structure exactly, at the cost of verifying a
      window displaced by up to 1.5 h from the day the gauge actually
      measured. Not unbiased: for a fixed UTC offset the displacement is
      systematic, so it can alias with the diurnal cycle of precipitation.

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
    python src/ensemble_pop.py compute [--mode prorata|snap|all]
    python src/ensemble_pop.py compare          # boundary-mode sensitivity
    python src/ensemble_pop.py vendor-control [model]
    python src/ensemble_pop.py selftest         # boundary maths, no network
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

from config import (
    GEFS_BOUNDARY_MODES,
    GEFS_BOUNDARY_PRIMARY,
    GEFS_RAW,
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

POP_TABLE = PROCESSED / "gefs_pop.parquet"
BOUNDARY_TABLE = PROCESSED / "gefs_boundary_sensitivity.parquet"
VENDOR_CONTROL_TABLE = PROCESSED / "vendor_pop_quantisation.parquet"


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
                        lead_days=LEAD_DAYS) -> int:
    """Highest lead INDEX needed to cover local days `lead_days` from one init.

    Lead index i holds the mean rate over the interval (3(i-1) h, 3i h], so
    the index needed to close a day ending at H hours is ceil(H / 3).

    This is the number that decides the real cost of the whole campaign. One
    chunk carries lead indices 0-63 (0-189 h). Local day k ends at
    24(k+1) - offset hours after the 00Z init, so:

      * days 1-6 need at most 168 + 12 = 180 h -> index 60, inside ONE chunk
        for every time zone on Earth;
      * day 7 needs 192 - offset hours, i.e. index 64 for any offset below
        +3 h - a SECOND chunk, for the sake of one or two steps.

    The feasibility report's 79 GB projection assumed days 1-7 fit in one
    chunk; they do not, except east of UTC+3. Measured rather than argued by
    src/collect_gefs.py, which reports chunks per (tile, init).
    """
    init_date = pd.Timestamp(init_time).date()
    worst = 0.0
    for k in lead_days:
        _, end_h = lead_hours_window(init_time, init_date + timedelta(days=k), tz)
        worst = max(worst, end_h)
    return int(np.ceil(worst / STEP_H))


def step_weights(start_h: float, end_h: float, n_index: int,
                 mode: str = GEFS_BOUNDARY_PRIMARY) -> np.ndarray:
    """Weight of each lead index in a local day. Index i covers (3i-3, 3i].

    Returns an array of length `n_index` (index 0 always weighs zero: it is
    the initialisation instant and carries no accumulation interval, which is
    why the archive stores NaN there).
    """
    w = np.zeros(n_index, dtype=np.float64)
    lo = np.arange(n_index) * STEP_H - STEP_H     # interval start of index i
    hi = np.arange(n_index) * STEP_H              # interval end of index i

    if mode == "prorata":
        overlap = np.minimum(hi, end_h) - np.maximum(lo, start_h)
        w = np.clip(overlap, 0.0, None) / STEP_H
    elif mode == "snap":
        s = np.round(start_h / STEP_H) * STEP_H
        e = np.round(end_h / STEP_H) * STEP_H
        w = ((lo >= s - 1e-9) & (hi <= e + 1e-9)).astype(np.float64)
    else:
        raise ValueError(f"unknown boundary mode {mode!r}; "
                         f"expected one of {GEFS_BOUNDARY_MODES}")
    w[0] = 0.0
    return w


# --------------------------------------------------------------------------
# PoP from member rates
# --------------------------------------------------------------------------
def day_totals(rates: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Per-member local-day accumulation in mm, from rates in kg m-2 s-1.

    `rates` is (n_members, n_index) indexed by lead index. Only the weighted
    entries are allowed to be NaN-free; a NaN under a non-zero weight means
    the day is incomplete and the caller must drop it rather than silently
    treat missing rain as no rain. That check lives in `_day_row`.
    """
    # Zero-weight columns are masked out BEFORE the multiply. Lead index 0 is
    # all-NaN in the archive (there is no accumulation interval before t=0) and
    # 0.0 * NaN is NaN, not 0.0 -- multiplying first would poison every total.
    contrib = np.where(weights[None, :] > 0, rates * weights[None, :], 0.0)
    return contrib.sum(axis=1) * GEFS_STEP_SECONDS


def pop_from_totals(totals: np.ndarray, threshold: float) -> float:
    return float((totals >= threshold).mean())


def _day_row(rates: np.ndarray, weights: np.ndarray, threshold: float) -> dict:
    used = weights > 0
    if not used.any():
        return {"usable": False, "why": "no steps cover the day"}
    if np.isnan(rates[:, used]).any():
        # Loud rather than quiet: a truncated member (the archive records
        # `ingested_forecast_length` per member for exactly this reason) would
        # otherwise contribute a spuriously dry total and bias PoP downward.
        return {"usable": False, "why": "NaN under a contributing step"}

    totals = day_totals(rates, weights)
    # Per-step accumulation for the "any step wet" event, which is what the
    # vendor's daily max-of-hourly-PoP actually answers.
    step_mm = rates * GEFS_STEP_SECONDS
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
        # offsets that are multiples of 3 h, up to ~2/8 elsewhere.
        "boundary_weight": float(weights[(weights > 0) & (weights < 1)].sum()),
    }


# --------------------------------------------------------------------------
# Driving it over the collected archive
# --------------------------------------------------------------------------
def _slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def load_city_rates(city: City) -> pd.DataFrame:
    """Every collected (init_time, lead_index, member, rate) for one city."""
    files = sorted(GEFS_RAW.glob(f"*/gefs_{_slug(city.name)}.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    return df.drop_duplicates(subset=["init_time", "lead_index", "member"],
                              ignore_index=True)


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


def compute(modes=(GEFS_BOUNDARY_PRIMARY,), thresholds=RAIN_THRESHOLD_VARIANTS_MM,
            lead_days=LEAD_DAYS, cities=None) -> pd.DataFrame:
    """Member-derived PoP for every collected city-day. Long form.

    Long over (boundary mode x threshold) rather than wide, for the same
    reason src/collect_ensemble.py is long over members: the set of modes and
    thresholds is a study parameter that will grow, and a long table gains
    rows where a wide one would need a migration.
    """
    cities = cities or load_capitals()
    rows = []
    for name, city in sorted(cities.items()):
        df = load_city_rates(city)
        if df.empty:
            print(f"  {name}: no GEFS extraction on disk - skipping")
            continue
        inits, cube, n_members = _rate_cube(df)
        n_index = cube.shape[2]
        for t, init in enumerate(inits):
            init_ts = pd.Timestamp(init)
            init_date = init_ts.date()
            for k in lead_days:
                local_date = init_date + timedelta(days=k)
                start_h, end_h = lead_hours_window(init_ts, local_date,
                                                   city.timezone)
                for mode in modes:
                    w = step_weights(start_h, end_h, n_index, mode)
                    for thr in thresholds:
                        r = _day_row(cube[t], w, thr)
                        rows.append({
                            "city": name, "country": city.country,
                            "timezone": city.timezone,
                            "init_time": init_ts, "local_date": local_date,
                            "lead_days": k, "boundary_mode": mode,
                            "threshold_mm": thr, "n_members": n_members,
                            # start_h is the lead hour of local midnight for a
                            # 00Z init, i.e. 24*k - offset; invert for a signed
                            # offset that reads the conventional way (UTC+2 ->
                            # +2), which is what the G17 sensitivity table keys on.
                            "utc_offset_h": round(24 * k - start_h, 2),
                            **r,
                        })
        print(f"  {name}: {len(inits)} inits x {len(lead_days)} leads")
    out = pd.DataFrame(rows)
    if out.empty:
        raise SystemExit("nothing computed - run `python src/collect_gefs.py "
                         "collect` first")
    out["usable"] = out["usable"].astype(bool)
    POP_TABLE.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(POP_TABLE, index=False)
    bad = int((~out.usable).sum())
    print(f"wrote {POP_TABLE}  rows={len(out)}  cities={out.city.nunique()}  "
          f"unusable={bad} ({bad / len(out):.2%})")
    return out


def compare_boundary_modes(thresholds=(RAIN_THRESHOLD_MM,)) -> pd.DataFrame:
    """G17 sensitivity: how much does the boundary handling move PoP?

    Reported per city because the answer is a property of the UTC offset:
    zero for offsets that are multiples of 3 h, largest at offsets of 1.5 h
    from a multiple. A study that reported only the primary mode would be
    asserting that this table is small; it is cheaper to publish it.
    """
    df = compute(modes=GEFS_BOUNDARY_MODES, thresholds=thresholds)
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
    BOUNDARY_TABLE.parent.mkdir(parents=True, exist_ok=True)
    summary.to_parquet(BOUNDARY_TABLE, index=False)
    print(summary.to_string(index=False))
    print(f"\nwrote {BOUNDARY_TABLE}")
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
                w = step_weights(start_h, end_h, int(np.ceil(end_h / STEP_H)) + 1,
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

    # UTC+3 (Bucharest, summer): local day 1 is 21..45 h, a whole number of
    # steps, so both modes must agree exactly and no step is partial.
    s, e = lead_hours_window(init, date(2024, 9, 2), "Europe/Bucharest")
    assert (s, e) == (21.0, 45.0), (s, e)
    wp = step_weights(s, e, 70, "prorata")
    ws = step_weights(s, e, 70, "snap")
    assert np.allclose(wp, ws) and wp.sum() == 8.0
    assert not ((wp > 0) & (wp < 1)).any(), "no partial step expected at UTC+3"

    # UTC+1 (Berlin, winter): 23..47 h. Two partial steps under prorata; snap
    # displaces the window by 1 h.
    s, e = lead_hours_window(pd.Timestamp("2024-12-01T00:00:00Z"),
                             date(2024, 12, 2), "Europe/Berlin")
    assert (s, e) == (23.0, 47.0), (s, e)
    wp = step_weights(s, e, 70, "prorata")
    assert abs(wp.sum() - 8.0) < 1e-9, wp.sum()
    assert ((wp > 0) & (wp < 1)).sum() == 2
    ws = step_weights(s, e, 70, "snap")
    assert ws.sum() == 8.0 and not ((ws > 0) & (ws < 1)).any()

    # UTC+5:45 (Kathmandu): the pathological offset. prorata still conserves.
    s, e = lead_hours_window(init, date(2024, 9, 2), "Asia/Kathmandu")
    assert abs(s - 18.25) < 1e-9, s
    wp = step_weights(s, e, 70, "prorata")
    assert abs(wp.sum() - 8.0) < 1e-9

    # Conservation: consecutive days must partition the steps exactly, or the
    # boundary handling is creating or destroying precipitation.
    tot = np.zeros(70)
    for k in (1, 2, 3):
        s, e = lead_hours_window(init, date(2024, 9, 1) + timedelta(days=k),
                                 "Asia/Kathmandu")
        tot += step_weights(s, e, 70, "prorata")
    # Steps strictly inside the three-day span. The first and last steps are
    # clipped by the 5:45 offset and are partial by construction.
    inner = tot[8:31]
    assert np.allclose(inner, 1.0), inner

    # DST: a 23-hour local day must be 23 hours, not 24.
    s, e = lead_hours_window(pd.Timestamp("2025-03-29T00:00:00Z"),
                             date(2025, 3, 30), "Europe/Berlin")
    assert abs((e - s) - 23.0) < 1e-9, e - s

    # Chunk economics (see required_lead_index): days 1-6 fit one chunk
    # everywhere; day 7 does not west of UTC+3.
    assert required_lead_index(init, "Europe/Bucharest", [1, 2, 3, 4, 5, 6]) <= 63
    assert required_lead_index(init, "Pacific/Midway", [1, 2, 3, 4, 5, 6]) <= 63
    assert required_lead_index(init, "Atlantic/Reykjavik", LEAD_DAYS) == 64
    assert required_lead_index(init, "Europe/Bucharest", LEAD_DAYS) == 63

    # Day totals: a constant 1 mm/3h rate over a whole day must give 8 mm.
    rate = np.full((3, 70), 1.0 / GEFS_STEP_SECONDS, dtype=np.float64)
    s, e = lead_hours_window(init, date(2024, 9, 2), "Europe/Bucharest")
    w = step_weights(s, e, 70, "prorata")
    assert np.allclose(day_totals(rate, w), 8.0)

    print("ensemble_pop selftest: all boundary, DST and conservation "
          "assertions passed")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "compute"
    args = sys.argv[2:]
    if cmd == "compute":
        mode = next((a.split("=")[1] for a in args if a.startswith("--mode=")),
                    GEFS_BOUNDARY_PRIMARY)
        compute(modes=GEFS_BOUNDARY_MODES if mode == "all" else (mode,))
    elif cmd == "compare":
        compare_boundary_modes()
    elif cmd == "vendor-control":
        vendor_pop_control(args[0] if args else PRIMARY_MODEL)
    elif cmd == "selftest":
        selftest()
    else:
        raise SystemExit(f"unknown command: {cmd}")
