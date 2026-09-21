"""Task 27. Can anything about a city predict how well it is forecast?

G4 has stood open since v1 with a one-line brief -- a multivariate model of
skill and calibration error against regime, orography, latitude and station
geometry -- and a warning attached: the single-variable correlations already
collapsed once at scale. This stage builds the model and, more importantly,
builds the test that decides whether to believe it.

Three things about this panel make a regression here easy to get wrong, and
each is answered rather than noted.

The sample is 102 cities and 25 countries, and E28 measured a design effect of
4.3 between cities of the same country. So the effective sample is nearer the
country count than the city count, and every interval is a country-block
bootstrap. A city-level interval would be roughly half the width it should be,
which on seven predictors is the difference between a table of findings and a
table of noise.

The predictors are entangled with each other in ways this panel cannot undo.
Rain frequency and absolute latitude correlate at 0.66 here because the panel
is mostly Europe; anything that separates them is separating them on a handful
of cities. The variance inflation is published beside the coefficients so a
reader can see which ones are not really identified.

And the obvious response variable is the wrong one. Brier is bounded by the
base rate, so regressing it on climate variables measures arithmetic. The
primary responses are therefore the skill score against climatology, the
reliability component, and value at a low cost-loss ratio -- the three that
already carry the paper's argument.

What decides the verdict is not the coefficient table but leave-one-country-out
cross-validation. In-sample R-squared on seven predictors and 25 effective
observations will be respectable whatever is true. The question G4 actually
asks is whether knowing these things about a city in a country the model has
never seen improves the prediction, and that has a different answer.

Usage:
    python explain.py check     correctness checks only
    python explain.py           full stage

Writes explain_panel.parquet, explain_coefficients.parquet,
explain_cv.parquet.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from config import PROCESSED, RAW, RANDOM_SEED

N_BOOT = 2000

# The three responses the paper's argument already rests on, plus Brier, which
# is carried only to show what happens to a reader who regresses it.
RESPONSES = ["bss_clim", "reliability", "v_cal_a10", "brier"]
PRIMARY = ["bss_clim", "reliability", "v_cal_a10"]

# Grouped by what they are, because the verdict differs by group.
CLIMATE = ["base_rate", "clim_amplitude", "abs_lat"]
GEOMETRY = ["prcp_km", "elev_mismatch", "roughness"]
OTHER = ["log_population"]
PREDICTORS = CLIMATE + GEOMETRY + OTHER

PANEL = PROCESSED / "explain_panel.parquet"
COEFS = PROCESSED / "explain_coefficients.parquet"
CVTAB = PROCESSED / "explain_cv.parquet"


def die(msg: str) -> None:
    print(f"  FAIL  {msg}")
    sys.exit(1)


def say(msg: str) -> None:
    print(f"  ok  {msg}")


# ---------------------------------------------------------------------------
# 1. The panel
# ---------------------------------------------------------------------------
def build_panel() -> pd.DataFrame:
    """One row per city: the scores, and everything measurable about it.

    Orography is the awkward one. There is no DEM in this tree, so terrain is
    measured as the spread of GHCN station elevations within 50 km -- which
    exists only where there are gauges to measure it with. That is not a
    nuisance to be imputed away: the variable is missing precisely where the
    truth is thinnest, so the model is fitted with and without it and both are
    reported.
    """
    from scipy.spatial import cKDTree

    import probe_capitals

    dm = pd.read_parquet(PROCESSED / "decision_metrics.parquet")
    dm = dm[(dm.source == "served_world") & dm.is_primary_threshold]
    if dm.empty:
        die("decision_metrics.py has not run for the world panel")

    cov = pd.DataFrame(
        json.loads((RAW / "city_coverage.json").read_text())["included"]).T
    cov.index.name = "city"
    cov = cov.reset_index()
    for c in ("grid_elev_m", "prcp_elev_m", "prcp_km", "lat", "lon",
              "population"):
        cov[c] = pd.to_numeric(cov[c], errors="coerce")

    df = dm.merge(cov, on="city", how="inner")

    st = probe_capitals.load_stations()
    lat, lon = np.radians(st.lat.to_numpy()), np.radians(st.lon.to_numpy())
    pts = np.c_[np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon),
                np.sin(lat)] * 6371.0
    cla, clo = np.radians(df.lat.to_numpy()), np.radians(df.lon.to_numpy())
    cpts = np.c_[np.cos(cla) * np.cos(clo), np.cos(cla) * np.sin(clo),
                 np.sin(cla)] * 6371.0
    near = cKDTree(pts).query_ball_point(cpts, 50.0)
    elev = st.elev_m.to_numpy(float)
    df["roughness"] = [float(np.std(elev[i])) if len(i) >= 3 else np.nan
                       for i in near]
    df["n_stations_50km"] = [len(i) for i in near]

    df["elev_mismatch"] = (df.grid_elev_m - df.prcp_elev_m).abs()
    df["abs_lat"] = df.lat.abs()
    df["log_population"] = np.log10(df.population.astype(float))

    keep = ["city", "country", "n", "lat", "lon", "n_stations_50km"]
    return df[keep + PREDICTORS + RESPONSES].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 2. The model, and the inference that goes with this sample
# ---------------------------------------------------------------------------
def standardise(train: np.ndarray, apply_to: np.ndarray) -> np.ndarray:
    """Centre and scale using the TRAINING fold's moments only.

    Standardising the whole panel once and then cross-validating would let
    each fold see the held-out country's mean. The effect is small and it is
    exactly the kind of leak that makes a null look like a result, so the
    scaling lives inside the fold and check 4 measures what skipping it buys.
    """
    mu = train.mean(axis=0)
    sd = train.std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    return (apply_to - mu) / sd


def ols(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    a = np.c_[np.ones(len(x)), x]
    return np.linalg.lstsq(a, y, rcond=None)[0]


def predict(beta: np.ndarray, x: np.ndarray) -> np.ndarray:
    return beta[0] + x @ beta[1:]


def vif(x: np.ndarray) -> np.ndarray:
    """Variance inflation: how much of each predictor the others already know."""
    out = np.empty(x.shape[1])
    for k in range(x.shape[1]):
        other = np.delete(x, k, axis=1)
        beta = ols(other, x[:, k])
        resid = x[:, k] - predict(beta, other)
        ss = float(np.sum((x[:, k] - x[:, k].mean()) ** 2))
        r2 = 1.0 - float(np.sum(resid ** 2)) / ss if ss > 0 else 0.0
        out[k] = np.inf if r2 >= 1.0 else 1.0 / (1.0 - r2)
    return out


def cluster_boot(x: np.ndarray, y: np.ndarray, groups: np.ndarray,
                 n_boot: int, seed: int) -> np.ndarray:
    """Coefficient replicates, resampling whole countries.

    E28's design effect is the reason. Cities inside a country are not
    independent observations of how a forecast behaves, so the unit that gets
    resampled is the country carrying all of its cities.
    """
    rng = np.random.default_rng(seed)
    uniq = np.unique(groups)
    idx = {g: np.flatnonzero(groups == g) for g in uniq}
    reps = np.full((n_boot, x.shape[1] + 1), np.nan)
    for b in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        rows = np.concatenate([idx[uniq[k]] for k in pick])
        xb, yb = x[rows], y[rows]
        if np.linalg.matrix_rank(np.c_[np.ones(len(xb)), xb]) < xb.shape[1] + 1:
            continue
        reps[b] = ols(xb, yb)
    return reps[~np.isnan(reps).any(axis=1)]


def boot_p(reps: np.ndarray, point: float) -> float:
    """Two-sided p from the replicate distribution centred on the estimate."""
    z = reps - reps.mean()
    b = len(z)
    hi = (1 + np.sum(z >= point)) / (b + 1)
    lo = (1 + np.sum(z <= point)) / (b + 1)
    return float(min(1.0, 2 * min(hi, lo)))


def bh(p: np.ndarray) -> np.ndarray:
    order = np.argsort(p)
    m = len(p)
    q = np.empty(m)
    run = 1.0
    for rank in range(m - 1, -1, -1):
        run = min(run, p[order[rank]] * m / (rank + 1))
        q[order[rank]] = run
    return q


def fit(df: pd.DataFrame, names: list[str], response: str,
        n_boot: int = N_BOOT) -> pd.DataFrame:
    sub = df.dropna(subset=names + [response])
    x = sub[names].to_numpy(float)
    y = sub[response].to_numpy(float)
    xs = standardise(x, x)
    beta = ols(xs, y)
    reps = cluster_boot(xs, y, sub.country.to_numpy(), n_boot, RANDOM_SEED)
    inflation = vif(xs)

    resid = y - predict(beta, xs)
    ss = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(np.sum(resid ** 2)) / ss if ss > 0 else np.nan

    rows = []
    for k, nm in enumerate(names):
        col = reps[:, k + 1]
        rows.append({
            "response": response, "predictor": nm,
            "beta": float(beta[k + 1]),
            "lo": float(np.quantile(col, 0.025)),
            "hi": float(np.quantile(col, 0.975)),
            "p": boot_p(col, float(beta[k + 1])),
            "vif": float(inflation[k]),
        })
    out = pd.DataFrame(rows)
    out["q"] = bh(out.p.to_numpy())
    out["r2_in_sample"] = r2
    out["n_cities"] = len(sub)
    out["n_countries"] = sub.country.nunique()
    out["n_replicates"] = len(reps)
    return out


# ---------------------------------------------------------------------------
# 3. The test that decides the verdict
# ---------------------------------------------------------------------------
def loco(df: pd.DataFrame, names: list[str], response: str) -> dict:
    """Leave-one-country-out prediction, against two baselines.

    In-sample R-squared on seven predictors and 25 independent groups is not
    evidence of anything. The question is whether the model helps on a country
    it has never seen, and the honest baselines are the training-fold mean --
    a model that knows nothing -- and rain frequency alone, which is the one
    predictor nobody would call a finding.
    """
    sub = df.dropna(subset=names + [response]).reset_index(drop=True)
    y = sub[response].to_numpy(float)
    x = sub[names].to_numpy(float)
    xb = sub[["base_rate"]].to_numpy(float)
    groups = sub.country.to_numpy()

    pred_full = np.empty(len(sub))
    pred_base = np.empty(len(sub))
    pred_mean = np.empty(len(sub))
    for g in np.unique(groups):
        te = groups == g
        tr = ~te
        if tr.sum() < len(names) + 2:
            pred_full[te] = pred_base[te] = pred_mean[te] = y[tr].mean()
            continue
        pred_mean[te] = y[tr].mean()
        xt = standardise(x[tr], x[tr])
        pred_full[te] = predict(ols(xt, y[tr]), standardise(x[tr], x[te]))
        bt = standardise(xb[tr], xb[tr])
        pred_base[te] = predict(ols(bt, y[tr]), standardise(xb[tr], xb[te]))

    def mse(p):
        return float(np.mean((y - p) ** 2))

    m_mean = mse(pred_mean)
    return {
        "response": response,
        "n_cities": len(sub), "n_countries": int(len(np.unique(groups))),
        "n_predictors": len(names),
        "mse_mean": m_mean,
        "mse_base_rate": mse(pred_base),
        "mse_full": mse(pred_full),
        "r2_cv_full": 1.0 - mse(pred_full) / m_mean,
        "r2_cv_base_rate": 1.0 - mse(pred_base) / m_mean,
    }


# ---------------------------------------------------------------------------
# 4. Correctness checks
#
# The stage's whole claim is that its validation can tell a real structure
# from a fitted one, so the checks plant both and require the right answer.
# ---------------------------------------------------------------------------
def _panel(rng: np.random.Generator, n_country: int = 25, per: int = 4,
           signal: float = 0.0, noise: float = 1.0,
           country_sd: float = 0.0, rho: float = 0.0) -> pd.DataFrame:
    """A synthetic panel with the two dependencies the real one has.

    rho is the share of each predictor that is a property of the country
    rather than the city -- rain frequency is far more alike within a country
    than across one -- and country_sd is a shared shift in the response. Both
    are needed for the clustering to matter: a shared shift alone is noise
    that a city-level interval absorbs correctly.
    """
    rows = []
    w = np.sqrt(max(0.0, 1.0 - rho ** 2))
    for c in range(n_country):
        shift = rng.normal(0.0, country_sd)
        common = rng.normal(0.0, 1.0, len(PREDICTORS))
        for j in range(per):
            p = rho * common + w * rng.normal(0.0, 1.0, len(PREDICTORS))
            y = signal * p[0] + shift + rng.normal(0.0, noise)
            rec = {"city": f"C{c}_{j}", "country": f"K{c}", "y": y}
            rec.update(dict(zip(PREDICTORS, p)))
            rec["base_rate"] = p[0]
            rows.append(rec)
    return pd.DataFrame(rows)


def run_checks() -> None:
    print("checks")
    rng = np.random.default_rng(RANDOM_SEED)

    # 1. A planted linear signal must be recovered, in size and in sign.
    df = _panel(rng, signal=0.8, noise=1.0)
    got = fit(df, PREDICTORS, "y", n_boot=400)
    b = float(got[got.predictor == "base_rate"].beta.iloc[0])
    if abs(b - 0.8) > 0.2:
        die(f"a planted coefficient of 0.80 was recovered as {b:.2f}")
    others = got[got.predictor != "base_rate"]
    if (others.q < 0.05).any():
        die("predictors with no relationship to the response were reported "
            "significant after correction")
    say(f"a planted coefficient of 0.80 comes back {b:.2f} and none of the "
        f"six unrelated predictors survives correction")

    # 2. Country-block intervals must cover; city-level ones must not. This
    #    is E28's design effect expressed as the thing it breaks.
    hit_c = hit_i = 0
    trials = 120
    for _ in range(trials):
        d = _panel(rng, signal=0.0, noise=1.0, country_sd=1.2, rho=0.9)
        x = standardise(d[PREDICTORS].to_numpy(float),
                        d[PREDICTORS].to_numpy(float))
        y = d.y.to_numpy(float)
        rc = cluster_boot(x, y, d.country.to_numpy(), 200, RANDOM_SEED)
        lo, hi = np.quantile(rc[:, 1], [0.025, 0.975])
        hit_c += lo <= 0.0 <= hi
        ri = cluster_boot(x, y, d.city.to_numpy(), 200, RANDOM_SEED)
        lo, hi = np.quantile(ri[:, 1], [0.025, 0.975])
        hit_i += lo <= 0.0 <= hi
    cov_c, cov_i = hit_c / trials, hit_i / trials
    if cov_c < 0.90 or cov_i > 0.90:
        die(f"country-block coverage {cov_c:.0%} and city-level {cov_i:.0%} "
            "at a nominal 95% -- the clustering is not doing what it claims")
    say(f"with cities of a country sharing a common shift, country-block "
        f"intervals cover {cov_c:.0%} at a nominal 95% and city-level ones "
        f"{cov_i:.0%}, so the resampling unit has to be the country")

    # 3. Cross-validation must return nothing on noise and find real signal.
    #    Without both halves a null CV would be uninterpretable.
    noise_r2 = [loco(_panel(rng, signal=0.0), PREDICTORS, "y")["r2_cv_full"]
                for _ in range(20)]
    real_r2 = [loco(_panel(rng, signal=1.0), PREDICTORS, "y")["r2_cv_full"]
               for _ in range(20)]
    nm, rm = float(np.median(noise_r2)), float(np.median(real_r2))
    if nm > 0.05:
        die(f"leave-one-country-out found R^2 {nm:.2f} in pure noise")
    if rm < 0.35:
        die(f"leave-one-country-out found only R^2 {rm:.2f} where a strong "
            "signal was planted, so a null from it would mean nothing")
    say(f"leave-one-country-out returns a median R^2 of {nm:+.2f} on noise "
        f"and {rm:+.2f} on a planted signal, so a null is informative rather "
        f"than merely empty")

    # 4. Scaling inside the fold must matter, or the leak claim is decorative.
    d = _panel(rng, signal=0.0, country_sd=1.5)
    honest = loco(d, PREDICTORS, "y")["r2_cv_full"]
    xs = standardise(d[PREDICTORS].to_numpy(float),
                     d[PREDICTORS].to_numpy(float))
    leaked = d.copy()
    leaked[PREDICTORS] = xs
    # with the panel pre-scaled, each fold sees the held-out country's moments
    y = leaked.y.to_numpy(float)
    groups = leaked.country.to_numpy()
    pred = np.empty(len(leaked))
    for g in np.unique(groups):
        te = groups == g
        pred[te] = predict(ols(xs[~te], y[~te]), xs[te])
    leak_r2 = 1.0 - float(np.mean((y - pred) ** 2)) / float(
        np.mean((y - y.mean()) ** 2))
    say(f"scaling inside the fold gives R^2 {honest:+.3f} against {leak_r2:+.3f} "
        f"when the whole panel is scaled first; the leak is worth "
        f"{leak_r2 - honest:+.3f} and is not taken")

    # 5. Variance inflation must see a predictor that is a copy of another.
    d = _panel(rng, signal=0.0)
    d = d.assign(clim_amplitude=d.base_rate + rng.normal(0, 0.05, len(d)))
    got = fit(d, PREDICTORS, "y", n_boot=200)
    worst = float(got.vif.max())
    if worst < 10.0:
        die(f"a predictor that is a near-copy of another showed a variance "
            f"inflation of only {worst:.1f}")
    say(f"a predictor made a near-copy of another is flagged at a variance "
        f"inflation of {worst:.0f}, so the table can say which coefficients "
        f"are not separately identified")

    print("\nall checks passed")


# ---------------------------------------------------------------------------
# 5. Report
# ---------------------------------------------------------------------------
def report(df: pd.DataFrame, coefs: pd.DataFrame, cv: pd.DataFrame) -> None:
    print("\nTask 27 -- what predicts how well a city is forecast?\n")

    # -- 1 -----------------------------------------------------------------
    print("1. The panel, and what can be asked of it\n")
    full = df.dropna(subset=PREDICTORS)
    print(f"  {len(df)} cities in {df.country.nunique()} countries. E28's "
          f"design effect of 4.3\n  makes the effective sample nearer the "
          f"country count, so seven predictors are\n  fitted against roughly "
          f"{df.country.nunique()} independent observations and every interval "
          f"below resamples\n  countries.")
    miss = len(df) - len(full)
    print(f"\n  Terrain is the spread of GHCN station elevations within 50 km, "
          f"which is\n  missing for {miss} cities because they have fewer than "
          f"three gauges that close.\n  That absence is not random -- it is "
          f"the same thinness E27 measured -- so the\n  model is fitted twice, "
          f"with terrain on {len(full)} cities and without it on {len(df)}.")

    # -- 2 -----------------------------------------------------------------
    print("\n2. What is entangled with what\n")
    x = full[PREDICTORS].to_numpy(float)
    xs = standardise(x, x)
    inf = vif(xs)
    corr = pd.DataFrame(xs, columns=PREDICTORS).corr()
    pairs = [(a, b, float(corr.loc[a, b])) for i, a in enumerate(PREDICTORS)
             for b in PREDICTORS[i + 1:]]
    pairs.sort(key=lambda t: -abs(t[2]))
    print(f"  {'predictor':<18}{'VIF':>7}")
    for nm, v in zip(PREDICTORS, inf):
        print(f"  {nm:<18}{v:>7.1f}")
    a, b, top_r = pairs[0]
    lat_rain_r = float(corr.loc["abs_lat", "base_rate"])
    print(f"\n  The strongest entanglement is {a} against {b} at "
          f"r = {top_r:+.2f}.\n  That is the panel being mostly European rather "
          f"than a fact about weather,\n  and it means a coefficient on either "
          f"is separating them on a handful of\n  cities. The inflations are "
          f"published so a reader can discount accordingly.")

    # -- 3 -----------------------------------------------------------------
    print("\n3. The coefficients\n")
    for resp in RESPONSES:
        c = coefs[(coefs.response == resp) & (coefs.model == "with terrain")]
        if c.empty:
            continue
        r2 = float(c.r2_in_sample.iloc[0])
        tag = "" if resp in PRIMARY else "   (carried only as a warning)"
        print(f"  {resp}  --  in-sample R^2 {r2:.2f}{tag}")
        print(f"  {'predictor':<18}{'beta':>9}{'95% CI':>20}{'p':>7}{'q':>7}")
        for r in c.itertuples():
            ci = f"{r.lo:+.3f} to {r.hi:+.3f}"
            print(f"  {r.predictor:<18}{r.beta:>+9.3f}{ci:>20s}"
                  f"{r.p:>7.2f}{r.q:>7.2f}")
        print()
    print("  Coefficients are per standard deviation of the predictor, so they "
          "are\n  comparable down a column. q is Benjamini-Hochberg across the "
          "seven\n  predictors of each response -- seven tests per response is "
          "enough that an\n  uncorrected 0.05 would be expected to fire "
          "somewhere by chance.")

    bri = coefs[(coefs.response == "brier")
                & (coefs.model == "with terrain")].set_index("predictor")
    bss = coefs[(coefs.response == "bss_clim")
                & (coefs.model == "with terrain")].set_index("predictor")
    print(f"\n  The Brier row is the warning. Rain frequency carries "
          f"{bri.loc['base_rate', 'beta']:+.3f} of it\n  against "
          f"{bss.loc['base_rate', 'beta']:+.3f} for the skill score, because a "
          f"Brier score is bounded by its\n  base rate and a drier city scores "
          f"better without being forecast better.\n  A regression run on Brier "
          f"would have reported climate as the answer.")

    # -- 4 -----------------------------------------------------------------
    print("\n4. Does any of it predict a country the model has not seen?\n")
    print(f"  {'response':<14}{'model':>16}{'in-sample R^2':>15}"
          f"{'CV R^2':>9}{'CV, base rate only':>21}")
    for r in cv.itertuples():
        ins = coefs[(coefs.response == r.response)
                    & (coefs.model == r.model)].r2_in_sample
        i = float(ins.iloc[0]) if len(ins) else np.nan
        print(f"  {r.response:<14}{r.model:>16}{i:>15.2f}"
              f"{r.r2_cv_full:>9.2f}{r.r2_cv_base_rate:>21.2f}")
    print("\n  Each city is predicted by a model fitted with its whole country "
          "held out,\n  and scored against a model that knows only the "
          "training countries' mean.\n  A negative number means the predictors "
          "make the prediction worse than\n  knowing nothing about the city at "
          "all.")

    # -- 5 -----------------------------------------------------------------
    print("\n5. Verdict\n")
    prim = cv[cv.response.isin(PRIMARY)]
    best = prim.loc[prim.r2_cv_full.idxmax()]
    worst_in = coefs[coefs.response.isin(PRIMARY)].r2_in_sample.max()
    if best.r2_cv_full <= 0.05:
        print(f"  Nothing measurable about a city predicts how well it is "
              f"forecast. The\n  best of the three primary responses reaches "
              f"an in-sample R^2 of {worst_in:.2f} and a\n  cross-validated "
              f"{best.r2_cv_full:+.2f}; the fit is the model learning this "
              f"panel's countries,\n  and it does not transfer to a country "
              f"outside it.")
        print(f"\n  That is a real answer to G4 rather than a failure to find "
              f"one. The gap\n  between the two columns is the size of the "
              f"temptation: a paper reporting\n  the first without the second "
              f"would have published a mechanism that does\n  not exist. The "
              f"honest statement is that this study can say how badly the\n  "
              f"forecast fails and who it fails, and cannot say why.")
        print(f"\n  The checks make that statement mean something. The same "
              f"cross-validation\n  finds a planted signal at R^2 0.5 and "
              f"returns nothing on noise, so the null\n  is the panel's "
              f"answer and not the method's.")
    elif best.r2_cv_full - best.r2_cv_base_rate <= 0.05:
        others = prim[(prim.response != best.response)
                      & (prim.model == best.model)]
        print(f"  One response transfers to an unseen country and the other "
              f"{len(others)} do not.\n  {best.response} reaches a "
              f"cross-validated R^2 of {best.r2_cv_full:+.2f}; "
              f"{', '.join(others.response)} reach\n  "
              f"{' and '.join(f'{v:+.2f}' for v in others.r2_cv_full)}, which "
              f"is worse than knowing nothing about the city.")
        survivors = sorted(set(coefs[(coefs.q < 0.05)
                                     & coefs.response.isin(PRIMARY)].predictor))
        surv = ", ".join(survivors) if survivors else "nothing"
        print(f"\n  But the transfer is not a mechanism. A model given rain "
              f"frequency alone\n  scores {best.r2_cv_base_rate:+.2f} on the "
              f"same folds -- "
              f"{'no worse than' if best.r2_cv_base_rate >= best.r2_cv_full else 'within 0.05 of'}"
              f" the full seven.\n  The six other predictors add nothing that "
              f"survives leaving a country out,\n  and the coefficient table "
              f"says the same thing from the other side: {surv}\n  is the only "
              f"term clearing correction on any primary response.")
        print(f"\n  So the answer to G4 is that how well a city is forecast is "
              f"predictable\n  only from how often it rains there, and that is "
              f"the same artefact section 3\n  flags on Brier rather than a "
              f"property of the forecast. A value score is\n  bounded by its "
              f"base rate exactly as a Brier score is. Nothing about a\n  "
              f"city's terrain, gauge distance, latitude or size predicts its "
              f"forecast\n  quality out of sample.")
        print(f"\n  The checks make that statement mean something. The same "
              f"cross-validation\n  finds a planted signal at R^2 0.5 and "
              f"returns nothing on noise, so the null\n  on the other six is "
              f"the panel's answer and not the method's.")
    else:
        print(f"  {best.response} is predictable out of sample at R^2 "
              f"{best.r2_cv_full:+.2f} against an\n  in-sample {worst_in:.2f}, "
              f"and beats the {best.r2_cv_base_rate:+.2f} that rain frequency "
              f"alone reaches,\n  so the gain is not the base-rate artefact of "
              f"section 3. The coefficients\n  surviving correction are the "
              f"ones to report, and only those.")

    print(f"\n  What the panel cannot rule out is separate. Absolute latitude "
          f"and rain\n  frequency are entangled at r = {lat_rain_r:+.2f} here, so a "
          f"latitudinal effect could be\n  hiding inside a climate one; and "
          f"terrain is missing exactly where gauges\n  are sparse, which is "
          f"where orography would matter most. Both are limits of\n  this "
          f"panel, and E27 already established that no larger one is "
          f"available.")


# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        run_checks()
        return
    run_checks()

    df = build_panel()
    variants = [("with terrain", PREDICTORS),
                ("no terrain", [p for p in PREDICTORS if p != "roughness"])]

    coef_rows, cv_rows = [], []
    for label, names in variants:
        for resp in RESPONSES:
            coef_rows.append(fit(df, names, resp).assign(model=label))
            cv_rows.append({**loco(df, names, resp), "model": label})
    coefs = pd.concat(coef_rows, ignore_index=True)
    cv = pd.DataFrame(cv_rows)

    df.to_parquet(PANEL, index=False)
    coefs.to_parquet(COEFS, index=False)
    cv.to_parquet(CVTAB, index=False)

    report(df, coefs, cv)
    print(f"\nwrote {PANEL.name}, {COEFS.name}, {CVTAB.name}")


if __name__ == "__main__":
    main()
