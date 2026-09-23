"""Task 10b: a current truth source for countries GHCN-Daily cannot reach.

ISD, the first attempt (`isd_truth`), is sound but frozen: NOAA's public ISD
files stop on 2025-08-24, 13 days short of the ceiling a 500-pair city needs.
Three replacements were examined on 2026-09-22 before this one was chosen:

  OGIMET    raw SYNOP, current - but its robots.txt disallows all automated
            clients, so a bulk fetch would be using the service against its
            operator's stated wishes. Not used.
  Meteostat current and CC BY 4.0 - but for Tehran and Dubai *every* hourly
            precipitation value in 2025 and 2026 carries source `dwd_mosmix`,
            a forecast model. Verifying forecasts against a forecast would
            measure agreement between models and report it as skill. Not used,
            and worth knowing: its daily files show no sign of this.
  GHCNh     NOAA's Global Historical Climatology Network - hourly, the named
            successor to ISD. Observations only (every value carries its report
            type: FM-12 SYNOP, FM-15 METAR ...), public domain, and its SYNOP
            accumulation groups run to early March 2026 - about 680 days of the
            study window, clear of the full 500-pair gate.

So GHCNh cities meet exactly the rule GHCN cities meet; they are not
provisional (see `truth_sources`).

Everything after the download is `isd_truth`'s machinery, reused rather than
copied: the same non-overlapping reconstruction of a day from accumulation
records of mixed length, the same station-chosen day boundary (to the minute,
for half-hour time zones), the same "missing is not dry" gap rule, the same
sampled daily maximum. What differs is the reading of the file:

  * Precipitation comes only from the `precipitation_{N}_hour` columns, which
    GHCNh documents as the SYNOP accumulation groups (N = 3..24). The plain
    `precipitation` column is a mixed "nominally hourly" total that may fold
    in intermediate reports, so it is not used.
  * A value is dropped if its measurement code says the amount is not for its
    stated period (accumulated, deleted, missing, estimated, incomplete) or
    its quality code says suspect or erroneous. Trace is kept as a real zero.
  * Temperature keeps values whose quality code is a legacy pass code or
    empty; GHCNh's letter codes are the failed checks of its own QC suite.

Usage:
    python src/ghcnh_truth.py          # screen, fetch, reconstruct, write
    python src/ghcnh_truth.py check    # correctness checks only
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree

import truth_sources
from config import CACHE, OBS_END, RAW
from constants import POP_ARCHIVE_START
from isd_coverage import _xyz
from isd_truth import choose_end_hour, daily_tmax, daily_totals

BASE = "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly"
STATION_LIST_URL = f"{BASE}/doc/ghcnh-station-list.txt"
INVENTORY_URL = f"{BASE}/doc/ghcnh-inventory.txt"
YEAR_URL = BASE + "/access/by-year/{y}/parquet/GHCNh_{s}_{y}.parquet"

STATION_LIST = CACHE / "ghcnh_station_list.txt"
INVENTORY = CACHE / "ghcnh_inventory.txt"
YEAR_CACHE = CACHE / "ghcnh"

PREFIX = truth_sources.GHCNH_PREFIX
CANDIDATES = RAW / "ghcnh_candidates.json"
DAILY = RAW / "ghcnh_daily.parquet"

YEARS = (2024, 2025, 2026)

# 2025 is complete in GHCNh (it is not in ISD) and inside the window, so the
# screen year and the heart of the study window coincide.
SCREEN_YEAR = 2025
SCREEN_MIN_COVERAGE = 0.60

# Cities probed per country, and nearest stations tried per city. GHCNh keeps
# a site's ICAO-keyed and WMO-keyed records as separate stations, and only one
# of the pair may carry the SYNOP groups, so the nearest station alone is not
# enough: two is the smallest number that survives the split.
PROBE_PER_COUNTRY = 4
STATIONS_PER_CITY = 2

# "Active" from the inventory: observations in at least this many of the
# window's months, at a SYNOP-or-better rate (2 a day).
ACTIVE_FROM, ACTIVE_TO = "2024-05", "2026-02"
ACTIVE_MIN_MONTHS = 20
ACTIVE_MIN_OBS = 60

SYNOP_PERIODS = (3, 6, 9, 12, 15, 18, 21, 24)
# Measurement codes: 1 impossible/inaccurate, 3/4 begin/end accumulated,
# 5/6 deleted, 7/8 missing, E estimated from a neighbour, I/J incomplete.
BAD_MEASUREMENT = {"1", "3", "4", "5", "6", "7", "8", "E", "I", "J"}
# Legacy pass codes (GHCNh documentation, Table 3). Anything else - suspect,
# erroneous, or a letter from GHCNh's own QC - is dropped.
GOOD_QUALITY = {"0", "1", "4", "5", "9"}

WORKERS = 6


def die(msg: str) -> None:
    print(f"\nFAILED: {msg}\n")
    sys.exit(1)


def say(msg: str) -> None:
    print(f"  ok  {msg}")


# ---------------------------------------------------------------------------
# 1. Downloads (cached; a 404 is cached too, as an empty marker)
# ---------------------------------------------------------------------------
def _get(url: str, path, timeout: int = 300) -> bool:
    if path.exists():
        return path.stat().st_size > 0
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 404:
                path.write_bytes(b"")
                return False
            r.raise_for_status()
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(r.content)
            tmp.replace(path)
            return True
        except requests.RequestException as e:
            if attempt == 3:
                print(f"  {url.rsplit('/', 1)[-1]}: {e}")
                return False
            time.sleep(5 * (attempt + 1))
    return False


def fetch_year(station: str, year: int) -> pd.DataFrame | None:
    path = YEAR_CACHE / f"{station}_{year}.parquet"
    if not _get(YEAR_URL.format(s=station, y=year), path):
        return None
    return pd.read_parquet(path)


# ---------------------------------------------------------------------------
# 2. Parsing
# ---------------------------------------------------------------------------
def _ok_quality(q: pd.Series) -> pd.Series:
    return q.isna() | q.astype(str).str.strip().isin(GOOD_QUALITY)


def _when(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["DATE"], errors="coerce", utc=True)


def parse_precip(df: pd.DataFrame) -> pd.DataFrame:
    """SYNOP accumulation records as (end, hours, mm), the shape
    `isd_truth.daily_totals` reconstructs days from."""
    cols = ["end", "hours", "mm"]
    if df is None or "DATE" not in df.columns:
        return pd.DataFrame(columns=cols)
    when = _when(df)
    out = []
    for n in SYNOP_PERIODS:
        c = f"precipitation_{n}_hour"
        if c not in df.columns:
            continue
        v = pd.to_numeric(df[c], errors="coerce")
        mc = df.get(f"{c}_Measurement_Code")
        bad_m = (mc.astype(str).str.strip().isin(BAD_MEASUREMENT)
                 if mc is not None else pd.Series(False, index=df.index))
        q = df.get(f"{c}_Quality_Code")
        ok_q = (_ok_quality(q) if q is not None
                else pd.Series(True, index=df.index))
        keep = v.notna() & (v >= 0) & ~bad_m & ok_q & when.notna()
        if keep.any():
            out.append(pd.DataFrame({"end": when[keep], "hours": float(n),
                                     "mm": v[keep].astype(float)}))
    if not out:
        return pd.DataFrame(columns=cols)
    rec = pd.concat(out, ignore_index=True)
    return rec.drop_duplicates().sort_values("end").reset_index(drop=True)


def parse_temp(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or "temperature" not in df.columns or "DATE" not in df.columns:
        return pd.DataFrame(columns=["when", "t"])
    when = _when(df)
    t = pd.to_numeric(df["temperature"], errors="coerce")
    q = df.get("temperature_Quality_Code")
    ok = t.notna() & when.notna() & (t.abs() < 70)
    if q is not None:
        ok &= _ok_quality(q)
    return (pd.DataFrame({"when": when[ok], "t": t[ok].astype(float)})
            .drop_duplicates("when").reset_index(drop=True))


def reconstruct_station(frames: list[pd.DataFrame], timezone: str
                        ) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """(daily precip, daily tmax, window end minute) for one station."""
    raw = pd.concat([f for f in frames if f is not None and len(f)],
                    ignore_index=True) if frames else pd.DataFrame()
    rec = parse_precip(raw)
    end, _ = choose_end_hour(rec, timezone)
    p = daily_totals(rec, timezone, end)
    t = daily_tmax(parse_temp(raw), timezone)
    p["obs_date"] = pd.to_datetime(p.obs_date)
    t["obs_date"] = pd.to_datetime(t.obs_date)
    return p, t, end


# ---------------------------------------------------------------------------
# 3. Which stations, which cities
# ---------------------------------------------------------------------------
def station_table() -> pd.DataFrame:
    """The fixed-width station list (GHCNh documentation, section II)."""
    _get(STATION_LIST_URL, STATION_LIST)
    rows = []
    for line in STATION_LIST.read_text(errors="replace").splitlines():
        if len(line) < 37:
            continue
        try:
            lat, lon = float(line[12:20]), float(line[21:30])
            elev = float(line[31:37])
        except ValueError:
            continue
        rows.append((line[0:11].strip(), lat, lon,
                     np.nan if elev <= -999 else elev, line[41:71].strip(),
                     line[80:85].strip()))
    return pd.DataFrame(rows, columns=["id", "lat", "lon", "elev_m", "name",
                                       "wmo"])


def active_ids() -> set[str]:
    _get(INVENTORY_URL, INVENTORY, timeout=600)
    inv = pd.read_csv(INVENTORY, sep=r"\s+", dtype={"GHCNh_ID": str})
    inv = inv[inv.YEAR.between(int(ACTIVE_FROM[:4]), int(ACTIVE_TO[:4]))]
    months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
              "OCT", "NOV", "DEC"]
    long = inv.melt(id_vars=["GHCNh_ID", "YEAR"], value_vars=months,
                    var_name="m", value_name="n")
    long["ym"] = (long.YEAR.astype(str) + "-"
                  + (long.m.map({m: i + 1 for i, m in enumerate(months)})
                     .astype(str).str.zfill(2)))
    long = long[(long.ym >= ACTIVE_FROM) & (long.ym <= ACTIVE_TO)
                & (long.n >= ACTIVE_MIN_OBS)]
    good = long.groupby("GHCNh_ID").size()
    return set(good[good >= ACTIVE_MIN_MONTHS].index)


def candidates(stations: pd.DataFrame) -> pd.DataFrame:
    """Up to STATIONS_PER_CITY usable stations for the largest cities of
    every country the GHCN-Daily pool cannot reach at the floor."""
    import probe_cities
    from probe_capitals import MAX_ELEV_DIFF_M, MAX_STATION_KM

    cities = probe_cities.load_geonames()
    cities = cities[cities.population >= probe_cities.MIN_POPULATION]
    pool = probe_cities.candidate_pool()
    # Only GHCN-Daily rows decide which countries are "unreached": the pool
    # also carries GHCNh and ISD rows written by earlier runs of these stages.
    pool = pool[pool.truth == "GHCN"]
    ghcn_countries = set(pool[pool.population >= probe_cities.MIN_POPULATION]
                         .country)
    todo = cities[~cities.country.isin(ghcn_countries)].reset_index(drop=True)
    todo = (todo.sort_values("population", ascending=False)
                .groupby("country", group_keys=False).head(PROBE_PER_COUNTRY)
                .reset_index(drop=True))

    st = stations.reset_index(drop=True)
    tree = cKDTree(_xyz(st.lat, st.lon))
    k = min(8, len(st))
    d, idx = tree.query(_xyz(todo.lat, todo.lon), k=k)
    rows = []
    for i, r in todo.iterrows():
        took = 0
        for km, j in zip(np.atleast_1d(d[i]), np.atleast_1d(idx[i])):
            if km > MAX_STATION_KM or j >= len(st) or took >= STATIONS_PER_CITY:
                break
            s = st.iloc[j]
            if (pd.notna(s.elev_m) and pd.notna(r.dem)
                    and abs(s.elev_m - r.dem) > MAX_ELEV_DIFF_M):
                continue
            rows.append({**r.to_dict(), "station": s.id, "km": float(km),
                         "elev_m": float(s.elev_m) if pd.notna(s.elev_m)
                         else np.nan})
            took += 1
    return pd.DataFrame(rows)


def screen(cands: pd.DataFrame) -> pd.DataFrame:
    """Days reconstructable in SCREEN_YEAR, per station."""
    ids = sorted(set(cands.station))
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(lambda s: fetch_year(s, SCREEN_YEAR), ids))
    days = 366 if SCREEN_YEAR % 4 == 0 else 365
    tz = cands.drop_duplicates("station").set_index("station").timezone
    cov = {}
    for s in ids:
        df = fetch_year(s, SCREEN_YEAR)
        if df is None:
            cov[s] = 0.0
            continue
        _, n = choose_end_hour(parse_precip(df), tz[s])
        cov[s] = n / days
    out = cands.copy()
    out["screen_coverage"] = out.station.map(cov)
    return out


# ---------------------------------------------------------------------------
# 4. What the pipeline reads
# ---------------------------------------------------------------------------
def is_ghcnh(station_id: str | None) -> bool:
    return truth_sources.source_of(station_id) == "GHCNh"


def _store() -> pd.DataFrame | None:
    return pd.read_parquet(DAILY) if DAILY.exists() else None


def load_prcp(station_id: str) -> pd.DataFrame:
    """Same shape as `capitals.load_ghcn`: ghcn_date, prcp."""
    s = _store()
    if s is None:
        return pd.DataFrame()
    g = s[(s.station == station_id) & s.prcp.notna()]
    return pd.DataFrame({"ghcn_date": pd.to_datetime(g.obs_date),
                         "prcp": g.prcp.values}).reset_index(drop=True)


def load_temp(station_id: str) -> pd.DataFrame:
    """Same shape as `capitals.load_ghcn_temp`."""
    s = _store()
    if s is None:
        return pd.DataFrame()
    g = s[(s.station == station_id) & s.tmax.notna()]
    out = pd.DataFrame({"local_date": pd.to_datetime(g.obs_date).dt.date,
                        "obs_tmax": g.tmax.values, "obs_tmin": g.tmin.values})
    out["qc_fail"] = out.obs_tmin > out.obs_tmax
    return out.reset_index(drop=True)


def pool_rows() -> pd.DataFrame:
    if not CANDIDATES.exists():
        return pd.DataFrame()
    return pd.DataFrame(json.loads(CANDIDATES.read_text())["cities"])


# ---------------------------------------------------------------------------
# 5. Correctness checks
# ---------------------------------------------------------------------------
def run_checks() -> None:
    print("=== GHCNh correctness checks ===")

    # 1. Column -> period, and the codes that disqualify an amount.
    df = pd.DataFrame({
        "DATE": ["2025-03-01T00:00:00", "2025-03-01T06:00:00",
                 "2025-03-01T12:00:00", "2025-03-01T18:00:00"],
        "precipitation_6_hour": [1.0, 2.0, 3.0, 0.0],
        "precipitation_6_hour_Measurement_Code": [np.nan, "4", np.nan, "T"],
        "precipitation_6_hour_Quality_Code": ["1", "1", "2", "1"],
        "precipitation_24_hour": [5.0, np.nan, np.nan, np.nan],
        "precipitation_24_hour_Measurement_Code": [np.nan] * 4,
        "precipitation_24_hour_Quality_Code": ["1", None, None, None],
        "precipitation": [9.0, 9.0, 9.0, 9.0],
    })
    got = parse_precip(df)
    want = {(6.0, 1.0), (6.0, 0.0), (24.0, 5.0)}
    if set(zip(got.hours, got.mm)) != want:
        die(f"precip parse kept the wrong records: {got}")
    say("6 h and 24 h groups keep their periods; an end-of-accumulation (4) "
        "and a suspect (2) amount are dropped, trace is kept as zero, and the "
        "mixed 'nominally hourly' column is never read")

    # 2. Temperature: GHCNh's own QC letters and suspect codes drop a value.
    t = parse_temp(pd.DataFrame({
        "DATE": ["2025-07-01T10:00:00", "2025-07-01T11:00:00",
                 "2025-07-01T12:00:00", "2025-07-01T13:00:00"],
        "temperature": [30.0, 55.0, 31.0, 32.0],
        "temperature_Quality_Code": ["1", "s", "2", np.nan]}))
    if sorted(t.t.tolist()) != [30.0, 32.0]:
        die(f"temperature QC not applied: {t}")
    say("a spike-flagged (s) or suspect (2) temperature never becomes a daily "
        "maximum; unflagged values are kept")

    # 3. The Dubai case: 6-hourly SYNOP at 00/06/12/18 UTC in UTC+4 tiles a
    #    04:00-to-04:00 local day, four records to a day.
    rows = []
    for day in pd.date_range("2025-01-01", periods=30, freq="D"):
        for h in (0, 6, 12, 18):
            rows.append({"DATE": (day + pd.Timedelta(hours=h)).isoformat(),
                         "precipitation_6_hour": 0.5,
                         "precipitation_6_hour_Quality_Code": "1"})
    p, _, end = reconstruct_station([pd.DataFrame(rows)], "Asia/Dubai")
    if end != 240 or len(p) < 28 or abs(p.prcp.iloc[1] - 2.0) > 1e-9:
        die(f"6-hourly UTC+4 station: end {end}, {len(p)} days, {p.head(3)}")
    say("6-hourly SYNOP in UTC+4 reconstructs 04:00-04:00 local days summing "
        "all four reports - the ISD reconstruction, reused unchanged")

    # 4. The fixed-width station list, read at the documented columns.
    line = list(" " * 90)
    for start, text in ((0, "AEI0000OMDB"), (12, " 25.2550"), (21, "  55.3643"),
                        (31, "  10.4"), (41, "DUBAI INTL"), (80, "41194"),
                        (86, "OMDB")):
        line[start:start + len(text)] = text
    line = "".join(line)
    import tempfile
    from pathlib import Path
    global STATION_LIST
    real = STATION_LIST
    with tempfile.TemporaryDirectory() as tmp:
        STATION_LIST = Path(tmp) / "list.txt"
        STATION_LIST.write_text(line + "\n")
        try:
            st = station_table()
        finally:
            STATION_LIST = real
    r = st.iloc[0]
    if (r.id, r.lat, r.lon, r.elev_m, r.wmo) != ("AEI0000OMDB", 25.255,
                                                 55.3643, 10.4, "41194"):
        die(f"station list misread: {st}")
    say("station list read at its documented fixed columns")

    # 5. Loader shape matches the GHCN loaders.
    fake = pd.DataFrame({"station": [PREFIX + "x"] * 2,
                         "obs_date": pd.to_datetime(["2025-01-01",
                                                     "2025-01-02"]),
                         "prcp": [1.0, np.nan], "tmax": [5.0, 6.0],
                         "tmin": [1.0, 7.0], "gap_h": [0.0, 0.0]})
    global _store
    real_store = _store
    _store = lambda: fake  # noqa: E731
    try:
        pp, tt = load_prcp(PREFIX + "x"), load_temp(PREFIX + "x")
    finally:
        _store = real_store
    if list(pp.columns) != ["ghcn_date", "prcp"] or len(pp) != 1 \
            or tt.qc_fail.tolist() != [False, True]:
        die(f"loader shapes wrong: {pp} {tt}")
    if truth_sources.load_prcp(PREFIX + "missing") is None:
        die("truth_sources does not dispatch GHCNh ids here")
    say("loaders speak the GHCN loaders' shape, and truth_sources routes "
        "GHCNH: ids to them")
    print()


# ---------------------------------------------------------------------------
# Cross-check: the same gauge through both pipes
# ---------------------------------------------------------------------------


def crosscheck() -> pd.DataFrame:
    """Score GHCN-verified study cities a second time, against GHCNh.

    One truth source per country means source and region are confounded by
    design: if GHCNh cities behave differently, nothing inside the main run
    can say whether the forecast differs there or the reconstruction does.
    But many study gauges are synoptic stations that GHCNh also carries under
    the same id. For those, every forecast and every day is held fixed and
    only the pipe the rain amount came through changes - so any difference in
    the verdict is the reconstruction's, not the climate's.

    The day alignment is taken from the data (the shift in -1..+1 that makes
    the two records agree best), because the GHCN-Daily day convention is
    the observer's and the GHCNh one is the reconstruction's.
    """
    from benchmarks import wet_bias_test
    from config import PROCESSED, RAIN_THRESHOLD_MM

    cov = json.loads((RAW / "city_coverage.json").read_text())["included"]
    pop = pd.read_parquet(PROCESSED / "cities_pop.parquet")
    act = active_ids()
    pairs = [(c, v["prcp_station"], v.get("timezone", "UTC"))
             for c, v in cov.items()
             if v["prcp_station"] in act and c in set(pop.city)]
    print(f"crosscheck: {len(pairs)} study cities whose GHCN gauge GHCNh "
          f"also carries")
    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(lambda a: [fetch_year(a[1], y) for y in YEARS], pairs))

    rows = []
    for city, sid, tz in sorted(pairs):
        p, _, _ = reconstruct_station([fetch_year(sid, y) for y in YEARS], tz)
        if p.empty:
            continue
        h = p.set_index("obs_date").prcp.dropna()
        g = pop[pop.city == city].copy()
        g["d"] = pd.to_datetime(g.local_date)
        best = None
        for shift in (-1, 0, 1):
            v = g.d.map(lambda d: h.get(d + pd.Timedelta(days=shift)))
            ok = v.notna()
            if ok.sum() < 200:
                continue
            ev = v[ok] >= RAIN_THRESHOLD_MM
            agree = float((ev == g.observed_event[ok]).mean())
            if best is None or agree > best[0]:
                best = (agree, shift, ok, ev)
        if best is None:
            continue
        agree, shift, ok, ev = best
        a = g[ok].copy()
        b = a.assign(observed_event=ev.values)
        wa = wet_bias_test(a, n_boot=400).set_index("group")
        wb = wet_bias_test(b, n_boot=400).set_index("group")
        lo = [i for i in wa.index if i.startswith("low")][0]
        hi = [i for i in wa.index if i.startswith("high")][0]
        light = a.obs_precip_mm.between(RAIN_THRESHOLD_MM, 1.0)
        rows.append({
            "city": city, "station": sid, "n": int(ok.sum()), "shift": shift,
            "agree": agree,
            "wet_ghcn": float(a.observed_event.mean()),
            "wet_ghcnh": float(ev.mean()),
            # Of the GHCN light-rain days (0.2-1 mm), how many GHCNh also
            # calls wet: the mechanism the low-end gap is most sensitive to.
            "light_kept": float(ev[light.values].mean()) if light.any()
            else np.nan,
            "low_gap_ghcn": float(wa.loc[lo, "gap"]),
            "low_gap_ghcnh": float(wb.loc[lo, "gap"]),
            "high_gap_ghcn": float(wa.loc[hi, "gap"]),
            "high_gap_ghcnh": float(wb.loc[hi, "gap"]),
        })
    out = pd.DataFrame(rows)
    path = PROCESSED / "ghcnh_crosscheck.parquet"
    out.to_parquet(path, index=False)
    if len(out):
        print(out.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
        print(f"\n  median wet-day rate  GHCN {out.wet_ghcn.median():.3f}  "
              f"GHCNh {out.wet_ghcnh.median():.3f}")
        print(f"  light-rain days GHCNh also calls wet: "
              f"{out.light_kept.median():.0%} (median)")
        print(f"  low-end gap > 0      GHCN {(out.low_gap_ghcn > 0).sum()}/"
              f"{len(out)}  GHCNh {(out.low_gap_ghcnh > 0).sum()}/{len(out)}")
        print(f"  high-end gap < 0     GHCN {(out.high_gap_ghcn < 0).sum()}/"
              f"{len(out)}  GHCNh {(out.high_gap_ghcnh < 0).sum()}/{len(out)}")
    print(f"wrote {path}")
    return out


# ---------------------------------------------------------------------------
PIPE_PER_COUNTRY = 6
PIPE_MAX = 400


def crosscheck_pipe() -> pd.DataFrame:
    """The same question without forecasts, so it can be asked widely.

    `crosscheck` needs a study city on the gauge; this needs only a gauge
    both datasets carry. For each, the GHCN-Daily total and the GHCNh
    reconstruction are compared day by day at the best-agreeing shift:
    how often they agree on "wet" at the study threshold, how the wet-day
    rates compare, and - the number the low-end calibration gap is most
    sensitive to - what share of GHCN's light-rain days (threshold to 1 mm)
    GHCNh also calls wet. Countries are sampled evenly (at most
    PIPE_PER_COUNTRY) so the answer is not the United States'.

    Stations whose two records agree on >99.5% of days are reported but
    left out of the summary: GHCNh evidently carries the daily report there,
    so they test nothing about the reconstruction.
    """
    import ghcn_bulk
    from config import PROCESSED, RAIN_THRESHOLD_MM

    st = station_table().set_index("id")
    act = active_ids()
    cen = ghcn_bulk.census()
    dense = set(cen[cen.prcp_days >= 700].station)
    # Pair by id where GHCNh reuses the GHCN id, and by WMO number where the
    # GHCN id encodes one (xxM000NNNNN): that is the same synoptic station
    # under GHCNh's own identifier, and it is where the SYNOP reconstruction
    # - rather than a copied daily report - is actually being tested.
    wmo = (st[st.wmo.notna() & (st.wmo.astype(str).str.len() > 0)]
           .reset_index().assign(w=lambda d: d.wmo.astype(str).str.zfill(5)))
    wmo = wmo[wmo.id.isin(act)].drop_duplicates("w").set_index("w").id
    pairs = {}
    for g in dense:
        if g in act and g in st.index:
            pairs[g] = g
        elif len(g) == 11 and g[2:6] == "M000" and g[6:] in wmo.index:
            pairs[g] = wmo[g[6:]]
    both = sorted(pairs)
    # Seeded shuffle, then an even draw per country: sorted ids would sample
    # the alphabet (every pick from AQ..RQ) rather than the world.
    pick = (pd.DataFrame({"id": both}).sample(frac=1, random_state=11)
              .assign(cc=lambda d: d.id.str[:2])
              .groupby("cc").head(PIPE_PER_COUNTRY).head(PIPE_MAX)
              .sort_values("id"))
    print(f"pipe crosscheck: {len(both)} gauges in both datasets; sampling "
          f"{len(pick)} across {pick.cc.nunique()} country codes")
    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(lambda s: [fetch_year(pairs[s], y) for y in YEARS], pick.id))
    daily = ghcn_bulk.extract(set(pick.id), write=False)

    lo, hi = pd.Timestamp(POP_ARCHIVE_START), pd.Timestamp(OBS_END)
    rows = []
    for sid in pick.id:
        hid = pairs[sid]
        lon = float(st.loc[hid, "lon"])
        off = int(round(lon / 15))
        tz = f"Etc/GMT{-off:+d}" if off else "UTC"   # Etc/ signs are inverted
        p, _, _ = reconstruct_station([fetch_year(hid, y) for y in YEARS], tz)
        if p.empty:
            continue
        h = p.set_index("obs_date").prcp.dropna()
        d = daily[daily.station == sid].set_index("ghcn_date").prcp.dropna()
        d = d[(d.index >= lo) & (d.index <= hi)]
        best = None
        for shift in (-1, 0, 1):
            hh = h.copy(); hh.index = hh.index - pd.Timedelta(days=shift)
            j = pd.concat([d.rename("g"), hh.rename("h")], axis=1,
                          sort=True).dropna()
            if len(j) < 300:
                continue
            ag = float(((j.g >= RAIN_THRESHOLD_MM) == (j.h >= RAIN_THRESHOLD_MM)).mean())
            if best is None or ag > best[0]:
                best = (ag, shift, j)
        if best is None:
            continue
        ag, shift, j = best
        gw, hw = j.g >= RAIN_THRESHOLD_MM, j.h >= RAIN_THRESHOLD_MM
        light = j.g.between(RAIN_THRESHOLD_MM, 1.0)
        rows.append({"station": sid, "ghcnh": hid, "cc": sid[:2], "n": len(j), "shift": shift,
                     "agree": ag, "wet_ghcn": float(gw.mean()),
                     "wet_ghcnh": float(hw.mean()),
                     "light_kept": float(hw[light].mean()) if light.sum() >= 10
                     else np.nan,
                     "heavy_kept": float(hw[j.g >= 5].mean()) if (j.g >= 5).sum() >= 10
                     else np.nan,
                     "false_wet": float(hw[~gw].mean()),
                     "same_data": ag > 0.995})
    out = pd.DataFrame(rows)
    path = PROCESSED / "ghcnh_pipe_crosscheck.parquet"
    out.to_parquet(path, index=False)
    s = out[~out.same_data]
    print(f"  compared {len(out)} gauges; {int(out.same_data.sum())} carry the "
          f"daily report itself and are set aside; {len(s)} test the "
          f"reconstruction across {s.cc.nunique()} country codes")
    if len(s):
        q = lambda c: s[c].quantile([.25, .5, .75]).round(3).tolist()
        print(f"  agreement on wet/dry (IQR)      {q('agree')}")
        print(f"  wet-day rate GHCN / GHCNh       {s.wet_ghcn.median():.3f} / "
              f"{s.wet_ghcnh.median():.3f}")
        print(f"  light-rain days kept (IQR)      {q('light_kept')}")
        print(f"  5 mm+ days kept (IQR)           {q('heavy_kept')}")
        print(f"  dry days GHCNh calls wet (IQR)  {q('false_wet')}")
    print(f"wrote {path}")
    return out


# ---------------------------------------------------------------------------
def main() -> None:
    run_checks()
    if "check" in sys.argv[1:]:
        return
    if "crosscheck-pipe" in sys.argv[1:]:
        crosscheck_pipe()
        return
    if "crosscheck" in sys.argv[1:]:
        crosscheck()
        return

    from probe_capitals import MIN_PAIRS

    st = station_table()
    act = active_ids()
    st = st[st.id.isin(act)]
    print(f"GHCNh: {len(st):,} stations active {ACTIVE_FROM}..{ACTIVE_TO}")

    cands = candidates(st)
    if cands.empty:
        die("no candidate stations")
    print(f"candidates: {cands.station.nunique()} stations for "
          f"{len(cands.drop_duplicates(['city', 'country']))} cities in "
          f"{cands.country.nunique()} countries GHCN-Daily cannot reach")

    scr = screen(cands)
    scr = scr[scr.screen_coverage >= SCREEN_MIN_COVERAGE]
    # The best-covered station per city, then one city per station.
    best = (scr.sort_values(["screen_coverage", "km"], ascending=[False, True])
               .drop_duplicates(["city", "country"])
               .sort_values("population", ascending=False)
               .drop_duplicates("station"))
    print(f"screen ({SCREEN_YEAR}, >= {SCREEN_MIN_COVERAGE:.0%} of days "
          f"reconstructable): {len(best)} cities in "
          f"{best.country.nunique()} countries")

    jobs = [(s, y) for s in best.station for y in YEARS]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(lambda a: fetch_year(*a), jobs))

    lo, hi = pd.Timestamp(POP_ARCHIVE_START), pd.Timestamp(OBS_END)
    daily, meta = [], []
    for r in best.itertuples(index=False):
        p, t, end = reconstruct_station(
            [fetch_year(r.station, y) for y in YEARS], r.timezone)
        d = p.merge(t, on="obs_date", how="outer")
        d["station"] = PREFIX + r.station
        daily.append(d)
        w = d[(d.obs_date >= lo) & (d.obs_date <= hi)]
        meta.append({"station": r.station, "end_min": int(end),
                     "prcp_days": int(p.prcp.notna().sum()),
                     "tmax_days": int(len(t)),
                     "pair_days": int(w.prcp.notna().sum()),
                     "tmax_pair_days": int(w.tmax.notna().sum()),
                     "last_prcp": str(p.obs_date.max().date()) if len(p)
                     else ""})
    meta = pd.DataFrame(meta)
    good = meta[(meta.pair_days >= MIN_PAIRS)
                & (meta.tmax_pair_days >= MIN_PAIRS)]
    keep = best.merge(good, on="station")
    print(f"pairable: {len(keep)} cities in {keep.country.nunique()} countries "
          f"with {MIN_PAIRS}+ in-window days of both (the full gate)")
    short = best.merge(meta, on="station").query("station not in @keep.station")
    if len(short):
        # Both sides are shown: most cities short of the gate have plenty of
        # rain days and fail on the daily maximum, which a bare rain count
        # would make look like a bug in the gate.
        print("  short of the gate (rain days / daily-max days): " + ", ".join(
            f"{r.city} {r.pair_days}/{r.tmax_pair_days}" for r in
            short.sort_values("pair_days", ascending=False).head(8)
            .itertuples()))

    cols = ["station", "obs_date", "prcp", "tmax", "tmin", "gap_h"]
    out = pd.concat(daily, ignore_index=True)[cols]
    out = out[out.station.isin(PREFIX + keep.station)]
    out.sort_values(["station", "obs_date"]).reset_index(drop=True) \
       .to_parquet(DAILY, index=False)

    rows = [{"city": r.city, "country": r.country, "lat": float(r.lat),
             "lon": float(r.lon), "population": int(r.population),
             "dem": float(r.dem), "timezone": r.timezone,
             "prcp_station": PREFIX + r.station, "prcp_km": float(r.km),
             "prcp_elev_m": float(r.elev_m), "prcp_days": int(r.prcp_days),
             "tmax_station": PREFIX + r.station, "tmax_km": float(r.km),
             "tmax_elev_m": float(r.elev_m), "tmax_days": int(r.tmax_days),
             "end_min": int(r.end_min), "pair_days": int(r.pair_days),
             "last_prcp": r.last_prcp, "provisional": False,
             "screen_coverage": round(float(r.screen_coverage), 3)}
            for r in keep.itertuples(index=False)]
    CANDIDATES.write_text(json.dumps({
        "source": "NOAA GHCNh (public domain)", "base_url": BASE,
        "years": list(YEARS), "screen_year": SCREEN_YEAR,
        "screen_min_coverage": SCREEN_MIN_COVERAGE, "min_pairs": MIN_PAIRS,
        "provisional": False,
        "cities": sorted(rows, key=lambda r: (r["country"], -r["population"])),
    }, indent=2, sort_keys=True))
    print(f"\nwrote {CANDIDATES.name} ({len(rows)} cities) and {DAILY.name}")
    print("  by country: " + ", ".join(
        f"{k}:{v}" for k, v in keep.country.value_counts().items()))


if __name__ == "__main__":
    main()
