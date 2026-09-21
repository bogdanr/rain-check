"""Task 12. Can a satellite stand in for the gauge that is not there?

E30 left G1 split in two. Asia is an engineering problem -- ISD reports there.
Africa and South America are not: the stations exist and are silent on rain, so
either a non-gauge truth source works or those continents stay outside every
verification study including this one.

This stage tests one, and the test is possible for a reason worth stating. The
408 GHCN gauges reporting in Africa and South America are far too sparse to
score cities -- that is E27's finding and it does not change. They are ample to
score a *satellite*, because a satellite needs no gauge near a city, only
enough gauges somewhere to establish how far it can be trusted.

Two products, because the obvious one is circular. CHIRPS blends station reports
into its estimate, and the stations it blends in Africa are largely the ones
tested against here, so agreement would partly be the product recognising its
own input. CHIRP is the same pipeline with the blending step removed. Both are
read, and the gap between them is the size of the circularity -- which turns
out to point the other way once both are asked to rain as often as the bucket.

The comparison is against a floor this project already measured. E20 put
gauge-against-gauge disagreement at 0.083 for two buckets in the same field and
0.095 at the satellite's own 5 km footprint. A satellite cannot beat that, and
the question is not whether it is noisier than a gauge -- it is -- but whether
what it adds is small next to the effects the study needs to see.

    python satellite_truth.py check     correctness checks only
    python satellite_truth.py           full stage

Writes satellite_truth_stations.parquet, satellite_truth_daily.parquet,
satellite_truth_scores.parquet.
"""
from __future__ import annotations

import gzip
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from config import CACHE, PROCESSED, RAIN_THRESHOLD_MM, RANDOM_SEED

# The balanced window Task 17 settled on.
WINDOW_START = "2024-06-01"
WINDOW_END = "2026-05-31"

# CHIRP/CHIRPS cover 50S-50N only; nothing outside the band is comparable.
BAND = 50.0

# Sampled days come in short consecutive blocks so the +-1 day alignment scan
# has neighbours to look at. Isolated days cannot support it.
N_BLOCKS = 40
BLOCK_LEN = 5

# All of the scarce continents, a cap on the abundant ones. A thinning radius
# stops one dense national network supplying its continent's whole sample.
CAP_PER_CONTINENT = 60
THIN_KM = 40.0

SCARCE = ("Africa", "South America", "Central America & Caribbean")

PRODUCTS = {
    "chirp": ("https://data.chc.ucsb.edu/products/CHIRP/daily/"
              "{y}/chirp.{y}.{m:02d}.{d:02d}.tif", False),
    "chirps": ("https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/"
               "tifs/p05/{y}/chirps-v2.0.{y}.{m:02d}.{d:02d}.tif.gz", True),
}

GRID_CACHE = CACHE / "chirp"
STATIONS = PROCESSED / "satellite_truth_stations.parquet"
DAILY = PROCESSED / "satellite_truth_daily.parquet"
SCORES = PROCESSED / "satellite_truth_scores.parquet"

# E20's numbers, quoted rather than recomputed so the comparison is against the
# published floor and not a private one.
GAUGE_FLOOR_0KM = 0.083
GAUGE_FLOOR_5KM = 0.095

N_BOOT = 600


def die(msg: str) -> None:
    print(f"\nFAILED: {msg}", file=sys.stderr)
    raise SystemExit(1)


def say(msg: str) -> None:
    print(f"  ok  {msg}")


# ---------------------------------------------------------------------------
# Continents from coordinates
#
# The city-level code reads a continent off country plus timezone. A GHCN
# station has neither, so this works from the position. Check 1 requires the
# two to agree on the city pool, which is the only reason to trust it.
# ---------------------------------------------------------------------------
def continent_of(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    lat = np.asarray(lat, float)
    lon = np.where(np.asarray(lon, float) > 180, np.asarray(lon, float) - 360,
                   np.asarray(lon, float))
    out = np.full(len(lat), "Other", dtype=object)
    out[(lat > -60) & (lat < 15) & (lon > -82) & (lon < -34)] = "South America"
    out[(lat >= 5) & (lat < 33) & (lon >= -118) & (lon < -58)] = \
        "Central America & Caribbean"
    out[(lat >= 15) & (lat <= 84) & (lon >= -170) & (lon < -52)] = \
        "North America"
    out[(lat > -36) & (lat < 38) & (lon > -19) & (lon < 52)] = "Africa"
    out[(lat >= 34) & (lat <= 72) & (lon >= -25) & (lon < 40)] = "Europe"
    out[(lat >= -12) & (lat <= 78) & (lon >= 40) & (lon <= 180)] = "Asia"
    out[(lat >= 5) & (lat <= 45) & (lon >= 25) & (lon < 45)] = "Asia"
    out[(lat <= -10) & (lon >= 110) & (lon <= 180)] = "Oceania"
    return out


def _xyz(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    la, lo = np.radians(lat), np.radians(lon)
    return np.c_[np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo),
                 np.sin(la)] * 6371.0


def thin(df: pd.DataFrame, km: float, limit: int | None = None
         ) -> pd.DataFrame:
    """Greedy thinning: keep a station only if no kept station is nearer."""
    pts = _xyz(df.lat.to_numpy(), df.lon.to_numpy())
    keep: list[int] = []
    kept = np.empty((0, 3))
    for i in range(len(df)):
        if len(keep) and float(np.linalg.norm(kept - pts[i],
                                             axis=1).min()) <= km:
            continue
        keep.append(i)
        kept = np.vstack([kept, pts[i]])
        if limit is not None and len(keep) >= limit:
            break
    return df.iloc[keep].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 1. The gauge sample
# ---------------------------------------------------------------------------
def station_sample() -> pd.DataFrame:
    if STATIONS.exists():
        return pd.read_parquet(STATIONS)
    from probe_capitals import load_stations, load_inventory
    st = load_stations()
    inv = load_inventory()
    have = inv[(inv.elem == "PRCP") & (inv.year_last >= 2025)
               & (inv.year_first <= 2024)]
    st = st[st.id.isin(set(have.id))].reset_index(drop=True)
    st = st[(st.lat > -BAND) & (st.lat < BAND)].reset_index(drop=True)
    st["continent"] = continent_of(st.lat.to_numpy(), st.lon.to_numpy())
    st = st[st.continent != "Other"].reset_index(drop=True)

    rng = np.random.default_rng(RANDOM_SEED)
    out = []
    for cont, g in st.groupby("continent"):
        g = g.sample(frac=1.0, random_state=int(rng.integers(1 << 30)))
        g = thin(g, THIN_KM,
                 None if cont in SCARCE else CAP_PER_CONTINENT)
        out.append(g.assign(continent=cont))
    sample = pd.concat(out, ignore_index=True)
    STATIONS.parent.mkdir(parents=True, exist_ok=True)
    sample.to_parquet(STATIONS, index=False)
    return sample


def sampled_days() -> pd.DatetimeIndex:
    """Blocks of consecutive days, evenly spread over the balanced window."""
    all_days = pd.date_range(WINDOW_START, WINDOW_END, freq="D")
    starts = np.linspace(0, len(all_days) - BLOCK_LEN, N_BLOCKS).astype(int)
    idx = np.unique(np.concatenate([np.arange(s, s + BLOCK_LEN)
                                    for s in starts]))
    return all_days[idx]


# ---------------------------------------------------------------------------
# 2. The gauge record
# ---------------------------------------------------------------------------
def gauge_daily(sample: pd.DataFrame) -> pd.DataFrame:
    """PRCP for the sampled stations, streamed out of the yearly GHCN files."""
    cache = CACHE / "satellite_truth_gauge.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    from config import RAW
    want = set(sample.id)
    rows = []
    for year in (2024, 2025, 2026):
        path = RAW / "ghcn_year" / f"{year}.csv.gz"
        if not path.exists():
            continue
        with gzip.open(path, "rt") as fh:
            for line in fh:
                sid, date, elem, val = line.split(",", 4)[:4]
                if elem != "PRCP" or sid not in want:
                    continue
                rows.append((sid, date, int(val)))
    df = pd.DataFrame(rows, columns=["id", "date", "tenths"])
    df["ghcn_date"] = pd.to_datetime(df.date, format="%Y%m%d")
    df["mm"] = df.tenths / 10.0
    df = df[(df.ghcn_date >= WINDOW_START) & (df.ghcn_date <= WINDOW_END)]
    df = df[["id", "ghcn_date", "mm"]].reset_index(drop=True)
    df.to_parquet(cache, index=False)
    return df


# ---------------------------------------------------------------------------
# 3. The satellite grids
# ---------------------------------------------------------------------------
def grid_path(product: str, day: pd.Timestamp) -> "os.PathLike":
    return GRID_CACHE / product / f"{day:%Y%m%d}.npy"


def fetch_day(product: str, day: pd.Timestamp, row: np.ndarray,
              col: np.ndarray) -> np.ndarray | None:
    """Download one global day and keep only the station cells.

    The download is tens of megabytes and the kept vector is a few kilobytes,
    so caching the extraction rather than the grid is what makes a re-run
    cheap and keeps the cache off the disk budget.
    """
    out = grid_path(product, day)
    if out.exists():
        return np.load(out)
    tmpl, gz = PRODUCTS[product]
    url = tmpl.format(y=day.year, m=day.month, d=day.day)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "rain-check"})
        with urllib.request.urlopen(req, timeout=300) as fh:
            blob = fh.read()
    except Exception:
        return None
    if gz:
        blob = gzip.decompress(blob)
    import rasterio
    from rasterio.io import MemoryFile
    with MemoryFile(blob) as mem, mem.open() as src:
        if (src.width, src.height) != (7200, 2000):
            die(f"{product} {day:%Y-%m-%d} is {src.width}x{src.height}, "
                "not the 0.05 degree 50S-50N grid this stage assumes")
        band = src.read(1)
    v = band[row, col].astype(np.float32)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, v)
    return v


def grid_index(sample: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Row and column of each station in the 0.05 degree CHIRP grid."""
    row = np.floor((BAND - sample.lat.to_numpy()) / 0.05).astype(int)
    col = np.floor((sample.lon.to_numpy() + 180.0) / 0.05).astype(int)
    return np.clip(row, 0, 1999), np.clip(col, 0, 7199)


def satellite_daily(sample: pd.DataFrame, days: pd.DatetimeIndex,
                    threads: int = 6) -> pd.DataFrame:
    cache = CACHE / "satellite_truth_sat.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    row, col = grid_index(sample)

    frames = []
    for product in PRODUCTS:
        def one(day: pd.Timestamp, product: str = product):
            v = fetch_day(product, day, row, col)
            if v is None:
                return None
            v = v.astype(float)
            v[v < 0] = np.nan          # -9999 fill; not a dry day
            return pd.DataFrame({"id": sample.id.to_numpy(),
                                 "sat_date": day, "product": product,
                                 "mm": v})
        with ThreadPoolExecutor(max_workers=threads) as ex:
            got = list(ex.map(one, days))
        ok = [g for g in got if g is not None]
        print(f"    {product}: {len(ok)}/{len(days)} days")
        if not ok:
            die(f"no {product} days downloaded")
        frames.append(pd.concat(ok, ignore_index=True))
    df = pd.concat(frames, ignore_index=True)
    df.to_parquet(cache, index=False)
    return df


# ---------------------------------------------------------------------------
# 4. Alignment, then disagreement
#
# E24 measured what a one-day error costs: Brier 0.167 to 0.238. Reporting a
# satellite's disagreement without first checking the two calendars line up
# would measure the calendar.
# ---------------------------------------------------------------------------
def event(mm: np.ndarray) -> np.ndarray:
    return (mm >= RAIN_THRESHOLD_MM).astype(float)


def align(sat: pd.DataFrame, gauge: pd.DataFrame,
          lags=(-1, 0, 1)) -> pd.DataFrame:
    """Disagreement at each whole-day shift of the satellite calendar."""
    rows = []
    for lag in lags:
        s = sat.assign(key=sat.sat_date + pd.Timedelta(days=lag))
        j = s.merge(gauge, left_on=["id", "key"],
                    right_on=["id", "ghcn_date"], how="inner").dropna(
                        subset=["sat_mm", "gauge_mm"])
        if not len(j):
            continue
        rows.append({"lag": lag, "n": len(j),
                     "q": float(np.mean(event(j.sat_mm.to_numpy())
                                       != event(j.gauge_mm.to_numpy())))})
    return pd.DataFrame(rows)


def joined(sample: pd.DataFrame, gauge: pd.DataFrame,
           sat: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Align each product to each continent's gauges, then score.

    Per continent rather than globally because the gauge day convention is
    geography (E24), and per continent rather than per station because
    choosing the best of three lags for each of 649 stations would fit the
    noise -- here each choice is made on tens of thousands of days.
    """
    g = gauge.rename(columns={"mm": "gauge_mm"})
    s = sat.rename(columns={"mm": "sat_mm"}).merge(
        sample[["id", "continent"]], on="id")
    scans, out = [], []
    for (product, cont), sp in s.groupby(["product", "continent"]):
        scan = align(sp, g).assign(product=product, continent=cont)
        if scan.empty:
            continue
        lag = int(scan.loc[scan.q.idxmin(), "lag"])
        scans.append(scan.assign(chosen=scan.lag == lag))
        j = sp.assign(key=sp.sat_date + pd.Timedelta(days=lag)).merge(
            g, left_on=["id", "key"], right_on=["id", "ghcn_date"],
            how="inner")
        out.append(j.assign(lag=lag))
    df = pd.concat(out, ignore_index=True).dropna(
        subset=["sat_mm", "gauge_mm"])
    df = df.merge(sample[["id", "lat", "lon"]], on="id")
    df["sat_event"] = event(df.sat_mm.to_numpy())
    df["gauge_event"] = event(df.gauge_mm.to_numpy())
    df["miss"] = (df.sat_event != df.gauge_event).astype(float)
    return df, pd.concat(scans, ignore_index=True)


def gauge_boot(df: pd.DataFrame, col: str, n_boot: int,
               seed: int) -> tuple[float, float]:
    """Resample gauges, not station-days.

    E21 established why: one bad gauge contributes hundreds of rows, so
    treating rows as independent gives an interval far too tight.
    """
    rng = np.random.default_rng(seed)
    ids = df.id.to_numpy()
    uniq, inv = np.unique(ids, return_inverse=True)
    vals = df[col].to_numpy()
    by = [vals[inv == k] for k in range(len(uniq))]
    reps = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        reps[b] = float(np.concatenate([by[k] for k in pick]).mean())
    return float(np.quantile(reps, 0.025)), float(np.quantile(reps, 0.975))


def matched_threshold(v: np.ndarray, base: float) -> float:
    """The satellite threshold that would make it rain as often as the gauge.

    A grid cell is a 5 km area mean over a day and a gauge is a point, so at a
    low threshold the cell is wet whenever anything fell anywhere in it. Fixing
    the threshold at the study's 0.2 mm therefore measures that mismatch as
    well as the satellite's skill. Matching the base rate removes the part of
    the disagreement that is only a difference of definition, and is the more
    generous of the two numbers to the satellite.
    """
    pos = np.sort(v[v > 0])
    if not len(pos):
        return np.inf
    k = int(round(base * len(v)))
    k = min(max(k, 1), len(pos))
    return float(pos[len(pos) - k])


def _row(product: str, cont: str, g: pd.DataFrame) -> dict:
    lo, hi = gauge_boot(g, "miss", N_BOOT, RANDOM_SEED)
    wet = g[g.gauge_event == 1]
    dry = g[g.gauge_event == 0]
    base = float(g.gauge_event.mean())
    thr = matched_threshold(g.sat_mm.to_numpy(), base)
    ev = (g.sat_mm.to_numpy() >= thr).astype(float)
    gm = g.assign(miss_matched=(ev != g.gauge_event.to_numpy()).astype(float))
    mlo, mhi = gauge_boot(gm, "miss_matched", N_BOOT, RANDOM_SEED)
    return {
        "product": product, "continent": cont,
        "n": len(g), "n_gauges": g.id.nunique(),
        "gauge_base": base, "sat_base": float(g.sat_event.mean()),
        "q": float(g.miss.mean()), "q_lo": lo, "q_hi": hi,
        "thr_matched": thr, "sat_base_matched": float(ev.mean()),
        "q_matched": float(gm.miss_matched.mean()),
        "q_matched_lo": mlo, "q_matched_hi": mhi,
        "miss_rate": float(1 - wet.sat_event.mean()) if len(wet) else np.nan,
        "false_rate": float(dry.sat_event.mean()) if len(dry) else np.nan,
    }


def score(df: pd.DataFrame) -> pd.DataFrame:
    rows = [_row(p, c, g) for (p, c), g in df.groupby(["product", "continent"])
            if len(g) >= 500]
    rows += [_row(p, "ALL", g) for p, g in df.groupby("product")]
    return pd.DataFrame(rows).sort_values(["product", "continent"])

# ---------------------------------------------------------------------------
# 5. Correctness checks
#
# Every one of these has a way to fail. The first two police the two places a
# silent coordinate error could live; the rest police the inference.
# ---------------------------------------------------------------------------
def _synth(rng: np.random.Generator, n_gauge: int = 40, n_day: int = 300,
           q: float = 0.0, shift: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Gauge and satellite frames with a known disagreement and shift."""
    days = pd.date_range("2024-06-01", periods=n_day, freq="D")
    gr, sr = [], []
    for k in range(n_gauge):
        wet = rng.random(n_day) < 0.3
        flip = rng.random(n_day) < q
        sat = np.where(flip, ~wet, wet)
        gr.append(pd.DataFrame({"id": f"G{k:03d}", "ghcn_date": days,
                                "gauge_mm": np.where(wet, 5.0, 0.0)}))
        sr.append(pd.DataFrame({"id": f"G{k:03d}",
                                "sat_date": days - pd.Timedelta(days=shift),
                                "product": "synth",
                                "sat_mm": np.where(sat, 5.0, 0.0)}))
    return (pd.concat(gr, ignore_index=True), pd.concat(sr, ignore_index=True))


def run_checks() -> None:
    print("checks")
    rng = np.random.default_rng(RANDOM_SEED)

    # 1. The coordinate rule has to agree with the one the city code uses.
    import sampling
    pool = sampling.load_pool()
    theirs = np.array([sampling.continent(c, t)
                       for c, t in zip(pool.country, pool.timezone)])
    mine = continent_of(pool.lat.to_numpy(), pool.lon.to_numpy())
    band = (pool.lat > -BAND) & (pool.lat < BAND)
    agree = float(np.mean(theirs[band.to_numpy()] == mine[band.to_numpy()]))
    if agree < 0.95:
        die(f"the coordinate continent rule agrees with the city rule on only "
            f"{agree:.1%} of the pool inside the CHIRP band")
    say(f"continent from coordinates agrees with continent from country and "
        f"timezone on {agree:.1%} of the {int(band.sum())} pool cities inside "
        f"the band, so the station-level rule is the city-level one")

    # 2. Arithmetic indexing must equal the file's own affine transform.
    #
    # Except on a cell boundary, where the two round opposite ways and neither
    # is wrong. GHCN publishes many coordinates as exact multiples of 0.05, so
    # this is common rather than rare, and the tie has to be named rather than
    # silently resolved by whichever route runs.
    import rasterio
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    url = ("/vsicurl/https://data.chc.ucsb.edu/products/CHIRP/daily/2025/"
           "chirp.2025.01.01.tif")
    sample = station_sample().head(200)
    row, col = grid_index(sample)
    fr = (BAND - sample.lat.to_numpy()) / 0.05
    fc = (sample.lon.to_numpy() + 180.0) / 0.05
    edge = ((np.abs(fr - np.round(fr)) < 1e-6)
            | (np.abs(fc - np.round(fc)) < 1e-6))
    with rasterio.open(url) as src:
        theirs = np.array([src.index(lo, la) for la, lo
                           in zip(sample.lat, sample.lon)])
    off = (theirs[:, 0] != row) | (theirs[:, 1] != col)
    bad = int(np.sum(off & ~edge))
    if bad:
        die(f"{bad} of {int(np.sum(~edge))} interior stations land on a "
            "different cell under the file's own transform than under this "
            "stage's arithmetic")
    if int(np.max(np.abs(theirs - np.c_[row, col]))) > 1:
        die("an indexing disagreement of more than one cell is not a "
            "rounding tie")
    say(f"all {int(np.sum(~edge))} interior stations land on the same cell "
        f"under the file's transform as under this stage's arithmetic; the "
        f"{int(edge.sum())} sitting exactly on a cell edge round opposite ways "
        f"and are taken south and east, worth at most the "
        f"{GAUGE_FLOOR_5KM - GAUGE_FLOOR_0KM:.3f} E20 attributes to a 5 km move")

    # 3. Fill value must not become a dry day.
    g, s = _synth(rng, n_gauge=5, n_day=50)
    s.loc[s.index[:100], "sat_mm"] = -9999.0
    v = s.sat_mm.to_numpy().astype(float)
    v[v < 0] = np.nan
    s = s.assign(sat_mm=v)
    st = pd.DataFrame({"id": sorted(set(g.id)), "continent": "Africa",
                       "lat": 0.0, "lon": 0.0})
    df, _ = joined(st, g.rename(columns={"gauge_mm": "mm"}),
                   s.rename(columns={"sat_mm": "mm"}))
    if len(df) != len(s) - 100:
        die(f"{len(s) - 100} rows should survive after dropping the fill "
            f"value, {len(df)} did")
    say("the -9999 fill is dropped rather than scored as a dry day, so a gap "
        "in the satellite cannot be counted as agreement about no rain")

    # 4. A planted one-day shift has to be found and undone.
    g, s = _synth(rng, q=0.08, shift=1)
    st = pd.DataFrame({"id": sorted(set(g.id)), "continent": "Africa",
                       "lat": 0.0, "lon": 0.0})
    scan = align(s, g)
    lag = int(scan.loc[scan.q.idxmin(), "lag"])
    q_at_0 = float(scan.loc[scan.lag == 0, "q"].iloc[0])
    if lag != 1:
        die(f"a satellite shifted a day early was corrected by {lag:+d} days, "
            "not +1")
    say(f"a planted one-day offset is found and undone (q {q_at_0:.3f} "
        f"unaligned against {scan.q.min():.3f} aligned), so a calendar error "
        "cannot be reported as satellite noise")

    # 5. Resampling rows instead of gauges gives an interval that lies.
    #
    # The target is the disagreement of the gauge population, not of the 40
    # gauges that happened to be drawn -- that is the quantity a continental
    # number is claiming. Row resampling cannot see that gauges differ from
    # each other, so it reports the drawn sample as if it were the population.
    truth = 0.235                      # mean of the gauge-quality prior below
    cov_row = cov_gauge = 0
    n_sim = 60
    for _ in range(n_sim):
        gr = []
        for k in range(40):
            qk = rng.uniform(0.02, 0.45)       # gauges differ in quality
            miss = (rng.random(250) < qk).astype(float)
            gr.append(pd.DataFrame({"id": f"G{k:03d}", "miss": miss}))
        panel = pd.concat(gr, ignore_index=True)
        lo, hi = gauge_boot(panel, "miss", 200, int(rng.integers(1 << 30)))
        cov_gauge += lo <= truth <= hi
        v = panel.miss.to_numpy()
        se = v.std(ddof=1) / np.sqrt(len(v))
        cov_row += abs(v.mean() - truth) <= 1.96 * se
    cg, cr = cov_gauge / n_sim, cov_row / n_sim
    if cg < 0.85 or cr > 0.60:
        die(f"gauge resampling covers {cg:.0%} and row resampling {cr:.0%}; "
            "expected roughly 95% and far less")
    say(f"resampling gauges covers {cg:.0%} at a nominal 95% while treating "
        f"station-days as independent covers {cr:.0%}, so the interval has to "
        "be built over gauges")

    # 6. The scorer must return what was put in.
    for want in (0.0, 0.20):
        g, s = _synth(rng, n_gauge=60, n_day=400, q=want)
        st = pd.DataFrame({"id": sorted(set(g.id)), "continent": "Africa",
                           "lat": 0.0, "lon": 0.0})
        df, _ = joined(st, g.rename(columns={"gauge_mm": "mm"}),
                       s.rename(columns={"sat_mm": "mm"}))
        got = float(score(df).query("continent == 'ALL'").q.iloc[0])
        if abs(got - want) > 0.01:
            die(f"a satellite built to disagree {want:.2f} of the time scored "
                f"{got:.3f}")
    say("a satellite built to disagree a known fraction of the time is scored "
        "at that fraction, at 0.00 and at 0.20")

    # 7. Base-rate matching must remove a pure definition mismatch and must
    #    not remove a real one.
    days = pd.date_range("2024-06-01", periods=600, freq="D")
    gr, sr = [], []
    for k in range(50):
        wet = rng.random(600) < 0.30
        # a satellite that is right about which days, but scaled so that the
        # study's threshold calls far too many of them wet
        amt = np.where(wet, rng.gamma(3.0, 3.0, 600), rng.gamma(1.0, 0.4, 600))
        gr.append(pd.DataFrame({"id": f"G{k:03d}", "ghcn_date": days,
                                "mm": np.where(wet, 5.0, 0.0)}))
        sr.append(pd.DataFrame({"id": f"G{k:03d}", "sat_date": days,
                                "product": "synth", "mm": amt}))
    st = pd.DataFrame({"id": [f"G{k:03d}" for k in range(50)],
                       "continent": "Africa", "lat": 0.0, "lon": 0.0})
    df, _ = joined(st, pd.concat(gr, ignore_index=True),
                   pd.concat(sr, ignore_index=True))
    r = score(df).query("continent == 'ALL'").iloc[0]
    if not (r.q > 0.25 and r.q_matched < 0.10):
        die(f"a satellite that knows the right days but rains too often "
            f"scored {r.q:.3f} raw and {r.q_matched:.3f} matched; matching "
            "should have removed nearly all of it")
    g2, s2 = _synth(rng, n_gauge=50, n_day=600, q=0.25)
    st2 = pd.DataFrame({"id": sorted(set(g2.id)), "continent": "Africa",
                        "lat": 0.0, "lon": 0.0})
    df2, _ = joined(st2, g2.rename(columns={"gauge_mm": "mm"}),
                    s2.rename(columns={"sat_mm": "mm"}))
    r2 = score(df2).query("continent == 'ALL'").iloc[0]
    if r2.q_matched < 0.20:
        die(f"matching cut a genuine 0.25 disagreement to {r2.q_matched:.3f}; "
            "it is supposed to remove the definition mismatch only")
    say(f"re-cutting at the gauge's rain frequency removes a pure definition "
        f"mismatch ({r.q:.3f} to {r.q_matched:.3f}) and leaves a genuine "
        f"disagreement standing ({r2.q:.3f} to {r2.q_matched:.3f}), so the "
        "matched column cannot manufacture agreement")

    print("\nall checks passed")


# ---------------------------------------------------------------------------
# 6. Report
# ---------------------------------------------------------------------------
def report(sample: pd.DataFrame, df: pd.DataFrame, scan: pd.DataFrame,
           sc: pd.DataFrame, days: pd.DatetimeIndex) -> None:
    print("\n" + "=" * 78)
    print("SATELLITE AS TRUTH  --  Task 12, the second half of G1")
    print("=" * 78)

    # -- 1 -----------------------------------------------------------------
    print("\n1. Why this test is possible where the study itself is not\n")
    n = sample.groupby("continent").size()
    have = df[df["product"] == "chirp"].groupby("continent").id.nunique()
    print(f"  {'continent':<30}{'gauges':>8}{'reporting':>11}")
    for c in n.index:
        print(f"  {c:<30}{n[c]:>8}{int(have.get(c, 0)):>11}")
    print(f"\n  E27 found 1 African and 0 South American cities with a gauge "
          f"close enough\n  to score them. That is still true. But {n.get('Africa', 0)}"
          f" African and "
          f"{n.get('South America', 0)} South American\n  gauges report "
          f"somewhere, and a satellite does not need a gauge beside a city --\n"
          f"  it needs enough gauges anywhere to establish how far it can be "
          f"trusted.\n  The continents that cannot be scored can still be used "
          "to score the thing\n  that would score them.")
    print(f"\n  {len(days)} days in {N_BLOCKS} blocks of {BLOCK_LEN}, spread "
          f"over {WINDOW_START} to {WINDOW_END}.\n  Blocks rather than "
          "scattered days because section 2 needs neighbours.")

    # -- 2 -----------------------------------------------------------------
    print("\n2. Do the two calendars agree before anything else is measured?\n")
    w = scan.pivot_table(index=["product", "continent"], columns="lag",
                         values="q")
    ch = scan[scan.chosen].set_index(["product", "continent"]).lag
    print(f"  {'product':<9}{'continent':<30}" +
          "".join(f"{f'q at {l:+d}':>11}" for l in w.columns) + f"{'taken':>8}")
    for key, r in w.iterrows():
        print(f"  {key[0]:<9}{key[1]:<30}" +
              "".join(f"{r[l]:>11.3f}" for l in w.columns) +
              f"{ch.loc[key]:>+8d}")
    print("\n  E24 priced a one-day error at Brier 0.167 against 0.238, so a "
          "disagreement\n  reported without this scan would partly be a "
          "disagreement about which day.\n  The choice is made per continent "
          "because the gauge day convention is\n  geography, and not per "
          "station because the best of three lags chosen 649\n  times would "
          "fit the noise.")

    # -- 3 -----------------------------------------------------------------
    print("\n3. How often does the satellite disagree with the bucket?\n")
    print(f"  {'product':<9}{'continent':<30}{'q':>8}{'95% CI':>17}"
          f"{'matched':>9}{'95% CI':>17}{'gauges':>8}{'n':>9}")
    for _, r in sc.iterrows():
        print(f"  {r['product']:<9}{r.continent:<30}{r.q:>8.3f}"
              f"{f'{r.q_lo:.3f}-{r.q_hi:.3f}':>17}{r.q_matched:>9.3f}"
              f"{f'{r.q_matched_lo:.3f}-{r.q_matched_hi:.3f}':>17}"
              f"{int(r.n_gauges):>8}{int(r.n):>9}")
    print(f"\n  The floor to beat is E20's: two buckets in the same field "
          f"disagree {GAUGE_FLOOR_0KM:.3f}\n  of the time, and at the "
          f"satellite's own 5 km footprint {GAUGE_FLOOR_5KM:.3f}. That is what "
          "truth\n  costs when truth is a gauge.")
    print("\n  The matched column is the fairer of the two and is the one the "
          "verdict\n  uses. A grid cell is a 5 km area mean and a gauge is a "
          "point, so at the\n  study's 0.2 mm the cell is wet whenever "
          "anything fell anywhere inside it;\n  that is a difference of "
          "definition rather than of skill. Re-cutting each\n  product at the "
          "threshold that makes it rain as often as the gauge removes\n  it, "
          "and the disagreement that remains is the satellite's own.")

    # -- 4 -----------------------------------------------------------------
    print("\n4. How much of the agreement is the product recognising its own "
          "input?\n")
    a = sc[sc["product"] == "chirps"].set_index("continent")
    b = sc[sc["product"] == "chirp"].set_index("continent")
    both = [c for c in b.index if c in a.index]
    print(f"  {'':<30}{'at 0.2 mm':>21}{'base-rate matched':>27}")
    print(f"  {'continent':<30}{'blended':>9}{'free':>7}{'gap':>7}"
          f"{'blended':>10}{'free':>7}{'gap':>7}")
    for c in both:
        print(f"  {c:<30}{a.loc[c, 'q']:>9.3f}{b.loc[c, 'q']:>7.3f}"
              f"{b.loc[c, 'q'] - a.loc[c, 'q']:>+7.3f}"
              f"{a.loc[c, 'q_matched']:>10.3f}{b.loc[c, 'q_matched']:>7.3f}"
              f"{b.loc[c, 'q_matched'] - a.loc[c, 'q_matched']:>+7.3f}")
    raw = float(b.loc["ALL", "q"] - a.loc["ALL", "q"])
    gap = float(b.loc["ALL", "q_matched"] - a.loc["ALL", "q_matched"])
    print(f"\n  CHIRPS blends station reports into its estimate and CHIRP is "
          "the same\n  pipeline with that step removed, so the difference "
          "between the two columns\n  is what the gauges bought. At the "
          f"study's threshold the blended product\n  looks {abs(raw):.3f} "
          "better, and the obvious reading is that it is better for\n  having "
          "been told the answer. That reading does not survive the control.")
    print(f"\n  Matched, the ordering reverses: the station-free product is "
          f"{abs(gap):.3f}\n  ahead, and ahead on every continent including "
          "the two this stage exists\n  for. So the blending does not buy "
          "agreement about which days it rained.\n  It buys a rain frequency "
          "nearer the gauge's, and at a fixed threshold\n  that is worth more "
          "than skill is. The advantage is calibration, and it\n  evaporates "
          "the moment both products are asked to rain as often as the\n  "
          "bucket does.")
    print(f"\n  It is worth naming what that costs the blended product. It "
          "cannot reach\n  the gauge's rain frequency at any threshold --  "
          f"{a.loc['ALL', 'sat_base_matched']:.3f} against "
          f"{a.loc['ALL', 'gauge_base']:.3f} -- so its\n  matched column is "
          "not a like-for-like re-cut but the closest it can get,\n  which is "
          "why matching makes it slightly worse rather than better. A\n  "
          "product that cannot be asked to rain as often as the thing it is\n"
          "  standing in for is not a drop-in replacement for it.")

    # -- 5 -----------------------------------------------------------------
    print("\n5. Verdict\n")
    row = b.loc["ALL"]
    print(f"  A forecast that predicted the gauge perfectly would score Brier "
          f"{row.q_matched:.3f}\n  against the station-free satellite, because "
          "that is how often the two\n  disagree once the definitions are "
          "matched. The vendor's actual Brier at\n  lead 1 is 0.167 and the "
          f"gauge floor is {GAUGE_FLOOR_0KM:.3f}. So this truth source does "
          "not\n  merely add noise to the comparison -- its floor sits above "
          "the entire\n  score being compared, and the differences the study "
          "needs to see are\n  0.0068 and 0.0081.")
    br = float(row.sat_base - row.gauge_base)
    ar = a.loc["ALL"]
    abr = float(ar.sat_base - ar.gauge_base)
    print(f"\n  The two products fail in opposite directions, which is why "
          "neither is a\n  safe substitute. At the study's threshold the "
          f"station-free product calls\n  rain on {row.sat_base:.3f} of days "
          f"against the gauge's {row.gauge_base:.3f} ({br:+.3f}), inventing "
          f"rain on\n  {row.false_rate:.0%} of dry days; the blended one runs "
          f"the other way at {abr:+.3f} and cannot\n  reach the gauge's rain "
          f"frequency at any threshold, topping out at "
          f"{ar.sat_base_matched:.3f}.\n  E13's finding is that the served "
          "forecast is systematically drier than the\n  ensemble supports. "
          "Scored against the blended product that finding would\n  have come "
          "out smaller, and the study would have reported a forecast\n  "
          "flattered by its own referee.")
    scarce = [c for c in SCARCE if c in b.index]
    if scarce:
        worst = max(scarce, key=lambda c: b.loc[c, "q_matched"])
        print(f"\n  And it is worst where it is needed. {worst} is at "
              f"{b.loc[worst, 'q_matched']:.3f} against "
              f"{b.loc['North America', 'q_matched']:.3f}\n  in North America, "
              "so the product degrades along the same axis as the\n  gauge "
              "network it was supposed to replace -- it is most trustworthy "
              "where\n  there are already gauges to check it against.")
    print("\n  So Task 12 answers G1's second half in the negative. Africa and "
          "South\n  America are not reachable by gauge (E30) and not reachable "
          "by this\n  satellite either. The scope caveat is not a gap waiting "
          "to be filled by\n  more collection; it is a property of what can be "
          "verified today.")


# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        run_checks()
        return
    run_checks()
    print("\nbuilding")
    sample = station_sample()
    print(f"  {len(sample)} gauges")
    gauge = gauge_daily(sample)
    print(f"  {len(gauge):,} gauge-days")
    days = sampled_days()
    sat = satellite_daily(sample, days)
    df, scan = joined(sample, gauge, sat)
    sc = score(df)
    report(sample, df, scan, sc, days)

    df.drop(columns=["key"], errors="ignore").to_parquet(DAILY, index=False)
    sc.to_parquet(SCORES, index=False)
    print(f"\nwrote {STATIONS.name}, {DAILY.name}, {SCORES.name}")


if __name__ == "__main__":
    main()
