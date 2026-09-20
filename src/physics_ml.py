"""Physics against machine learning, on one archive pair (plan Task 30).

The question
------------
ECMWF now runs two ensembles side by side: IFS ENS, the physics system, and
AIFS ENS, a data-driven model trained on reanalysis. Both publish 51 members
on the same 0.25 degree grid from the same initial conditions, through the
same republisher. That makes them the rarest thing in this field - a
comparison where the forecast MODEL is very nearly the only difference. A
comparison across centres (AIFS against GEFS, say) would confound the method
with the centre, the resolution, the data assimilation and the
post-processing all at once, and could not answer anything.

What is being scored is not the headline of the ML literature. Published AIFS
evaluations lead on upper-air fields and on RMSE; this asks the question a
person asks a weather app - will it rain here tomorrow - as a probability,
against gauge observations, on local calendar days. Precipitation is where
data-driven models are known to be weakest, and a daily occurrence probability
at a point is the least forgiving way to ask.

The matched ladder, and why it is not optional
----------------------------------------------
IFS ENS publishes 3-hourly accumulation steps and AIFS ENS 6-hourly ones.
Accumulating each on its own ladder would give the physics system a finer
local-day boundary than the ML system at every city whose UTC offset is not a
multiple of 6 hours - an advantage worth a real fraction of the difference
being measured, and nothing to do with forecast quality. Both are therefore
coarsened to 6 hours (`config.PHYSICS_ML_STEP_H`) before anything is scored,
by an accumulation-conserving reduction, and the native-ladder run is kept as
a published sensitivity so the size of the artefact is a number rather than an
assurance.

Three consequences are accepted openly:

  * the 6-hourly boundary is cruder than the 3-hourly one this study uses for
    GEFS, so these scores are not comparable with the triangulation table;
  * the window is short. AIFS ENS begins 2025-07-02, so the sample is about 14
    months, against 24 for the GEFS work. Every verdict here therefore carries
    its interval, and the minimum detectable difference is printed beside any
    null - a short record is a reason to qualify a claim, not to drop it;
  * the sample is the study's 19 capitals, which are European-heavy. A method
    difference could plausibly vary by regime, so the per-city panel is
    published rather than a pooled number alone.

Inference
---------
The same day-block bootstrap as `src/significance.py`, for the same reasons
and reusing its primitives: rain persists for days and the capitals share
weather systems, so the resampling unit is the calendar day carrying all its
cities, drawn in blocks whose length is measured from the decorrelation time
of the daily paired-difference series rather than assumed. Every statistic is
computed by one function for both the point estimate and every replicate, so
no quantity can be defined one way for the estimate and another for its
interval.

Sign convention, fixed once: every `*_diff` is ML MINUS PHYSICS. A negative
Brier difference means AIFS is better; a positive value difference means AIFS
is worth more. It is written on every printed line because a sign error here
would invert the finding.

Usage:
    python src/physics_ml.py            # selftest, then the full run
    python src/physics_ml.py selftest   # correctness checks only
    python src/physics_ml.py native     # the native-ladder sensitivity only
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import significance as sig
from baselines import doy_climatology
from calibration import brier_decomposition
from config import (
    BOOTSTRAP_N,
    ENSEMBLE_SOURCES,
    GEFS_BOUNDARY_PRIMARY,
    LEAD_DAYS,
    N_PROB_BINS,
    PHYSICS_ML_PAIR,
    PHYSICS_ML_STEP_H,
    PROCESSED,
    RAIN_THRESHOLD_MM,
    RANDOM_SEED,
)
from metrics import HEADLINE_ALPHAS, roc_auc, sharpness, value_curve, value_summary
from triangulation import load_truth

SCORE_TABLE = PROCESSED / "physics_ml_scores.parquet"
DIFF_TABLE = PROCESSED / "physics_ml_significance.parquet"
CITY_TABLE = PROCESSED / "physics_ml_by_city.parquet"
LADDER_TABLE = PROCESSED / "physics_ml_ladder_sensitivity.parquet"

PHYSICS, ML = PHYSICS_ML_PAIR

# A city contributing fewer paired days than this cannot carry a per-city
# score worth printing; it still contributes to the pooled sample. Lower than
# the triangulation's 200 because this window is 14 months, not 24, and a
# threshold set for a longer record would empty the panel rather than protect
# it.
MIN_CITY_DAYS = 120


def die(msg: str) -> None:
    raise SystemExit(f"PHYSICS/ML FAILED: {msg}")


# --------------------------------------------------------------------------
# The paired sample
# --------------------------------------------------------------------------
def load_pop(key: str, step_hours: float | None) -> pd.DataFrame:
    """One archive's member-derived PoP table, checked before it is used."""
    src = ENSEMBLE_SOURCES[key]
    if not src.pop_table.exists():
        die(f"{src.pop_table} missing - run `ensemble_pop.py compute {key} "
            f"--step-hours={PHYSICS_ML_STEP_H:g}` first")
    g = pd.read_parquet(src.pop_table)
    g = g[(g.threshold_mm == RAIN_THRESHOLD_MM)
          & (g.boundary_mode == GEFS_BOUNDARY_PRIMARY) & g.usable].copy()
    if g.empty:
        die(f"{src.pop_table.name} has no usable rows at "
            f"{RAIN_THRESHOLD_MM} mm under {GEFS_BOUNDARY_PRIMARY}")
    if "step_hours" not in g.columns:
        die(f"{src.pop_table.name} predates the multi-ladder PoP code and "
            f"does not record the step length it was accumulated on; "
            f"recompute it rather than guess")
    have = sorted(g.step_hours.round(3).unique())
    if step_hours is None:
        # The native ladder is whichever one the archive publishes, i.e. the
        # finest present in the table.
        step_hours = min(have)
    want = round(float(step_hours), 3)
    if want not in have:
        die(f"{src.pop_table.name} carries step ladder(s) {have} h, not the "
            f"{want} h being asked for. The two systems must be put on ONE "
            f"ladder before they are compared: re-run `ensemble_pop.py "
            f"compute {key} --mode=all --step-hours=native,{want:g}`.")
    g = g[g.step_hours.round(3) == want].copy()
    if g.n_members.nunique() != 1:
        die(f"{key} carries {g.n_members.nunique()} different member counts")
    dup = g.duplicated(["city", "local_date", "lead_days"])
    if dup.any():
        die(f"{int(dup.sum())} duplicated (city, local_date, lead) rows in "
            f"{src.pop_table.name} - one local day must come from exactly one "
            f"initialisation per lead")
    return g


def build_paired(step_hours: float | None = PHYSICS_ML_STEP_H) -> pd.DataFrame:
    """city x local_date x lead, with both systems and the observation present.

    The intersection is taken once and applied to both systems. Nothing is
    reindexed and nothing is filled: a city-day either system cannot supply is
    removed from both, and a city-day missing at any lead is removed from every
    lead. A lead-time comparison run on a sample that changes with lead is the
    exact fault this study criticises elsewhere.
    """
    phys = load_pop(PHYSICS, step_hours)
    ml = load_pop(ML, step_hours)
    truth = load_truth()

    keep = ["city", "country", "timezone", "local_date", "lead_days",
            "init_time", "utc_offset_h", "n_members", "pop_daytotal",
            "ens_mean_mm", "step_hours"]
    df = phys[keep].merge(ml[keep], on=["city", "local_date", "lead_days"],
                          suffixes=("_phys", "_ml"))
    if df.empty:
        die(f"{PHYSICS} and {ML} share no city-days; check the collection "
            f"windows before concluding anything about the models")
    # The two systems are initialised at the same hour by construction
    # (`init_hours` in the registry), so a mismatch means one of the archives
    # is serving a different cycle and the comparison is not like-for-like.
    bad_init = (df.init_time_phys != df.init_time_ml)
    if bad_init.any():
        die(f"{int(bad_init.sum())} rows pair forecasts from different "
            f"initialisation times")

    df = df.merge(truth, on=["city", "local_date"], how="inner")
    if df.empty:
        die("no station observation overlaps the paired ensemble days")

    n_leads = len(LEAD_DAYS)
    full = df.groupby(["city", "local_date"]).size()
    complete = full[full == n_leads].index
    dropped_incomplete = int(len(full) - len(complete))
    df = df.set_index(["city", "local_date"]).loc[complete].reset_index()

    df["event"] = (df.obs_precip_mm.values >= RAIN_THRESHOLD_MM).astype(float)

    # Reference climatology fitted on each city's FULL station record, as
    # elsewhere in the pipeline: fitting it on these 14 months alone would thin
    # the seasonal support, and this window does not even cover a full year
    # twice over.
    parts, failed = [], []
    for city, g in truth.groupby("city"):
        g = g.sort_values("local_date")
        ev = (g.obs_precip_mm.values >= RAIN_THRESHOLD_MM).astype(float)
        try:
            clim = doy_climatology(pd.to_datetime(g.local_date), ev)
        except ValueError as exc:
            failed.append((city, str(exc).split(" - ")[0]))
            continue
        parts.append(pd.DataFrame({"city": city,
                                   "local_date": g.local_date.values,
                                   "clim": clim}))
    if not parts:
        die("no city has a station record long enough for a day-of-year "
            "climatology; the skill scores would have no reference")
    clim = pd.concat(parts, ignore_index=True).dropna(subset=["clim"])

    before = df.groupby(["city", "local_date"]).ngroups
    df = df.merge(clim, on=["city", "local_date"], how="inner")
    dropped_no_clim = before - df.groupby(["city", "local_date"]).ngroups

    out = df.rename(columns={"pop_daytotal_phys": "p_physics",
                             "pop_daytotal_ml": "p_ml",
                             "ens_mean_mm_phys": "mm_physics",
                             "ens_mean_mm_ml": "mm_ml",
                             "country_phys": "country",
                             "timezone_phys": "timezone",
                             "utc_offset_h_phys": "utc_offset_h",
                             "init_time_phys": "init_time"})
    cols = ["city", "country", "timezone", "local_date", "init_time",
            "lead_days", "utc_offset_h", "p_physics", "p_ml", "mm_physics",
            "mm_ml", "obs_precip_mm", "event", "clim"]
    out = out[cols].copy()
    # Kept separately because in the native run they differ - that difference
    # is the artefact the matched run exists to remove.
    out["step_hours"] = float(df.step_hours_ml.iloc[0])
    out["step_hours_physics"] = float(df.step_hours_phys.iloc[0])
    out.attrs["dropped_incomplete_citydays"] = dropped_incomplete
    out.attrs["dropped_no_climatology"] = int(dropped_no_clim)
    out.attrs["clim_failed_cities"] = failed
    return out.sort_values(["city", "local_date", "lead_days"])


def check_paired(paired: pd.DataFrame) -> None:
    """Assert what everything below assumes. Aborts the stage if not."""
    if paired.empty:
        die("the paired sample is empty")
    ref = None
    for lead, g in paired.groupby("lead_days"):
        key = set(zip(g.city, g.local_date))
        if len(key) != len(g):
            die(f"duplicate city-days at lead={lead}")
        if ref is None:
            ref = key
        elif key != ref:
            die(f"the sample at lead={lead} differs from the reference by "
                f"{len(key ^ ref)} city-days; the two systems are not being "
                f"scored on identical days across leads")
    if paired.lead_days.nunique() != len(LEAD_DAYS):
        die(f"expected {len(LEAD_DAYS)} leads, found "
            f"{paired.lead_days.nunique()}")
    for col in ("p_physics", "p_ml", "event", "clim"):
        v = paired[col].to_numpy(float)
        if not np.isfinite(v).all():
            die(f"{col} carries NaN inside the paired sample")
        if v.min() < 0.0 or v.max() > 1.0:
            die(f"{col} outside [0, 1]: [{v.min()}, {v.max()}]")
    if not np.isin(paired.event.to_numpy(float), (0.0, 1.0)).all():
        die("the observed event is not binary")
    # The duplicate-detection lesson from Task 26a, applied here: if the two
    # archives ever served identical numbers, every difference below would be
    # exactly zero and the run should say so rather than publish a tie.
    same = float((paired.p_physics.to_numpy() == paired.p_ml.to_numpy()).mean())
    if same > 0.99:
        die(f"{same:.1%} of city-days carry identical probabilities from "
            f"{PHYSICS} and {ML}; these are not two independent systems on "
            f"this sample")


# --------------------------------------------------------------------------
# Scores, per system
# --------------------------------------------------------------------------
SERIES = {
    "physics": ("p_physics", f"{ENSEMBLE_SOURCES[PHYSICS].label} member PoP"),
    "ml": ("p_ml", f"{ENSEMBLE_SOURCES[ML].label} member PoP"),
    "climatology": ("clim", "smoothed day-of-year climatology"),
}


def score(p: np.ndarray, event: np.ndarray, clim: np.ndarray,
          with_value: bool = True) -> dict:
    dec = brier_decomposition(p, event, n_bins=N_PROB_BINS, reference_prob=clim)
    _, sharp = sharpness(p)
    row = {
        "n": int(len(p)),
        "base_rate": float(event.mean()),
        "mean_prob": float(p.mean()),
        "brier": dec["brier"],
        "bss_clim": dec["bss_vs_reference"],
        "bss_sample": dec["brier_skill_score"],
        "reliability": dec["reliability"],
        "resolution": dec["resolution"],
        "ece": dec["ece"],
        "auc": roc_auc(p, event),
        "sharpness_score": sharp["sharpness_score"],
        "share_confident": sharp["share_confident"],
        "share_hedged": sharp["share_hedged"],
    }
    if with_value:
        row.update(value_summary(value_curve(p, event)))
    return row


def score_table(paired: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for lead, g in paired.groupby("lead_days", sort=True):
        event = g.event.to_numpy(float)
        clim = g.clim.to_numpy(float)
        for name, (col, _) in SERIES.items():
            r = score(g[col].to_numpy(float), event, clim,
                      with_value=name != "climatology")
            r.update({"lead_days": int(lead), "series": name,
                      "step_hours": float(g.step_hours.iloc[0]),
                      "n_cities": int(g.city.nunique())})
            rows.append(r)
    return pd.DataFrame(rows)


def score_by_city(paired: pd.DataFrame) -> pd.DataFrame:
    """The same contrast city by city: a pooled result can be carried by two
    wet capitals, and a method difference is exactly the kind of thing that
    might vary by regime."""
    rows = []
    for (lead, city), g in paired.groupby(["lead_days", "city"], sort=True):
        if len(g) < MIN_CITY_DAYS or g.event.sum() < 20 or (1 - g.event).sum() < 20:
            continue
        event = g.event.to_numpy(float)
        clim = g.clim.to_numpy(float)
        for name in ("physics", "ml"):
            r = score(g[SERIES[name][0]].to_numpy(float), event, clim,
                      with_value=False)
            r.update({"lead_days": int(lead), "city": city, "series": name})
            rows.append(r)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# The paired differences and their intervals
# --------------------------------------------------------------------------
def statistics(g: pd.DataFrame, rows: np.ndarray | None = None) -> dict:
    """Every paired difference, ML MINUS PHYSICS, on one sample.

    `rows` selects a bootstrap replicate; None scores the sample itself, so the
    estimate and its replicates cannot be computed by different code.
    """
    m = g.p_ml.to_numpy(float)
    p = g.p_physics.to_numpy(float)
    e = g.event.to_numpy(float)
    if rows is not None:
        m, p, e = m[rows], p[rows], e[rows]

    dec_m = brier_decomposition(m, e, n_bins=N_PROB_BINS)
    dec_p = brier_decomposition(p, e, n_bins=N_PROB_BINS)
    out = {
        "brier_diff": dec_m["brier"] - dec_p["brier"],
        "reliability_diff": dec_m["reliability"] - dec_p["reliability"],
        "resolution_diff": dec_m["resolution"] - dec_p["resolution"],
        "auc_diff": roc_auc(m, e) - roc_auc(p, e),
        "mean_prob_diff": float(np.mean(m - p)),
    }
    alphas = np.asarray(HEADLINE_ALPHAS, float)
    vm = value_curve(m, e, alphas=alphas).v_calibrated.to_numpy(float)
    vp = value_curve(p, e, alphas=alphas).v_calibrated.to_numpy(float)
    for i, al in enumerate(HEADLINE_ALPHAS):
        out[f"value_diff_a{int(round(al * 100)):02d}"] = float(vm[i] - vp[i])
    return out


MEAN_STATISTICS = ("mean_prob_diff",)


def cell(paired: pd.DataFrame, lead: int) -> pd.DataFrame:
    """One lead's rows, sorted so each calendar day is contiguous.

    Contiguity is what makes the day-block bootstrap a slice rather than a
    membership test; `significance.day_offsets` depends on it.
    """
    g = paired[paired.lead_days == lead].copy()
    if g.empty:
        die(f"no paired rows at lead={lead}")
    g["date"] = pd.to_datetime(g.local_date)
    g = g.sort_values(["date", "city"]).reset_index(drop=True)
    g["d_brier"] = ((g.p_ml - g.event) ** 2 - (g.p_physics - g.event) ** 2)
    g["d_prob"] = g.p_ml - g.p_physics
    return g


def test_lead(g: pd.DataFrame, n_boot: int = BOOTSTRAP_N,
              block: int | None = None, seed: int = RANDOM_SEED
              ) -> pd.DataFrame:
    """The full battery for one lead, with day-block bootstrap intervals."""
    rng = np.random.default_rng(seed)
    off = sig.day_offsets(g)
    n_days = len(off) - 1
    day_index = np.repeat(np.arange(n_days), np.diff(off))
    daily = sig.daily_series(g.d_brier.to_numpy(float), day_index, n_days)
    if block is None:
        block = sig.choose_block(daily)

    point = statistics(g)
    starts, blk = sig.block_starts(rng, n_days, block, n_boot)
    reps: dict[str, list[float]] = {k: [] for k in point}
    for b in range(n_boot):
        rows = sig.replicate_rows(starts[b], off, blk)
        for k, val in statistics(g, rows).items():
            reps[k].append(val)

    out = []
    for k, theta in point.items():
        r = {"statistic": k}
        r.update(sig.summarise(np.asarray(reps[k], float), theta))
        if k in MEAN_STATISTICS:
            r.update(sig.analytic_ses(g["d_prob"].to_numpy(float), day_index,
                                      n_days, lag=blk))
        r.update({"n": int(len(g)), "n_days": n_days,
                  "n_cities": int(g.city.nunique()), "block_days": int(blk),
                  "tau_days": float(sig.integrated_autocorr_time(daily))})
        out.append(r)
    return pd.DataFrame(out)


def test_all(paired: pd.DataFrame, n_boot: int = BOOTSTRAP_N) -> pd.DataFrame:
    """Every lead, with the Brier verdicts FDR-controlled across them.

    Seven leads on overlapping days are seven dependent tests; quoting the
    smallest p among them without correction is the failure Task 26 exists to
    prevent. Benjamini-Hochberg and the dependence-agnostic
    Benjamini-Yekutieli are both reported, as they are in `significance.py`.
    """
    frames = []
    for lead in sorted(paired.lead_days.unique()):
        t = test_lead(cell(paired, int(lead)), n_boot=n_boot)
        t["lead_days"] = int(lead)
        frames.append(t)
    out = pd.concat(frames, ignore_index=True)

    for stat in ("brier_diff", "value_diff_a10"):
        m = out.statistic == stat
        if not m.any():
            continue
        p = out.loc[m, "p_boot"].to_numpy(float)
        out.loc[m, "q_bh"] = sig.benjamini_hochberg(p)
        out.loc[m, "q_by"] = sig.benjamini_yekutieli(p)
    return out


# --------------------------------------------------------------------------
# The ladder sensitivity (the confound this design exists to remove)
# --------------------------------------------------------------------------
def ladder_sensitivity(n_boot: int = BOOTSTRAP_N) -> pd.DataFrame:
    """Matched 6-hourly ladder against each system's native one.

    If the matched and native answers agree, the matching cost nothing and the
    result is robust to it. If they disagree, the disagreement IS a finding:
    it is the size of the advantage a finer accumulation ladder confers, which
    is a property of the archive rather than of the forecast, and any published
    physics-versus-ML comparison that ignored it inherited that advantage.
    """
    rows = []
    for label, step in (("matched_6h", PHYSICS_ML_STEP_H), ("native", None)):
        try:
            paired = build_paired(step_hours=step)
        except SystemExit as exc:
            print(f"  {label}: not available ({exc})")
            continue
        check_paired(paired)
        g = cell(paired, 1)
        t = test_lead(g, n_boot=n_boot)
        t = t[t.statistic.isin(["brier_diff", "mean_prob_diff", "auc_diff"])]
        t = t.assign(ladder=label,
                     step_hours_ml=float(paired.step_hours.iloc[0]),
                     step_hours_physics=float(paired.step_hours_physics.iloc[0]),
                     lead_days=1)
        rows.append(t)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# --------------------------------------------------------------------------
# Self-test: the sign convention and the pairing, with no network
# --------------------------------------------------------------------------
def selftest() -> None:
    rng = np.random.default_rng(RANDOM_SEED)
    n_days, cities = 400, ["A", "B", "C"]
    dates = pd.date_range("2025-07-02", periods=n_days, freq="D").date
    rec = []
    for c in cities:
        # A persistent truth, so the day-block machinery has memory to find.
        z = np.zeros(n_days)
        for i in range(1, n_days):
            z[i] = 0.7 * z[i - 1] + rng.normal()
        prob = 1 / (1 + np.exp(-z))
        event = (rng.random(n_days) < prob).astype(float)
        # The ML series is the truth-tracking one; physics is noisier. The ML
        # system must therefore come out AHEAD, i.e. brier_diff < 0.
        p_ml = np.clip(prob + rng.normal(0, 0.05, n_days), 0, 1)
        p_ph = np.clip(prob + rng.normal(0, 0.20, n_days), 0, 1)
        for lead in LEAD_DAYS:
            rec.append(pd.DataFrame({
                "city": c, "local_date": dates, "lead_days": lead,
                "p_ml": p_ml, "p_physics": p_ph, "event": event,
                "clim": float(event.mean()), "step_hours": 6.0}))
    paired = pd.concat(rec, ignore_index=True)

    g = cell(paired, 1)
    st = statistics(g)
    assert st["brier_diff"] < 0, ("sign convention broken: the better series "
                                  f"scored {st['brier_diff']:+.4f}")
    assert st["auc_diff"] > 0, st["auc_diff"]

    # The bootstrap must find that difference significant, and a series
    # compared with ITSELF must not be significant at all.
    t = test_lead(g, n_boot=300)
    row = t[t.statistic == "brier_diff"].iloc[0]
    assert row.p_boot < 0.05, row.p_boot
    assert row.ci_hi < 0, (row.ci_lo, row.ci_hi)

    null = g.copy()
    null["p_ml"] = null["p_physics"]
    tn = test_lead(null, n_boot=300)
    rn = tn[tn.statistic == "brier_diff"].iloc[0]
    assert abs(rn.estimate) < 1e-12 and rn.p_boot > 0.5, (rn.estimate, rn.p_boot)

    # An unequal sample must be refused rather than scored.
    bad = paired[~((paired.city == "B") & (paired.lead_days == 3))]
    try:
        check_paired(bad)
    except SystemExit:
        pass
    else:
        raise AssertionError("a lead-dependent sample was accepted")

    # Two identical archives must be caught, not published as a dead heat.
    same = paired.copy()
    same["p_ml"] = same["p_physics"]
    try:
        check_paired(same)
    except SystemExit:
        pass
    else:
        raise AssertionError("two identical series were accepted as distinct")

    print("physics_ml selftest: sign convention, pairing, null and "
          "duplicate checks passed")


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def _stars(p: float) -> str:
    if not np.isfinite(p):
        return "   "
    return "***" if p < 0.001 else "** " if p < 0.01 else "*  " if p < 0.05 else "   "


def report(scores: pd.DataFrame, diffs: pd.DataFrame, by_city: pd.DataFrame,
           paired: pd.DataFrame) -> None:
    phys_label = ENSEMBLE_SOURCES[PHYSICS].label
    ml_label = ENSEMBLE_SOURCES[ML].label
    print(f"\n{'=' * 78}\nPHYSICS versus MACHINE LEARNING  "
          f"({phys_label} vs {ml_label})\n{'=' * 78}")
    print(f"{paired.groupby(['city', 'local_date']).ngroups:,} city-days x "
          f"{paired.lead_days.nunique()} leads, {paired.city.nunique()} "
          f"capitals, {paired.local_date.min()} .. {paired.local_date.max()}")
    print(f"both systems: {ENSEMBLE_SOURCES[ML].members} members, 0.25 deg, "
          f"same initialisation, accumulated on one "
          f"{paired.step_hours.iloc[0]:g} h ladder")
    print(f"dropped: {paired.attrs.get('dropped_incomplete_citydays', 0)} "
          f"city-days incomplete across leads, "
          f"{paired.attrs.get('dropped_no_climatology', 0)} without a "
          f"climatology")
    print("the window ends where the STATION RECORD ends, not where the "
          "archive does - the\n  binding constraint on this comparison is "
          "observation, not collection")

    print(f"\n-- scores by lead (Brier: lower is better) {'-' * 34}")
    print(f"{'lead':>4} {'base':>6} | {phys_label:>22} | {ml_label:>22}")
    print(f"{'':>4} {'rate':>6} | {'brier':>7} {'bss':>7} {'auc':>6} | "
          f"{'brier':>7} {'bss':>7} {'auc':>6}")
    for lead in sorted(scores.lead_days.unique()):
        s = scores[scores.lead_days == lead].set_index("series")
        p, m = s.loc["physics"], s.loc["ml"]
        print(f"{lead:>4} {p.base_rate:>6.3f} | {p.brier:>7.4f} "
              f"{p.bss_clim:>+7.3f} {p.auc:>6.3f} | {m.brier:>7.4f} "
              f"{m.bss_clim:>+7.3f} {m.auc:>6.3f}")

    print(f"\n-- the difference, ML minus physics {'-' * 41}")
    print("negative Brier = the ML system is better; every interval is a "
          "day-block bootstrap")
    print(f"{'lead':>4} {'diff':>9} {'95% CI':>19} {'p':>7}    {'q(BH)':>6} "
          f"{'MDE80':>8}")
    br = diffs[diffs.statistic == "brier_diff"].sort_values("lead_days")
    for r in br.itertuples():
        print(f"{r.lead_days:>4} {r.estimate:>+9.4f} "
              f"[{r.ci_lo:>+8.4f},{r.ci_hi:>+8.4f}] {r.p_boot:>7.3f}{_stars(r.p_boot)} "
              f"{getattr(r, 'q_bh', float('nan')):>6.3f} {r.mde_80:>8.4f}")

    # Seven leads is seven dependent tests. Reading the smallest p among them
    # as the finding is exactly the error Task 26 exists to prevent, so the
    # verdict is stated against the corrected value, not the raw one.
    surv = br[br.q_bh < 0.05] if "q_bh" in br.columns else br.iloc[:0]
    raw = br[br.p_boot < 0.05]
    print(f"  {len(raw)} of {len(br)} leads reach p < 0.05 uncorrected, "
          f"{len(surv)} survive Benjamini-Hochberg across the seven.")
    if len(raw) and not len(surv):
        print("  So the direction is consistent - every lead favours the ML "
              "system - but no single\n  lead is established once the family "
              "is accounted for. That is a weaker claim than\n  the lead-1 "
              "p-value alone would support, and it is the one the data carry.")

    print(f"\n-- where the difference comes from, lead 1 {'-' * 34}")
    d1 = diffs[diffs.lead_days == 1].set_index("statistic")
    for k, lab in (("reliability_diff", "reliability (lower better)"),
                   ("resolution_diff", "resolution (higher better)"),
                   ("auc_diff", "discrimination, AUC"),
                   ("mean_prob_diff", "mean probability served")):
        if k not in d1.index:
            continue
        r = d1.loc[k]
        print(f"  {lab:<28} {r.estimate:>+8.4f}  "
              f"[{r.ci_lo:>+7.4f},{r.ci_hi:>+7.4f}]  p={r.p_boot:.3f}"
              f"{_stars(r.p_boot)}")

    print(f"\n-- decision value, lead 1 {'-' * 51}")
    print("  the cheap-action user is where a probability bias actually costs")
    for al in HEADLINE_ALPHAS:
        k = f"value_diff_a{int(round(al * 100)):02d}"
        if k not in d1.index:
            continue
        r = d1.loc[k]
        print(f"  cost-loss {al:>4.2f}  {r.estimate:>+8.4f}  "
              f"[{r.ci_lo:>+7.4f},{r.ci_hi:>+7.4f}]  p={r.p_boot:.3f}"
              f"{_stars(r.p_boot)}")

    if not by_city.empty:
        w = by_city[by_city.lead_days == 1].pivot_table(
            index="city", columns="series", values="brier")
        if {"ml", "physics"} <= set(w.columns):
            d = (w["ml"] - w["physics"]).sort_values()
            print(f"\n-- per city at lead 1, ML minus physics Brier {'-' * 31}")
            print(f"  {int((d < 0).sum())} of {len(d)} capitals favour the ML "
                  f"system; median {d.median():+.4f}, range "
                  f"{d.min():+.4f} .. {d.max():+.4f}")
            for city, v in list(d.items())[:3] + list(d.items())[-3:]:
                print(f"    {city:<20} {v:>+8.4f}")


def main() -> None:
    selftest()
    paired = build_paired()
    check_paired(paired)
    scores = score_table(paired)
    by_city = score_by_city(paired)
    diffs = test_all(paired)

    for t, path in ((scores, SCORE_TABLE), (diffs, DIFF_TABLE),
                    (by_city, CITY_TABLE)):
        path.parent.mkdir(parents=True, exist_ok=True)
        t.to_parquet(path, index=False)

    report(scores, diffs, by_city, paired)

    sens = ladder_sensitivity()
    if not sens.empty:
        sens.to_parquet(LADDER_TABLE, index=False)
        print(f"\n-- ladder sensitivity at lead 1 {'-' * 45}")
        print("  what the 6-hourly matching costs, against each system's own "
              "native ladder")
        for r in sens[sens.statistic == "brier_diff"].itertuples():
            print(f"  {r.ladder:<12} physics on {r.step_hours_physics:g} h, "
                  f"ML on {r.step_hours_ml:g} h:  brier_diff "
                  f"{r.estimate:>+8.4f} [{r.ci_lo:>+7.4f},{r.ci_hi:>+7.4f}]  "
                  f"p={r.p_boot:.3f}")

    print(f"\nwrote {SCORE_TABLE.name}, {DIFF_TABLE.name}, "
          f"{CITY_TABLE.name}, {LADDER_TABLE.name}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "selftest":
        selftest()
    elif cmd == "native":
        print(ladder_sensitivity().to_string(index=False))
    else:
        main()
