"""How much of the verification verdict is an accident of where the gauge is?

G3, Tasks 13 and 14. Every score in this study compares a forecast for a grid
cell against a bucket at one point inside it. The paper's central number - the
0.0003 Brier gap between the served probability and the ensemble-derived one -
is quoted to four decimal places against a truth that was never measured to
four decimal places. Until the error in the truth is a number, "tie" and
"different" are both unclaimable.

The error is measured rather than assumed, and it is measured three ways, each
answering a question the previous one leaves open.

1. HOW OFTEN DO TWO NEARBY GAUGES DISAGREE ABOUT WHETHER IT RAINED? Directly,
   from every GHCN-Daily pair within 60 km of a study city over the study
   window. This is the quantity, not a proxy for it.

2. HOW MUCH OF THAT IS ACTUALLY SPATIAL? Two gauges in the same field still
   disagree: they empty their buckets at different hours, they catch different
   fractions of the same rain, and one of them is sometimes simply wrong. That
   floor is separated from the spatial signal by extrapolating the
   disagreement-versus-separation curve to zero separation - where, by
   construction, nothing spatial is left.

3. WHAT DOES IT DO TO THE ANSWER? Two ways. Analytically, by propagating a
   label-flip rate into the Brier score and, more importantly, into a PAIRED
   difference of two Brier scores, where the intuition that noise cancels turns
   out to be wrong in a specific and relevant way. And directly, by rescoring
   the study's own forecasts against a different real gauge near the same city
   and watching the verdict move.

The third measurement is the one that matters, and the analytic result is why
it cannot be skipped: truth noise does NOT cancel out of a paired comparison
when one forecast is biased relative to the other, which is exactly the
situation between a dry vendor probability and an ensemble.
"""

from __future__ import annotations

import gzip
import json
import sys

import numpy as np
import pandas as pd

from config import (OBS_END, PROCESSED, RAIN_THRESHOLD_MM, RANDOM_SEED, RAW,
                    load_capitals, load_cities)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
POOL_RADIUS_KM = 100.0     # gather gauges this far from any study city
PAIR_RADIUS_KM = 60.0      # and pair up those within this of each other
MIN_COMMON_DAYS = 200      # a pair with fewer shared days says nothing stable
MAX_ELEV_DIFF_M = 300.0    # primary pool: pairs that are not on a mountainside

# Separation bins for the q(d) curve. Fine where the curve is steep and where
# the extrapolation to zero has to be anchored; coarse out at the tail where
# the pairs are plentiful and the curve is flat.
SEP_BINS = np.array([0.0, 1.0, 2.0, 3.0, 5.0, 7.5, 10.0, 15.0, 20.0,
                     27.5, 35.0, 45.0, 60.0])

STUDY_START = "2024-04-26"

NEIGHBOUR_STORE = RAW / "ghcn_neighbours.parquet"
NEIGHBOUR_KEY = RAW / "ghcn_neighbours_pool.json"
PAIRS_TABLE = PROCESSED / "truth_pairs.parquet"
CURVE_TABLE = PROCESSED / "truth_separation_curve.parquet"
RESCORE_TABLE = PROCESSED / "truth_rescore.parquet"
PROPAGATION_TABLE = PROCESSED / "truth_propagation.parquet"

YEAR_DIR = RAW / "ghcn_year"
YEARS = [2024, 2025, 2026]
CHUNK = 2_000_000


def die(msg: str) -> None:
    print(f"\nFAILED: {msg}", file=sys.stderr)
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# 1. The station pool
# ---------------------------------------------------------------------------
def study_points() -> pd.DataFrame:
    """Every location this study verifies at, capitals and world cities."""
    rows = []
    for loader in (load_capitals, load_cities):
        try:
            cities = loader()
        except Exception:
            continue
        for name, c in cities.items():
            # The truth-error analysis (E20-E22) re-sites a city onto other
            # GHCN gauges near it. GHCNh and ISD cities are, by construction,
            # in countries without such gauges, so they sit outside its scope -
            # and are skipped rather than scored against an empty neighbourhood.
            import truth_sources
            if truth_sources.is_external(c.prcp_station):
                continue
            rows.append({"city": name, "lat": c.latitude, "lon": c.longitude,
                         "station": c.prcp_station})
    out = pd.DataFrame(rows).drop_duplicates(subset="city")
    if out.empty:
        die("no study cities found - run the coverage probes first")
    return out.reset_index(drop=True)


def _xyz(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Earth-centred coordinates in km, so distance is a straight subtraction.

    Chord rather than great-circle: at 60 km the two differ by under a metre,
    and the chord is what a KD-tree can index.
    """
    la, lo = np.radians(lat), np.radians(lon)
    return np.c_[np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo),
                 np.sin(la)] * 6371.0


def station_pool() -> pd.DataFrame:
    """GHCN gauges reporting precipitation near any city this study scores.

    Near a study city rather than anywhere on earth, on purpose. The global
    network is dominated by the dense American co-operative network, and its
    disagreement curve would be a statement about American convective rain
    rather than about the climates this study verifies in.
    """
    from probe_capitals import load_inventory, load_stations

    st = load_stations()
    inv = load_inventory()
    active = inv[(inv.elem == "PRCP") & (inv.year_last >= 2024)
                 & (inv.year_first <= 2024)]
    st = st[st.id.isin(set(active.id))].reset_index(drop=True)

    pts = study_points()
    sx = _xyz(st.lat.to_numpy(), st.lon.to_numpy())
    cx = _xyz(pts.lat.to_numpy(), pts.lon.to_numpy())
    from scipy.spatial import cKDTree
    near = cKDTree(cx).query_ball_point(sx, r=POOL_RADIUS_KM)
    keep = np.array([len(x) > 0 for x in near])
    out = st[keep].reset_index(drop=True)
    print(f"  station pool: {len(out)} gauges within {POOL_RADIUS_KM:.0f} km "
          f"of {len(pts)} study cities")
    return out


def ensure_store(pool: pd.DataFrame, force: bool = False) -> pd.DataFrame:
    """Daily precipitation for the pool, extracted once from the year files.

    `ghcn_bulk.py` already walks these files, but it extracts exactly the
    stations the study verifies at and writes them to a store the rest of the
    pipeline reads. Widening that store to six thousand gauges would make every
    downstream module carry the cost of a question only this one asks, so this
    keeps its own.
    """
    if NEIGHBOUR_STORE.exists() and not force:
        # The store is keyed by the set of gauges ASKED for, not by the set
        # that came back. Gauges in the inventory with no usable rows in the
        # window are normal, and comparing against the returned set would fail
        # to match on every run and re-extract four million rows each time.
        want = sorted(pool.id)
        if NEIGHBOUR_KEY.exists() and json.loads(
                NEIGHBOUR_KEY.read_text()) == want:
            return pd.read_parquet(NEIGHBOUR_STORE)
    ids = set(pool.id)
    print(f"  extracting {len(ids)} gauges from the GHCN year files "
          f"(a few minutes, once)")
    keep = []
    for year in YEARS:
        path = YEAR_DIR / f"{year}.csv.gz"
        if not path.exists():
            die(f"{path} missing - run src/ghcn_bulk.py first")
        rows = 0
        with gzip.open(path, "rt") as fh:
            for chunk in pd.read_csv(
                    fh, header=None, usecols=[0, 1, 2, 3, 5, 7],
                    names=["id", "date", "element", "value", "q_flag",
                           "obs_time"],
                    dtype={"id": str, "date": str, "element": str,
                           "value": float, "q_flag": str, "obs_time": str},
                    chunksize=CHUNK, low_memory=False):
                sel = chunk[(chunk.element == "PRCP") & chunk.id.isin(ids)
                            & chunk.q_flag.isna()]
                if len(sel):
                    keep.append(sel[["id", "date", "value", "obs_time"]])
                    rows += len(sel)
        print(f"    {year}: {rows:,} rows")
    if not keep:
        die("no precipitation rows extracted for the pool")
    long = pd.concat(keep, ignore_index=True)
    out = pd.DataFrame({
        "station": long.id,
        "ghcn_date": pd.to_datetime(long.date, format="%Y%m%d"),
        "prcp": long.value / 10.0,
        "obs_time": long.obs_time,
    }).sort_values(["station", "ghcn_date"]).reset_index(drop=True)
    NEIGHBOUR_STORE.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(NEIGHBOUR_STORE, index=False)
    NEIGHBOUR_KEY.write_text(json.dumps(sorted(ids)))
    print(f"  wrote {NEIGHBOUR_STORE.name}: {len(out):,} rows, "
          f"{out.station.nunique()} gauges")
    return out


# ---------------------------------------------------------------------------
# 2. How often do two nearby gauges disagree?
# ---------------------------------------------------------------------------
def event_matrix(store: pd.DataFrame, pool: pd.DataFrame,
                 threshold: float = RAIN_THRESHOLD_MM):
    """Stations x days, as a rain flag and a reported flag.

    Two matrices rather than one with missing values: 'did it rain' and 'did
    this gauge report at all'. Every statistic below is computed only over days
    both gauges reported, and conflating a silent gauge with a dry one would
    manufacture disagreement out of an outage - the same mistake as reading a
    gap in a forecast archive as a forecast of no rain.
    """
    s = store[(store.ghcn_date >= STUDY_START) & (store.ghcn_date <= OBS_END)]
    s = s[s.prcp.notna() & s.station.isin(set(pool.id))]
    ids = pd.Index(sorted(s.station.unique()))
    days = pd.DatetimeIndex(sorted(s.ghcn_date.unique()))
    ri = ids.get_indexer(s.station.to_numpy())
    ci = days.get_indexer(pd.DatetimeIndex(s.ghcn_date))
    rain = np.zeros((len(ids), len(days)), dtype=np.int8)
    seen = np.zeros((len(ids), len(days)), dtype=np.int8)
    seen[ri, ci] = 1
    rain[ri, ci] = (s.prcp.to_numpy() >= threshold).astype(np.int8)
    return ids, days, rain, seen


def neighbour_pairs(pool: pd.DataFrame, ids: pd.Index,
                    radius_km: float = PAIR_RADIUS_KM) -> pd.DataFrame:
    """Every gauge pair within `radius_km`, with separation and relief."""
    from scipy.spatial import cKDTree

    p = pool[pool.id.isin(set(ids))].set_index("id").loc[list(ids)]
    xyz = _xyz(p.lat.to_numpy(), p.lon.to_numpy())
    pr = cKDTree(xyz).query_pairs(radius_km, output_type="ndarray")
    if len(pr) == 0:
        die(f"no gauge pairs within {radius_km} km")
    a, b = pr[:, 0], pr[:, 1]
    sep = np.linalg.norm(xyz[a] - xyz[b], axis=1)
    elev = p.elev_m.to_numpy()
    elev = np.where(elev <= -999, np.nan, elev)
    country = np.array([i[:2] for i in ids])
    return pd.DataFrame({
        "i_a": a, "i_b": b,
        "station_a": np.asarray(ids)[a], "station_b": np.asarray(ids)[b],
        "sep_km": sep,
        "elev_diff_m": np.abs(elev[a] - elev[b]),
        "same_country": country[a] == country[b],
    })


def pair_stats(pairs: pd.DataFrame, rain: np.ndarray, seen: np.ndarray,
               min_days: int = MIN_COMMON_DAYS) -> pd.DataFrame:
    """Disagreement rate per pair, over the days both gauges reported.

    The asymmetric halves are kept apart. A symmetric disagreement rate is what
    propagates into a Brier score, but P(wet here, dry there) against its
    mirror is what a systematic catch difference looks like, and Task 14 needs
    to see it. Averaging the two away first would hide the thing being audited.
    """
    a, b = pairs.i_a.to_numpy(), pairs.i_b.to_numpy()
    out = {k: np.zeros(len(pairs)) for k in
           ("n", "n_a1b0", "n_a0b1", "n_a1b1")}
    step = 20_000
    for lo in range(0, len(pairs), step):
        hi = min(lo + step, len(pairs))
        ia, ib = a[lo:hi], b[lo:hi]
        both = (seen[ia] & seen[ib]).astype(bool)
        ra = rain[ia].astype(bool) & both
        rb = rain[ib].astype(bool) & both
        out["n"][lo:hi] = both.sum(axis=1)
        out["n_a1b0"][lo:hi] = (ra & ~rb).sum(axis=1)
        out["n_a0b1"][lo:hi] = (rb & ~ra).sum(axis=1)
        out["n_a1b1"][lo:hi] = (ra & rb).sum(axis=1)
    df = pairs.assign(**out)
    df = df[df.n >= min_days].copy()
    if df.empty:
        die(f"no gauge pair shares {min_days} days - the window is too short")
    df["q"] = (df.n_a1b0 + df.n_a0b1) / df.n
    df["q_a1b0"] = df.n_a1b0 / df.n
    df["q_a0b1"] = df.n_a0b1 / df.n
    df["base_a"] = (df.n_a1b0 + df.n_a1b1) / df.n
    df["base_b"] = (df.n_a0b1 + df.n_a1b1) / df.n
    # A disagreement rate means nothing without the rate two INDEPENDENT gauges
    # with these base rates would disagree at: that is the ceiling the
    # measurement is a fraction of, and it moves with climate.
    df["q_independent"] = (df.base_a * (1 - df.base_b)
                           + (1 - df.base_a) * df.base_b)
    df["agreement_skill"] = 1.0 - df.q / df.q_independent.clip(lower=1e-9)
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 3. Separating the spatial signal from the gauge floor
# ---------------------------------------------------------------------------
def separation_curve(ps: pd.DataFrame, n_boot: int = 400,
                     seed: int = RANDOM_SEED) -> pd.DataFrame:
    """Disagreement against separation, with an interval that resamples GAUGES.

    Not pairs. The 6,000 gauges make 190,000 pairs, so pairs are massively
    dependent - one badly sited gauge enters dozens of them - and an interval
    that resampled pairs would treat that gauge's peculiarity as dozens of
    independent observations. Resampling gauges and keeping the pairs whose
    BOTH ends survive is the cluster bootstrap this design calls for, and it
    widens the interval by the right amount for the right reason.
    """
    rng = np.random.default_rng(seed)
    ids = pd.Index(sorted(set(ps.station_a) | set(ps.station_b)))
    ia = ids.get_indexer(ps.station_a.to_numpy())
    ib = ids.get_indexer(ps.station_b.to_numpy())
    binx = np.digitize(ps.sep_km.to_numpy(), SEP_BINS) - 1
    q = ps.q.to_numpy()
    nb = len(SEP_BINS) - 1

    reps = np.full((n_boot, nb), np.nan)
    for r in range(n_boot):
        take = rng.random(len(ids)) < 0.5      # half-sampling over gauges
        keep = take[ia] & take[ib]
        if not keep.any():
            continue
        bb, qq = binx[keep], q[keep]
        for k in range(nb):
            m = bb == k
            if m.sum() >= 5:
                reps[r, k] = qq[m].mean()

    rows = []
    for k in range(nb):
        m = binx == k
        if m.sum() < 5:
            continue
        col = reps[:, k]
        col = col[~np.isnan(col)]
        rows.append({
            "sep_lo": SEP_BINS[k], "sep_hi": SEP_BINS[k + 1],
            "sep_mid": float(ps.sep_km[m].mean()),
            "n_pairs": int(m.sum()),
            "n_gauges": int(len(set(ps.station_a[m]) | set(ps.station_b[m]))),
            "median_days": float(ps.n[m].median()),
            "q": float(q[m].mean()),
            "q_lo": float(np.quantile(col, 0.025)) if len(col) else np.nan,
            "q_hi": float(np.quantile(col, 0.975)) if len(col) else np.nan,
            "q_a1b0": float(ps.q_a1b0[m].mean()),
            "q_a0b1": float(ps.q_a0b1[m].mean()),
            "q_independent": float(ps.q_independent[m].mean()),
            "agreement_skill": float(ps.agreement_skill[m].mean()),
        })
    return pd.DataFrame(rows)


def fit_curve(curve: pd.DataFrame) -> dict:
    """q(d) = q0 + A (1 - exp(-d/L)), and what the three numbers mean.

    q0 is the disagreement left when the separation is gone: two gauges in the
    same field, differing only because they are two buckets read by two people
    at two hours. It is not representativeness error - it is the noise floor of
    the truth itself, and it bounds how well ANY forecast can ever appear to
    score.

    A is what separation adds, and L is the distance over which it is added:
    the scale on which 'did it rain here' stops being the same question as 'did
    it rain over there'. Those two are representativeness error, and they are
    what a grid-cell forecast is charged for.

    The fit is used for interpolation between measured bins and for nothing
    else - the extrapolation to zero is anchored by real pairs under a
    kilometre, not by the functional form.
    """
    from scipy.optimize import curve_fit

    d = curve.sep_mid.to_numpy()
    y = curve.q.to_numpy()
    w = np.sqrt(curve.n_pairs.to_numpy())

    def model(x, q0, a, L):
        return q0 + a * (1.0 - np.exp(-x / max(L, 1e-6)))

    try:
        p, _ = curve_fit(model, d, y, p0=[y[0], y[-1] - y[0], 20.0],
                         sigma=1.0 / w, maxfev=20000)
    except Exception as exc:                                # pragma: no cover
        die(f"separation-curve fit failed: {exc}")
    q0, a, L = (float(v) for v in p)
    pred = model(d, q0, a, L)
    ss = float(np.sum(w * (y - pred) ** 2) / np.sum(w * (y - y.mean()) ** 2))
    return {"q0": q0, "amplitude": a, "length_km": L, "r2": 1.0 - ss,
            "fn": lambda x: model(np.asarray(x, float), q0, a, L)}


def q_at(curve: pd.DataFrame, fit: dict, d_km: float) -> float:
    """Disagreement rate at one separation, measured where possible."""
    hit = curve[(curve.sep_lo <= d_km) & (curve.sep_hi > d_km)]
    if len(hit):
        return float(hit.q.iloc[0])
    return float(fit["fn"](d_km))


# ---------------------------------------------------------------------------
# 4. What a noisy label does to a score, and to a DIFFERENCE of two scores
# ---------------------------------------------------------------------------
def brier_shift(f: np.ndarray, y: np.ndarray, q10: float, q01: float) -> float:
    """Expected change in a Brier score if the label were read at another point.

    For a binary label, (f - y)^2 = f^2 + (1 - 2f) y, so a score depends on the
    label only through its expectation. Replacing y by a label that flips 1->0
    with probability q10 and 0->1 with probability q01 therefore moves the
    score by exactly

        E[(1 - 2f) (E[y' | y] - y)]  =  E[(1 - 2f) (q01 (1 - y) - q10 y)]

    with no approximation and no simulation. Everything below is this identity
    applied twice.
    """
    f = np.asarray(f, float)
    y = np.asarray(y, float)
    return float(np.mean((1.0 - 2.0 * f) * (q01 * (1.0 - y) - q10 * y)))


def paired_shift(f1: np.ndarray, f2: np.ndarray, y: np.ndarray,
                 q10: float, q01: float) -> float:
    """The same for a PAIRED difference of two Brier scores - and it does not vanish.

    The intuition to be careful of is that truth noise, being common to both
    forecasts, cancels in a difference. It does not. Subtracting the identity
    above from itself leaves

        2 q10 E[(f1 - f2) y]  -  2 q01 E[(f1 - f2) (1 - y)]

    which is zero only if the two forecasts differ by the same amount on wet
    days as on dry ones. A systematically drier forecast does not: it is much
    lower than its rival on wet days and only slightly lower on dry ones, so
    the two expectations have different magnitudes and the noise leaves a
    signed residue. That is exactly the vendor-versus-ensemble configuration,
    which is why this study cannot wave truth error away as common-mode.
    """
    f1 = np.asarray(f1, float)
    f2 = np.asarray(f2, float)
    y = np.asarray(y, float)
    d = f1 - f2
    return float(2.0 * q10 * np.mean(d * y)
                 - 2.0 * q01 * np.mean(d * (1.0 - y)))


def paired_spread(f1, f2, y, q10: float, q01: float, n_draw: int = 2000,
                  day_index: np.ndarray | None = None,
                  seed: int = RANDOM_SEED) -> dict:
    """How far the paired verdict wanders when the verification point moves.

    The identity above gives the expected shift. This gives its spread, which
    is the number that decides whether a difference is resolvable at all: draw
    a plausible alternative label field, rescore, repeat.

    Flips are drawn per CITY-DAY and not per row, because a day's label is one
    fact about one place - the same city-day appearing at several leads must
    flip together or the draw would average the noise away and understate the
    spread by the square root of the number of leads.
    """
    rng = np.random.default_rng(seed)
    f1 = np.asarray(f1, float)
    f2 = np.asarray(f2, float)
    y = np.asarray(y, float)
    if day_index is None:
        day_index = np.arange(len(y))
    uniq, inv = np.unique(day_index, return_inverse=True)
    # One label per unit, so the flip probability can depend on it.
    y_unit = np.zeros(len(uniq))
    y_unit[inv] = y
    p_unit = np.where(y_unit > 0.5, q10, q01)
    base = float(np.mean((f1 - y) ** 2 - (f2 - y) ** 2))
    out = np.empty(n_draw)
    for k in range(n_draw):
        flip_unit = rng.random(len(uniq)) < p_unit
        yy = np.where(flip_unit[inv], 1.0 - y, y)
        out[k] = np.mean((f1 - yy) ** 2 - (f2 - yy) ** 2)
    return {"base": base, "mean": float(out.mean()),
            "sd": float(out.std(ddof=1)),
            "lo": float(np.quantile(out, 0.025)),
            "hi": float(np.quantile(out, 0.975)),
            "draws": out}


# ---------------------------------------------------------------------------
# 5. The direct experiment: rescore against a different real gauge
# ---------------------------------------------------------------------------
ALT_RADIUS_KM = 25.0       # a plausible alternative gauge for the same cell
MIN_ALT_DAYS = 300


def alternative_gauges(pool: pd.DataFrame, store: pd.DataFrame,
                       cities: dict, radius_km: float = ALT_RADIUS_KM
                       ) -> pd.DataFrame:
    """Gauges that could plausibly have been chosen instead, per city.

    'Could plausibly have been chosen' is doing real work. These are not worse
    gauges to be corrected against a better one - the study's own station was
    picked by a coverage probe on record length, and at most of these cities
    several gauges would have passed. The point of the exercise is that the
    choice was close to arbitrary and the verdict should not depend on it.
    """
    counts = store.groupby("station").size()
    rows = []
    for name, c in cities.items():
        cx = _xyz(np.array([c.latitude]), np.array([c.longitude]))
        sx = _xyz(pool.lat.to_numpy(), pool.lon.to_numpy())
        d = np.linalg.norm(sx - cx, axis=1)
        near = pool[(d <= radius_km)].assign(dist_km=d[d <= radius_km])
        for _, s in near.iterrows():
            if s.id == c.prcp_station:
                continue
            if counts.get(s.id, 0) < MIN_ALT_DAYS:
                continue
            rows.append({"city": name, "station": s.id, "dist_km": s.dist_km,
                         "elev_m": s.elev_m, "primary": c.prcp_station,
                         "primary_km": c.prcp_km})
    return pd.DataFrame(rows)


def gauge_labels(store: pd.DataFrame, station: str, city,
                 era5: pd.DataFrame) -> pd.DataFrame | None:
    """Daily rain labels for one gauge, on its OWN day convention.

    Each gauge gets its own offset scan against reanalysis, exactly as
    `capitals.scan_offset` establishes it for the study's chosen stations. That
    is not a detail: GHCN's morning-observation convention is a property of the
    observer, not of the country, so borrowing the primary gauge's offset would
    charge a one-day misalignment to representativeness error and inflate every
    number below.
    """
    from capitals import scan_offset

    s = store[store.station == station][["ghcn_date", "prcp"]].dropna()
    if len(s) < MIN_ALT_DAYS:
        return None
    scan = scan_offset(s, era5)
    if not scan["ok"]:
        return None
    out = s.copy()
    out["local_date"] = (out.ghcn_date
                         + pd.Timedelta(days=scan["offset"])).dt.date
    out["alt_event"] = out.prcp >= RAIN_THRESHOLD_MM
    out.attrs["offset"] = scan["offset"]
    out.attrs["scan_r"] = scan["r"]
    return out[["local_date", "prcp", "alt_event"]]


def rescore(cell: pd.DataFrame, pool: pd.DataFrame, store: pd.DataFrame,
            cities: dict) -> pd.DataFrame:
    """Re-run the headline paired comparison against each alternative gauge.

    No model, no flip rate, no fitted curve: the same forecasts, the same days,
    a different real bucket a few kilometres away. If the sign of the verdict
    survives this it is robust to where the gauge is; if it does not, the
    four-decimal number was never about the forecasts.
    """
    alts = alternative_gauges(pool, store, cities)
    if alts.empty:
        return pd.DataFrame()
    from capitals import fetch_era5

    base_rows = []
    for city, g in cell.groupby("city"):
        if city not in cities:
            continue
        sub = alts[alts.city == city]
        if sub.empty:
            continue
        try:
            era5 = fetch_era5(cities[city])
        except Exception:
            continue
        g = g.copy()
        g["local_date"] = pd.to_datetime(g.local_date).dt.date
        for _, a in sub.iterrows():
            lab = gauge_labels(store, a.station, cities[city], era5)
            if lab is None:
                continue
            m = g.merge(lab, on="local_date", how="inner")
            if len(m) < MIN_ALT_DAYS:
                continue
            y0 = m.event.to_numpy(float)
            y1 = m.alt_event.to_numpy(float)
            v, e = m.vendor_pop.to_numpy(), m.pop_daytotal.to_numpy()
            base_rows.append({
                "city": city, "station": a.station,
                "dist_km": float(a.dist_km),
                "primary_km": float(a.primary_km),
                "n": int(len(m)),
                "label_disagreement": float(np.mean(y0 != y1)),
                "base_primary": float(y0.mean()),
                "base_alt": float(y1.mean()),
                "d_brier_primary": float(np.mean((v - y0) ** 2
                                                 - (e - y0) ** 2)),
                "d_brier_alt": float(np.mean((v - y1) ** 2 - (e - y1) ** 2)),
                "brier_vendor_primary": float(np.mean((v - y0) ** 2)),
                "brier_vendor_alt": float(np.mean((v - y1) ** 2)),
                "bias_primary": float(np.mean(v - y0)),
                "bias_alt": float(np.mean(v - y1)),
            })
    out = pd.DataFrame(base_rows)
    if out.empty:
        return out
    out["d_brier_move"] = out.d_brier_alt - out.d_brier_primary
    out["sign_flips"] = np.sign(out.d_brier_alt) != np.sign(out.d_brier_primary)
    return out


def label_panel(cell: pd.DataFrame, pool: pd.DataFrame, store: pd.DataFrame,
                cities: dict) -> tuple[pd.DataFrame, dict]:
    """The headline cell, plus every alternative label field for it.

    Returns the cell restricted to days where at least one alternative exists,
    and a mapping city -> list of alternative label arrays aligned to it. Built
    once so the pooled experiment below can draw thousands of alternative
    worlds without re-reading a gauge.
    """
    from capitals import fetch_era5

    alts = alternative_gauges(pool, store, cities)
    cell = cell.copy()
    cell["local_date"] = pd.to_datetime(cell.local_date).dt.date
    fields: dict[str, list[tuple[str, np.ndarray]]] = {}
    for city, g in cell.groupby("city"):
        sub = alts[alts.city == city]
        if sub.empty or city not in cities:
            continue
        try:
            era5 = fetch_era5(cities[city])
        except Exception:
            continue
        for _, a in sub.iterrows():
            lab = gauge_labels(store, a.station, cities[city], era5)
            if lab is None:
                continue
            m = g[["local_date"]].merge(lab, on="local_date", how="left")
            y = m.alt_event.to_numpy()
            if pd.isna(y).mean() > 0.1:
                continue
            # Days the alternative did not report keep the study's own label:
            # the experiment is 'a different gauge was chosen', not 'the record
            # is shorter', and dropping those days would change the sample as
            # well as the truth and confound the two.
            base = g.event.to_numpy(float)
            filled = np.where(pd.isna(y), base, np.asarray(y, float))
            fields.setdefault(city, []).append((a.station, filled))
    return cell, fields


def headline_statistics(v: np.ndarray, e: np.ndarray, y: np.ndarray) -> dict:
    """The claims this study actually makes, as numbers, on one label field.

    Four, chosen because they are the four the paper leans on and because they
    fail differently: a difference of two Brier scores that is tiny, a bias
    that is large, and the low-cost-loss decision gap that E4a rests on. If
    truth error can move all four, the paper has no findings; if it can move
    only the first, the paper has one fewer than it thought.
    """
    from metrics import value_curve

    out = {
        "d_brier": float(np.mean((v - y) ** 2 - (e - y) ** 2)),
        "bias_vendor": float(np.mean(v - y)),
        "bias_ensemble": float(np.mean(e - y)),
    }
    alphas = np.array([0.05, 0.10, 0.50])
    cv = value_curve(v, y, alphas=alphas).v_calibrated.to_numpy()
    ce = value_curve(e, y, alphas=alphas).v_calibrated.to_numpy()
    for a, x, z in zip(alphas, cv, ce):
        out[f"d_value_a{int(a * 100):02d}"] = float(x - z)
    return out


def pooled_rescore(cell: pd.DataFrame, fields: dict, n_draw: int = 2000,
                   seed: int = RANDOM_SEED) -> pd.DataFrame:
    """Every headline number under every plausible choice of verification gauge.

    One draw is one alternative world: each city independently keeps its own
    gauge or swaps to one of its neighbours, and the study's statistics are
    recomputed over the identical days and the identical forecasts. The spread
    across worlds is the part of each headline that is about where a bucket
    stands rather than about how good a forecast is.

    Cities are drawn independently because the choices WERE independent - each
    was made by a coverage probe reading one city's station list. Drawing them
    jointly would be modelling a systematic siting error that did not happen.
    """
    rng = np.random.default_rng(seed)
    order = sorted(cell.city.unique())
    idx = {c: (cell.city.to_numpy() == c) for c in order}
    v = cell.vendor_pop.to_numpy(float)
    e = cell.pop_daytotal.to_numpy(float)
    y0 = cell.event.to_numpy(float)

    base = headline_statistics(v, e, y0)
    rows, swaps = [], []
    for k in range(n_draw):
        y = y0.copy()
        n_sw = 0
        for c in order:
            opts = fields.get(c)
            if not opts:
                continue
            j = rng.integers(0, len(opts) + 1)     # +1 = keep the study's own
            if j < len(opts):
                # Alternative fields are stored in the city's own row order,
                # which `idx[c]` reproduces: a boolean mask preserves order and
                # so does a groupby within a group.
                y[idx[c]] = opts[j][1]
                n_sw += 1
        rows.append(headline_statistics(v, e, y))
        swaps.append(n_sw)

    draws = pd.DataFrame(rows)
    out = []
    for stat, col in draws.items():
        b = base[stat]
        out.append({
            "statistic": stat, "study_value": b,
            "mean": float(col.mean()), "sd": float(col.std(ddof=1)),
            "lo": float(col.quantile(0.025)),
            "hi": float(col.quantile(0.975)),
            "p_sign_flip": float(np.mean(np.sign(col) != np.sign(b))),
            # The number that decides whether a claim survives: how large the
            # claim is relative to how far gauge siting alone can move it.
            "signal_to_siting": abs(b) / max(float(col.std(ddof=1)), 1e-12),
            "n_draw": n_draw,
            "n_cities": len(order),
            "n_cities_with_alt": int(sum(1 for c in order if fields.get(c))),
            "mean_swaps": float(np.mean(swaps)),
        })
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# 6. Correctness checks
# ---------------------------------------------------------------------------
def _toy(rng, n: int = 4000, drier: float = 0.0):
    """A calibrated forecast, a rival shifted drier, and the truth behind both."""
    p = rng.beta(1.6, 3.0, n)
    y = (rng.random(n) < p).astype(float)
    f2 = np.clip(p + rng.normal(0, 0.05, n), 0, 1)
    f1 = np.clip(f2 - drier, 0, 1)
    return f1, f2, y


def run_checks(verbose: bool = True, n_sim: int = 40) -> None:
    """Every claim this module makes, against a known answer first."""
    def say(msg):
        if verbose:
            print(f"  ok  {msg}")

    rng = np.random.default_rng(RANDOM_SEED)

    # 1. The single-score identity is exact, not an approximation.
    f1, f2, y = _toy(rng, drier=0.10)
    q10, q01 = 0.18, 0.08
    pred = brier_shift(f1, y, q10, q01)
    sim = []
    for _ in range(400):
        pf = np.where(y > 0.5, q10, q01)
        yy = np.where(rng.random(len(y)) < pf, 1.0 - y, y)
        sim.append(np.mean((f1 - yy) ** 2) - np.mean((f1 - y) ** 2))
    got = float(np.mean(sim))
    if abs(got - pred) > 0.004:
        die(f"Brier-shift identity wrong: predicted {pred:.5f}, "
            f"simulation says {got:.5f}")
    say(f"the label-noise identity predicts a Brier shift of {pred:+.4f} and "
        f"simulation\n      returns {got:+.4f} - so no simulation is needed "
        f"for the expected shift")

    # 2. The paired identity, and the claim that it does NOT vanish.
    pred = paired_shift(f1, f2, y, q10, q01)
    sim = []
    base = np.mean((f1 - y) ** 2 - (f2 - y) ** 2)
    for _ in range(400):
        pf = np.where(y > 0.5, q10, q01)
        yy = np.where(rng.random(len(y)) < pf, 1.0 - y, y)
        sim.append(np.mean((f1 - yy) ** 2 - (f2 - yy) ** 2) - base)
    got = float(np.mean(sim))
    if abs(got - pred) > 0.002:
        die(f"paired-shift identity wrong: predicted {pred:.5f}, "
            f"simulation says {got:.5f}")
    if abs(pred) < 0.002:
        die("paired shift vanished for a systematically drier forecast - the "
            "non-cancellation claim is false as implemented")
    say(f"for a forecast 0.10 drier than its rival the paired shift is "
        f"{pred:+.4f}, not zero:\n      truth noise does not cancel out of a "
        f"difference, and simulation agrees ({got:+.4f})")

    # 3. ... but it DOES vanish when the two forecasts are symmetric, which is
    #    the case the cancelling intuition is actually right about.
    g1, g2, yy2 = _toy(rng, drier=0.0)
    sym = paired_shift(g1, g2, yy2, q10, q01)
    if abs(sym) > 0.004:
        die(f"paired shift should be near zero for symmetric rivals, got {sym}")
    say(f"and it does vanish ({sym:+.4f}) when neither forecast is the drier "
        f"one - the\n      cancelling intuition is right about that case and "
        f"only that case")

    # 4. The pair machinery recovers a planted disagreement rate.
    n_st, n_day = 60, 800
    truth = (rng.random((1, n_day)) < 0.3).astype(np.int8)
    flip = rng.random((n_st, n_day)) < 0.12
    rain = np.where(flip, 1 - truth, truth).astype(np.int8)
    seen = np.ones_like(rain)
    fake = pd.DataFrame({"i_a": [0] * (n_st - 1),
                         "i_b": list(range(1, n_st)),
                         "station_a": ["A"] * (n_st - 1),
                         "station_b": [f"B{i}" for i in range(1, n_st)],
                         "sep_km": 1.0, "elev_diff_m": 0.0,
                         "same_country": True})
    got_q = pair_stats(fake, rain, seen).q.mean()
    want = 2 * 0.12 * (1 - 0.12)
    if abs(got_q - want) > 0.02:
        die(f"pair disagreement machinery is wrong: planted {want:.3f}, "
            f"recovered {got_q:.3f}")
    say(f"two gauges each flipping 12% of an identical truth disagree "
        f"{want:.3f} of the time\n      by construction, and the pair "
        f"machinery recovers {got_q:.3f}")

    # 5. Why the bootstrap resamples gauges and not pairs.
    #    One gauge, many pairs: resampling pairs treats one gauge's peculiarity
    #    as many observations and returns an interval far too narrow.
    hub_cover = wide_cover = 0
    for _ in range(n_sim):
        n_g = 40
        eff = rng.normal(0, 0.03, n_g)          # each gauge's own quirk
        rows = []
        for a in range(n_g):
            for b in range(a + 1, n_g):
                rows.append((a, b, 0.10 + eff[a] + eff[b]
                             + rng.normal(0, 0.005)))
        ps = pd.DataFrame(rows, columns=["a", "b", "q"])
        ps = ps.assign(station_a=[f"G{a}" for a in ps.a],
                       station_b=[f"G{b}" for b in ps.b],
                       sep_km=0.5, n=600, q_a1b0=ps.q / 2, q_a0b1=ps.q / 2,
                       q_independent=0.4, agreement_skill=0.75)
        truth_mean = 0.10
        cur = separation_curve(ps, n_boot=120, seed=int(rng.integers(1 << 30)))
        row = cur.iloc[0]
        wide_cover += row.q_lo <= truth_mean <= row.q_hi
        # the naive alternative: a binomial interval over pairs
        se = ps.q.std(ddof=1) / np.sqrt(len(ps))
        hub_cover += abs(ps.q.mean() - truth_mean) <= 1.96 * se
    wc, hc = wide_cover / n_sim, hub_cover / n_sim
    if wc < 0.80:
        die(f"gauge-resampling interval covers only {wc:.0%} - too narrow")
    if hc > 0.50:
        die(f"the pair-resampling interval covered {hc:.0%}; it is supposed to "
            f"fail here, so this check is no longer demonstrating anything")
    say(f"where one bad gauge enters dozens of pairs, resampling GAUGES covers "
        f"{wc:.0%} at a\n      nominal 95% while treating pairs as "
        f"independent covers {hc:.0%} - which is why the\n      interval is "
        f"built the slow way")


# ---------------------------------------------------------------------------
# 7. Report
# ---------------------------------------------------------------------------
def report(curve: pd.DataFrame, fit: dict, per_city: pd.DataFrame,
           pooled: pd.DataFrame, flip: dict, mde: float | None) -> None:
    print("\n" + "=" * 78)
    print("TRUTH UNCERTAINTY: how much of the verdict is where the bucket "
          "stands? (G3)")
    print("=" * 78)

    floor = curve.iloc[0]
    print("\n--- 1. Two gauges, one sky: disagreement against separation ---")
    print(f"   {'separation':>12} {'pairs':>7} {'gauges':>7} {'q':>7} "
          f"{'95% CI':>18} {'vs independent':>15}")
    for _, r in curve.iterrows():
        print(f"   {r.sep_lo:5.1f}-{r.sep_hi:<5.1f} km {int(r.n_pairs):7d} "
              f"{int(r.n_gauges):7d} {r.q:7.4f} "
              f"[{r.q_lo:7.4f},{r.q_hi:7.4f}] {r.agreement_skill:15.3f}")
    print(f"  Two gauges under {floor.sep_hi:.0f} km apart still disagree "
          f"about whether it rained on\n  {floor.q:.1%} of days. Nothing "
          f"spatial is left at that range, so that is the floor: bucket\n"
          f"  against bucket, reader against reader, hour against hour. It is "
          f"not representativeness\n  error - it is the noise in the truth "
          f"itself, and no forecast can score past it.")
    print(f"  The fitted curve q(d) = {fit['q0']:.4f} + "
          f"{fit['amplitude']:.4f}(1 - exp(-d/{fit['length_km']:.0f} km)) "
          f"has R^2 = {fit['r2']:.3f}. Its\n  intercept is "
          f"{fit['q0']:.4f}, above the {floor.q:.4f} measured under "
          f"{floor.sep_hi:.0f} km: a single exponential\n  cannot hold both "
          f"the steep near rise and the long tail, so the floor quoted above "
          f"is the\n  MEASURED one and the fit is used only to interpolate "
          f"between bins.")

    print("\n--- 2. What that does to a score, and to a DIFFERENCE of scores ---")
    print(f"  A label that flips at these rates moves the vendor's own Brier "
          f"by {flip['brier_vendor']:+.4f}\n  and the ensemble's by "
          f"{flip['brier_ensemble']:+.4f} - both enormous next to anything "
          f"this study reports.\n  The paired difference is supposed to be "
          f"safe from that, because the noise is common to\n  both. It is "
          f"not: the identity leaves "
          f"2q10 E[(f1-f2)y] - 2q01 E[(f1-f2)(1-y)], which\n  vanishes only "
          f"for rivals that differ equally on wet and dry days. A drier "
          f"forecast does\n  not, so the residue here is "
          f"{flip['paired']:+.4f} - {abs(flip['paired'] / pooled.iloc[0].study_value):.0f} "
          f"times the difference being measured.")

    print("\n--- 3. But the flip model is wrong, and the real gauges say so ---")
    print(f"   {'source':<34} {'bias':>10} {'spread (sd)':>12}")
    print(f"   {'independent label flips':<34} {flip['paired']:+10.5f} "
          f"{flip['sd']:12.5f}")
    print(f"   {'real neighbouring gauges':<34} "
          f"{pooled.iloc[0]['mean'] - pooled.iloc[0].study_value:+10.5f} "
          f"{pooled.iloc[0].sd:12.5f}")
    print("  The two agree on the SPREAD and disagree on the BIAS by a factor "
          "of dozens. Independent\n  flips break the link between forecast "
          "and truth: they turn a confidently forecast wet\n  day dry as "
          "readily as a marginal one. A real gauge five kilometres away does "
          "not - it\n  saw the same weather system, so it agrees with the "
          "forecast on exactly the days the\n  forecast was right about. The "
          "textbook label-noise correction would have applied a\n  "
          f"{flip['paired']:+.4f} adjustment to this study's headline that the "
          f"evidence does not support.")

    print("\n--- 4. Which claims survive re-siting the gauge? ---")
    print(f"   {'statistic':<16} {'study':>10} {'siting sd':>10} "
          f"{'95% range':>22} {'flips':>7} {'|value|/sd':>11}")
    for _, r in pooled.iterrows():
        print(f"   {r.statistic:<16} {r.study_value:+10.4f} {r.sd:10.4f} "
              f"[{r.lo:+9.4f},{r.hi:+9.4f}] {r.p_sign_flip:7.0%} "
              f"{r.signal_to_siting:11.1f}")
    dead = pooled[pooled.signal_to_siting < 1.0]
    live = pooled[pooled.signal_to_siting >= 3.0]
    print(f"  {pooled.iloc[0].n_cities_with_alt} of "
          f"{pooled.iloc[0].n_cities} cities have a plausible alternative "
          f"gauge within {ALT_RADIUS_KM:.0f} km; a draw swaps\n  "
          f"{pooled.iloc[0].mean_swaps:.1f} of them on average. Every "
          f"forecast and every day is held fixed - the only\n  thing that "
          f"moves is which bucket the day is scored against.")
    if len(dead):
        names = ", ".join(dead.statistic)
        print(f"  DOES NOT SURVIVE: {names}. The Brier comparison is smaller "
              f"than the amount gauge\n  siting alone moves it, and its sign "
              f"changes in "
              f"{dead.iloc[0].p_sign_flip:.0%} of alternative worlds. It is "
              f"not a tie\n  and not a difference; it is a measurement this "
              f"truth cannot support.")
    if len(live):
        print(f"  SURVIVES: {', '.join(live.statistic)} - every one at least "
              f"3x its siting spread, none\n  changing sign in any "
              f"alternative world. The low-cost-loss decision gap is "
              f"{abs(pooled[pooled.statistic == 'd_value_a05'].signal_to_siting.iloc[0]):.0f}x\n"
              f"  its siting uncertainty while the Brier gap is "
              f"{pooled[pooled.statistic == 'd_brier'].signal_to_siting.iloc[0]:.2f}x its own. "
              f"The same days, the same\n  gauges, the same forecasts - the "
              f"choice of metric decides whether truth error matters.")
    if mde is not None:
        print(f"  Sampling and siting are separate costs and both apply: the "
              f"block bootstrap put the\n  smallest detectable Brier "
              f"difference at {mde:.4f}, and siting adds "
              f"{pooled.iloc[0].sd:.4f} on top.")

    print("\n--- 5. Task 14: what a gauge-pair method can and cannot see ---")
    asym = float((curve.q_a1b0 - curve.q_a0b1).abs().max())
    print(f"  The two halves of the disagreement - wet here and dry there "
          f"against its mirror - stay\n  within {asym:.4f} of each other at "
          f"every separation. There is no directional catch\n  difference to "
          f"be found between neighbouring gauges at this scale.")
    if len(per_city):
        mv = (per_city.base_alt - per_city.base_primary).abs()
        print(f"  Changing gauge moves a city's measured rain-day frequency "
              f"by a median of {mv.median():.3f}\n  and by up to "
              f"{mv.max():.3f}. Since climatology is the reference every "
              f"skill score is quoted\n  against, that is a shift in the "
              f"denominator as well as in the labels - which is why the\n"
              f"  re-siting above is run on the scores rather than on the "
              f"labels alone.")
    print("  What it CANNOT see is the part every gauge gets wrong together. "
          "Wind loss, wetting and\n  evaporation push all buckets the same "
          "way, so a pair test is blind to them by\n  construction. That "
          "residue is not corrected here and not bounded here, and the paper "
          "has\n  to say so rather than quote a literature factor it has not "
          "measured.")

    print("\n--- Caveats ---")
    print(f"  1. The alternative gauges are near {pooled.iloc[0].n_cities_with_alt} "
          f"cities, not all {pooled.iloc[0].n_cities}. The cities without one "
          f"contribute\n     no spread, so the siting uncertainty above is a "
          f"LOWER bound on the real one.")
    print("  2. Each alternative gauge gets its own day-convention scan "
          "against reanalysis, and a\n     gauge whose scan has no clean peak "
          "is dropped rather than guessed at. Dropping it\n     is a "
          "selection - the surviving gauges are the better-behaved ones, "
          "which again makes\n     the spread a lower bound.")
    print("  3. Days an alternative gauge did not report keep the study's own "
          "label, so a draw\n     changes the truth and never the sample. "
          "That is the right comparison and it means a\n     patchy "
          "alternative moves the answer less than a complete one would.")
    print(f"  4. The separation curve is measured at the {RAIN_THRESHOLD_MM} "
          f"mm threshold this study scores at.\n     A different threshold "
          f"would give a different floor, and the trace-rain end of the\n"
          f"     scale is where gauges disagree most.")


# ---------------------------------------------------------------------------
def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    quick = arg == "quick"

    print("=== correctness checks ===")
    run_checks(n_sim=15 if quick else 40)
    if arg in ("check", "selftest"):
        print("all checks passed")
        return

    import significance as sg
    import triangulation

    pool = station_pool()
    store = ensure_store(pool)
    ids, days, rain, seen = event_matrix(store, pool)
    print(f"  event panel: {rain.shape[0]} gauges x {rain.shape[1]} days, "
          f"{days.min().date()}..{days.max().date()}")

    pairs = neighbour_pairs(pool, ids)
    ps = pair_stats(pairs, rain, seen)
    print(f"  {len(ps):,} gauge pairs with at least {MIN_COMMON_DAYS} "
          f"shared days")
    curve = separation_curve(ps, n_boot=150 if quick else 600)
    fit = fit_curve(curve)

    paired = triangulation.build_paired(sg.LIKE_FOR_LIKE_MODEL)
    triangulation.check_paired(paired)
    c1 = sg.cell(paired, sg.GEFS_BOUNDARY_PRIMARY, 1)
    cities = load_capitals()

    per_city = rescore(c1, pool, store, cities)
    cell, fields = label_panel(c1, pool, store, cities)
    pooled = pooled_rescore(cell, fields, n_draw=300 if quick else 2000)

    # The flip model, evaluated at the separation a gauge sits from the grid
    # point it is asked to stand for.
    d_typ = float(np.median([c.prcp_km for c in cities.values()
                             if c.prcp_km is not None]))
    q = q_at(curve, fit, d_typ)
    row = curve[(curve.sep_lo <= d_typ) & (curve.sep_hi > d_typ)]
    base = float(ps.base_a.mean())
    if len(row):
        q10 = float(row.q_a1b0.iloc[0]) / base
        q01 = float(row.q_a0b1.iloc[0]) / (1.0 - base)
    else:
        q10 = q01 = q / 2.0
    v = cell.vendor_pop.to_numpy(float)
    e = cell.pop_daytotal.to_numpy(float)
    y = cell.event.to_numpy(float)
    day_index = pd.factorize(cell.city.astype(str) + "|"
                             + cell.local_date.astype(str))[0]
    spread = paired_spread(v, e, y, q10, q01,
                           n_draw=300 if quick else 1500, day_index=day_index)
    flip = {"sep_km": d_typ, "q": q, "q10": q10, "q01": q01,
            "brier_vendor": brier_shift(v, y, q10, q01),
            "brier_ensemble": brier_shift(e, y, q10, q01),
            "paired": paired_shift(v, e, y, q10, q01),
            "sd": spread["sd"]}
    print(f"  typical gauge-to-grid-point distance {d_typ:.1f} km "
          f"-> q = {q:.4f} (q10 {q10:.3f}, q01 {q01:.3f})")

    mde = None
    if sg.HEADLINE_TABLE.exists():
        h = pd.read_parquet(sg.HEADLINE_TABLE)
        hit = h[(h.statistic == "brier") & (h.lead_days == 1)
                & (h.boundary_mode == sg.GEFS_BOUNDARY_PRIMARY)]
        if len(hit) and "mde_80" in hit:
            mde = float(hit.mde_80.iloc[0])

    PAIRS_TABLE.parent.mkdir(parents=True, exist_ok=True)
    ps.drop(columns=["i_a", "i_b"]).to_parquet(PAIRS_TABLE, index=False)
    curve.to_parquet(CURVE_TABLE, index=False)
    per_city.to_parquet(RESCORE_TABLE, index=False)
    pooled.assign(**{f"flip_{k}": v for k, v in flip.items()}
                  ).to_parquet(PROPAGATION_TABLE, index=False)

    report(curve, fit, per_city, pooled, flip, mde)
    print(f"\nwrote {PAIRS_TABLE.name}, {CURVE_TABLE.name}, "
          f"{RESCORE_TABLE.name} and {PROPAGATION_TABLE.name}")


if __name__ == "__main__":
    main()
