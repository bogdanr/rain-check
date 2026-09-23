"""Tasks 10 and 16 (G1, G14, G20): what sample can this study actually have?

Phase 3 says "lift the city budget toward the full qualifying pool" and G1 sets
the target at 2,000 cities on all inhabited continents. Before spending days of
API budget reaching for that, this module asks three questions the plan has
been assuming answers to:

  1. Does the pool CONTAIN such a sample? The qualifying pool is 8,150 cities,
     which sounds like plenty until it is broken down. It is not plenty, and
     the shortfall is not where the plan expects it.

  2. What does the country cap DO? It exists to stop the sample being a map of
     GHCN reporting density. That is a real risk and the cap is a blunt
     instrument, so its effect on the composition and on the headline numbers
     is measured here rather than asserted (Task 16, G14).

  3. How many cities would actually BUY anything? Cities in the same region
     share weather, so a panel's precision does not improve as the square root
     of its size. The saturation curve here says what n is worth collecting,
     and it is the number Task 10 should be aimed at rather than a round
     figure from the gap list.

Everything runs off tables already on disk plus the cached GHCN inventory. No
network.

Run:  python sampling.py            full run, checks first
      python sampling.py check      correctness checks only
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from config import PROCESSED, RAW, load_capitals

POOL_CACHE = RAW / "city_pool.parquet"

CENSUS_TABLE = PROCESSED / "sampling_pool_census.parquet"
CAP_TABLE = PROCESSED / "sampling_cap_sensitivity.parquet"
SATURATION_TABLE = PROCESSED / "sampling_saturation.parquet"
DESIGN_TABLE = PROCESSED / "sampling_design.parquet"

# The margin that matters, carried over from the boundary-rule sensitivity: a
# Brier difference smaller than this is inside the study's own arbitrary
# choices and is not worth resolving.
MARGIN_BRIER = 0.0015
N_BOOT = 600
RANDOM_SEED = 20260921

# ---------------------------------------------------------------------------
# Continent. The timezone prefix gets Europe, Asia, Africa and Australia right
# and lumps two continents into "America", so that one is split by country.
# Written out rather than pulled from a package so the assignment is auditable.
# ---------------------------------------------------------------------------
SOUTH_AMERICA = {"AR", "BO", "BR", "CL", "CO", "EC", "FK", "GF", "GY", "PE",
                 "PY", "SR", "UY", "VE"}
CENTRAL_AMERICA = {"BZ", "CR", "GT", "HN", "MX", "NI", "PA", "SV",
                   "CU", "DO", "HT", "JM", "PR", "TT", "BS", "BB", "VI",
                   "AG", "DM", "GD", "KN", "LC", "VC", "AW", "CW", "BM",
                   "KY", "TC", "VG", "AI", "MS", "GP", "MQ", "BL", "MF"}


def continent(country: str, timezone: str) -> str:
    head = str(timezone).split("/")[0]
    if head == "America":
        if country in SOUTH_AMERICA:
            return "South America"
        if country in CENTRAL_AMERICA:
            return "Central America & Caribbean"
        return "North America"
    if head in ("Europe", "Asia", "Africa", "Australia", "Pacific",
                "Indian", "Atlantic", "Antarctica", "Arctic"):
        return {"Australia": "Oceania", "Pacific": "Oceania",
                "Indian": "Indian Ocean", "Atlantic": "Atlantic islands"}.get(
                    head, head)
    return head


def die(msg: str) -> None:
    print(f"\nFAILED: {msg}", file=sys.stderr)
    raise SystemExit(1)


def say(msg: str) -> None:
    print(f"  ok  {msg}")


# ---------------------------------------------------------------------------
# 1. What is in the pool?
# ---------------------------------------------------------------------------
def load_pool() -> pd.DataFrame:
    # The pool also carries GHCNh/ISD cities (probe_cities.with_external), so
    # a cache written before those stages last ran is a different pool -
    # rebuild rather than sweep a stale one.
    feeders = [RAW / "ghcnh_candidates.json", RAW / "isd_candidates.json"]
    stale = POOL_CACHE.exists() and any(
        f.exists() and f.stat().st_mtime > POOL_CACHE.stat().st_mtime
        for f in feeders)
    if POOL_CACHE.exists() and not stale and \
            "truth" not in pd.read_parquet(POOL_CACHE).columns:
        stale = True
    if stale or not POOL_CACHE.exists():
        import probe_cities
        pool = probe_cities.candidate_pool()
        pool.to_parquet(POOL_CACHE, index=False)
    pool = pd.read_parquet(POOL_CACHE)
    pool["continent"] = [continent(c, t)
                         for c, t in zip(pool.country, pool.timezone)]
    return pool


def pool_census(pool: pd.DataFrame,
                floors: tuple[int, ...] = (0, 50_000, 100_000, 300_000)
                ) -> pd.DataFrame:
    """The pool by continent at several population floors.

    Two floors are shown together because they trade against each other: the
    floor is the only lever that turns 1,061 qualifying cities into 8,150, and
    what it buys is not more of the world.
    """
    rows = []
    for floor in floors:
        sub = pool[pool.population >= floor]
        for cont, g in sub.groupby("continent"):
            rows.append({"floor": floor, "continent": cont, "n_cities": len(g),
                         "n_countries": g.country.nunique(),
                         "population_m": float(g.population.sum() / 1e6)})
        rows.append({"floor": floor, "continent": "ALL", "n_cities": len(sub),
                     "n_countries": sub.country.nunique(),
                     "population_m": float(sub.population.sum() / 1e6)})
    return pd.DataFrame(rows)


# World urban population by continent, UN World Urbanization Prospects 2018
# order of magnitude, in millions. Used only to state the representation gap as
# a ratio; the conclusion does not turn on the second digit.
WORLD_URBAN_M = {
    "Asia": 2_300, "Africa": 588, "Europe": 553, "South America": 353,
    "North America": 299, "Central America & Caribbean": 165, "Oceania": 28,
}


def representation(pool: pd.DataFrame, floor: int = 100_000) -> pd.DataFrame:
    sub = pool[pool.population >= floor]
    n = len(sub)
    rows = []
    for cont, urban in sorted(WORLD_URBAN_M.items(), key=lambda kv: -kv[1]):
        got = int((sub.continent == cont).sum())
        want = urban / sum(WORLD_URBAN_M.values())
        rows.append({"continent": cont, "n_pool": got,
                     "share_pool": got / n if n else np.nan,
                     "share_world_urban": want,
                     "ratio": (got / n) / want if n and want else np.nan})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 2. What does the country cap do? (Task 16, G14)
# ---------------------------------------------------------------------------
def apply_cap(pool: pd.DataFrame, cap: int, floor: int = 100_000,
              budget: int | None = None) -> pd.DataFrame:
    """The existing selection rule: largest cities first, `cap` per country.

    Reproduced here rather than imported so the cap can be swept without
    touching the collection path, and so the sweep and the collection cannot
    drift apart silently - a check below asserts they agree at the cap the
    study actually used.
    """
    sub = pool[pool.population >= floor].sort_values(
        "population", ascending=False)
    keep = sub.groupby("country", sort=False).head(cap)
    if budget is not None:
        keep = keep.head(budget)
    return keep


def cap_sensitivity(pool: pd.DataFrame, caps=(1, 2, 4, 8, 16, 32, 10_000),
                    floor: int = 100_000) -> pd.DataFrame:
    """Composition against cap. The cap buys regime spread and costs n."""
    rows = []
    for cap in caps:
        sel = apply_cap(pool, cap, floor)
        shares = sel.continent.value_counts(normalize=True)
        top = sel.country.value_counts(normalize=True)
        rows.append({
            "cap": cap, "n_cities": len(sel),
            "n_countries": sel.country.nunique(),
            "n_continents": sel.continent.nunique(),
            "largest_country_share": float(top.iloc[0]) if len(top) else np.nan,
            "largest_country": top.index[0] if len(top) else "",
            "top3_country_share": float(top.head(3).sum()),
            "share_europe": float(shares.get("Europe", 0.0)),
            "share_north_america": float(shares.get("North America", 0.0)),
            "share_asia": float(shares.get("Asia", 0.0)),
            # Shannon evenness over countries: 1.0 is a perfectly balanced
            # sample, 0 is one country. A single number for "is this a world
            # sample or a national one?".
            "country_evenness": _evenness(top.to_numpy()),
        })
    return pd.DataFrame(rows)


def _evenness(p: np.ndarray) -> float:
    p = p[p > 0]
    if len(p) <= 1:
        return 0.0
    return float(-(p * np.log(p)).sum() / np.log(len(p)))


def cap_effect_on_result(caps=(1, 2, 4, 8, 16, 10_000)) -> pd.DataFrame:
    """Does the cap move the ANSWER, or only the composition?

    A sampling rule that changes the headline is a rule the paper has to
    defend; one that does not is a rule it only has to describe. Run on the
    world panel that exists, by re-weighting the cities it already has to the
    composition each cap would have produced - the panel cannot be re-collected
    per cap, but it can be re-weighted, and that isolates composition from
    sample size.
    """
    metrics = pd.read_parquet(PROCESSED / "cities_metrics.parquet")
    pool = load_pool()
    key = pool.drop_duplicates("city").set_index("city")
    m = metrics.join(key[["country", "continent", "population"]], on="city")
    m = m.dropna(subset=["country"])
    if m.empty:
        die("no city in the world panel could be matched to the pool")

    rows = []
    for cap in caps:
        # Weight each city by how many slots its country would get under this
        # cap, relative to how many the panel actually has.
        have = m.country.value_counts()
        want = have.clip(upper=cap)
        w = m.country.map(want / have).to_numpy(float)
        w = w / w.sum()
        rows.append({
            "cap": cap,
            "n_effective": float(1.0 / np.sum(w ** 2)),
            "bss": float(np.sum(w * m.bss)),
            "brier": float(np.sum(w * m.brier)),
            "base_rate": float(np.sum(w * m.base_rate)),
            "reliability": float(np.sum(w * m.reliability)),
            "resolution": float(np.sum(w * m.resolution)),
        })
    out = pd.DataFrame(rows)
    ref = float(out[out.cap == 8].bss.iloc[0]) if (out.cap == 8).any() \
        else float(out.bss.iloc[0])
    out["bss_vs_cap8"] = out.bss - ref
    return out


# ---------------------------------------------------------------------------
# 3. How many cities buy anything? (G20)
# ---------------------------------------------------------------------------
def city_panel() -> pd.DataFrame:
    """Per-city, per-day scored series for the world track."""
    d = pd.read_parquet(PROCESSED / "cities_pop.parquet")
    d = d.dropna(subset=["forecast_prob", "observed_event"])
    d["local_date"] = pd.to_datetime(d.local_date)
    return d


def city_clusters(panel: pd.DataFrame) -> pd.Series:
    """Country label per scored city, for the clustered saturation curve.

    The country is the unit a marginal city is actually bought in: adding a
    ninth German city buys a different thing from adding the first Kenyan one,
    and a curve that cannot tell them apart cannot price Task 10.

    Three sources, in order of authority - the coverage file the world track
    was selected from, the candidate pool, then the capitals table, which is
    where the three microstate capitals that never went through the world
    selection live. Anything still unlabelled stops the stage: a silently
    dropped city would bias the curve towards whichever countries happen to
    be easy to label.
    """
    import json
    lab: dict[str, str] = {}
    cov = json.loads((RAW / "city_coverage.json").read_text())["included"]
    for name, rec in cov.items():
        lab[name] = rec["country"]
    pool = load_pool()
    for name, country in zip(pool.city, pool.country):
        lab.setdefault(name, country)
    for name, c in load_capitals().items():
        lab.setdefault(name, c.country)

    names = pd.Index(sorted(panel.city.unique()))
    out = pd.Series([lab.get(n) for n in names], index=names, name="country")
    if out.isna().any():
        die(f"no country for {sorted(out[out.isna()].index)} - the clustered "
            f"curve would be drawn from a panel it cannot describe")
    return out


def _pooled_brier(panel: pd.DataFrame, cities: np.ndarray) -> float:
    sub = panel[panel.city.isin(cities)]
    return float(np.mean((sub.forecast_prob - sub.observed_event) ** 2))


def _city_sums(panel: pd.DataFrame):
    g = panel.assign(_se=(panel.forecast_prob - panel.observed_event) ** 2) \
             .groupby("city", sort=True)._se.agg(["sum", "size"])
    return g.index.to_numpy(), g["sum"].to_numpy(float), g["size"].to_numpy(float)


def saturation(panel: pd.DataFrame, sizes=(5, 10, 20, 40, 80, None),
               n_rep: int = 2000, seed: int = RANDOM_SEED,
               cluster: pd.Series | None = None) -> pd.DataFrame:
    """How fast does the interval close as cities are added?

    Cities are resampled, not days: the question is what another CITY buys,
    which is exactly what Task 10 proposes to spend money on. Days inside a
    city stay together, so the within-city serial correlation is carried along
    rather than broken - breaking it would make every extra city look better
    than it is.

    A city drawn twice counts twice. Selecting with `isin` instead would
    silently collapse duplicates, so a draw of 80 from 109 would really be a
    draw of about 60 and every large-n point would be biased pessimistic.

    With `cluster`, whole clusters are drawn instead of individual cities and
    the cities they carry come with them. That is the version that matters:
    resampling cities one at a time treats two German cities as two
    independent observations of the world, which they are not. The two curves
    are reported side by side, and the gap between them is what a city inside
    a country already sampled is worth against a country that is not.
    """
    rng = np.random.default_rng(seed)
    names, sse, cnt = _city_sums(panel)
    n_city = len(sse)

    if cluster is None:
        groups = [np.array([i]) for i in range(n_city)]
    else:
        lab = pd.Series(cluster).reindex(names)
        if lab.isna().any():
            die(f"{int(lab.isna().sum())} cities have no cluster label - "
                f"the clustered curve would silently drop them")
        groups = [np.flatnonzero((lab == k).to_numpy())
                  for k in pd.unique(lab)]
    n_group = len(groups)
    per_group = np.array([len(g) for g in groups], dtype=float)

    rows = []
    for size in sizes:
        k = n_group if size is None else size
        if k > n_group:
            continue
        idx = rng.integers(0, n_group, size=(n_rep, k))
        num = np.empty(n_rep)
        den = np.empty(n_rep)
        for r in range(n_rep):
            members = np.concatenate([groups[i] for i in idx[r]])
            num[r] = sse[members].sum()
            den[r] = cnt[members].sum()
        draws = num / den
        sd = float(draws.std(ddof=1))
        rows.append({
            "n_units": k,
            "n_cities": float(per_group[idx].sum(axis=1).mean()),
            "n_rep": n_rep,
            "mean_brier": float(draws.mean()), "sd_brier": sd,
            "ci_width": float(np.quantile(draws, 0.975)
                              - np.quantile(draws, 0.025)),
            # Minimum difference an unpaired comparison at this n could detect
            # with 80% power at 5%. The headline comparison is PAIRED and so
            # does better, but this is the right scale for a cross-city claim.
            "mde": 2.80 * sd * np.sqrt(2.0),
        })
    return pd.DataFrame(rows)


def fit_saturation(sat: pd.DataFrame, x_col: str = "n_cities") -> dict:
    """sd ~ a * n^(-b), n counted in CITIES however the draw was made.

    Both curves decay at b = 0.5 when clusters are of equal size, because k
    clusters of m cities is k*m cities and sqrt(k) is sqrt(n/m). Clustering
    does not bend the curve - it RAISES it. So the exponent is a check that
    the estimator works, and the level is the finding; `design_effect` reads
    the level, and that is the number Task 10 has to be priced against.
    """
    x = np.log(sat[x_col].to_numpy(float))
    y = np.log(sat.sd_brier.to_numpy(float))
    b, a = np.polyfit(x, y, 1)
    pred = a + b * x
    ss = 1.0 - np.sum((y - pred) ** 2) / np.sum((y - y.mean()) ** 2)
    return {"exponent": float(-b), "log_intercept": float(a), "r2": float(ss)}


def design_effect(fit_city: dict, fit_cluster: dict, n_cities: float) -> float:
    """Raw variance ratio between the two curves at a matched number of cities.

    Not a design effect on its own. Drawing whole clusters fixes the cities
    inside them, while drawing cities with replacement can take the same city
    twice, and that difference alone puts the ratio near 0.84 on a panel with
    no dependence at all. `clustered_design_effect` divides it out.
    """
    def sd(f):
        return np.exp(f["log_intercept"]) * n_cities ** (-f["exponent"])
    return float((sd(fit_cluster) / sd(fit_city)) ** 2)


def clustered_design_effect(panel: pd.DataFrame, cluster: pd.Series,
                            sizes_city, sizes_cluster, n_at: float,
                            n_rep: int = 1500, n_shuffle: int = 200,
                            seed: int = RANDOM_SEED) -> dict:
    """What a city inside an already-sampled cluster is worth, against 1.

    The null is measured, not assumed, and it is a DISTRIBUTION rather than a
    number. Shuffling the cluster labels across cities keeps every cluster
    size and destroys every real grouping; the spread of the ratio over those
    shuffles is large, because 20 arbitrary groups of 4 differ a good deal in
    how much their means vary. Dividing by a single null draw would therefore
    report grouping noise as a finding.

    So the observed ratio is placed in that distribution and a permutation
    p-value comes back with it. The same bootstrap seed is used throughout,
    which cancels the resampling noise and leaves only the grouping - the
    thing being tested.
    """
    rng = np.random.default_rng(seed)
    fit_city = fit_saturation(saturation(panel, sizes=sizes_city,
                                         n_rep=n_rep, seed=seed))
    fit_clu = fit_saturation(saturation(panel, sizes=sizes_cluster,
                                        n_rep=n_rep, seed=seed,
                                        cluster=cluster))
    raw = design_effect(fit_city, fit_clu, n_at)

    nulls = []
    for _ in range(n_shuffle):
        shuffled = pd.Series(rng.permutation(cluster.to_numpy()),
                             index=cluster.index)
        f = fit_saturation(saturation(panel, sizes=sizes_cluster, n_rep=n_rep,
                                      seed=seed, cluster=shuffled))
        nulls.append(design_effect(fit_city, f, n_at))
    nulls = np.array(nulls)
    med = float(np.median(nulls))
    p = float((1 + np.sum(nulls >= raw)) / (len(nulls) + 1))
    return {"raw": raw, "null_median": med,
            "null_lo": float(np.quantile(nulls, 0.025)),
            "null_hi": float(np.quantile(nulls, 0.975)),
            "design_effect": raw / med, "p_permutation": p,
            "n_shuffle": int(n_shuffle), "n_at": float(n_at),
            "exponent_city": fit_city["exponent"],
            "exponent_cluster": fit_clu["exponent"]}


def n_required(fit: dict, target_mde: float) -> float:
    """Cities needed for the MDE to fall to `target_mde`, at the measured rate."""
    # mde = 2.80 * sqrt(2) * exp(a) * n^-b
    k = 2.80 * np.sqrt(2.0) * np.exp(fit["log_intercept"])
    return float((k / target_mde) ** (1.0 / fit["exponent"]))


# ---------------------------------------------------------------------------
# 4. The designed sample
# ---------------------------------------------------------------------------
def design(pool: pd.DataFrame, budget: int, floor: int = 100_000,
           cap: int = 8) -> pd.DataFrame:
    """Allocate the budget across continents, then fill from within.

    Allocation is proportional to the square root of world urban population
    rather than to it directly. Proportional allocation would hand Asia two
    thirds of the sample and leave Oceania with one city; equal allocation
    would treat a continent with 28 million urban residents as it treats one
    with 2.3 billion. The square root is the standard compromise and is stated
    as a choice rather than smuggled in - a reader who prefers another rule can
    read the shortfall column and apply it.

    Every stratum is then capped at what the pool can actually supply, and the
    shortfall is REPORTED rather than reallocated in silence. A continent that
    cannot be sampled is the finding, not an inconvenience.
    """
    sub = pool[pool.population >= floor]
    weights = {c: np.sqrt(v) for c, v in WORLD_URBAN_M.items()}
    total = sum(weights.values())
    rows = []
    for cont, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        want = int(round(budget * w / total))
        have = sub[sub.continent == cont]
        capped = have.sort_values("population", ascending=False) \
                     .groupby("country", sort=False).head(cap)
        rows.append({
            "continent": cont, "target": want,
            "available": len(have), "available_after_cap": len(capped),
            "allocated": min(want, len(capped)),
            "shortfall": max(0, want - len(capped)),
            "n_countries": int(capped.country.nunique()),
        })
    out = pd.DataFrame(rows)
    out["share_target"] = out.target / out.target.sum()
    out["share_allocated"] = out.allocated / max(out.allocated.sum(), 1)
    return out


# ---------------------------------------------------------------------------
# 5. Correctness checks
# ---------------------------------------------------------------------------
def run_checks() -> None:
    print("=== correctness checks ===")
    rng = np.random.default_rng(RANDOM_SEED)

    # 1. The cap reproduction matches the selection the study actually shipped.
    import json
    cov = json.loads((RAW / "city_coverage.json").read_text())
    rules = cov["rules"]
    # Capitals and already-published cities ride past the population floor by
    # a documented exemption (probe_cities.select). The sweep reproduces the
    # floor-and-cap rule, so it is checked against the cities that rule chose.
    shipped = {c for c, v in cov["included"].items()
               if (v.get("population") or 0) >= rules["min_population"]}
    pool = load_pool()
    mine = apply_cap(pool, rules["max_per_country"], rules["min_population"])
    overlap = len(shipped & set(mine.city)) / max(len(shipped), 1)
    if overlap < 0.80:
        die(f"the cap reproduced here recovers only {overlap:.0%} of the "
            f"cities the study shipped - the sweep is not describing the "
            f"rule that was used")
    say(f"the cap reproduced here recovers {overlap:.0%} of the cities the "
        f"study actually shipped, so the sweep describes the real rule")

    # 2. On INDEPENDENT cities the saturation exponent must come back at 0.5.
    #    If it does not, the estimator is wrong and the shared-weather finding
    #    below would be an artefact of the method rather than of the world.
    n_day = 300
    frames = []
    for i in range(80):
        p = rng.uniform(0.15, 0.45, n_day)
        y = rng.random(n_day) < p
        frames.append(pd.DataFrame({"city": f"C{i}", "forecast_prob": p,
                                    "observed_event": y.astype(float)}))
    indep = pd.concat(frames, ignore_index=True)
    sat = saturation(indep, sizes=(5, 10, 20, 40, 80), n_rep=1500)
    fit = fit_saturation(sat)
    if not 0.42 <= fit["exponent"] <= 0.58:
        die(f"on independent cities the saturation exponent came back "
            f"{fit['exponent']:.3f}, not 0.5 - the estimator is broken")
    say(f"on synthetic INDEPENDENT cities the saturation exponent is "
        f"{fit['exponent']:.2f} (R^2 {fit['r2']:.3f}), as it must be")

    # 3. Clustering must be a genuine generalisation, not a second code path.
    #    With one city per cluster it has to return the city curve EXACTLY;
    #    if it does not, any gap between the two curves below is the code
    #    disagreeing with itself rather than the world being clustered.
    singleton = pd.Series(indep.city.unique(), index=indep.city.unique())
    a = saturation(indep, sizes=(10, 40), n_rep=200, cluster=None)
    b = saturation(indep, sizes=(10, 40), n_rep=200, cluster=singleton)
    gap = float((a.sd_brier - b.sd_brier).abs().max())
    if gap > 1e-12:
        die(f"with one city per cluster the clustered curve differs from the "
            f"city curve by {gap:.2e} - it is a second code path, not a "
            f"generalisation")
    say("with one city per cluster the clustered curve reproduces the city "
        "curve exactly, so the two are the same estimator")

    # 4. The design effect must read 1.0 when the clusters are arbitrary. This
    #    is the false-positive direction and it is the one that matters: a
    #    method that inflates the price of a city whenever you group cities at
    #    all would manufacture the headline conclusion of this stage.
    n_day, n_clust, per = 300, 20, 4
    def _panel(sd_cluster: float):
        eff = rng.normal(0, sd_cluster, n_clust)
        frames, lab = [], {}
        for c in range(n_clust):
            for j in range(per):
                p = rng.uniform(0.15, 0.45, n_day)
                y = rng.random(n_day) < np.clip(p + eff[c], 0.01, 0.99)
                name = f"K{c}_{j}"
                lab[name] = f"K{c}"
                frames.append(pd.DataFrame({"city": name, "forecast_prob": p,
                                            "observed_event": y.astype(float)}))
        return pd.concat(frames, ignore_index=True), pd.Series(lab)

    def _deff(panel, lab):
        # Both curves must SPAN the n the ratio is read at - an extrapolated
        # design effect is a statement about the fit, not about the panel.
        return clustered_design_effect(
            panel, lab, sizes_city=(4, 8, 20, 40, 80),
            sizes_cluster=(1, 2, 5, 10, 20), n_at=n_clust * per,
            n_rep=600, n_shuffle=80)

    flat, flat_lab = _panel(0.0)
    d0 = _deff(flat, flat_lab)
    if d0["p_permutation"] < 0.05:
        die(f"on cities grouped into meaningless clusters the permutation "
            f"test fired at p = {d0['p_permutation']:.3f} - the method "
            f"invents dependence")
    say(f"group unrelated cities into arbitrary clusters and the design "
        f"effect is {d0['design_effect']:.2f} at p = "
        f"{d0['p_permutation']:.2f} - the method does not invent dependence")

    # 5. ...and it must rise towards the cluster size when the cities inside a
    #    cluster really are interchangeable, or it cannot see the thing it is
    #    for. It cannot exceed the cluster size: four identical cities are
    #    worth one, never less.
    dep, dep_lab = _panel(0.20)
    d1 = _deff(dep, dep_lab)
    if d1["p_permutation"] > 0.05 or not 2.0 <= d1["design_effect"] <= per * 1.2:
        die(f"on cities that share a cluster-level error the design effect "
            f"came back {d1['design_effect']:.2f} at p = "
            f"{d1['p_permutation']:.3f} - it does not see the dependence it "
            f"exists for")
    say(f"make the cities inside a cluster near-interchangeable and it rises "
        f"to {d1['design_effect']:.1f} (p = {d1['p_permutation']:.3f}) "
        f"against a ceiling of {per} - it measures dependence on the right "
        f"scale")

    # 6. The design must not invent cities the pool does not hold.
    d = design(pool, budget=2000)
    if (d.allocated > d.available_after_cap).any():
        die("the design allocated more cities than the pool can supply")
    say(f"a 2,000-city design allocates {int(d.allocated.sum())} and reports "
        f"a shortfall of {int(d.shortfall.sum())} rather than quietly "
        f"reallocating it")
    print()


# ---------------------------------------------------------------------------
# 6. Report
# ---------------------------------------------------------------------------
def report(pool, census, rep, caps, cap_res, sat, fit, des, deff,
           mean_per_country) -> None:
    bar = "=" * 78
    print(bar)
    print("WHAT SAMPLE CAN THIS STUDY HAVE? (Tasks 10 and 16; G1, G14, G20)")
    print(bar)

    print("\n--- 1. The pool is not what 8,150 cities sounds like ---")
    order = [c for c in census[census.floor == 0]
             .sort_values("n_cities", ascending=False).continent if c != "ALL"]
    print(f"  {'continent':30s}" + "".join(f"{f'>={f//1000}k':>10s}"
                                           for f in sorted(census.floor.unique())))
    for c in order + ["ALL"]:
        r = census[census.continent == c].set_index("floor").n_cities
        print(f"  {c:30s}" + "".join(f"{int(r.get(f, 0)):10d}"
                                     for f in sorted(census.floor.unique())))
    afr = int(census[(census.continent == "Africa")
                     & (census.floor == 0)].n_cities.sum())
    sam = int(census[(census.continent == "South America")
                     & (census.floor == 0)].n_cities.sum())
    hund = int(census[(census.continent == "ALL")
                      & (census.floor == 100_000)].n_cities.iloc[0])
    print(f"  G1 asks for 2,000 cities on all inhabited continents. At a "
          f"100,000 floor the\n  pool holds {hund}. Africa contributes {afr} "
          f"cities in total and South America {sam}.")
    print("  Dropping the floor to reach 2,000 does not fix that - it buys "
          "American and\n  German small towns, because the floor is the only "
          "lever and it pulls in the\n  direction of wherever gauges are "
          "dense. The target as written is not reachable\n  from GHCN-Daily, "
          "and no amount of API budget changes it.")

    print("\n--- 2. Against the world it would claim to describe ---")
    print(f"  {'continent':30s}{'in pool':>8s}{'pool %':>8s}"
          f"{'urban %':>9s}{'ratio':>8s}")
    for _, r in rep.iterrows():
        print(f"  {r.continent:30s}{int(r.n_pool):8d}{r.share_pool:8.1%}"
              f"{r.share_world_urban:9.1%}{r.ratio:8.2f}")
    print("  A ratio of 1.0 is proportional representation of world urban "
          "population. Asia\n  and Africa are the two that matter and they are "
          "the two that are missing. Any\n  sentence beginning 'globally, "
          "forecasts...' drawn from this pool is a sentence\n  about Europe, "
          "North America and Japan.")

    print("\n--- 3. What the country cap does (Task 16) ---")
    print(f"  {'cap':>6s}{'cities':>8s}{'countries':>11s}{'largest':>9s}"
          f"{'top-3':>8s}{'evenness':>10s}{'Europe':>8s}")
    for _, r in caps.iterrows():
        tag = "none" if r.cap >= 1000 else str(int(r.cap))
        print(f"  {tag:>6s}{int(r.n_cities):8d}{int(r.n_countries):11d}"
              f"{r.largest_country_share:9.1%}{r.top3_country_share:8.1%}"
              f"{r.country_evenness:10.2f}{r.share_europe:8.1%}")
    nocap = caps[caps.cap >= 1000].iloc[0]
    at8 = caps[caps.cap == 8].iloc[0]
    print(f"  Uncapped, one country supplies {nocap.largest_country_share:.0%} "
          f"of the sample and three supply\n  {nocap.top3_country_share:.0%}. "
          f"At the cap of 8 the study uses, that falls to "
          f"{at8.largest_country_share:.0%} and "
          f"{at8.top3_country_share:.0%}, at the\n  cost of "
          f"{int(nocap.n_cities - at8.n_cities)} cities. The cap is not "
          f"tidiness; without it the league table is a\n  ranking of American "
          f"suburbs with a world-shaped caption.")

    print("\n--- 4. ...and whether it moves the ANSWER ---")
    print(f"  {'cap':>6s}{'n_eff':>9s}{'BSS':>9s}{'vs cap 8':>10s}"
          f"{'base rate':>11s}")
    for _, r in cap_res.iterrows():
        tag = "none" if r.cap >= 1000 else str(int(r.cap))
        print(f"  {tag:>6s}{r.n_effective:9.1f}{r.bss:9.4f}"
              f"{r.bss_vs_cap8:+10.4f}{r.base_rate:11.3f}")
    span = float(cap_res.bss_vs_cap8.abs().max())
    print(f"  Re-weighting the panel it already has to each cap's composition "
          f"moves pooled BSS\n  by at most {span:.4f}. So the cap is a rule the "
          f"paper has to DESCRIBE, not one it has\n  to defend: it changes who "
          f"is in the sample far more than it changes what the\n  sample says. "
          f"That is the cap-sensitivity result G14 asked for, and it is the\n"
          f"  reassuring version.")

    print("\n--- 5. What another city is worth (G20) ---")
    print(f"  {'cities':>8s}{'sd(Brier)':>11s}{'95% width':>11s}{'MDE':>9s}")
    for _, r in sat.iterrows():
        print(f"  {int(r.n_cities):8d}{r.sd_brier:11.5f}{r.ci_width:11.5f}"
              f"{r.mde:9.5f}")
    print(f"  Fitted sd ~ n^-{fit['exponent']:.2f} (R^2 {fit['r2']:.3f}). "
          f"Independent cities would give 0.50,\n  and so does this - because "
          f"resampling cities ONE AT A TIME cannot see that two\n  of them "
          f"are in the same country. That curve prices a city as if it were "
          f"new\n  evidence, and it is the optimistic one.")

    print(f"\n  Draw whole COUNTRIES instead and the same panel gives "
          f"{deff['design_effect']:.1f} times the variance\n  at a matched "
          f"{int(deff['n_at'])} cities (permutation p = "
          f"{deff['p_permutation']:.3f}, null median "
          f"{deff['null_median']:.2f}, 95% of shuffles\n  between "
          f"{deff['null_lo']:.2f} and {deff['null_hi']:.2f}). The mean "
          f"country in this panel holds {mean_per_country:.1f} cities, so a "
          f"design\n  effect of {deff['design_effect']:.1f} says cities "
          f"inside a country are very nearly interchangeable:\n  the eighth "
          f"German city is worth close to nothing that the first one did not "
          f"already\n  buy. The label is shuffled to get that null rather "
          f"than assumed, because grouping\n  cities at all changes the "
          f"variance a little even when the grouping is meaningless.")

    need_margin = n_required(fit, MARGIN_BRIER)
    d = deff["design_effect"]
    print(f"\n  {'target MDE':>12s}{'cities, naive':>15s}{'cities, real':>14s}"
          f"{'countries':>11s}")
    for tgt, lbl in ((MARGIN_BRIER, "boundary rule"), (0.0081, "today's test")):
        naive = n_required(fit, tgt)
        print(f"  {tgt:12.4f}{naive:15,.0f}{naive * d:14,.0f}"
              f"{naive * d / mean_per_country:11,.0f}   ({lbl})")
    print(f"  The naive column is the one a reader would compute; the real "
          f"one multiplies it by\n  the measured design effect. To reach the "
          f"{MARGIN_BRIER} the day-boundary rule already moves\n  things by "
          f"needs about {need_margin * d:,.0f} cities drawn this way - more "
          f"cities than the pool holds at\n  any population floor, so it is "
          f"not a question of budget.")
    print("  The honest reading: 2,000 cities is not a precision argument, "
          "and the plan should\n  stop justifying it as one. It is a "
          "COVERAGE argument - the claim 'this holds\n  outside Europe' "
          "cannot be made at any n inside Europe, and can be made at modest\n"
          "  n outside it. The design effect says the same thing from the "
          "other side: the\n  cheap axis is COUNTRIES, not cities, and Task "
          "10 should be re-specified in those\n  units. That is a cheaper "
          "and more defensible paper.")

    print("\n--- 6. A designed sample, and what it cannot have ---")
    print(f"  {'continent':30s}{'target':>8s}{'pool':>7s}{'capped':>8s}"
          f"{'get':>6s}{'short':>7s}")
    for _, r in des.iterrows():
        print(f"  {r.continent:30s}{int(r.target):8d}{int(r.available):7d}"
              f"{int(r.available_after_cap):8d}{int(r.allocated):6d}"
              f"{int(r.shortfall):7d}")
    print(f"  Allocation is proportional to the SQUARE ROOT of world urban "
          f"population: straight\n  proportionality hands Asia two thirds of "
          f"the sample and Oceania one city, and\n  equal allocation treats 28 "
          f"million urban residents like 2.3 billion. Stated as a\n  choice, "
          f"with the shortfall column left in so another rule can be applied "
          f"to it.")
    print(f"  {int(des.allocated.sum())} of the {int(des.target.sum())} "
          f"requested can be supplied; the shortfall is "
          f"{int(des.shortfall.sum())},\n  and it is almost entirely Africa, "
          f"South America and Asia. That shortfall is the\n  deliverable: it "
          f"is the size of the hole that Task 11 (NOAA ISD) and Task 12\n"
          f"  (IMERG) exist to fill, measured rather than asserted.")

    print("\n--- Caveats ---")
    print("  1. The saturation curve and the design effect are measured on "
          "the world panel that\n     exists, which is itself Europe-heavy "
          "and capped at eight cities a country. A\n     genuinely global "
          "panel might decorrelate faster, which would make the required n\n"
          "     smaller - so these are the pessimistic end and should be "
          "re-measured once any\n     new continent is in. The cap also "
          "bounds what the design effect can be seen at:\n     a country "
          "with 40 cities in it might be worse still, and this panel has "
          "none.")
    print("  2. The urban-population weights are round numbers from the UN "
          "series. They set\n     the allocation and the representation "
          "ratios; neither conclusion turns on the\n     second digit, but "
          "neither is a precise accounting.")
    print("  3. The cap re-weighting in section 4 reweights the panel that "
          "exists; it cannot\n     see a country the panel has none of. It "
          "bounds the effect of the cap WITHIN\n     the sampled world, not "
          "the effect of the sampled world itself - which is what\n     "
          "section 2 is for, and section 2 is the one with bad news.")


# ---------------------------------------------------------------------------
def main() -> None:
    args = set(sys.argv[1:])
    run_checks()
    if "check" in args:
        return

    pool = load_pool()
    census = pool_census(pool)
    rep = representation(pool)
    caps = cap_sensitivity(pool)
    cap_res = cap_effect_on_result()
    panel = city_panel()
    sat = saturation(panel)
    fit = fit_saturation(sat)
    des = design(pool, budget=2000)

    clusters = city_clusters(panel)
    n_city = int(clusters.size)
    n_country = int(clusters.nunique())
    mean_per_country = n_city / n_country
    deff = clustered_design_effect(
        panel, clusters,
        sizes_city=(8, 16, 32, 64, n_city),
        sizes_cluster=(2, 4, 8, 16, n_country),
        n_at=n_city, n_rep=1000, n_shuffle=200)

    print()
    report(pool, census, rep, caps, cap_res, sat, fit, des, deff,
           mean_per_country)

    for k, v in fit.items():
        sat[f"fit_{k}"] = v
    for k, v in deff.items():
        sat[f"deff_{k}"] = v
    sat["n_countries"] = n_country
    sat["mean_cities_per_country"] = mean_per_country
    sat["n_for_margin"] = n_required(fit, MARGIN_BRIER)
    sat["n_for_margin_clustered"] = (n_required(fit, MARGIN_BRIER)
                                     * deff["design_effect"])
    census.to_parquet(CENSUS_TABLE, index=False)
    caps.merge(cap_res, on="cap", how="outer").to_parquet(CAP_TABLE, index=False)
    sat.to_parquet(SATURATION_TABLE, index=False)
    des.to_parquet(DESIGN_TABLE, index=False)
    print(f"\nwrote {CENSUS_TABLE.name}, {CAP_TABLE.name}, "
          f"{SATURATION_TABLE.name} and {DESIGN_TABLE.name}")


if __name__ == "__main__":
    main()
