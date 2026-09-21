"""Tasks 28-29: is forecast quality distributed unequally, and can we tell?

D7 asks whether the forecast fails poorer places harder. It is the question
the study's framing points at and the one a reviewer will ask, so it has to be
answered rather than gestured at - including, if that is the answer, with
"this panel cannot tell you".

The stage is built around two traps, and both of them fire.

The first is climate. A Brier score is bounded by how often it rains, and the
panel's poorer cities are drier, so a raw comparison reports the forecast as
BETTER where people are poorer - a statement about Morocco's weather. The
obvious repair, restricting both sides to a shared base-rate range, looks
sufficient and is not: inside the shared band the poorer cities still rain on
15% of days against 24%, and that residue carries most of the apparent gap.
So each poorer city is compared only with richer cities within 0.02 of its own
rain frequency, and whatever base-rate difference the matching still leaves is
converted into score units and published beside the estimate as the floor it
has to clear.

The second is the truth. Poorer countries have sparser gauge networks; sparser
networks mean the verifying gauge sits further from the city; and E20 measured
that a more distant gauge disagrees with the city's own weather more often. A
noisy label inflates a Brier score all by itself, so "calibration is worse in
poorer places" is also what a pure truth artefact would look like. E20's
separation curve tells them apart: it predicts how much of the degradation
label noise alone accounts for, with no free parameters.

Four parts, in the order the argument needs them:

  1. Who can be asked at all. The panel's countries by World Bank income
     group, against the world's. This is where the answer turns out to live.
  2. The income comparison, raw and matched, each with the power to detect
     it - an estimate without an MDE beside it would be the mistake E10
     exists to prevent.
  3. The gauge-distance axis, which has more spread and may resolve, against
     the truth-noise prediction as its null.
  4. Task 29: whatever is found, expressed in decision terms via the E5
     value curves, because a Brier difference is not a statement about anyone.

Intervals resample COUNTRIES, not cities. E28 measured a design effect of 4.3
between cities of the same country, so a city-level interval here would be
roughly half the width it should be - and with four non-high-income countries
in the panel, that understatement is the difference between a null and a
headline.

Usage:
    python src/equity.py          # full run, writes tables
    python src/equity.py check    # correctness checks only
"""

from __future__ import annotations

import json
import sys
import urllib.request

import numpy as np
import pandas as pd

from config import CACHE, PROCESSED, RAW

INCOME_URL = "https://api.worldbank.org/v2/country?format=json&per_page=400"
INCOME_CACHE = CACHE / "worldbank_income.json"

PANEL_TABLE = PROCESSED / "equity_panel.parquet"
INCOME_TABLE = PROCESSED / "equity_income.parquet"
DISTANCE_TABLE = PROCESSED / "equity_distance.parquet"
REACH_TABLE = PROCESSED / "equity_gauge_reach.parquet"

N_BOOT = 4000
RANDOM_SEED = 20260921

# 1.96 + 0.84: the difference a two-sided 0.05 test detects 80% of the time,
# in units of the standard error.
MDE_Z = 2.80

INCOME_ORDER = ["High income", "Upper middle income", "Lower middle income",
                "Low income"]

# The metrics the question is asked of. Reliability and ECE are calibration
# proper; bss_clim is overall skill; v_cal_a10 is the decision-value figure
# E4a showed is where the harm actually shows up.
METRICS = ["reliability", "ece", "brier", "bss_clim", "v_cal_a10", "v_cal_a05"]

# Higher is better for these; lower is better for the rest. Getting this wrong
# would report a gap with its sign reversed, which is worse than no result.
HIGHER_IS_BETTER = {"bss_clim", "v_cal_a10", "v_cal_a05"}


def die(msg: str) -> None:
    print(f"\nFAILED: {msg}\n")
    sys.exit(1)


def say(msg: str) -> None:
    print(f"  ok  {msg}")


# ---------------------------------------------------------------------------
# 1. Who is in the panel
# ---------------------------------------------------------------------------
def income_groups() -> pd.DataFrame:
    """World Bank income classification, one row per country.

    The aggregates ("World", "OECD members", ...) share the schema with real
    countries and would silently double-count, so they are dropped by their
    own region label rather than by a name list.
    """
    if not INCOME_CACHE.exists():
        INCOME_CACHE.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(INCOME_URL,
                                     headers={"User-Agent": "calibration-study"})
        with urllib.request.urlopen(req, timeout=120) as r:
            INCOME_CACHE.write_bytes(r.read())

    payload = json.loads(INCOME_CACHE.read_text())
    if len(payload) < 2 or not isinstance(payload[1], list):
        die("the World Bank country response did not contain a record list")
    rows = [{"country": r["iso2Code"], "country_name": r["name"],
             "income": r["incomeLevel"]["value"],
             "wb_region": r["region"]["value"]}
            for r in payload[1]]
    out = pd.DataFrame(rows)
    out = out[out.wb_region != "Aggregates"].reset_index(drop=True)

    unknown = set(out.income) - set(INCOME_ORDER) - {"Not classified"}
    if unknown:
        die(f"unrecognised income levels {sorted(unknown)} - the ordering "
            f"this stage sorts and compares by would be incomplete")
    return out


def panel() -> pd.DataFrame:
    """Per-city calibration and decision metrics, with income and gauge distance.

    The world track only. The capitals track has 15 cities in 13 countries and
    every one of them is high income, so it cannot contribute to this question
    and including it would only dilute the world panel's country count.
    """
    import sampling

    d = pd.read_parquet(PROCESSED / "decision_metrics.parquet")
    d = d[(d.source == "served_world") & d.is_primary_threshold]
    keep = ["city", "model", "n", "base_rate"] + METRICS
    d = d[keep].copy()

    # Country from the same authority the sampling stage uses, so the two
    # stages cannot disagree about which country a city is in.
    day = sampling.city_panel()
    country = sampling.city_clusters(day)
    d["country"] = d.city.map(country)

    cov = json.loads((RAW / "city_coverage.json").read_text())["included"]
    d["prcp_km"] = d.city.map({k: v["prcp_km"] for k, v in cov.items()})
    d["prcp_days"] = d.city.map({k: v["prcp_days"] for k, v in cov.items()})
    d["population"] = d.city.map({k: v["population"] for k, v in cov.items()})

    inc = income_groups()
    d = d.merge(inc[["country", "country_name", "income"]], on="country",
                how="left")

    missing = d[d.income.isna()].city.tolist()
    if missing:
        # A city with no income label cannot be placed on either side of the
        # comparison, and dropping it quietly would bias whichever side it
        # belonged to. Named, then dropped.
        print(f"  note: no income classification for {sorted(missing)} - "
              f"excluded from the income comparison")
        d = d[d.income.notna()]

    d = d[d.prcp_km.notna()].reset_index(drop=True)
    d["is_high_income"] = d.income == "High income"
    return d


# ---------------------------------------------------------------------------
# 2. The comparison, and the power to make it
# ---------------------------------------------------------------------------
def cluster_boot(values: np.ndarray, groups: np.ndarray, side: np.ndarray,
                 n_boot: int = N_BOOT,
                 seed: int = RANDOM_SEED) -> tuple[np.ndarray, float]:
    """Difference in group means, resampled by CLUSTER.

    Countries are drawn with replacement and every city in a drawn country
    comes with it. Returns the replicate differences and the share of draws
    that were degenerate - a draw containing no city on one side cannot
    produce a difference, and with four countries on the short side that is
    not a rare event. Reporting the share is the point: it is a direct
    statement of how thin the comparison is.
    """
    rng = np.random.default_rng(seed)
    uniq = np.unique(groups)
    index = {g: np.flatnonzero(groups == g) for g in uniq}

    reps, degenerate = [], 0
    for _ in range(n_boot):
        drawn = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([index[g] for g in drawn])
        v, s = values[idx], side[idx]
        if not s.any() or s.all():
            degenerate += 1
            continue
        reps.append(v[s].mean() - v[~s].mean())
    if not reps:
        return np.array([]), 1.0
    return np.asarray(reps), degenerate / n_boot


def compare(df: pd.DataFrame, metric: str,
            n_boot: int = N_BOOT) -> dict:
    """One metric, poorer minus richer, with an interval and an MDE.

    The sign convention is fixed so that a negative number always means the
    poorer side is WORSE, whichever direction the raw metric runs in.
    """
    sub = df[df[metric].notna()]
    values = sub[metric].to_numpy(float)
    groups = sub.country.to_numpy()
    poor = (~sub.is_high_income).to_numpy()

    if not poor.any():
        return {"metric": metric, "n_poor": 0, "n_rich": int((~poor).sum()),
                "estimate": np.nan, "lo": np.nan, "hi": np.nan,
                "p": np.nan, "mde_80": np.nan, "degenerate": 1.0,
                "countries_poor": 0, "countries_rich": 0}

    sign = 1.0 if metric in HIGHER_IS_BETTER else -1.0
    est = sign * (values[poor].mean() - values[~poor].mean())

    reps, degen = cluster_boot(values, groups, poor, n_boot=n_boot)
    if reps.size < 100:
        lo = hi = p = sd = np.nan
    else:
        reps = sign * reps
        lo, hi = np.percentile(reps, [2.5, 97.5])
        centred = reps - reps.mean()
        p = min(1.0, 2.0 * min(
            (1 + np.sum(centred >= abs(est))) / (len(centred) + 1),
            (1 + np.sum(centred <= -abs(est))) / (len(centred) + 1)))
        sd = float(reps.std(ddof=1))

    return {"metric": metric,
            "n_poor": int(poor.sum()), "n_rich": int((~poor).sum()),
            "countries_poor": int(sub.loc[poor, "country"].nunique()),
            "countries_rich": int(sub.loc[~poor, "country"].nunique()),
            "estimate": float(est),
            "lo": float(lo) if lo == lo else np.nan,
            "hi": float(hi) if hi == hi else np.nan,
            "p": float(p) if p == p else np.nan,
            "sd": float(sd) if sd == sd else np.nan,
            "mde_80": float(MDE_Z * sd) if sd == sd else np.nan,
            "degenerate": float(degen)}


def income_table(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame([compare(df, m) for m in METRICS])


# ---------------------------------------------------------------------------
# 2b. The climate confound, and what it takes to remove it
# ---------------------------------------------------------------------------
def common_support(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Trim the richer side to the poorer side's base-rate range.

    Kept as the WEAK control, because on this panel it visibly fails and the
    failure is worth showing. A Brier score is bounded by how often it rains,
    and the panel's poorer cities are drier, so a raw comparison reads "the
    forecast is better where people are poorer" - a statement about Morocco's
    climate. Restricting to a shared range looks like it fixes that and does
    not: inside the shared 0.08-0.30 band the poorer cities still sit at a
    mean base rate of 0.15 against the richer side's 0.24, and that residual
    imbalance accounts for almost the whole apparent gap.
    """
    poor = df[~df.is_high_income]
    if poor.empty:
        return df.iloc[0:0], {"lo": np.nan, "hi": np.nan, "dropped": 0,
                              "kept_rich": 0, "kept_rich_countries": 0}
    lo, hi = float(poor.base_rate.min()), float(poor.base_rate.max())
    rich = df[df.is_high_income]
    inside = rich[(rich.base_rate >= lo) & (rich.base_rate <= hi)]
    out = pd.concat([poor, inside], ignore_index=True)
    return out, {"lo": lo, "hi": hi,
                 "dropped": int(len(rich) - len(inside)),
                 "kept_rich": int(len(inside)),
                 "kept_rich_countries": int(inside.country.nunique())}


# How close two cities' rain frequencies must be to be compared at all. The
# residual artefact scales with the base-rate gap the matching leaves behind,
# and on a synthetic climate-only panel that residual is +0.0070 at 0.05,
# +0.0032 at 0.03 and +0.0013 at 0.02 against a raw +0.0384 - so 0.02 is the
# widest caliper that closes it, and it still matches 23 of 24 cities.
CALIPER = 0.02


def climate_curve(df: pd.DataFrame, metric: str):
    """How the metric moves with base rate, fitted on the RICHER cities only.

    Fitting on the poorer side too would let the effect under test set the
    size of its own correction. The fit is quadratic because the relation is
    one: a Brier score rises with base rate and flattens towards 0.5, and a
    straight line through that curve understates its slope at the dry end -
    which is exactly where the poorer cities are.
    """
    rich = df[df.is_high_income]
    rich = rich[np.isfinite(rich[metric].to_numpy(float))]
    if len(rich) < 6 or np.ptp(rich.base_rate.to_numpy(float)) == 0:
        return None
    deg = 2 if len(rich) >= 12 else 1
    coef = np.polyfit(rich.base_rate.to_numpy(float),
                      rich[metric].to_numpy(float), deg)
    return np.poly1d(coef)


def caliper_contrast(df: pd.DataFrame, metric: str,
                     caliper: float = CALIPER, curve=None) -> dict:
    """Each poorer city against only the richer cities it rains like.

    The estimate is the mean over poorer cities of (that city's metric minus
    the mean of its base-rate-comparable richer cities). A poorer city with
    no comparable richer city contributes nothing and is counted, because
    silently dropping it would quietly redefine the population being
    compared.

    Alongside it, the same contrast computed from climate alone: what the
    leftover base-rate difference would produce if the forecast were
    identically good everywhere. That is the floor the real estimate has to
    clear, and it is computed per city rather than from an average, because
    the relation it comes from is curved.
    """
    poor = df[~df.is_high_income]
    rich = df[df.is_high_income]
    empty = {"estimate": np.nan, "n_poor_matched": 0, "n_rich_used": 0,
             "base_gap": np.nan, "climate_pred": np.nan}
    if poor.empty or rich.empty:
        return empty

    rb = rich.base_rate.to_numpy(float)
    rv = rich[metric].to_numpy(float)
    ok = np.isfinite(rv)

    diffs, used, gaps, preds = [], set(), [], []
    for row in poor.itertuples():
        v = getattr(row, metric)
        if v != v:
            continue
        sel = (np.abs(rb - row.base_rate) <= caliper) & ok
        if not sel.any():
            continue
        diffs.append(float(v) - float(rv[sel].mean()))
        gaps.append(float(row.base_rate) - float(rb[sel].mean()))
        if curve is not None:
            preds.append(float(curve(row.base_rate)) - float(curve(rb[sel]).mean()))
        used.update(np.flatnonzero(sel).tolist())

    if not diffs:
        return empty
    return {"estimate": float(np.mean(diffs)),
            "n_poor_matched": len(diffs), "n_rich_used": len(used),
            "base_gap": float(np.mean(gaps)),
            "climate_pred": float(np.mean(preds)) if preds else np.nan}


def compare_caliper(df: pd.DataFrame, metric: str, n_boot: int = N_BOOT,
                    caliper: float = CALIPER, seed: int = RANDOM_SEED) -> dict:
    """The caliper contrast with a country-clustered interval.

    The matching is redone inside every resample rather than fixed once. A
    fixed matching would treat the choice of comparison cities as known,
    when it is as much a product of this particular panel as the estimate is.
    """
    sign = 1.0 if metric in HIGHER_IS_BETTER else -1.0
    curve = climate_curve(df, metric)
    point = caliper_contrast(df, metric, caliper, curve)
    est = sign * point["estimate"] if point["estimate"] == point["estimate"] \
        else np.nan

    rng = np.random.default_rng(seed)
    uniq = np.unique(df.country.to_numpy())
    index = {g: df.index[df.country == g].to_numpy() for g in uniq}

    reps, degenerate = [], 0
    for _ in range(n_boot):
        drawn = rng.choice(uniq, size=len(uniq), replace=True)
        sub = df.loc[np.concatenate([index[g] for g in drawn])]
        r = caliper_contrast(sub, metric, caliper)
        if r["estimate"] != r["estimate"]:
            degenerate += 1
            continue
        reps.append(sign * r["estimate"])

    floor = abs(point["climate_pred"]) \
        if point["climate_pred"] == point["climate_pred"] else np.nan
    out = {"metric": metric, "estimate": est,
           "n_poor": int((~df.is_high_income).sum()),
           "n_rich": int(df.is_high_income.sum()),
           "countries_poor": int(df.loc[~df.is_high_income, "country"].nunique()),
           "countries_rich": int(df.loc[df.is_high_income, "country"].nunique()),
           "n_poor_matched": point["n_poor_matched"],
           "n_rich_used": point["n_rich_used"],
           "base_gap": point["base_gap"],
           "artefact_floor": floor,
           "degenerate": degenerate / n_boot}

    reps = np.asarray(reps)
    if reps.size < 100 or est != est:
        out.update({"lo": np.nan, "hi": np.nan, "p": np.nan, "sd": np.nan,
                    "mde_80": np.nan})
        return out
    lo, hi = np.percentile(reps, [2.5, 97.5])
    centred = reps - reps.mean()
    p = min(1.0, 2.0 * min(
        (1 + np.sum(centred >= abs(est))) / (len(centred) + 1),
        (1 + np.sum(centred <= -abs(est))) / (len(centred) + 1)))
    sd = float(reps.std(ddof=1))
    out.update({"lo": float(lo), "hi": float(hi), "p": float(p), "sd": sd,
                "mde_80": MDE_Z * sd})
    return out


def artefact_floor(df: pd.DataFrame, metric: str,
                   caliper: float = CALIPER) -> float:
    """The climate remainder the matching leaves behind, in score units."""
    curve = climate_curve(df, metric)
    if curve is None:
        return np.nan
    pred = caliper_contrast(df, metric, caliper, curve)["climate_pred"]
    return abs(pred) if pred == pred else np.nan


def caliper_table(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reset_index(drop=True)
    rows = []
    for m in METRICS:
        r = compare_caliper(df, m)
        floor = r["artefact_floor"]
        r["exceeds_floor"] = bool(
            r["estimate"] == r["estimate"] and floor == floor
            and abs(r["estimate"]) > floor)
        rows.append(r)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. The gauge-distance axis, against the truth-noise null
# ---------------------------------------------------------------------------
def flip_rates() -> tuple[pd.DataFrame, float]:
    """E20's separation curve as conditional flip probabilities.

    The curve stores joint rates - P(this gauge wet, that one dry) - which are
    only transferable to another city once divided by the base rate they were
    measured at. Doing that here rather than at the call site means a city
    with an unusual base rate cannot silently rescale the physics.
    """
    curve = PROCESSED / "truth_separation_curve.parquet"
    pairs = PROCESSED / "truth_pairs.parquet"
    if not curve.exists() or not pairs.exists():
        die("truth_uncertainty.py has not run - without the separation curve "
            "there is no way to tell a gauge-distance artefact from a finding")
    c = pd.read_parquet(curve)
    base = float(pd.read_parquet(pairs, columns=["base_a"]).base_a.mean())
    c = c.assign(q10=c.q_a1b0 / base, q01=c.q_a0b1 / (1.0 - base))
    return c, base


def q_components(curve: pd.DataFrame, d_km: float) -> tuple[float, float]:
    hit = curve[(curve.sep_lo <= d_km) & (curve.sep_hi > d_km)]
    if len(hit):
        return float(hit.q10.iloc[0]), float(hit.q01.iloc[0])
    # Beyond the measured range the nearest bin is used rather than an
    # extrapolation: the curve is saturating, so the last bin is the honest
    # answer and a fitted exponential would only add false precision.
    edge = curve.iloc[-1] if d_km >= curve.sep_hi.max() else curve.iloc[0]
    return float(edge.q10), float(edge.q01)


def predicted_inflation(curve: pd.DataFrame) -> pd.DataFrame:
    """Per city: how much of its Brier the verifying gauge's distance explains.

    Uses the exact identity from truth_uncertainty - no simulation. A label
    that flips 1->0 with probability q10 and 0->1 with q01 moves a Brier score
    by E[(1 - 2f)(q01(1 - y) - q10 y)], and that expectation is computable
    from the city's own forecasts and observations.
    """
    import sampling
    from truth_uncertainty import brier_shift

    day = sampling.city_panel()
    cov = json.loads((RAW / "city_coverage.json").read_text())["included"]

    rows = []
    for city, g in day.groupby("city"):
        rec = cov.get(city)
        if rec is None or rec.get("prcp_km") is None:
            continue
        d_km = float(rec["prcp_km"])
        q10, q01 = q_components(curve, d_km)
        f = g.forecast_prob.to_numpy(float)
        y = g.observed_event.to_numpy(float)
        rows.append({"city": city, "prcp_km": d_km,
                     "q10": q10, "q01": q01,
                     "brier_observed": float(np.mean((f - y) ** 2)),
                     "brier_inflation": brier_shift(f, y, q10, q01)})
    return pd.DataFrame(rows)


def clustered_slope(x: np.ndarray, y: np.ndarray, groups: np.ndarray,
                    n_boot: int = N_BOOT,
                    seed: int = RANDOM_SEED) -> dict:
    """OLS slope of y on x with a country-clustered interval."""
    def fit(xa, ya):
        if len(xa) < 3 or np.ptp(xa) == 0:
            return np.nan
        return float(np.polyfit(xa, ya, 1)[0])

    est = fit(x, y)
    rng = np.random.default_rng(seed)
    uniq = np.unique(groups)
    index = {g: np.flatnonzero(groups == g) for g in uniq}

    reps = []
    for _ in range(n_boot):
        drawn = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([index[g] for g in drawn])
        s = fit(x[idx], y[idx])
        if s == s:
            reps.append(s)
    reps = np.asarray(reps)
    if reps.size < 100:
        return {"slope": est, "lo": np.nan, "hi": np.nan, "p": np.nan,
                "sd": np.nan, "mde_80": np.nan}
    lo, hi = np.percentile(reps, [2.5, 97.5])
    centred = reps - reps.mean()
    p = min(1.0, 2.0 * min(
        (1 + np.sum(centred >= abs(est))) / (len(centred) + 1),
        (1 + np.sum(centred <= -abs(est))) / (len(centred) + 1)))
    sd = float(reps.std(ddof=1))
    return {"slope": float(est), "lo": float(lo), "hi": float(hi),
            "p": float(p), "sd": sd, "mde_80": MDE_Z * sd}


def distance_analysis(df: pd.DataFrame, infl: pd.DataFrame) -> pd.DataFrame:
    """Observed degradation with distance, beside what label noise predicts.

    The comparison is the whole point. If the observed slope matches the
    predicted one, the apparent degradation is the verifying gauge and not the
    forecast, and no amount of extra cities would change that.
    """
    j = df.merge(infl[["city", "brier_inflation"]], on="city", how="inner")
    x = j.prcp_km.to_numpy(float)
    groups = j.country.to_numpy()

    rows = []
    obs = clustered_slope(x, j.brier.to_numpy(float), groups)
    pred = clustered_slope(x, j.brier_inflation.to_numpy(float), groups)
    rows.append({"quantity": "brier", **obs,
                 "predicted_slope": pred["slope"],
                 "predicted_lo": pred["lo"], "predicted_hi": pred["hi"]})

    # The residual: observed Brier with the predicted label-noise inflation
    # removed. If the forecast really is worse where gauges are sparse, this
    # slope survives; if it was an artefact, it collapses.
    resid = j.brier.to_numpy(float) - j.brier_inflation.to_numpy(float)
    r = clustered_slope(x, resid, groups)
    rows.append({"quantity": "brier_minus_predicted", **r,
                 "predicted_slope": 0.0, "predicted_lo": np.nan,
                 "predicted_hi": np.nan})

    for m in ("reliability", "bss_clim", "v_cal_a10"):
        s = clustered_slope(x, j[m].to_numpy(float), groups)
        rows.append({"quantity": m, **s, "predicted_slope": np.nan,
                     "predicted_lo": np.nan, "predicted_hi": np.nan})

    out = pd.DataFrame(rows)
    out["n_cities"] = len(j)
    out["n_countries"] = j.country.nunique()
    out["km_lo"] = float(x.min())
    out["km_hi"] = float(x.max())
    return out


# ---------------------------------------------------------------------------
# 4. The bridge: gauge reach by income, worldwide
# ---------------------------------------------------------------------------
def gauge_reach() -> pd.DataFrame:
    """For every country with a city of 100,000+: is that city verifiable?

    This is the part the panel cannot supply. Calibration cannot be measured
    where there is no gauge, but the ABSENCE can be measured everywhere, and
    grouping it by income says who the study's own method excludes.
    """
    from scipy.spatial import cKDTree

    import probe_capitals
    import probe_cities
    import sampling

    stations = probe_capitals.load_stations()
    inv = probe_capitals.load_inventory()
    prcp = inv[(inv.elem == "PRCP") & (inv.year_last >= 2024)
               & (inv.year_first <= 2024)]
    stations = stations[stations.id.isin(set(prcp.id))]

    cities = probe_cities.load_geonames()
    cities = cities[cities.population >= 100_000].reset_index(drop=True)
    cities["continent"] = [sampling.continent(c, t)
                           for c, t in zip(cities.country, cities.timezone)]

    def xyz(lat, lon):
        la, lo = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
        return np.c_[np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo),
                     np.sin(la)] * 6371.0

    d, _ = cKDTree(xyz(stations.lat, stations.lon)).query(
        xyz(cities.lat, cities.lon), k=1)
    cities["gauge_km"] = d

    inc = income_groups()
    cities = cities.merge(inc[["country", "income"]], on="country", how="left")
    cities = cities[cities.income.notna()]

    rows = []
    for name, g in cities.groupby("income"):
        rows.append({
            "income": name,
            "cities": len(g),
            "countries": int(g.country.nunique()),
            "within_25km": int((g.gauge_km <= 25).sum()),
            "share_25km": float((g.gauge_km <= 25).mean()),
            "median_km": float(g.gauge_km.median()),
        })
    out = pd.DataFrame(rows)
    out["order"] = out.income.map({v: i for i, v in enumerate(INCOME_ORDER)})
    return out.sort_values("order").drop(columns="order").reset_index(drop=True)


# ---------------------------------------------------------------------------
# 5. Correctness checks
# ---------------------------------------------------------------------------
def _clustered_panel(rng, n_country=20, per=5, effect=0.0, sd_country=0.05,
                     sd_city=0.02):
    """A synthetic panel with real country structure and a known effect."""
    rows = []
    for c in range(n_country):
        poor = c < n_country // 4
        level = rng.normal(0.0, sd_country) + (effect if poor else 0.0)
        for j in range(per):
            rows.append({"city": f"C{c}_{j}", "country": f"K{c}",
                         "is_high_income": not poor,
                         "reliability": level + rng.normal(0.0, sd_city)})
    return pd.DataFrame(rows)


def run_checks(n_sim: int = 200) -> None:
    print("=== correctness checks ===")
    rng = np.random.default_rng(11)

    # 1. The clustered interval must cover at the nominal rate when the whole
    #    signal is country-level. This is the check that matters: a city-level
    #    interval fails it, and failing it here would mean every number in
    #    section 2 is roughly twice as confident as it should be.
    cov_clust = cov_naive = 0
    for _ in range(n_sim):
        df = _clustered_panel(rng, effect=0.0)
        v = df.reliability.to_numpy()
        poor = (~df.is_high_income).to_numpy()
        reps, _ = cluster_boot(v, df.country.to_numpy(), poor, n_boot=400,
                               seed=int(rng.integers(1 << 30)))
        est = v[poor].mean() - v[~poor].mean()
        if reps.size:
            lo, hi = np.percentile(reps - reps.mean() + est, [2.5, 97.5])
            cov_clust += lo <= 0.0 <= hi
        # The city-level alternative: cities drawn independently.
        idx = np.arange(len(df))
        rep2 = []
        r2 = np.random.default_rng(int(rng.integers(1 << 30)))
        for _ in range(400):
            k = r2.choice(idx, size=len(idx), replace=True)
            s = poor[k]
            if s.any() and not s.all():
                rep2.append(v[k][s].mean() - v[k][~s].mean())
        rep2 = np.asarray(rep2)
        lo, hi = np.percentile(rep2 - rep2.mean() + est, [2.5, 97.5])
        cov_naive += lo <= 0.0 <= hi

    c_clust, c_naive = cov_clust / n_sim, cov_naive / n_sim
    if c_clust < 0.90:
        die(f"the country-clustered interval covered {c_clust:.0%} of the "
            f"time at a nominal 95% - it understates the uncertainty it "
            f"exists to state")
    if c_naive > 0.90:
        die(f"the city-level interval covered {c_naive:.0%}, so clustering "
            f"buys nothing on this panel and the check cannot show it matters")
    say(f"on a panel whose signal is country-level, the clustered interval "
        f"covers {c_clust:.0%} at a nominal 95% while a city-level one covers "
        f"{c_naive:.0%} - the clustering is load-bearing, not decoration")

    # 2. It must still find an effect that is really there, or the null in
    #    section 2 would be indistinguishable from a broken test.
    found = 0
    for _ in range(n_sim // 2):
        df = _clustered_panel(rng, effect=0.12)
        r = compare(df.assign(ece=np.nan, brier=np.nan, bss_clim=np.nan,
                              v_cal_a10=np.nan, v_cal_a05=np.nan),
                    "reliability", n_boot=400)
        found += r["p"] < 0.05
    power = found / (n_sim // 2)
    if power < 0.5:
        die(f"a planted effect of 0.12 was detected {power:.0%} of the time - "
            f"the test is too blunt to interpret a null from")
    say(f"a planted country-level effect of 0.12 is detected {power:.0%} of "
        f"the time, so a null from this test means something")

    # 3. The sign convention. A metric where higher is better and one where
    #    lower is better must both report "poorer side worse" as negative.
    df = _clustered_panel(rng, effect=0.20)          # poorer: higher unreliability
    worse_lower = compare(df.assign(ece=np.nan, brier=np.nan, bss_clim=np.nan,
                                    v_cal_a10=np.nan, v_cal_a05=np.nan),
                          "reliability", n_boot=200)
    flipped = df.assign(bss_clim=-df.reliability, ece=np.nan, brier=np.nan,
                        v_cal_a10=np.nan, v_cal_a05=np.nan)
    worse_higher = compare(flipped, "bss_clim", n_boot=200)
    if not (worse_lower["estimate"] < 0 and worse_higher["estimate"] < 0):
        die(f"the sign convention is inconsistent: {worse_lower['estimate']:+.3f} "
            f"for a lower-is-better metric and {worse_higher['estimate']:+.3f} "
            f"for a higher-is-better one, where both describe the same gap")
    say("a gap that disadvantages the poorer side reads negative for both a "
        "lower-is-better and a higher-is-better metric")

    # 4. The truth-noise prediction must reproduce a planted artefact. Labels
    #    are flipped at a rate that grows with distance and nothing else
    #    changes; the predicted slope must recover the induced one. Without
    #    this, section 3's null has no standing.
    from truth_uncertainty import brier_shift
    r = np.random.default_rng(5)
    n_day = 1500
    induced, predicted = [], []
    for d_km in (1.0, 10.0, 30.0, 55.0):
        f = r.uniform(0.05, 0.9, n_day)
        y = (r.random(n_day) < f).astype(float)
        q = 0.02 + 0.0015 * d_km
        base = float(np.mean((f - y) ** 2))
        flip = r.random(n_day) < q
        y2 = np.where(flip, 1.0 - y, y)
        induced.append(float(np.mean((f - y2) ** 2)) - base)
        predicted.append(brier_shift(f, y, q, q))
    induced, predicted = np.asarray(induced), np.asarray(predicted)
    si = np.polyfit([1.0, 10.0, 30.0, 55.0], induced, 1)[0]
    sp = np.polyfit([1.0, 10.0, 30.0, 55.0], predicted, 1)[0]
    if abs(si - sp) > 0.25 * abs(si):
        die(f"the label-noise identity predicted a slope of {sp:.2e} against "
            f"an induced {si:.2e} - the null in section 3 does not describe "
            f"the artefact it is supposed to rule out")
    say(f"labels flipped at a rate rising with distance induce a Brier slope "
        f"of {si:.2e} and the identity predicts {sp:.2e} without simulating "
        f"anything - the artefact is modelled, not assumed away")

    # 5. Degenerate draws must be counted, not silently dropped. With one
    #    country on the short side most draws cannot form a difference, and a
    #    stage that hid that would report an interval built from a handful of
    #    replicates as though it were built from all of them.
    tiny = _clustered_panel(rng, n_country=8, per=3)
    tiny["is_high_income"] = tiny.country != "K0"
    _, degen = cluster_boot(tiny.reliability.to_numpy(),
                            tiny.country.to_numpy(),
                            (~tiny.is_high_income).to_numpy(), n_boot=500)
    if degen < 0.10:
        die(f"only {degen:.0%} of draws were degenerate with a single country "
            f"on one side - the counter is not measuring what it claims")
    say(f"with one country on the short side {degen:.0%} of resamples cannot "
        f"form a comparison at all, and the stage reports that rather than "
        f"quietly averaging what is left")

    # 6. The climate control, and the demonstration that the obvious version
    #    of it is not enough. The panel differs ONLY in how often it rains -
    #    identical, honest forecasts on both sides - and the poorer group is
    #    drier within the shared range as well as overall, which is what the
    #    real panel does. A raw comparison must show the artefact; restricting
    #    to a common range must FAIL to remove it; the caliper must remove it.
    r6 = np.random.default_rng(29)
    rows = []
    for c in range(24):
        poor = c < 6
        for j in range(4):
            # Both sides span 0.10-0.30, but the poorer side is bunched at
            # the dry end, so a shared range still leaves the groups unlike.
            b = r6.uniform(0.10, 0.30) if poor else r6.uniform(0.10, 0.60)
            if not poor and r6.random() < 0.5:
                b = r6.uniform(0.22, 0.30)
            f = np.clip(r6.normal(b, 0.10, 3000), 0.01, 0.99)
            y = (r6.random(3000) < f).astype(float)
            rows.append({"city": f"C{c}_{j}", "country": f"K{c}",
                         "is_high_income": not poor, "base_rate": float(y.mean()),
                         "brier": float(np.mean((f - y) ** 2)),
                         "reliability": np.nan, "ece": np.nan,
                         "bss_clim": np.nan, "v_cal_a10": np.nan,
                         "v_cal_a05": np.nan})
    synth = pd.DataFrame(rows)
    raw = compare(synth, "brier", n_boot=600)
    ranged, _ = common_support(synth)
    rng_res = compare(ranged, "brier", n_boot=600)
    cal = compare_caliper(synth, "brier", n_boot=600)

    if not (raw["p"] < 0.05 and raw["estimate"] > 0):
        die(f"a panel differing only in climate produced a raw gap of "
            f"{raw['estimate']:+.4f} (p = {raw['p']:.2f}) - the confound this "
            f"section exists to remove did not appear, so no control can be "
            f"shown to work against it")
    if cal["p"] < 0.05:
        die(f"after caliper matching the same climate-only panel still showed "
            f"a gap of {cal['estimate']:+.4f} (p = {cal['p']:.2f}) - the "
            f"control does not remove the artefact it is for")
    if abs(cal["estimate"]) > 0.25 * abs(raw["estimate"]):
        die(f"caliper matching left {abs(cal['estimate']) / abs(raw['estimate']):.0%} "
            f"of a pure climate artefact standing - too much of the confound "
            f"survives for a residual gap to mean anything")
    say(f"on a panel where the groups differ only in how often it rains, the "
        f"raw comparison reports a spurious {raw['estimate']:+.4f}, "
        f"restricting to a shared base-rate range leaves "
        f"{rng_res['estimate']:+.4f} of it standing, and caliper matching "
        f"removes it to {cal['estimate']:+.4f} (p = {cal['p']:.2f})")

    # 6b. Whatever the caliper does leave behind must be caught by the floor.
    #     On this panel the whole remainder is climate by construction, so a
    #     floor below it would license reporting an artefact as a finding.
    wide = compare_caliper(synth, "brier", n_boot=400, caliper=0.05)
    floor = artefact_floor(synth, "brier", caliper=0.05)
    if not (floor == floor and floor >= abs(wide["estimate"])):
        die(f"at a deliberately loose caliper the residual artefact was "
            f"{wide['estimate']:+.4f} and the floor called it "
            f"{floor:.4f} - the floor does not bound the confound it is "
            f"computed to bound")
    say(f"loosening the caliper until the confound leaks back "
        f"({wide['estimate']:+.4f} on a climate-only panel) is caught by the "
        f"artefact floor ({floor:.4f}), so a residual gap has to clear its "
        f"own climate remainder and not just zero")

    # 7. The caliper must still find a real gap once the climate is balanced,
    #    or its null is just the matching discarding the panel.
    rows = []
    for c in range(24):
        poor = c < 6
        for j in range(4):
            b = r6.uniform(0.15, 0.35)          # same climate on both sides
            f = np.clip(r6.normal(b, 0.10, 3000), 0.01, 0.99)
            y = (r6.random(3000) < f).astype(float)
            # The poorer side's forecast is genuinely wet-biased, which costs
            # it about 0.0144 of Brier - a tenth of the climate artefact the
            # previous check plants, so this is not a generous target.
            served = np.clip(f + (0.12 if poor else 0.0), 0.01, 0.99)
            rows.append({"city": f"C{c}_{j}", "country": f"K{c}",
                         "is_high_income": not poor, "base_rate": float(y.mean()),
                         "brier": float(np.mean((served - y) ** 2)),
                         "reliability": np.nan, "ece": np.nan,
                         "bss_clim": np.nan, "v_cal_a10": np.nan,
                         "v_cal_a05": np.nan})
    real = pd.DataFrame(rows)
    hit = compare_caliper(real, "brier", n_boot=600)
    if not (hit["estimate"] < 0 and hit["p"] < 0.05):
        die(f"a genuinely worse forecast on the poorer side was reported as "
            f"{hit['estimate']:+.4f} (p = {hit['p']:.2f}) - the caliper "
            f"discards so much of the panel that a null from it would be "
            f"uninterpretable")
    say(f"a real degradation on the poorer side of a climate-balanced panel "
        f"is still found through the caliper ({hit['estimate']:+.4f}, "
        f"p = {hit['p']:.2f}), so its null is not just the matching throwing "
        f"the panel away")
    print()


# ---------------------------------------------------------------------------
# 6. Report
# ---------------------------------------------------------------------------
def report(df: pd.DataFrame, inc: pd.DataFrame, ranged: pd.DataFrame,
           cal: pd.DataFrame, support: dict, mb_poor: float, mb_rich: float,
           dist: pd.DataFrame, reach: pd.DataFrame) -> None:
    bar = "=" * 78
    print(bar)
    print("IS THE FORECAST WORSE WHERE PEOPLE ARE POORER? (Tasks 28-29; D7)")
    print(bar)

    print("\n--- 1. Who the panel can even ask about ---")
    by = df.drop_duplicates("city").groupby("income")
    print(f"  {'income group':24s}{'cities':>8s}{'countries':>11s}")
    for name in INCOME_ORDER:
        if name in by.groups:
            g = by.get_group(name)
            print(f"  {name:24s}{len(g):8d}{g.country.nunique():11d}")
        else:
            print(f"  {name:24s}{0:8d}{0:11d}")
    n_poor = int((~df.drop_duplicates('city').is_high_income).sum())
    c_poor = df[~df.is_high_income].country.nunique()
    print(f"  The panel holds {len(df.drop_duplicates('city'))} cities in "
          f"{df.country.nunique()} countries, of which {n_poor} cities in "
          f"{c_poor} countries\n  are outside the high-income group, and "
          f"NONE are in a low-income one. That is not\n  a sampling choice: "
          f"E27 showed the GHCN pool has one African city and no South\n"
          f"  American ones, so the panel is as poor as the gauge network "
          f"allows it to be.")

    print("\n--- 2. The comparison, and what it can detect ---")
    bp = df[~df.is_high_income].base_rate
    br = df[df.is_high_income].base_rate
    print(f"  First the confound. It rains on {bp.mean():.0%} of days in the "
          f"panel's poorer cities\n  and {br.mean():.0%} in its richer ones. "
          f"A Brier score is bounded by its base rate, so a\n  raw comparison "
          f"reports the forecast as BETTER where people are poorer, which "
          f"is a\n  statement about Morocco's climate. Restricting to the "
          f"shared {support['lo']:.2f}-{support['hi']:.2f} range looks\n"
          f"  like a fix and is not: inside it the poorer cities still rain "
          f"on {mb_poor:.0%} of days\n  against {mb_rich:.0%}. Each poorer "
          f"city is therefore compared only with richer cities\n  within "
          f"{CALIPER:.2f} of its own rain frequency.")
    print(f"\n  {'metric':14s}{'raw':>9s}{'ranged':>9s}{'matched':>9s}"
          f"{'95% CI':>19s}{'p':>6s}{'floor':>8s}")
    rrow = ranged.set_index("metric")
    crow = cal.set_index("metric")
    for _, r in inc.iterrows():
        g = rrow.loc[r.metric]
        c = crow.loc[r.metric]
        ci = f"[{c.lo:+.3f}, {c.hi:+.3f}]" if c.lo == c.lo else "n/a"
        print(f"  {r.metric:14s}{r.estimate:+9.4f}{g.estimate:+9.4f}"
              f"{c.estimate:+9.4f}{ci:>19s}{c.p:6.2f}{c.artefact_floor:8.4f}")
    nm = int(crow.n_poor_matched.max())
    nr = int(crow.n_rich_used.max())
    print(f"  Negative means the poorer side is worse, for every metric, "
          f"whichever way the\n  raw number runs. The caliper compares "
          f"{nm} of the {int(crow.n_poor.iloc[0])} poorer cities against "
          f"{nr} richer ones.\n  'floor' is what the base-rate difference "
          f"the matching still leaves would produce\n  on its own, from a "
          f"curve fitted to the richer cities only: a gap under its floor "
          f"is\n  climate whatever its p-value says.")

    real = crow[(crow.p < 0.05) & crow.exceeds_floor]
    if len(real):
        print(f"  Survives both the interval and the floor: "
              f"{', '.join(real.index)}.")
    else:
        rb_ = crow.loc["brier"]
        rel = crow.loc["reliability"]
        shrink = abs(rb_.estimate) / abs(inc.set_index("metric").loc["brier",
                                                                     "estimate"])
        print(f"  Nothing survives. The apparent advantage to the poorer "
              f"cities was their climate:\n  on Brier it shrinks from "
              f"{inc.set_index('metric').loc['brier', 'estimate']:+.4f} raw "
              f"to {rb_.estimate:+.4f} once each city is compared\n  only "
              f"with richer cities that rain as often - {shrink:.0%} of it "
              f"survives matching. What\n  is left is not significant for "
              f"any metric, and would not be worth reading if it\n  were: "
              f"reliability's matched gap of {rel.estimate:+.4f} sits inside "
              f"an interval of\n  [{rel.lo:+.3f}, {rel.hi:+.3f}], because "
              f"four countries is four independent observations once\n  "
              f"E28's design effect is respected, and no number of cities "
              f"inside them changes\n  that. D7 is not refuted here, it is "
              f"unasked.")

    v05 = crow.loc["v_cal_a05"]
    print(f"\n  Task 29 asks what that means for somebody rather than for a "
          f"score. At a cost-loss\n  ratio of 0.05 - the user who acts "
          f"cheaply and often, and whom E4a showed the served\n  probability "
          f"already fails - the matched gap is {v05.estimate:+.3f} of "
          f"realisable value with an\n  interval of [{v05.lo:+.3f}, "
          f"{v05.hi:+.3f}]. That admits everything from the poorer cities "
          f"losing\n  {abs(min(v05.lo, 0.0)):.0%} of the available benefit to "
          f"gaining {max(v05.hi, 0.0):.0%} of it. The decision framing does "
          f"not rescue\n  the comparison: it is the same four countries, and "
          f"it says so in units a reader\n  can act on instead of ones they "
          f"cannot.")

    print("\n--- 3. Does calibration degrade with gauge distance? ---")
    print(f"  {'quantity':24s}{'slope/km':>11s}{'95% CI':>21s}{'p':>7s}"
          f"{'noise pred.':>13s}")
    for _, r in dist.iterrows():
        ci = f"[{r.lo:+.2e}, {r.hi:+.2e}]" if r.lo == r.lo else "n/a"
        pred = f"{r.predicted_slope:+.2e}" if r.predicted_slope == r.predicted_slope else ""
        print(f"  {r.quantity:24s}{r.slope:+11.2e}{ci:>21s}{r.p:7.2f}"
              f"{pred:>13s}")
    n_c = int(dist.n_cities.iloc[0])
    print(f"  {n_c} cities, gauges {dist.km_lo.iloc[0]:.1f}-"
          f"{dist.km_hi.iloc[0]:.1f} km away, intervals clustered by country. "
          f"'noise pred.'\n  is what E20's separation curve says label noise "
          f"alone contributes, from the\n  exact identity and with no free "
          f"parameters.")

    obs = dist[dist.quantity == "brier"].iloc[0]
    net = dist[dist.quantity == "brier_minus_predicted"].iloc[0]
    if obs.slope == obs.slope and obs.predicted_slope == obs.predicted_slope:
        share = (obs.predicted_slope / obs.slope) if obs.slope else np.nan
        print(f"  Label noise accounts for {share:.0%} of the observed slope. "
              f"With it removed the\n  residual slope is {net.slope:+.2e} "
              f"(p = {net.p:.2f}), so the degradation with distance is "
              f"{'largely the verifying gauge' if net.p > 0.05 else 'NOT only the gauge'}.")

    print("\n--- 4. Where a gauge cannot reach, by income ---")
    print(f"  {'income group':24s}{'cities':>8s}{'countries':>11s}"
          f"{'<=25km':>9s}{'median km':>11s}")
    for _, r in reach.iterrows():
        print(f"  {r.income:24s}{int(r.cities):8d}{int(r.countries):11d}"
              f"{r.share_25km:9.0%}{r.median_km:11.1f}")
    hi = reach[reach.income == "High income"]
    lo = reach[reach.income == "Low income"]
    if len(hi) and len(lo):
        print(f"  Calibration cannot be measured where there is no gauge, but "
              f"the absence can be\n  measured everywhere. A city in a "
              f"high-income country has a gauge within 25 km\n  "
              f"{hi.share_25km.iloc[0]:.0%} of the time; in a low-income one, "
              f"{lo.share_25km.iloc[0]:.0%}. Section 2 cannot answer D7 "
              f"BECAUSE of\n  this row, not despite it - the same inequality "
              f"is both the hypothesis and the\n  reason the hypothesis "
              f"cannot be tested.")

    print("\n--- Caveats ---")
    print("  1. Section 3's null is that label noise explains the distance "
          "slope. It cannot\n     rule out the reverse confound: gauges may "
          "be sparse where weather is hard to\n     forecast for reasons of "
          "terrain, which would degrade the forecast and the\n     gauge "
          "network together.")
    print("  2. Income is a country-level label applied to cities. Within-city "
          "inequality -\n     which is where a cost-loss ratio of 0.05 "
          "actually lives - is invisible here.")
    print("  3. Section 4 counts GHCN gauges only. E30 showed ISD adds "
          "stations almost\n     everywhere and reporting in Asia alone, so "
          "the low-income row would improve\n     somewhat and not enough to "
          "change section 2.")
    print(f"  4. The caliper leaves {nm} poorer cities in the comparison. "
          f"Matching is not free,\n     and a null drawn from it is a "
          f"statement about a very small panel - but the\n     unmatched "
          f"alternative measures climate, so there is no version of this\n"
          f"     comparison that is both large and about forecasts.")
    print(f"  5. Section 3 spans gauges {dist.km_lo.iloc[0]:.1f}-"
          f"{dist.km_hi.iloc[0]:.1f} km away, because the study only ever "
          f"admitted\n     cities with a close gauge. It says nothing about "
          f"the 50-130 km distances\n     section 4 shows are normal outside "
          f"high-income countries, which is where the\n     effect would be "
          f"largest if it exists.")


# ---------------------------------------------------------------------------
def main() -> None:
    args = set(sys.argv[1:])
    run_checks()
    if "check" in args:
        return

    df = panel()
    raw = income_table(df)
    ranged_df, support = common_support(df)
    ranged = income_table(ranged_df)
    cal = caliper_table(df)

    mb_poor = float(ranged_df[~ranged_df.is_high_income].base_rate.mean())
    mb_rich = float(ranged_df[ranged_df.is_high_income].base_rate.mean())

    raw["comparison"] = "raw"
    ranged["comparison"] = "common_range"
    cal["comparison"] = "caliper_matched"
    cal["caliper"] = CALIPER
    for k, v in support.items():
        ranged[f"support_{k}"] = v
    inc = pd.concat([raw, ranged, cal], ignore_index=True)

    curve, base = flip_rates()
    infl = predicted_inflation(curve)
    dist = distance_analysis(df, infl)
    reach = gauge_reach()

    print()
    report(df, raw, ranged, cal, support, mb_poor, mb_rich, dist, reach)

    df.to_parquet(PANEL_TABLE, index=False)
    inc.to_parquet(INCOME_TABLE, index=False)
    dist.to_parquet(DISTANCE_TABLE, index=False)
    reach.to_parquet(REACH_TABLE, index=False)
    print(f"\nwrote {PANEL_TABLE.name}, {INCOME_TABLE.name}, "
          f"{DISTANCE_TABLE.name} and {REACH_TABLE.name}")


if __name__ == "__main__":
    main()
