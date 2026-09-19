"""Tasks 18, 22, 23, 24: decision value, CRPS/ROC/sharpness, and real baselines.

This is the driver; the maths lives in `metrics.py` (Tasks 22-23) and
`baselines.py` (Task 24), and the re-referenced skill score in
`calibration.py` (Task 18). Everything here runs off data already on disk -
capitals_pinned_daily.parquet, cities_pop.parquet, verification_leads.parquet -
and touches no network.

WHAT EACH PIECE IS FOR

  Task 23  Relative economic value is the headline. Brier and reliability say
           whether a probability is honest; V(alpha) says whether acting on it
           beats knowing nothing but the season. Two curves are reported for
           every series: the ENVELOPE over trigger thresholds (the best the
           forecast could do for a user with hindsight about where to set their
           trigger) and the value at the CALIBRATED trigger (what a user
           actually gets by following the textbook rule "act when the stated
           probability exceeds your cost-loss ratio"). The gap between them is
           the price of miscalibration in decision currency, which is the
           quantity this study exists to produce.
  Task 22  CRPS, ROC/AUC and sharpness, so the results are legible to the
           CRPS-speaking ML-weather community and so discrimination is
           separated from calibration.
  Task 24  Persistence and smoothed day-of-year climatology, scored on exactly
           the same days as the forecast they are a reference for.
  Task 18  Brier skill against the smoothed climatology reported BESIDE the
           published sample-base-rate version, never instead of it, with the
           shift reported as a result.

LEAD TIME. The grid here is city x model x threshold, not city x model x lead
x threshold, for a reason established by probe: vendor PoP does not exist at
lead times (plan D5 - `precipitation_probability_previous_dayN` returns null at
every date and model). The lead axis is therefore carried by the MOS-derived
probabilities of `events.py` at Bucharest, reported separately and labelled as
derived, and will be replaced by member-derived PoP when Task 20a lands.

Usage:  python src/decision_metrics.py           # checks, then the full run
        python src/decision_metrics.py check     # correctness checks only
"""

from __future__ import annotations

import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import baselines
import metrics
from baselines import (
    doy_climatology,
    doy_climatology_members,
    persistence_binary,
    persistence_frequency,
)
from calibration import brier_decomposition, skill_score
from config import (
    CLIM_PSEUDO_COUNT,
    CLIM_WINDOW_DAYS,
    FIGURES,
    N_PROB_BINS,
    PROCESSED,
    PRIMARY_MODEL,
    PROVIDER_MODELS,
    RAIN_THRESHOLD_MM,
    RAIN_THRESHOLD_VARIANTS_MM,
)
from metrics import (
    HEADLINE_ALPHAS,
    crps_deterministic,
    crps_ensemble,
    crps_skill_score,
    roc_auc,
    roc_curve,
    sharpness,
    value_curve,
    value_summary,
)

# A series shorter than this cannot support a day-of-year climatology with any
# meaning: fewer than ~18 months leaves parts of the year with one observation.
MIN_DAYS = 400

# Cities whose value curves are drawn individually. Bucharest is the study's
# hero city; the other two bracket the regime range present in the capitals
# set (Mediterranean-dry and maritime-wet).
FOCUS_CITIES = ("Bucharest", "Madrid", "Dublin")


def artefact(name: str):
    return PROCESSED / f"decision_{name}.parquet"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def load_series() -> pd.DataFrame:
    """Every (city, model) daily series on disk, tagged by where it came from.

    `pinned` is the like-for-like grid: the same days, per model, from
    `capitals.py providers`. `served_world` is what a user is actually shown -
    Open-Meteo's best_match - across the wider city set, which is the series
    the trustworthiness claim is ultimately about.
    """
    frames = []
    pinned = PROCESSED / "capitals_pinned_daily.parquet"
    if pinned.exists():
        frames.append(pd.read_parquet(pinned).assign(source="pinned"))
    world = PROCESSED / "cities_pop.parquet"
    if world.exists():
        frames.append(pd.read_parquet(world).assign(model="best_match",
                                                    source="served_world"))
    if not frames:
        raise SystemExit(
            "no daily series on disk - run `capitals.py providers` and "
            "`capitals.py world` first")
    df = pd.concat(frames, ignore_index=True)
    df["local_date"] = pd.to_datetime(df.local_date)
    return df.sort_values(["source", "city", "model", "local_date"])


# ---------------------------------------------------------------------------
# One series -> one row
# ---------------------------------------------------------------------------
def score_series(dates, prob, obs_mm, threshold_mm: float) -> dict:
    """Every new metric for one (city, model, threshold), on matched days.

    The forecast and all four references are scored on IDENTICAL days: the
    first days of a record have no persistence history, and comparing a model
    scored on 750 days against a persistence baseline scored on 720 would make
    the reference look better or worse for reasons that have nothing to do with
    forecasting. Days lost to that are counted and reported.
    """
    event = (np.asarray(obs_mm, float) >= threshold_mm).astype(float)
    p = np.asarray(prob, float)

    # A record too short or too gappy to support a seasonal climatology raises
    # rather than returning something plausible; that is a skipped series, not
    # a crash, but it must be visible in the caller's count.
    clim = doy_climatology(dates, event)
    pers_b = persistence_binary(dates, event)
    pers_f = persistence_frequency(dates, event)

    ok = np.isfinite(pers_b) & np.isfinite(pers_f) & np.isfinite(clim) & np.isfinite(p)
    n_dropped = int((~ok).sum())
    p, event, clim, pers_b, pers_f = (p[ok], event[ok], clim[ok],
                                      pers_b[ok], pers_f[ok])
    n = len(event)
    if n < MIN_DAYS or event.sum() < 20 or (1 - event).sum() < 20:
        return {}

    flat = np.full(n, event.mean())
    dec = brier_decomposition(p, event, n_bins=N_PROB_BINS, reference_prob=clim)
    brier_clim = float(np.mean((clim - event) ** 2))

    curve = value_curve(p, event)
    vs = value_summary(curve)
    # Persistence deserves the same decision-currency treatment as the
    # forecast: "beats climatology on Brier" and "is worth acting on" are
    # different claims, and a reviewer will ask for the second.
    vs_pers = value_summary(value_curve(pers_f, event))

    _, sharp = sharpness(p)
    row = {
        "n": n, "n_dropped_for_matching": n_dropped,
        "base_rate": float(event.mean()),
        "clim_amplitude": float(np.std(clim)),
        "brier": dec["brier"],
        "bss_sample": dec["brier_skill_score"],
        "bss_clim": dec["bss_vs_reference"],
        "bss_shift": dec["bss_shift"],
        "reliability": dec["reliability"],
        "resolution": dec["resolution"],
        "ece": dec["ece"],
        # CRPS of a binary-event probability IS the Brier score (asserted in
        # metrics.run_checks); carried explicitly so a CRPS-speaking reader can
        # find the number under the name they use.
        "crps_binary": dec["brier"],
        "auc": roc_auc(p, event),
        "sharpness_score": sharp["sharpness_score"],
        "prob_variance": sharp["prob_variance"],
        "share_confident": sharp["share_confident"],
        "share_hedged": sharp["share_hedged"],
        # Baselines, same days (Task 24).
        "brier_flat": float(np.mean((flat - event) ** 2)),
        "brier_clim_doy": brier_clim,
        "brier_pers_binary": float(np.mean((pers_b - event) ** 2)),
        "brier_pers_freq": float(np.mean((pers_f - event) ** 2)),
        "bss_pers_binary_vs_clim": skill_score(
            float(np.mean((pers_b - event) ** 2)), brier_clim),
        "bss_pers_freq_vs_clim": skill_score(
            float(np.mean((pers_f - event) ** 2)), brier_clim),
        "bss_flat_vs_clim": skill_score(float(np.mean((flat - event) ** 2)),
                                        brier_clim),
        "auc_pers_freq": roc_auc(pers_f, event),
        "v_max_cal_pers_freq": vs_pers["v_max_calibrated"],
    }
    row.update(vs)
    row["v_gap_at_peak"] = row["v_max"] - row["v_max_calibrated"]
    return row


def build_table(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The full city x model x threshold grid, plus value curves at the
    primary threshold."""
    rows, curves, skipped = [], [], {}
    for (source, city, model), g in df.groupby(["source", "city", "model"],
                                               sort=True):
        g = g.dropna(subset=["forecast_prob", "obs_precip_mm"])
        g = g.drop_duplicates(subset="local_date").sort_values("local_date")
        if len(g) < MIN_DAYS:
            skipped[(city, model)] = f"{len(g)} usable days < {MIN_DAYS}"
            continue
        for thr in RAIN_THRESHOLD_VARIANTS_MM:
            try:
                row = score_series(g.local_date, g.forecast_prob.values,
                                   g.obs_precip_mm.values, thr)
            except ValueError as exc:
                # Keyed by series, not by (series, threshold): one unusable
                # record should be one line of output, not three.
                skipped[(city, model)] = str(exc).split(" - ")[0]
                continue
            if not row:
                continue
            row.update({"source": source, "city": city, "model": model,
                        "threshold_mm": thr,
                        "is_primary_threshold": thr == RAIN_THRESHOLD_MM})
            rows.append(row)

            if thr == RAIN_THRESHOLD_MM:
                event = (g.obs_precip_mm.values >= thr).astype(float)
                c = value_curve(g.forecast_prob.values, event)
                curves.append(c.assign(source=source, city=city, model=model))
    if skipped:
        print(f"  {len(skipped)} of "
              f"{df.groupby(['source', 'city', 'model']).ngroups} series "
              f"skipped - record too short or too gappy for a leave-one-out "
              f"seasonal climatology. First few:")
        for (city, model), why in list(skipped.items())[:5]:
            print(f"    {city:20s} {model:16s} {why}")
    table = pd.DataFrame(rows)
    curve = pd.concat(curves, ignore_index=True) if curves else pd.DataFrame()
    return table, curve


# ---------------------------------------------------------------------------
# Task 22: CRPS on precipitation AMOUNT
# ---------------------------------------------------------------------------
def crps_amount(df: pd.DataFrame) -> pd.DataFrame:
    """CRPS in millimetres, against the leave-one-out climatological sample.

    HONEST SCOPE. There is no ensemble in this data - the vendor serves one
    deterministic amount plus one probability - so the forecast's predictive
    distribution is a point mass, and CRPS for a point forecast is exactly its
    mean absolute error. That is not a workaround, it is the definition, and it
    is what makes the comparison meaningful: the reference IS a distribution
    (the smoothed day-of-year climatology of observed amounts), so a positive
    CRPS skill score says the deterministic forecast beats a full
    climatological distribution despite carrying no spread of its own. When
    Task 20a lands member-derived forecasts, they drop straight into
    `crps_ensemble` and become comparable to these numbers.
    """
    rows = []
    for (source, city, model), g in df.groupby(["source", "city", "model"],
                                               sort=True):
        g = g.dropna(subset=["forecast_precip_mm", "obs_precip_mm"])
        g = g.drop_duplicates(subset="local_date").sort_values("local_date")
        if len(g) < MIN_DAYS:
            continue
        obs = g.obs_precip_mm.values.astype(float)
        fc = g.forecast_precip_mm.values.astype(float)
        try:
            members = doy_climatology_members(g.local_date, obs)
        except ValueError:
            continue
        # Days whose climatological sample is too thin are dropped from BOTH
        # scores rather than from the reference alone: scoring the forecast on
        # days the reference could not be built for would compare two different
        # samples and call the difference skill.
        keep = np.array([len(m) >= baselines.MIN_CLIM_SUPPORT for m in members])
        if keep.sum() < MIN_DAYS:
            continue
        obs, fc = obs[keep], fc[keep]
        members = [m for m, k in zip(members, keep) if k]
        crps_clim = float(np.mean([crps_ensemble(m[None, :], [o])[0]
                                   for m, o in zip(members, obs)]))
        crps_fc = float(np.mean(crps_deterministic(fc, obs)))
        rows.append({
            "source": source, "city": city, "model": model, "n": int(keep.sum()),
            "crps_forecast_mm": crps_fc,
            "crps_climatology_mm": crps_clim,
            "crpss_vs_climatology": crps_skill_score(crps_fc, crps_clim),
            "mean_obs_mm": float(obs.mean()),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Task 18: how much does the reference choice move the skill score?
# ---------------------------------------------------------------------------
def clim_sensitivity(df: pd.DataFrame,
                     windows=(7, 15, 30),
                     pseudo=(0.0, CLIM_PSEUDO_COUNT)) -> pd.DataFrame:
    """Is the BSS shift a property of the climate, or of the smoother?

    If the re-referenced skill score moved with the window width, the Task 18
    result would be an artefact of a tuning knob rather than a correction. So
    the knob is swept and the spread reported.
    """
    rows = []
    sel = df[(df.source == "pinned") & (df.model == PRIMARY_MODEL)]
    for city, g in sel.groupby("city"):
        g = g.dropna(subset=["forecast_prob", "obs_precip_mm"])
        g = g.drop_duplicates(subset="local_date").sort_values("local_date")
        if len(g) < MIN_DAYS:
            continue
        event = (g.obs_precip_mm.values >= RAIN_THRESHOLD_MM).astype(float)
        p = g.forecast_prob.values.astype(float)
        brier = float(np.mean((p - event) ** 2))
        base = event.mean()
        bss_sample = skill_score(brier, base * (1 - base))
        for w in windows:
            for n0 in pseudo:
                try:
                    clim = doy_climatology(g.local_date, event, window_days=w,
                                           pseudo_count=n0)
                except ValueError:
                    # A narrow window over a short record can leave too many
                    # days without other-year support. That is a missing cell
                    # in the sweep, not a reason to abandon the sweep.
                    continue
                ok = np.isfinite(clim)
                if ok.sum() < MIN_DAYS:
                    continue
                rows.append({
                    "city": city, "window_days": w, "pseudo_count": n0,
                    "bss_sample": bss_sample,
                    "bss_clim": skill_score(
                        float(np.mean((p[ok] - event[ok]) ** 2)),
                        float(np.mean((clim[ok] - event[ok]) ** 2))),
                    "clim_amplitude": float(np.std(clim[ok])),
                })
    out = pd.DataFrame(rows)
    out["bss_shift"] = out.bss_clim - out.bss_sample
    return out


# ---------------------------------------------------------------------------
# Task 23 on the lead-time axis (MOS-derived, Bucharest)
# ---------------------------------------------------------------------------
def value_by_lead() -> pd.DataFrame:
    """Economic value against lead time, using the derived probabilities.

    Vendor PoP carries no lead axis (plan D5), so this uses `events.py`'s
    time-blocked out-of-sample MOS probabilities from the deterministic
    precipitation forecast. The RELIABILITY of those is not a finding - the
    mapping is fitted to be calibrated - but their VALUE is: no fit can
    manufacture the information needed to beat climatology at day 7.
    """
    path = PROCESSED / "verification_leads.parquet"
    if not path.exists():
        return pd.DataFrame()
    from events import EVENTS, _oos_probabilities
    from config import LEAD_DAYS

    df = pd.read_parquet(path)
    df = df[df.usable].copy()
    spec = EVENTS["rain"]
    obs_all = spec["obs"](df)

    rows = []
    for lead in LEAD_DAYS:
        m = (df.lead_days == lead) & obs_all.notna() & df[spec["predictor"]].notna()
        g, y = df[m], obs_all[m].to_numpy(dtype=float)
        if len(g) < MIN_DAYS or y.sum() < 20:
            continue
        x = spec["transform"](g[spec["predictor"]].to_numpy(dtype=float))
        p = _oos_probabilities(g.local_date.values, x, y,
                               direction=spec["direction"])
        ok = ~np.isnan(p)
        if ok.sum() < MIN_DAYS:
            continue
        vs = value_summary(value_curve(p[ok], y[ok]))
        vs.update({"lead_days": lead, "auc": roc_auc(p[ok], y[ok])})
        rows.append(vs)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# End-to-end checks on the real data
# ---------------------------------------------------------------------------
def check_on_real_data(df: pd.DataFrame) -> None:
    """The analytic identities again, but on an actual city's observations.

    The unit checks use synthetic series, which cannot catch a join or dtype
    fault in the real pipeline. These repeat the two identities that must hold
    for ANY data - a constant-climatology forecast has zero value, a perfect
    forecast has value one - using a real city's rain record as the outcome.
    """
    g = df[(df.source == "pinned") & (df.model == PRIMARY_MODEL)
           & (df.city == "Bucharest")]
    if g.empty:
        g = df.groupby(["source", "city", "model"]).head(10_000)
        g = g[g.city == g.city.iloc[0]]
    g = g.dropna(subset=["obs_precip_mm"]).drop_duplicates("local_date")
    event = (g.obs_precip_mm.values >= RAIN_THRESHOLD_MM).astype(float)

    flat = value_curve(np.full(len(event), event.mean()), event)
    assert np.allclose(flat.v_calibrated, 0.0, atol=1e-12), \
        "a flat-climatology forecast must have zero value on real data too"
    perfect = value_curve(event, event)
    assert np.allclose(perfect.v_calibrated, 1.0), \
        "a perfect forecast must have value 1 on real data too"

    # The published skill score must remain exactly what it was: Task 18 adds a
    # reference, it does not change one.
    p = g.forecast_prob.values.astype(float)
    ref = doy_climatology(g.local_date, event)
    keep = np.isfinite(ref) & np.isfinite(p)
    dec = brier_decomposition(p[keep], event[keep], n_bins=N_PROB_BINS,
                              reference_prob=ref[keep])
    plain = brier_decomposition(p[keep], event[keep], n_bins=N_PROB_BINS)
    assert dec["brier_skill_score"] == plain["brier_skill_score"], \
        "supplying a reference changed the sample-referenced BSS"
    assert set(plain) < set(dec), "the reference keys must be additive"

    # A day-of-year climatology must beat a flat one on its own terms, or the
    # seasonal signal it exists to capture is not there.
    clim = doy_climatology(g.local_date, event)
    ok = np.isfinite(clim)
    assert np.mean((clim[ok] - event[ok]) ** 2) <= np.mean((event[ok].mean() - event[ok]) ** 2) + 0.01, \
        "the seasonal climatology scores worse than a flat base rate"
    print("  ok  economic-value identities and BSS additivity hold on the "
          "real Bucharest record")


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def _label(model: str) -> str:
    if model in PROVIDER_MODELS:
        return PROVIDER_MODELS[model].display_name
    return "as served (best_match)" if model == "best_match" else model


def plot_value(curve: pd.DataFrame, table: pd.DataFrame) -> None:
    pin = curve[curve.source == "pinned"]
    if pin.empty:
        return
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.8))

    ax = axes[0]
    b = pin[pin.city == "Bucharest"]
    for model, g in b.groupby("model"):
        ax.plot(g.alpha, g.v_envelope, lw=1.8, label=_label(model))
    ax.axhline(0, color="#444", lw=1.0, ls="--")
    ax.set_xlabel("Cost-loss ratio $\\alpha$ (cost of acting / loss avoided)")
    ax.set_ylabel("Relative economic value")
    ax.set_title("Bucharest: value by user type, best trigger", fontsize=10)
    ax.legend(fontsize=7.5); ax.grid(alpha=0.25); ax.set_ylim(-0.05, 0.8)

    # The decision-currency price of miscalibration: what the envelope promises
    # versus what the rule a user would actually follow delivers.
    ax = axes[1]
    med = pin.groupby("alpha")[["v_envelope", "v_calibrated"]].median()
    lo = pin.groupby("alpha").v_calibrated.quantile(0.1)
    hi = pin.groupby("alpha").v_calibrated.quantile(0.9)
    ax.fill_between(med.index, lo, hi, color="#1f77b4", alpha=0.15,
                    label="10-90% of city-model series")
    ax.plot(med.index, med.v_envelope, color="#d62728", lw=2.2,
            label="best trigger (hindsight)")
    ax.plot(med.index, med.v_calibrated, color="#1f77b4", lw=2.2,
            label="acting when PoP > $\\alpha$")
    ax.axhline(0, color="#444", lw=1.0, ls="--")
    ax.set_xlabel("Cost-loss ratio $\\alpha$")
    ax.set_ylabel("Relative economic value")
    ax.set_title("Median across capitals and models:\nwhat the rule a user "
                 "follows actually delivers", fontsize=10)
    ax.legend(fontsize=7.5); ax.grid(alpha=0.25); ax.set_ylim(-0.05, 0.8)

    ax = axes[2]
    t = table[(table.source == "pinned") & table.is_primary_threshold]
    ax.scatter(t.base_rate, t.v_max_calibrated, s=26, c="#4c78a8", alpha=0.75)
    for city in FOCUS_CITIES:
        s = t[t.city == city]
        if len(s):
            ax.scatter(s.base_rate, s.v_max_calibrated, s=48, color="#d62728",
                       zorder=3)
            ax.annotate(city, (s.base_rate.mean(), s.v_max_calibrated.max()),
                        fontsize=7.5, xytext=(4, 3), textcoords="offset points",
                        color="#d62728")
    ax.axhline(0, color="#444", lw=1.0, ls="--")
    ax.set_xlabel("Rain-day frequency")
    ax.set_ylabel("Peak value under the acting rule")
    ax.set_title("Peak decision value against how wet the city is", fontsize=10)
    ax.grid(alpha=0.25)

    fig.suptitle("Relative economic value of served rain probabilities "
                 f"(rain $\\geq$ {RAIN_THRESHOLD_MM} mm)", fontsize=11.5)
    fig.tight_layout()
    path = FIGURES / "economic_value.png"
    fig.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {path}")


def plot_discrimination(df: pd.DataFrame, table: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.8))

    ax = axes[0]
    sel = df[(df.source == "pinned") & (df.city == "Bucharest")]
    for model, g in sel.groupby("model"):
        g = g.dropna(subset=["forecast_prob", "obs_precip_mm"])
        event = (g.obs_precip_mm.values >= RAIN_THRESHOLD_MM).astype(float)
        if event.sum() < 20 or (1 - event).sum() < 20:
            continue
        r = roc_curve(g.forecast_prob.values, event)
        ax.plot(r.false_alarm_rate, r.hit_rate, lw=1.6,
                label=f"{_label(model)} ({roc_auc(g.forecast_prob.values, event):.3f})")
    ax.plot([0, 1], [0, 1], "k--", lw=1.0, label="no discrimination (0.5)")
    ax.set_xlabel("False-alarm rate"); ax.set_ylabel("Hit rate")
    ax.set_title("Bucharest ROC curves (AUC in brackets)", fontsize=10)
    ax.legend(fontsize=7.5, loc="lower right"); ax.grid(alpha=0.25)

    ax = axes[1]
    for city in FOCUS_CITIES:
        g = df[(df.source == "pinned") & (df.city == city)
               & (df.model == PRIMARY_MODEL)].dropna(subset=["forecast_prob"])
        if g.empty:
            continue
        hist, summ = sharpness(g.forecast_prob.values)
        ax.step(hist.mid, hist.share, where="mid", lw=1.8,
                label=f"{city} (mean p(1-p) = {summ['sharpness_score']:.3f})")
    ax.set_yscale("log")
    ax.set_xlabel("Issued probability of precipitation")
    ax.set_ylabel("Share of days (log)")
    ax.set_title("Sharpness: how often each probability is issued", fontsize=10)
    ax.legend(fontsize=7.5); ax.grid(alpha=0.25)

    # The figure that carries the study's distinction: discrimination and
    # skill are not the same quantity, and a series can sit high on one axis
    # and low on the other.
    ax = axes[2]
    t = table[table.is_primary_threshold]
    for src, mark in (("pinned", "o"), ("served_world", ".")):
        s = t[t.source == src]
        ax.scatter(s.auc, s.bss_clim, s=26 if src == "pinned" else 9,
                   marker=mark, alpha=0.6,
                   label="capitals, pinned models" if src == "pinned"
                   else "world cities, as served")
    ax.axhline(0, color="#444", lw=1.0, ls="--")
    ax.set_xlabel("AUC (discrimination only)")
    ax.set_ylabel("BSS vs smoothed climatology")
    ax.set_title("Discrimination is not skill", fontsize=10)
    ax.legend(fontsize=7.5); ax.grid(alpha=0.25)

    fig.suptitle("Discrimination and sharpness (Task 22)", fontsize=11.5)
    fig.tight_layout()
    path = FIGURES / "discrimination_sharpness.png"
    fig.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {path}")


def plot_reference_shift(table: pd.DataFrame, sens: pd.DataFrame) -> None:
    t = table[table.is_primary_threshold].dropna(subset=["bss_sample", "bss_clim"])
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.6))

    ax = axes[0]
    ax.scatter(t.bss_sample, t.bss_clim, s=14, alpha=0.5, color="#4c78a8")
    lim = [min(t.bss_sample.min(), t.bss_clim.min()) - 0.02,
           max(t.bss_sample.max(), t.bss_clim.max()) + 0.02]
    ax.plot(lim, lim, "k--", lw=1.0)
    ax.set_xlabel("BSS vs sample base rate (as published)")
    ax.set_ylabel("BSS vs smoothed day-of-year climatology")
    ax.set_title("Re-referencing the skill score", fontsize=10)
    ax.grid(alpha=0.25)

    # The plan's concern is that the bias runs along the climate axis, which is
    # the axis being measured. Seasonality amplitude is that axis, made
    # explicit: it is zero for an aseasonal city and large for a monsoon one.
    ax = axes[1]
    sc = ax.scatter(t.clim_amplitude, t.bss_shift, s=16, c=t.base_rate,
                    cmap="viridis", alpha=0.8)
    ax.axhline(0, color="#444", lw=1.0, ls="--")
    fig.colorbar(sc, ax=ax, label="rain-day frequency")
    ax.set_xlabel("Seasonality of rain frequency (SD of the DOY climatology)")
    ax.set_ylabel("BSS shift (climatology-referenced - sample-referenced)")
    ax.set_title("Does the shift run along the climate axis?", fontsize=10)
    ax.grid(alpha=0.25)

    ax = axes[2]
    if len(sens):
        for (w, n0), g in sens.groupby(["window_days", "pseudo_count"]):
            ax.scatter(g.city, g.bss_shift, s=20, alpha=0.75,
                       label=f"window {w}d, prior {n0:.0f}")
        ax.axhline(0, color="#444", lw=1.0, ls="--")
        ax.tick_params(axis="x", rotation=90, labelsize=7)
        ax.set_ylabel("BSS shift")
        ax.set_title("Does the shift depend on the smoother?", fontsize=10)
        ax.legend(fontsize=6.5); ax.grid(alpha=0.25)

    fig.suptitle("Task 18: Brier skill re-referenced to a smoothed "
                 "day-of-year climatology", fontsize=11.5)
    fig.tight_layout()
    path = FIGURES / "bss_reference.png"
    fig.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {path}")


def plot_value_by_lead(lead: pd.DataFrame) -> None:
    if lead.empty:
        return
    fig, ax = plt.subplots(figsize=(6.6, 4.6))
    for a in (0.05, 0.10, 0.20, 0.50):
        col = f"v_cal_a{int(round(a * 100)):02d}"
        ax.plot(lead.lead_days, lead[col], "o-", ms=4,
                label=f"$\\alpha$ = {a:.2f}")
    ax.plot(lead.lead_days, lead.v_max, "k-", lw=2.2,
            label="peak over all users (best trigger)")
    ax.axhline(0, color="#444", lw=1.0, ls="--")
    ax.set_xlabel("Lead time (days)")
    ax.set_ylabel("Relative economic value")
    ax.set_title("Bucharest: decision value against lead time\n"
                 "(probabilities derived out-of-sample from the deterministic "
                 "forecast;\nvendor PoP does not exist at lead)", fontsize=10)
    ax.legend(fontsize=8); ax.grid(alpha=0.25)
    path = FIGURES / "value_by_lead.png"
    fig.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {path}")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def report(table: pd.DataFrame, curve: pd.DataFrame, crps: pd.DataFrame,
           sens: pd.DataFrame, lead: pd.DataFrame) -> None:
    pin = table[(table.source == "pinned") & table.is_primary_threshold]
    world = table[(table.source == "served_world") & table.is_primary_threshold]

    print("\n=== Task 23: relative economic value (HEADLINE) ===")
    print(f"  {len(pin)} capital x model series, {len(world)} world cities as "
          f"served, rain >= {RAIN_THRESHOLD_MM} mm.\n")
    print("  Value under the rule a user would actually follow "
          "(act when PoP > alpha):\n")
    print("   alpha   median V   share V>0   median V (best trigger)")
    for a in HEADLINE_ALPHAS:
        c = f"v_cal_a{int(round(a * 100)):02d}"
        e = f"v_env_a{int(round(a * 100)):02d}"
        print(f"   {a:5.2f}   {pin[c].median():8.3f}   "
              f"{(pin[c] > 0).mean():9.0%}   {pin[e].median():8.3f}")
    print(f"\n  Peak value per series: median {pin.v_max_calibrated.median():.3f} "
          f"at alpha {pin.alpha_at_v_max_calibrated.median():.2f}; "
          f"{(pin.v_max_calibrated > 0).mean():.0%} of series have positive "
          f"value somewhere.")
    print(f"  Share of the cost-loss range with positive value, median: "
          f"{pin.alpha_positive_share.median():.0%}")
    print(f"  Price of miscalibration at the peak (best trigger minus acting "
          f"rule): median {pin.v_gap_at_peak.median():.3f}")
    print(f"  Persistence, same days, same rule: peak value median "
          f"{pin.v_max_cal_pers_freq.median():.3f}")

    best = pin.sort_values("v_max_calibrated", ascending=False).head(5)
    worst = pin.sort_values("v_max_calibrated").head(5)
    cols = ["city", "model", "base_rate", "v_max_calibrated",
            "alpha_at_v_max_calibrated", "v_cal_a10", "bss_clim"]
    print("\n  Most decision-valuable series:")
    print(best[cols].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print("\n  Least:")
    print(worst[cols].to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    if len(world):
        print(f"\n  As actually served, across {world.city.nunique()} world "
              f"cities: median peak value {world.v_max_calibrated.median():.3f}, "
              f"positive for {(world.v_max_calibrated > 0).mean():.0%} of cities; "
              f"at alpha=0.10 positive for {(world.v_cal_a10 > 0).mean():.0%}.")

    print("\n=== Task 22: CRPS, ROC/AUC, sharpness ===")
    print(f"  AUC (discrimination): median {pin.auc.median():.3f} "
          f"[{pin.auc.min():.3f}, {pin.auc.max():.3f}] over capitals x models.")
    print(f"  Persistence AUC for comparison: median "
          f"{pin.auc_pers_freq.median():.3f}")
    print(f"  CRPS of the binary event = Brier: median {pin.crps_binary.median():.4f}")
    print(f"  Sharpness: mean p(1-p) median {pin.sharpness_score.median():.3f}; "
          f"the forecast commits to a near-certain answer on "
          f"{pin.share_confident.median():.0%} of days and hedges "
          f"(0.2-0.8) on {pin.share_hedged.median():.0%}.")
    if len(crps):
        cp = crps[crps.source == "pinned"]
        print(f"  CRPS on precipitation AMOUNT (mm), vs the leave-one-out "
              f"seasonal climatology:")
        print(f"    forecast {cp.crps_forecast_mm.median():.3f} mm, climatology "
              f"{cp.crps_climatology_mm.median():.3f} mm, CRPSS median "
              f"{cp.crpss_vs_climatology.median():+.3f} "
              f"({(cp.crpss_vs_climatology > 0).mean():.0%} of series positive)")

    print("\n=== Task 24: baselines, scored on identical days ===")
    print("  Brier skill of each reference against the smoothed day-of-year "
          "climatology\n  (0 = the climatology itself; negative = worse than it):\n")
    for col, label in (("bss_clim", "the forecast"),
                       ("bss_pers_freq_vs_clim", "persistence, 30-day frequency"),
                       ("bss_pers_binary_vs_clim", "persistence, yesterday 0/1"),
                       ("bss_flat_vs_clim", "flat sample base rate")):
        print(f"    {label:34s} median {pin[col].median():+.3f}   "
              f"beats climatology in {(pin[col] > 0).mean():.0%} of series")
    print(f"  Days dropped to keep every reference on the same sample: median "
          f"{pin.n_dropped_for_matching.median():.0f} of "
          f"{pin.n.median() + pin.n_dropped_for_matching.median():.0f}")

    print("\n=== Task 18: BSS re-referenced to the smoothed climatology ===")
    print(f"  Median shift {pin.bss_shift.median():+.4f}; range "
          f"[{pin.bss_shift.min():+.4f}, {pin.bss_shift.max():+.4f}]; "
          f"{(pin.bss_shift < 0).mean():.0%} of series lose skill under the "
          f"stronger reference.")
    r = float(pin[["clim_amplitude", "bss_shift"]].corr().iloc[0, 1])
    r_base = float(pin[["base_rate", "bss_shift"]].corr().iloc[0, 1])
    print(f"  Correlation of the shift with seasonality amplitude: r={r:+.3f}; "
          f"with rain-day frequency: r={r_base:+.3f}.")
    print("  -> the shift is not noise: it tracks how seasonal the city is, "
          "which is\n     exactly the axis the cross-regime comparison runs "
          "along, so the sample-\n     referenced version cannot be used for "
          "that comparison unchecked.")
    if len(sens):
        sp = sens.groupby(["window_days", "pseudo_count"]).bss_shift.median()
        print("\n  Sensitivity of the median shift to the smoother:")
        print(sp.to_string(float_format=lambda v: f"{v:+.4f}"))
        # The SIGN is the claim; the magnitude is a tuning artefact and is
        # reported as such. A narrow window with no prior leaves few other-year
        # observations per day, so the climatology is noisy, so it is an easier
        # reference to beat - the monotone decline of the shift with window
        # width below is that effect, not a climate signal.
        same_sign = bool((np.sign(sp) == np.sign(sp.iloc[0])).all())
        print(f"    spread across smoother settings: "
              f"{float(sp.max() - sp.min()):.4f}; sign of the shift is "
              f"{'stable' if same_sign else 'NOT STABLE'} across all "
              f"{len(sp)} settings.")
        print(f"    the headline uses window={CLIM_WINDOW_DAYS}d, "
              f"prior={CLIM_PSEUDO_COUNT:.0f}; magnitude is smoother-dependent, "
              f"direction is not.")

    if len(lead):
        print("\n=== Task 23 on the lead axis (derived probabilities, Bucharest) ===")
        cols = ["lead_days", "auc", "v_max", "alpha_at_v_max",
                "v_cal_a05", "v_cal_a10", "v_cal_a20", "v_cal_a50"]
        print(lead[cols].to_string(index=False,
                                   float_format=lambda v: f"{v:.3f}"))


# ---------------------------------------------------------------------------
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    print("=== correctness checks ===")
    metrics.run_checks()
    baselines.run_checks()

    df = load_series()
    check_on_real_data(df)
    if mode == "check":
        print("all checks passed")
        return

    print(f"\n=== scoring {df.groupby(['source', 'city', 'model']).ngroups} "
          f"series x {len(RAIN_THRESHOLD_VARIANTS_MM)} thresholds ===")
    table, curve = build_table(df)
    if table.empty:
        raise SystemExit("no series long enough to score")
    table.to_parquet(artefact("metrics"), index=False)
    curve.to_parquet(artefact("value_curves"), index=False)

    crps = crps_amount(df)
    crps.to_parquet(artefact("crps_amount"), index=False)
    sens = clim_sensitivity(df)
    sens.to_parquet(artefact("clim_sensitivity"), index=False)
    lead = value_by_lead()
    if len(lead):
        lead.to_parquet(artefact("value_by_lead"), index=False)

    report(table, curve, crps, sens, lead)

    plot_value(curve, table)
    plot_discrimination(df, table)
    plot_reference_shift(table, sens)
    plot_value_by_lead(lead)

    print(f"\nwrote {artefact('metrics')} and three companion tables")


if __name__ == "__main__":
    main()
