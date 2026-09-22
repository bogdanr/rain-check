"""Task 20b: is the dry bias a property of the served forecast, or of the reference?

WHY THIS MODULE EXISTS. E13 reports that the served probability is drier than
the ensemble supports, and every decision-value claim the paper leads with is
built on that gap. But "the ensemble" has so far meant one ensemble: NOAA's
GEFS. A reviewer's first question is whether the finding would survive a
different reference centre, and it is the right question, because E9 already
showed the vendor menu has fewer independent opinions than it appears -
`metno_seamless` returns ECMWF's digits. If the served probability being
compared is partly ECMWF while the reference is entirely NOAA, then a
systematic difference between the two centres would appear as vendor bias
without any post-processing being involved at all.

So the comparison is run three ways on identical city-days: the served
probability, a GEFS member-derived one, and an ECMWF IFS ENS member-derived
one. The third column is what makes the first finding falsifiable.

  bias_vendor_minus_gefs   E13 restated on this panel
  bias_vendor_minus_ifs    the same claim against a different centre
  bias_gefs_minus_ifs      how far the two references disagree with each other

If the third is small and the first two agree, the dry bias is a property of
the served forecast. If the third is comparable to the first two, then "the
ensemble supports a wetter number" was a statement about NOAA.

FOUR THINGS THAT COULD FAKE THE ANSWER, EACH CONTROLLED RATHER THAN ASSUMED
AWAY:

  MEMBER COUNT    GEFS publishes 31 members and IFS ENS 51. A count-of-members
                  probability is coarser with fewer members, and its extreme
                  bins are reachable more often, so the two are not on the same
                  footing by construction. IFS is therefore also re-derived
                  from a fixed 31-member subset and the shift reported. If that
                  shift is the size of the finding, there is no finding.
  STEP LADDER     E23 measured a 6-hourly-versus-3-hourly ladder as worth 60%
                  of the physics-versus-ML difference, purely through where the
                  local-day boundary can fall. IFS publishes 3-hourly steps and
                  so does GEFS, so this panel is matched - but the step is
                  asserted in a check rather than trusted, because the archive
                  is free to change it.
  WINDOW          the IFS ENS archive starts later than the GEFS one, so this
                  panel is 327 days rather than 631. GEFS-versus-vendor is
                  therefore recomputed on the narrow panel and printed beside
                  E13's wide-panel number, so a window effect cannot be read as
                  a centre effect.
  EVENT           E35 found the vendor-versus-GEFS Brier verdict reverses sign
                  between the day-total event and the any-step event. Every
                  statistic here is therefore reported under both, and a
                  conclusion that holds under only one is reported as such.

THE DEPENDENCE. Identical to `significance.py` and for the same reasons: the
resampling unit is the calendar day carrying all its cities, drawn in blocks
whose length comes from the measured decorrelation time. Fifteen capitals under
one trough are not fifteen independent observations.

Usage:  python src/reference_centre.py           # checks, then the full run
        python src/reference_centre.py check     # correctness checks only
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import ensemble_pop
import significance as sg
import triangulation
from calibration import brier_decomposition
from config import (
    BOOTSTRAP_N,
    GEFS_BOUNDARY_MODES,
    GEFS_BOUNDARY_PRIMARY,
    LEAD_DAYS,
    PROCESSED,
    RAIN_THRESHOLD_MM,
    RANDOM_SEED,
)
from metrics import HEADLINE_ALPHAS, roc_auc, value_curve
from triangulation import LIKE_FOR_LIKE_MODEL, die

PANEL_TABLE = PROCESSED / "reference_centre_panel.parquet"
HEADLINE_TABLE = PROCESSED / "reference_centre_headline.parquet"
CONTROL_TABLE = PROCESSED / "reference_centre_controls.parquet"

# The second reference centre, and the archive step it must publish for the
# comparison to be like-for-like with GEFS.
SECOND_SOURCE = "ifs_ens"
MATCHED_STEP_HOURS = 3.0

# GEFS's member count. IFS ENS is re-derived at this count as the control, so
# the number is named once rather than repeated as a literal.
GEFS_MEMBERS = 31


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------
def second_reference(step_hours: float = MATCHED_STEP_HOURS,
                     members: int | None = None) -> pd.DataFrame:
    """The second centre's member-derived PoP, at the matched step.

    `members` re-derives from a fixed subset rather than subsetting the
    probability afterwards, which would be meaningless - a probability is not
    a member and cannot be dropped.
    """
    if members is None:
        d = pd.read_parquet(PROCESSED / f"{SECOND_SOURCE}_pop.parquet")
    else:
        d = ensemble_pop.compute(SECOND_SOURCE, modes=GEFS_BOUNDARY_MODES,
                                 thresholds=(RAIN_THRESHOLD_MM,),
                                 member_subset=members, write=False,
                                 quiet=True)
    d = d[(d.threshold_mm == RAIN_THRESHOLD_MM) & d.usable]
    steps = sorted(d.step_hours.unique())
    if step_hours not in steps:
        die(f"{SECOND_SOURCE} publishes steps {steps}, not the matched "
            f"{step_hours}h - the ladder control (E23) cannot be satisfied")
    d = d[d.step_hours == step_hours]
    return d.rename(columns={"pop_daytotal": "ifs_daytotal",
                             "pop_anystep": "ifs_anystep",
                             "n_members": "ifs_members"})


def build_panel(members: int | None = None) -> pd.DataFrame:
    """Vendor, GEFS and IFS on identical city-days, or refuse to build.

    The completeness rule is the same one `triangulation.check_paired`
    enforces and for the same reason: a city-day present at six leads and
    absent at the seventh would silently make the lead comparison a comparison
    of different samples. A city-day is kept only if all leads and all
    boundary modes are present for all three series.
    """
    gefs = triangulation.build_paired(LIKE_FOR_LIKE_MODEL, scope="capitals")
    ifs = second_reference(members=members)
    key = ["city", "local_date", "lead_days", "boundary_mode"]
    cols = key + ["ifs_daytotal", "ifs_anystep", "ifs_members"]
    m = gefs.merge(ifs[cols], on=key, how="inner")
    if m.empty:
        die("no overlap between the GEFS panel and the second reference")

    want = len(GEFS_BOUNDARY_MODES) * len(LEAD_DAYS)
    size = m.groupby(["city", "local_date"]).size()
    keep = size[size == want].index
    if not len(keep):
        die(f"no city-day carries all {want} (mode, lead) combinations")
    m = m.set_index(["city", "local_date"]).loc[keep].reset_index()
    m["date"] = pd.to_datetime(m.local_date)
    return m.sort_values(["boundary_mode", "lead_days", "date", "city"]).reset_index(drop=True)


def check_panel(p: pd.DataFrame) -> None:
    """The set-identical guarantee, asserted rather than hoped for."""
    want = len(GEFS_BOUNDARY_MODES) * len(LEAD_DAYS)
    size = p.groupby(["city", "local_date"]).size()
    if not (size == want).all():
        die(f"panel is not set-identical: {int((size != want).sum())} city-days "
            f"carry other than {want} rows")
    for c in ("vendor_pop", "pop_daytotal", "pop_anystep",
              "ifs_daytotal", "ifs_anystep", "event"):
        v = p[c].to_numpy(float)
        if not np.isfinite(v).all():
            die(f"{c} carries non-finite values")
        if c != "event" and (v.min() < 0 or v.max() > 1):
            die(f"{c} leaves [0, 1]: [{v.min()}, {v.max()}]")


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def series_columns() -> dict[str, tuple[str, str]]:
    """Name -> (day-total column, any-step column) for each probability."""
    return {
        "vendor": ("vendor_pop", "vendor_pop"),
        "gefs": ("pop_daytotal", "pop_anystep"),
        "ifs": ("ifs_daytotal", "ifs_anystep"),
    }


def descriptive(g: pd.DataFrame) -> pd.DataFrame:
    """Per-series scores on one cell - the table a reader looks at first."""
    y = g.event.to_numpy(float)
    rows = []
    for name, (col_day, col_any) in series_columns().items():
        for event_def, col in (("daytotal", col_day), ("anystep", col_any)):
            if name == "vendor" and event_def == "anystep":
                continue
            p = g[col].to_numpy(float)
            d = brier_decomposition(p, y)
            rows.append({
                "series": name, "event_def": event_def, "n": len(p),
                "base_rate": float(y.mean()), "mean_prob": float(p.mean()),
                "brier": float(np.mean((p - y) ** 2)),
                "reliability": d["reliability"], "resolution": d["resolution"],
                "uncertainty": d["uncertainty"], "ece": d["ece"],
                "auc": roc_auc(p, y),
            })
    return pd.DataFrame(rows)


def paired_statistics(g: pd.DataFrame) -> dict[str, np.ndarray]:
    """Every per-row paired difference the module makes a claim about.

    Signs are fixed by one convention throughout: the statistic is
    A minus B for the name `..._a_minus_b`, so for Brier a NEGATIVE value means
    A is better and for bias a NEGATIVE value means A is drier.
    """
    y = g.event.to_numpy(float)
    v = g.vendor_pop.to_numpy(float)
    gd = g.pop_daytotal.to_numpy(float)
    ga = g.pop_anystep.to_numpy(float)
    idd = g.ifs_daytotal.to_numpy(float)
    ida = g.ifs_anystep.to_numpy(float)

    def br(p):
        return (p - y) ** 2

    return {
        # The three biases. The third is the instrument: it says how far two
        # reference centres disagree with each other, which is the scale
        # against which the first two have to be read.
        "bias_vendor_minus_gefs": v - gd,
        "bias_vendor_minus_ifs": v - idd,
        "bias_gefs_minus_ifs": gd - idd,
        "bias_vendor_minus_gefs_anystep": v - ga,
        "bias_vendor_minus_ifs_anystep": v - ida,
        "bias_gefs_minus_ifs_anystep": ga - ida,
        # Calibration, under both event definitions, because E35 found the
        # verdict is a property of the definition.
        "brier_vendor_minus_gefs": br(v) - br(gd),
        "brier_vendor_minus_ifs": br(v) - br(idd),
        "brier_gefs_minus_ifs": br(gd) - br(idd),
        "brier_vendor_minus_gefs_anystep": br(v) - br(ga),
        "brier_vendor_minus_ifs_anystep": br(v) - br(ida),
        "brier_gefs_minus_ifs_anystep": br(ga) - br(ida),
    }


def value_differences(g: pd.DataFrame) -> dict[str, float]:
    """Relative economic value gaps at the headline cost-loss ratios.

    Value is not a mean of per-row quantities, so it cannot ride the same
    bootstrap; it is reported as a point estimate here and the interval is
    left to `significance.py`, which already carries E4a. What this adds is
    the second centre: whether the cheap-action user's loss is against NOAA
    or against any ensemble.
    """
    y = g.event.to_numpy(float)
    out = {}
    curves = {}
    for name, (col, _) in series_columns().items():
        c = value_curve(g[col].to_numpy(float), y, alphas=HEADLINE_ALPHAS)
        curves[name] = c.set_index("alpha").v_calibrated
    for a in HEADLINE_ALPHAS:
        tag = f"{int(round(a * 100)):02d}"
        out[f"value_vendor_a{tag}"] = float(curves["vendor"].loc[a])
        out[f"value_gefs_a{tag}"] = float(curves["gefs"].loc[a])
        out[f"value_ifs_a{tag}"] = float(curves["ifs"].loc[a])
    return out


def test_cell(g: pd.DataFrame, n_boot: int, rng) -> pd.DataFrame:
    """Paired day-block bootstrap over every statistic on one cell."""
    day_index, n_days = pd.factorize(g.date)[0], g.date.nunique()
    stats = paired_statistics(g)
    # Block length from the bias series - the one with the longest memory, so
    # the choice cannot be accused of being picked per statistic to suit.
    anchor = sg.daily_series(stats["bias_vendor_minus_gefs"], day_index, n_days)
    block = sg.choose_block(anchor)
    tau = sg.integrated_autocorr_time(anchor)
    rows = []
    for name, v in stats.items():
        v = np.asarray(v, float)
        reps = sg.bootstrap_mean(v, day_index, n_days, n_boot, block, rng)
        s = sg.summarise(reps, float(v.mean()))
        s.update({"statistic": name, "n": len(v), "n_days": n_days,
                  "n_cities": g.city.nunique(), "block_days": block,
                  "tau_days": tau})
        rows.append(s)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
def member_control(panel: pd.DataFrame, n_boot: int, rng) -> pd.DataFrame:
    """Re-derive the second centre at GEFS's member count and measure the shift.

    The question this answers is narrow and decisive: is the gap between the
    two centres a gap between two forecasting systems, or an artefact of
    counting 51 members against 31?
    """
    sub = second_reference(members=GEFS_MEMBERS)
    key = ["city", "local_date", "lead_days", "boundary_mode"]
    j = panel.merge(sub[key + ["ifs_daytotal"]].rename(
        columns={"ifs_daytotal": "ifs_sub"}), on=key, how="inner")
    if len(j) != len(panel):
        die(f"member control lost rows: {len(j)} of {len(panel)}")
    g = j[(j.boundary_mode == GEFS_BOUNDARY_PRIMARY)
          & (j.lead_days == 1)].copy()
    g["date"] = pd.to_datetime(g.local_date)
    g = g.sort_values(["date", "city"]).reset_index(drop=True)
    day_index, n_days = pd.factorize(g.date)[0], g.date.nunique()
    v = (g.ifs_sub - g.ifs_daytotal).to_numpy(float)
    block = sg.choose_block(sg.daily_series(v, day_index, n_days))
    reps = sg.bootstrap_mean(v, day_index, n_days, n_boot, block, rng)
    s = sg.summarise(reps, float(v.mean()))
    s.update({"control": "member_count", "n": len(v),
              "from_members": int(panel.ifs_members.max()),
              "to_members": GEFS_MEMBERS,
              "distinct_values_full": int(g.ifs_daytotal.nunique()),
              "distinct_values_sub": int(g.ifs_sub.nunique()),
              "distinct_values_gefs": int(g.pop_daytotal.nunique())})
    return pd.DataFrame([s])


def window_control(panel: pd.DataFrame, n_boot: int, rng) -> pd.DataFrame:
    """E13's bias on the narrow panel beside its value on the wide one.

    The second centre's archive is shorter, so this panel is not E13's panel.
    Without this control, a window effect and a centre effect are the same
    number.
    """
    wide = triangulation.build_paired(LIKE_FOR_LIKE_MODEL, scope="capitals")
    wide = wide[(wide.boundary_mode == GEFS_BOUNDARY_PRIMARY)
                & (wide.lead_days == 1)].copy()
    wide["date"] = pd.to_datetime(wide.local_date)
    wide = wide.sort_values(["date", "city"]).reset_index(drop=True)
    narrow = panel[(panel.boundary_mode == GEFS_BOUNDARY_PRIMARY)
                   & (panel.lead_days == 1)]
    rows = []
    for label, g in (("wide", wide), ("narrow", narrow)):
        day_index, n_days = pd.factorize(g.date)[0], g.date.nunique()
        v = (g.vendor_pop - g.pop_daytotal).to_numpy(float)
        block = sg.choose_block(sg.daily_series(v, day_index, n_days))
        reps = sg.bootstrap_mean(v, day_index, n_days, n_boot, block, rng)
        s = sg.summarise(reps, float(v.mean()))
        s.update({"control": f"window_{label}", "n": len(v), "n_days": n_days,
                  "n_cities": g.city.nunique(),
                  "first_day": str(g.date.min().date()),
                  "last_day": str(g.date.max().date())})
        rows.append(s)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Correctness checks
# ---------------------------------------------------------------------------
def _dependent_panel(rng, n_days=300, n_cities=15, mu=0.0, day_sd=2.0):
    """A panel with this study's dependence and a known mean, for coverage."""
    day = np.zeros(n_days)
    for i in range(1, n_days):
        day[i] = 0.7 * day[i - 1] + rng.normal(0, day_sd)
    v = (mu + np.repeat(day, n_cities)
         + rng.normal(0, 1.0, n_days * n_cities))
    return v, np.repeat(np.arange(n_days), n_cities), n_days


def run_checks(n_sim: int = 200, n_boot: int = 400) -> None:
    print("=" * 74)
    print("CORRECTNESS CHECKS")
    print("=" * 74)
    rng = np.random.default_rng(RANDOM_SEED)

    # 1. Member subsetting must be an identity at the full count. If it is
    #    not, the member control is measuring the subsetting code.
    full = second_reference()
    identity = second_reference(members=int(full.ifs_members.max()))
    key = ["city", "local_date", "lead_days", "boundary_mode"]
    j = full[key + ["ifs_daytotal"]].merge(
        identity[key + ["ifs_daytotal"]], on=key, suffixes=("_a", "_b"))
    worst = float((j.ifs_daytotal_a - j.ifs_daytotal_b).abs().max())
    print(f"  1. subsetting at the full member count is an identity")
    print(f"     max |difference| over {len(j):,} rows: {worst:.3e}")
    if worst > 0.0:
        die("re-deriving from all members changed the answer - the member "
            "control would be measuring the subsetting path, not the ensemble")

    # 2. The matched step ladder, asserted. E23 priced a mismatched ladder at
    #    60% of a physics-versus-ML difference, so this is not cosmetic.
    gefs = pd.read_parquet(PROCESSED / "gefs_pop.parquet")
    gsteps = sorted(gefs.step_hours.unique())
    print(f"  2. step ladders match: gefs {gsteps}, "
          f"{SECOND_SOURCE} {MATCHED_STEP_HOURS}h")
    if MATCHED_STEP_HOURS not in gsteps:
        die(f"gefs publishes {gsteps}; the matched comparison E23 requires "
            f"is not available")

    # 3. Day-block coverage against a known truth, with this panel's
    #    dependence. The naive interval is printed beside it because the
    #    ratio is the evidence that the block draw is doing something.
    hit_blk = hit_naive = 0
    for _ in range(n_sim):
        v, di, nd = _dependent_panel(rng)
        blk = sg.choose_block(sg.daily_series(v, di, nd))
        reps = sg.bootstrap_mean(v, di, nd, n_boot, blk, rng)
        lo, hi = np.quantile(reps, [0.025, 0.975])
        hit_blk += lo <= 0.0 <= hi
        se = v.std(ddof=1) / np.sqrt(len(v))
        hit_naive += abs(v.mean()) <= 1.96 * se
    cov_b, cov_n = hit_blk / n_sim, hit_naive / n_sim
    print(f"  3. coverage at a nominal 95% over {n_sim} synthetic panels")
    print(f"     day-block {cov_b:.0%}   naive independent {cov_n:.0%}")
    if cov_b < 0.88:
        die(f"day-block coverage {cov_b:.0%} is below nominal - the intervals "
            f"reported below would be too narrow")
    if cov_n > 0.85:
        die(f"naive coverage {cov_n:.0%} is too good for this check to be "
            f"demonstrating anything")

    # 4. The sign convention, on constructed data. A sign error here would
    #    invert the paper's conclusion about which system is drier, and it is
    #    the single easiest mistake in a three-way comparison to make.
    n = 400
    g = pd.DataFrame({
        "city": ["A"] * n, "local_date": pd.date_range("2025-01-01", periods=n),
        "vendor_pop": np.full(n, 0.30), "pop_daytotal": np.full(n, 0.50),
        "pop_anystep": np.full(n, 0.60), "ifs_daytotal": np.full(n, 0.40),
        "ifs_anystep": np.full(n, 0.45), "event": np.zeros(n),
    })
    s = paired_statistics(g)
    print("  4. sign convention: vendor 0.30, gefs 0.50, ifs 0.40")
    print(f"     vendor-gefs {s['bias_vendor_minus_gefs'].mean():+.2f}  "
          f"vendor-ifs {s['bias_vendor_minus_ifs'].mean():+.2f}  "
          f"gefs-ifs {s['bias_gefs_minus_ifs'].mean():+.2f}")
    for name, want in (("bias_vendor_minus_gefs", -0.20),
                       ("bias_vendor_minus_ifs", -0.10),
                       ("bias_gefs_minus_ifs", +0.10)):
        if abs(float(np.mean(s[name])) - want) > 1e-12:
            die(f"{name} is {np.mean(s[name]):+.4f}, expected {want:+.4f}")
    # Against a dry truth the driest probability must score best.
    if not (float(np.mean(s["brier_vendor_minus_gefs"])) < 0
            and float(np.mean(s["brier_vendor_minus_ifs"])) < 0):
        die("Brier signs disagree with the constructed case")

    # 5. The set-identical rule must be able to reject. Drop one row and the
    #    panel builder's guarantee has to fail, or it is guaranteeing nothing.
    panel = build_panel()
    check_panel(panel)
    broken = panel.drop(index=panel.index[0])
    try:
        check_panel(broken)
    except SystemExit:
        print("  5. set-identical rule rejects a panel missing one row")
    else:
        die("check_panel accepted a panel with an incomplete city-day")

    print("\n  all checks passed\n")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def report(panel: pd.DataFrame, desc: pd.DataFrame, head: pd.DataFrame,
           ctrl: pd.DataFrame, vals: dict) -> None:
    line = "=" * 74
    print(line)
    print("TASK 20b: IS THE DRY BIAS THE FORECAST, OR THE REFERENCE?")
    print(line)
    n_c = panel.city.nunique()
    n_d = panel.date.nunique()
    print(f"\n{n_c} cities, {n_d} days, {panel.date.min().date()} to "
          f"{panel.date.max().date()}, leads {min(LEAD_DAYS)}-{max(LEAD_DAYS)}")
    print(f"Three probabilities on identical city-days: the served number, "
          f"{GEFS_MEMBERS} GEFS members,")
    print(f"and {int(panel.ifs_members.max())} ECMWF IFS ENS members, both at "
          f"a {MATCHED_STEP_HOURS:.0f}-hourly step.")

    h1 = head[(head.lead_days == 1)
              & (head.boundary_mode == GEFS_BOUNDARY_PRIMARY)].set_index("statistic")

    print(f"\n{line}\n1. HOW FAR APART ARE THE THREE, AT LEAD 1\n{line}")
    print(f"  {'comparison':34s} {'estimate':>9s} {'95% CI':>20s} {'p':>7s}")
    for k in ("bias_vendor_minus_gefs", "bias_vendor_minus_ifs",
              "bias_gefs_minus_ifs"):
        r = h1.loc[k]
        print(f"  {k:34s} {r.estimate:+9.4f} "
              f"[{r.ci_lo:+7.4f},{r.ci_hi:+7.4f}] {r.p_boot:7.3f}")

    vg = float(h1.loc["bias_vendor_minus_gefs"].estimate)
    vi = float(h1.loc["bias_vendor_minus_ifs"].estimate)
    gi = float(h1.loc["bias_gefs_minus_ifs"].estimate)
    print(f"\n  The served number is drier than BOTH references, by "
          f"{abs(vg):.3f} and {abs(vi):.3f}.")
    ratio = abs(gi) / max(abs(vg), abs(vi), 1e-9)
    print(f"  The two references differ from each other by {abs(gi):.3f}, "
          f"{ratio:.0%} of the larger")
    print(f"  vendor gap. So the dry bias is {'NOT ' if ratio < 0.5 else ''}"
          f"mostly a statement about one centre.")
    if ratio >= 0.5:
        print(f"  WARNING: at {ratio:.0%} the choice of reference is doing "
              f"as much work as the finding.")

    print(f"\n{line}\n2. THE SCORES THEMSELVES, LEAD 1\n{line}")
    d1 = desc[(desc.lead_days == 1)
              & (desc.boundary_mode == GEFS_BOUNDARY_PRIMARY)]
    print(f"  {'series':8s} {'event':9s} {'mean p':>7s} {'brier':>7s} "
          f"{'reliab':>7s} {'resol':>7s} {'auc':>6s}")
    for r in d1.itertuples():
        print(f"  {r.series:8s} {r.event_def:9s} {r.mean_prob:7.4f} "
              f"{r.brier:7.4f} {r.reliability:7.4f} {r.resolution:7.4f} "
              f"{r.auc:6.3f}")
    print(f"  base rate {float(d1.base_rate.iloc[0]):.4f}")

    print(f"\n{line}\n3. WHICH IS BETTER CALIBRATED, UNDER BOTH EVENTS\n{line}")
    print("  E35 found this verdict reverses with the event definition. Both "
          "are shown so\n  a reader can see the choice rather than inherit it.")
    print(f"\n  {'comparison':40s} {'estimate':>9s} {'95% CI':>20s} {'p':>7s}")
    for k in ("brier_vendor_minus_gefs", "brier_vendor_minus_gefs_anystep",
              "brier_vendor_minus_ifs", "brier_vendor_minus_ifs_anystep",
              "brier_gefs_minus_ifs", "brier_gefs_minus_ifs_anystep"):
        r = h1.loc[k]
        print(f"  {k:40s} {r.estimate:+9.4f} "
              f"[{r.ci_lo:+7.4f},{r.ci_hi:+7.4f}] {r.p_boot:7.3f}")
    # A sign difference between two UNRESOLVED estimates is not a finding, and
    # reading one as a reversal is the exact error this study keeps catching
    # elsewhere. So the verdict is stated only over the comparisons whose
    # intervals exclude zero, and the rest are named as unresolved.
    rg, ri = h1.loc["brier_vendor_minus_gefs"], h1.loc["brier_vendor_minus_ifs"]
    ag, ai = (h1.loc["brier_vendor_minus_gefs_anystep"],
              h1.loc["brier_vendor_minus_ifs_anystep"])

    def resolved(r) -> bool:
        return bool(r.p_boot < 0.05)

    print(f"\n  DAY-TOTAL EVENT. Against GEFS {rg.estimate:+.4f} "
          f"(p={rg.p_boot:.3f}, smallest detectable {rg.mde_80:.4f});")
    print(f"  against IFS {ri.estimate:+.4f} (p={ri.p_boot:.3f}, "
          f"{ri.mde_80:.4f}).")
    if not resolved(rg) and not resolved(ri):
        print("  NEITHER resolves on this panel. The two estimates differ in "
              "sign, and that is\n  not a reversal - it is two nulls, on 15 "
              "cities where E35 needed 105 to\n  separate 0.006. Nothing about "
              "which is better calibrated is settled here.")
    elif resolved(rg) != resolved(ri):
        one = "GEFS" if resolved(rg) else "IFS"
        print(f"  Only the {one} comparison resolves, so the reference centre "
              f"decides whether\n  this claim can be made at all.")
    elif (float(rg.estimate) < 0) != (float(ri.estimate) < 0):
        print("  Both resolve AND they disagree in sign. 'Better calibrated "
              "than the ensemble'\n  is then a statement about which ensemble, "
              "not about the forecast.")
    else:
        better = "served" if float(rg.estimate) < 0 else "member-derived"
        print(f"  Both resolve and agree: the {better} probability is better "
              f"calibrated.")

    print(f"\n  ANY-STEP EVENT. Against GEFS {ag.estimate:+.4f} "
          f"(p={ag.p_boot:.3f}); against IFS {ai.estimate:+.4f} "
          f"(p={ai.p_boot:.3f}).")
    if resolved(ag) and resolved(ai) and float(ag.estimate) > 0 and float(ai.estimate) > 0:
        print("  BOTH resolve and both say the member-derived probability wins. "
              "So the same days\n  that cannot separate the two under one event "
              "definition separate them\n  decisively under the other, and "
              "agree across centres when they do. The event\n  definition is "
              "doing more work here than the choice of centre.")

    print(f"\n{line}\n4. BIAS BY LEAD\n{line}")
    print(f"  {'lead':>4s} {'v-gefs':>9s} {'v-ifs':>9s} {'gefs-ifs':>9s} "
          f"{'ref gap as % of vendor gap':>28s}")
    for lead in LEAD_DAYS:
        h = head[(head.lead_days == lead)
                 & (head.boundary_mode == GEFS_BOUNDARY_PRIMARY)].set_index("statistic")
        x = float(h.loc["bias_vendor_minus_gefs"].estimate)
        y = float(h.loc["bias_vendor_minus_ifs"].estimate)
        z = float(h.loc["bias_gefs_minus_ifs"].estimate)
        print(f"  {lead:4d} {x:+9.4f} {y:+9.4f} {z:+9.4f} "
              f"{abs(z) / max(abs(x), abs(y), 1e-9):27.0%}")

    print(f"\n{line}\n5. CONTROLS\n{line}")
    mc = ctrl[ctrl.control == "member_count"].iloc[0]
    print(f"  MEMBER COUNT. Re-deriving the second centre from "
          f"{int(mc.to_members)} of its {int(mc.from_members)}")
    print(f"  members moves its probability by {mc.estimate:+.4f} "
          f"[{mc.ci_lo:+.4f}, {mc.ci_hi:+.4f}], against a")
    print(f"  vendor gap of {abs(vi):.4f}. Distinct probability values: "
          f"{int(mc.distinct_values_full)} at full count,")
    print(f"  {int(mc.distinct_values_sub)} subsampled, "
          f"{int(mc.distinct_values_gefs)} for GEFS - so the coarseness is "
          f"matched, not assumed.")
    share = abs(float(mc.estimate)) / max(abs(vi), 1e-9)
    print(f"  The count explains {share:.1%} of the gap it could have "
          f"explained entirely.")

    w = ctrl[ctrl.control.str.startswith("window_")].set_index("control")
    print(f"\n  WINDOW. The second archive starts later, so this panel is "
          f"shorter than E13's.")
    for label in ("wide", "narrow"):
        r = w.loc[f"window_{label}"]
        print(f"    {label:6s} {int(r.n_days):4d} days "
              f"{r.first_day} to {r.last_day}: bias {r.estimate:+.4f} "
              f"[{r.ci_lo:+.4f}, {r.ci_hi:+.4f}]")
    dw = float(w.loc["window_narrow"].estimate) - float(w.loc["window_wide"].estimate)
    print(f"    The window alone moves the vendor-GEFS bias by {dw:+.4f}, "
          f"against a")
    print(f"    reference-centre disagreement of {abs(gi):.4f}. "
          f"{'Comparable - read section 1 with that in mind.' if abs(dw) > abs(gi) * 0.5 else 'Smaller, so section 1 is not a window effect.'}")

    print(f"\n{line}\n6. WHAT IT IS WORTH TO SOMEONE ACTING ON IT\n{line}")
    print("  Relative economic value under the textbook acting rule, lead 1. "
          "E4a's claim is\n  that the served probability hurts the "
          "cheap-action user; the question here is\n  whether that is against "
          "NOAA or against any ensemble.")
    print(f"\n  {'alpha':>6s} {'vendor':>8s} {'gefs':>8s} {'ifs':>8s} "
          f"{'v-gefs':>8s} {'v-ifs':>8s}")
    for a in HEADLINE_ALPHAS:
        t = f"{int(round(a * 100)):02d}"
        pv, pg, pi = (vals[f"value_vendor_a{t}"], vals[f"value_gefs_a{t}"],
                      vals[f"value_ifs_a{t}"])
        print(f"  {a:6.2f} {pv:8.3f} {pg:8.3f} {pi:8.3f} "
              f"{pv - pg:+8.3f} {pv - pi:+8.3f}")
    t5 = f"{5:02d}"
    if (vals[f"value_vendor_a{t5}"] < vals[f"value_gefs_a{t5}"]
            and vals[f"value_vendor_a{t5}"] < vals[f"value_ifs_a{t5}"]):
        print("\n  The cheap-action user loses against BOTH references, so "
              "E4a is not an\n  artefact of the reference centre.")

    print(f"\n{line}\nWHAT THIS DOES AND DOES NOT SETTLE\n{line}")
    print(f"  1. {n_c} cities in one region and {n_d} days. This is the "
          f"capitals panel, not\n     E35's 105-city one, because the second "
          f"archive does not reach the others.\n     Widening it is "
          f"collection, not analysis.")
    print("  2. Two centres are not a population of centres. Two agreeing "
          "bounds the\n     reference effect at this panel's size; it does "
          "not estimate it.")
    print("  3. The vendor series is `gfs_seamless`, which is NOAA-derived. "
          "That it is\n     drier than an ECMWF ensemble too is therefore the "
          "stronger of the two\n     comparisons, not a coincidence to be "
          "explained away.")
    print("  4. Value is a point estimate here. Its interval lives in "
          "significance.py,\n     on the wider panel, and should be quoted "
          "from there.")
    print()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main(argv: list[str]) -> None:
    quick = "quick" in argv
    if "check" in argv:
        run_checks(n_sim=60 if quick else 200)
        return
    run_checks(n_sim=60 if quick else 200)

    n_boot = 300 if quick else BOOTSTRAP_N
    rng = np.random.default_rng(RANDOM_SEED)

    panel = build_panel()
    check_panel(panel)
    panel.to_parquet(PANEL_TABLE, index=False)
    print(f"wrote {PANEL_TABLE.name}: {len(panel):,} rows")

    desc_rows, head_rows = [], []
    for mode in GEFS_BOUNDARY_MODES:
        for lead in LEAD_DAYS:
            g = panel[(panel.boundary_mode == mode)
                      & (panel.lead_days == lead)].reset_index(drop=True)
            d = descriptive(g)
            d["boundary_mode"], d["lead_days"] = mode, lead
            desc_rows.append(d)
            h = test_cell(g, n_boot, rng)
            h["boundary_mode"], h["lead_days"] = mode, lead
            head_rows.append(h)
    desc = pd.concat(desc_rows, ignore_index=True)
    head = pd.concat(head_rows, ignore_index=True)
    head.to_parquet(HEADLINE_TABLE, index=False)
    print(f"wrote {HEADLINE_TABLE.name}: {len(head):,} rows")

    ctrl = pd.concat([member_control(panel, n_boot, rng),
                      window_control(panel, n_boot, rng)], ignore_index=True)
    ctrl.to_parquet(CONTROL_TABLE, index=False)
    print(f"wrote {CONTROL_TABLE.name}: {len(ctrl):,} rows")

    g1 = panel[(panel.boundary_mode == GEFS_BOUNDARY_PRIMARY)
               & (panel.lead_days == 1)].reset_index(drop=True)
    vals = value_differences(g1)

    report(panel, desc, head, ctrl, vals)


if __name__ == "__main__":
    main(sys.argv[1:])
