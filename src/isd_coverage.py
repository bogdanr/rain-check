"""Task 11: can NOAA ISD supply a truth source where GHCN-Daily cannot?

E27 found that the 2,000-city target is unreachable from GHCN-Daily: the
qualifying pool holds one African city and no South American ones. That
re-specified G1 from "collect more cities" to "find a truth source outside
Europe and North America", and ISD is the first candidate because it is
already in this tree - `observations_hourly.py` reads it for Bucharest.

But that module carries a warning in its own docstring. Bucharest's stations
report almost no hourly precipitation amounts, which is why it fell back to
present-weather codes. Whether that is a Romanian quirk or a property of ISD
decides whether Phase 3 exists, and nobody has checked.

So this stage asks two questions in order, and the order matters:

  1. Does a station EXIST near the cities GHCN cannot reach? Cheap, answered
     from the station history file alone.
  2. Does that station report PRECIPITATION? Expensive, answered by reading
     the actual records - and this is the question, because a station that
     reports temperature and wind is worth nothing to a rainfall study.

Answering only the first would be the mistake this stage exists to avoid. It
is the answer that looks good.

Reading the ISD precipitation field (AA1-AA4):

    "01,0008,9,5"  ->  period 01 h, depth 0.8 mm, condition 9, quality 5
    "99,9999,9,9"  ->  period MISSING, depth MISSING

The depth is in tenths of a millimetre and 9999 is the missing sentinel, so a
parser that forgets to check it reports 999.9 mm of rain. The period is what
makes ISD awkward: it is not fixed. One station files hourly, another files
6-hourly, another files a single 24-hour total, and many files mix all three
within the same year. A daily total therefore has to be RECONSTRUCTED, and
whether it can be is the thing being measured here.

Usage:
    python src/isd_coverage.py          # full run, writes tables
    python src/isd_coverage.py check    # correctness checks only
    python src/isd_coverage.py census   # station existence only, no downloads
"""

from __future__ import annotations

import io
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from config import CACHE, PROCESSED

HISTORY_URL = "https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv"
HOURLY_BASE = "https://www.ncei.noaa.gov/data/global-hourly/access"
HISTORY_CACHE = CACHE / "isd_history.csv"
HOURLY_CACHE = CACHE / "isd_hourly"

CENSUS_TABLE = PROCESSED / "isd_city_census.parquet"
STATION_TABLE = PROCESSED / "isd_station_probe.parquet"

# The probe year. A single complete calendar year is enough to measure whether
# a station reports precipitation at all, and 175 station-years is already
# several hundred megabytes.
# The probe year. It must be a COMPLETE calendar year: the first attempt used
# 2025, whose archive stops in late August, so a station reporting perfectly
# scored 240/365 = 66% and the "share of stations above 80%" column read zero
# everywhere - an artefact of the denominator that looked exactly like the
# finding. 2024 is complete and inside the study window; `main` refuses to
# run if the history horizon says otherwise.
PROBE_YEAR = 2024

MIN_POP = 100_000
MAX_STATION_KM = 25.0
PER_CONTINENT = 20

# How far behind the history file's own horizon a station's last observation
# may sit and still count as active. Not zero: ingest lag means only 152 of
# 28,095 stations carry the horizon date itself, and reading that as "152
# active stations" would be a statement about NCEI's pipeline, not about the
# network. The knee is at a week and the count is flat either side of this
# value, which `report` prints so the choice can be checked rather than
# trusted.
ACTIVE_GRACE_DAYS = 30

# ISD quality flags. 2, 3, 6 and 7 mean the value failed a check or was
# erroneous; everything else is either passed, not checked, or corrected.
BAD_QUALITY = {"2", "3", "6", "7"}

# A day counts as covered if the accumulation periods reaching into it leave
# no more than this many hours unaccounted for. Not zero: a station filing
# 23 hourly reports and missing one at 03:00 has told us about that day.
MAX_GAP_HOURS = 3.0


def die(msg: str) -> None:
    print(f"\nFAILED: {msg}\n")
    sys.exit(1)


def say(msg: str) -> None:
    print(f"  ok  {msg}")


# ---------------------------------------------------------------------------
# 1. Which stations exist
# ---------------------------------------------------------------------------
def station_table() -> pd.DataFrame:
    """Active ISD stations, from the published history file.

    The history file has its own horizon, which is not today. It is recorded
    and reported rather than silently treated as current, because "active"
    means "active as far as this file knows" and a reader deserves the
    difference.
    """
    if not HISTORY_CACHE.exists():
        HISTORY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(HISTORY_URL,
                                     headers={"User-Agent": "calibration-study"})
        with urllib.request.urlopen(req, timeout=120) as r:
            HISTORY_CACHE.write_bytes(r.read())

    h = pd.read_csv(HISTORY_CACHE, dtype=str)
    h.columns = [c.strip() for c in h.columns]
    h = h.rename(columns={"STATION NAME": "name", "CTRY": "country"})
    h["id"] = h.USAF.str.zfill(6) + h.WBAN.str.zfill(5)
    h["lat"] = pd.to_numeric(h.LAT, errors="coerce")
    h["lon"] = pd.to_numeric(h.LON, errors="coerce")
    h["begin"] = pd.to_datetime(h.BEGIN, format="%Y%m%d", errors="coerce")
    h["end"] = pd.to_datetime(h.END, format="%Y%m%d", errors="coerce")

    h = h.dropna(subset=["lat", "lon", "begin", "end"])
    # (0, 0) is the null island of station metadata, not a station.
    h = h[(h.lat != 0) | (h.lon != 0)]
    return h[["id", "name", "country", "lat", "lon", "begin", "end"]]


def active(stations: pd.DataFrame,
           grace_days: int = ACTIVE_GRACE_DAYS) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Stations reporting across the probe year, and the file's own horizon.

    A station qualifies if it had started by the beginning of the probe year
    and its last observation is within `grace_days` of the horizon. The grace
    is the point: station END dates trail the file's compile date by however
    long that station's data took to arrive, so demanding the horizon exactly
    measures ingest latency instead of activity.
    """
    horizon = stations.end.max()
    lo = pd.Timestamp(f"{PROBE_YEAR}-01-01")
    hi = horizon - timedelta(days=grace_days)
    return stations[(stations.begin <= lo) & (stations.end >= hi)], horizon


def grace_sensitivity(stations: pd.DataFrame) -> pd.DataFrame:
    """How many stations each choice of grace period admits."""
    rows = [{"grace_days": g, "n_stations": int(len(active(stations, g)[0]))}
            for g in (0, 1, 3, 7, 14, 30, 60, 90, 180)]
    return pd.DataFrame(rows)


def _xyz(lat, lon) -> np.ndarray:
    la, lo = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    return np.c_[np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo),
                 np.sin(la)] * 6371.0


def city_census(stations: pd.DataFrame) -> pd.DataFrame:
    """Per continent: how many cities GHCN reaches, and how many ISD reaches.

    The denominator is EVERY GeoNames city at or above the population floor,
    not the study's pool. The pool is already filtered to cities with a GHCN
    gauge, so measuring ISD against it would ask whether ISD covers the places
    GHCN already covers - which is the one question with no bearing on G1.
    """
    import probe_cities
    import sampling

    cities = probe_cities.load_geonames()
    cities["continent"] = [sampling.continent(c, t)
                           for c, t in zip(cities.country, cities.timezone)]
    big = cities[cities.population >= MIN_POP].reset_index(drop=True)

    d, idx = cKDTree(_xyz(stations.lat, stations.lon)).query(
        _xyz(big.lat, big.lon), k=1)
    big["isd_km"] = d
    big["isd_station"] = stations.id.to_numpy()[idx]

    pool = sampling.load_pool()
    have_ghcn = set(zip(pool.city, pool.country))
    big["ghcn"] = [(c, k) in have_ghcn for c, k in zip(big.city, big.country)]

    rows = []
    for cont, g in big.groupby("continent"):
        near = g.isd_km <= MAX_STATION_KM
        rows.append({
            "continent": cont,
            "cities": len(g),
            "ghcn_cities": int(g.ghcn.sum()),
            "ghcn_share": float(g.ghcn.mean()),
            "isd_cities": int(near.sum()),
            "isd_share": float(near.mean()),
            "isd_stations": int(g.loc[near, "isd_station"].nunique()),
            # E28 found a city inside an already-sampled country is worth
            # about a quarter of one, so the country count is the currency
            # a scale-up is actually paid in.
            "countries": int(g.country.nunique()),
            "ghcn_countries": int(g.loc[g.ghcn, "country"].nunique()),
            "isd_countries": int(g.loc[near, "country"].nunique()),
        })
    out = pd.DataFrame(rows).sort_values("cities", ascending=False)
    out["max_station_km"] = MAX_STATION_KM
    out["min_population"] = MIN_POP
    return out, big


def sample_targets(big: pd.DataFrame) -> pd.DataFrame:
    """Stations to actually read, stratified by continent.

    The largest cities are taken first, and stations are deduplicated, because
    two cities sharing a station would be one measurement counted twice - the
    same rule `probe_cities` applies to GHCN. Taking the LARGEST cities is
    deliberately generous to ISD: if the reporting is thin at a capital
    airport it will not be better at a provincial town, so a negative result
    here is a strong one.
    """
    near = big[big.isd_km <= MAX_STATION_KM]
    picks = []
    for cont, g in near.groupby("continent"):
        g = g.sort_values("population", ascending=False)
        g = g.drop_duplicates("isd_station").head(PER_CONTINENT)
        picks.append(g)
    out = pd.concat(picks, ignore_index=True)
    return out[["city", "country", "continent", "population", "timezone",
                "lat", "lon", "isd_station", "isd_km"]]


# ---------------------------------------------------------------------------
# 2. Which stations report rain
# ---------------------------------------------------------------------------
def fetch_year(station_id: str, year: int) -> pd.DataFrame | None:
    HOURLY_CACHE.mkdir(parents=True, exist_ok=True)
    cache = HOURLY_CACHE / f"{station_id}_{year}.csv"
    if cache.exists():
        raw = cache.read_bytes()
        if not raw:
            return None
    else:
        url = f"{HOURLY_BASE}/{year}/{station_id}.csv"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "calibration-study"})
            with urllib.request.urlopen(req, timeout=180) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Cache the absence so a re-run does not ask again.
                cache.write_bytes(b"")
                return None
            raise
        cache.write_bytes(raw)
    return pd.read_csv(io.BytesIO(raw), low_memory=False)


def parse_precip(df: pd.DataFrame) -> pd.DataFrame:
    """Every usable accumulation record: when it ended, how long, how much.

    All of AA1..AA4 are read, not just AA1. A station filing an hourly total
    in AA1 and a 24-hour total in AA2 is common, and taking only the first
    slot would discard whichever the station happened to put second.
    """
    cols = [c for c in ("AA1", "AA2", "AA3", "AA4") if c in df.columns]
    if not cols or "DATE" not in df.columns:
        return pd.DataFrame(columns=["end", "hours", "mm"])

    when = pd.to_datetime(df["DATE"], errors="coerce", utc=True)
    out = []
    for c in cols:
        parts = df[c].astype(str).str.split(",", expand=True)
        if parts.shape[1] < 4:
            continue
        period = pd.to_numeric(parts[0], errors="coerce")
        depth = pd.to_numeric(parts[1], errors="coerce")
        qual = parts[3].astype(str)
        # 99 is the missing period and 9999 the missing depth. Reading either
        # as a number is the failure that turns a dry year into 1,000 mm.
        ok = (period.notna() & depth.notna() & when.notna()
              & (period != 99) & (period > 0) & (depth != 9999)
              & ~qual.isin(BAD_QUALITY))
        if not ok.any():
            continue
        out.append(pd.DataFrame({
            "end": when[ok],
            "hours": period[ok].astype(float),
            "mm": depth[ok].astype(float) / 10.0,
        }))
    if not out:
        return pd.DataFrame(columns=["end", "hours", "mm"])
    return pd.concat(out, ignore_index=True).sort_values("end")


def covered_days(rec: pd.DataFrame, timezone: str) -> dict:
    """How many local days can have a daily total reconstructed from these?

    An accumulation record covers the interval [end - period, end]. A local
    day is covered when those intervals, clipped to it, leave at most
    MAX_GAP_HOURS unaccounted for. That is the honest test: a 24-hour total
    covers a day in one record, twenty-four hourly totals cover it in
    twenty-four, and eleven scattered 6-hour totals cover nothing at all even
    though there are eleven of them.

    Records are NOT assumed to be non-overlapping - a station filing both an
    hourly and a daily total would double-count if they were simply summed -
    so the intervals are merged before the gap is measured.
    """
    empty = {"days_with_record": 0, "days_covered": 0, "days_24h": 0,
             "n_records": 0, "median_period_h": np.nan}
    if rec.empty:
        return empty

    # Wall-clock local time, with the offset applied and then dropped. Keeping
    # it tz-aware is not an option: in Havana and Santiago the clocks jump at
    # midnight, so local midnight does not exist on one day a year and
    # normalising throws. The cost is that the two transition days are treated
    # as 24 hours rather than 23 or 25, which can move one station's coverage
    # by 2 days in 366 and cannot move a verdict drawn at tens of percent.
    local = rec.end.dt.tz_convert(timezone).dt.tz_localize(None)
    start = local - pd.to_timedelta(rec.hours, unit="h")

    # A record can straddle midnight, so it is attributed to every local day
    # it touches rather than only to the day its end falls in.
    frames = []
    for day_shift in (0, -1):
        d = (local + timedelta(days=day_shift)).dt.normalize()
        frames.append(pd.DataFrame({"day": d, "lo": start, "hi": local}))
    span = pd.concat(frames, ignore_index=True)

    day_lo = span.day
    day_hi = span.day + timedelta(days=1)
    span["lo"] = span.lo.clip(lower=day_lo, upper=day_hi)
    span["hi"] = span.hi.clip(lower=day_lo, upper=day_hi)
    span = span[span.hi > span.lo]
    if span.empty:
        return empty

    covered = 0
    for day, g in span.groupby("day", sort=False):
        g = g.sort_values("lo")
        total = 0.0
        cur_lo, cur_hi = None, None
        for lo, hi in zip(g.lo, g.hi):
            if cur_hi is None or lo > cur_hi:
                if cur_hi is not None:
                    total += (cur_hi - cur_lo).total_seconds()
                cur_lo, cur_hi = lo, hi
            else:
                cur_hi = max(cur_hi, hi)
        if cur_hi is not None:
            total += (cur_hi - cur_lo).total_seconds()
        if 24.0 - total / 3600.0 <= MAX_GAP_HOURS:
            covered += 1

    return {
        "days_with_record": int(span.day.nunique()),
        "days_covered": int(covered),
        "days_24h": int((rec.hours >= 24).sum()),
        "n_records": int(len(rec)),
        "median_period_h": float(rec.hours.median()),
    }


def probe(targets: pd.DataFrame, year: int = PROBE_YEAR) -> pd.DataFrame:
    # Downloads dominate the runtime and the server is fine with a handful at
    # once; the parsing below stays serial so the result cannot depend on
    # scheduling.
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda s: fetch_year(s, year),
                      targets.isd_station.tolist()))

    rows = []
    for i, r in enumerate(targets.itertuples(index=False), 1):
        df = fetch_year(r.isd_station, year)
        base = {"city": r.city, "country": r.country,
                "continent": r.continent, "population": int(r.population),
                "station": r.isd_station, "km": float(r.isd_km),
                "year": year, "has_file": df is not None}
        if df is None:
            rows.append({**base, "n_rows": 0, "days_with_record": 0,
                         "days_covered": 0, "days_24h": 0, "n_records": 0,
                         "median_period_h": np.nan, "coverage": 0.0})
        else:
            rec = parse_precip(df)
            cov = covered_days(rec, r.timezone)
            days = 366 if year % 4 == 0 else 365
            rows.append({**base, "n_rows": len(df), **cov,
                         "coverage": cov["days_covered"] / days})
        if i % 20 == 0:
            print(f"  probed {i}/{len(targets)} stations")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. Correctness checks
# ---------------------------------------------------------------------------
def _rec(rows) -> pd.DataFrame:
    return pd.DataFrame(
        [{"end": pd.Timestamp(e, tz="UTC"), "hours": h, "mm": m}
         for e, h, m in rows])


def run_checks() -> None:
    print("=== correctness checks ===")

    # 1. The missing sentinel must not become rainfall. This is the single
    #    failure that would turn the whole stage into fiction: 9999 read as a
    #    number is 999.9 mm, and period 99 is four days of accumulation.
    df = pd.DataFrame({"DATE": ["2025-03-01T06:00:00", "2025-03-01T07:00:00"],
                       "AA1": ["99,9999,9,9", "01,0008,9,5"]})
    rec = parse_precip(df)
    if len(rec) != 1 or abs(rec.mm.iloc[0] - 0.8) > 1e-9:
        die(f"the missing sentinel was not rejected - parsed {len(rec)} "
            f"records {rec.mm.tolist()} where one of 0.8 mm was due")
    say("the missing sentinel 99,9999 is dropped and 01,0008 reads as 0.8 mm, "
        "so absent data cannot enter as 999.9 mm of rain")

    # 2. Values the provider flagged as erroneous must not be counted, or the
    #    coverage figure is a count of records rather than of observations.
    df = pd.DataFrame({"DATE": ["2025-03-01T06:00:00"], "AA1": ["01,0050,9,3"]})
    if len(parse_precip(df)) != 0:
        die("a record flagged erroneous (quality 3) was accepted")
    say("records the provider flagged erroneous are dropped, so coverage "
        "counts observations rather than rows")

    # 3. A day tiled by hourly records must count as covered, and the same
    #    number of records scattered over the day must NOT. Without this the
    #    stage cannot tell a reporting station from a sporadic one.
    full = _rec([(f"2025-03-02T{h:02d}:00:00", 1.0, 0.0) for h in range(24)])
    got = covered_days(full, "UTC")
    if got["days_covered"] != 1:
        die(f"24 hourly records did not cover their day: {got}")
    sparse = _rec([(f"2025-03-02T{h:02d}:00:00", 1.0, 0.0)
                   for h in range(0, 24, 2)])
    got_sparse = covered_days(sparse, "UTC")
    if got_sparse["days_covered"] != 0:
        die(f"12 records covering half a day were counted as covering it: "
            f"{got_sparse}")
    say("24 tiled hourly records cover their day and 12 alternating ones do "
        "not, so a sporadic station cannot pass as a reporting one")

    # 4. Overlapping records must not add up to a covered day. A station
    #    filing an hourly total and a 24-hour total for the same period would
    #    otherwise look like 48 hours of coverage.
    both = _rec([("2025-03-02T12:00:00", 1.0, 0.0),
                 ("2025-03-02T12:00:00", 12.0, 0.0)])
    if covered_days(both, "UTC")["days_covered"] != 0:
        die("overlapping records were summed and passed as a covered day")
    say("overlapping records are merged rather than summed, so a duplicate "
        "filing cannot manufacture coverage")

    # 5. A 24-hour total filed at 07:00 local covers the day it reaches back
    #    into, not the one it is stamped with. This is E24's finding applied
    #    to ISD, and getting it wrong would misattribute most of the record.
    morning = _rec([("2025-03-03T07:00:00", 24.0, 5.0)])
    got = covered_days(morning, "UTC")
    if got["days_covered"] != 0:
        die(f"a 24-hour total ending at 07:00 was credited with covering a "
            f"full local day: {got}")
    midnight = _rec([("2025-03-04T00:00:00", 24.0, 5.0)])
    if covered_days(midnight, "UTC")["days_covered"] != 1:
        die("a 24-hour total ending at local midnight did not cover its day")
    say("a 24-hour total ending at 07:00 covers no single local day while one "
        "ending at midnight covers exactly one - the day boundary is honoured")

    # 6. A station that reports every day of the probe year must score 1.0.
    #    The first run of this stage used an incomplete year against a
    #    365-day denominator, so a flawless station scored 66% and the
    #    "above 80%" column read zero everywhere. That looked like the
    #    finding, which is why it is now a check rather than a comment.
    days = 366 if PROBE_YEAR % 4 == 0 else 365
    stamps = pd.date_range(f"{PROBE_YEAR}-01-02", periods=days, freq="D",
                           tz="UTC")
    perfect = pd.DataFrame({"end": stamps, "hours": 24.0, "mm": 0.0})
    full = covered_days(perfect, "UTC")["days_covered"] / days
    if abs(full - 1.0) > 1e-9:
        die(f"a station reporting every day of {PROBE_YEAR} scored {full:.1%} "
            f"rather than 100% - the denominator does not match the probe "
            f"window, so every coverage figure is deflated")
    say(f"a station reporting every day of {PROBE_YEAR} scores 100%, so the "
        f"coverage ceiling is reachable and a low score means low reporting")
    print()


# ---------------------------------------------------------------------------
# 4. Report
# ---------------------------------------------------------------------------
def report(census: pd.DataFrame, horizon, probed: pd.DataFrame,
           grace: pd.DataFrame) -> None:
    bar = "=" * 78
    print(bar)
    print("CAN NOAA ISD REACH WHERE GHCN CANNOT? (Task 11; G1)")
    print(bar)

    print(f"\n--- 1. Stations exist, and that looks like very good news ---")
    print(f"  {'continent':30s}{'cities':>8s}{'GHCN':>7s}{'GHCN%':>8s}"
          f"{'ISD':>7s}{'ISD%':>8s}")
    for _, r in census.iterrows():
        print(f"  {r.continent:30s}{int(r.cities):8d}{int(r.ghcn_cities):7d}"
              f"{r.ghcn_share:8.1%}{int(r.isd_cities):7d}{r.isd_share:8.1%}")
    tot_c = int(census.cities.sum())
    tot_g = int(census.ghcn_cities.sum())
    tot_i = int(census.isd_cities.sum())
    print(f"  {'ALL':30s}{tot_c:8d}{tot_g:7d}{tot_g / tot_c:8.1%}"
          f"{tot_i:7d}{tot_i / tot_c:8.1%}")
    afr = census[census.continent == "Africa"].iloc[0]
    sam = census[census.continent == "South America"].iloc[0]
    print(f"  Against every GeoNames city of {MIN_POP:,}+ people - not the "
          f"study's pool, which is\n  already filtered to cities that HAVE a "
          f"GHCN gauge. Africa goes from "
          f"{int(afr.ghcn_cities)} city to\n  {int(afr.isd_cities)} and South "
          f"America from {int(sam.ghcn_cities)} to {int(sam.isd_cities)}. If "
          f"station existence were the question,\n  ISD would close G1 "
          f"outright.")
    print(f"  (Station history as published to {pd.Timestamp(horizon).date()}"
          f"; 'active' means active as far as\n  that file knows, which is "
          f"not the same as today.)")

    row = dict(zip(grace.grace_days, grace.n_stations))
    print(f"\n  'Active' allows a station's last observation to trail the "
          f"horizon by {ACTIVE_GRACE_DAYS} days,\n  and the count barely "
          f"notices where that line is drawn:")
    print("   " + "".join(f"{g:>8d}d" for g in grace.grace_days))
    print("   " + "".join(f"{n:>9,d}" for n in grace.n_stations))
    print(f"  Demanding the horizon exactly leaves {row[0]:,} stations and "
          f"anything from a fortnight\n  to six months leaves "
          f"{row[14]:,}-{row[180]:,}. The tail is ingest lag, not closure, so "
          f"the strict\n  reading would have measured NCEI's pipeline and "
          f"called it the network.")

    print(f"\n--- 2. ...but existence is not reporting ---")
    ok = probed[probed.has_file]
    print(f"  {len(probed)} stations read for {PROBE_YEAR}, the nearest "
          f"station to each of the {PER_CONTINENT} largest\n  cities per "
          f"continent. {len(ok)} returned a file.")
    print(f"\n  {'continent':30s}{'probed':>8s}{'median':>9s}{'>=80%':>8s}"
          f"{'>=50%':>8s}{'<10%':>7s}")
    g = probed.groupby("continent")
    for cont, sub in sorted(g, key=lambda kv: -len(kv[1])):
        print(f"  {cont:30s}{len(sub):8d}{sub.coverage.median():9.1%}"
              f"{(sub.coverage >= 0.80).mean():8.0%}"
              f"{(sub.coverage >= 0.50).mean():8.0%}"
              f"{(sub.coverage < 0.10).mean():7.0%}")
    print(f"  {'ALL':30s}{len(probed):8d}{probed.coverage.median():9.1%}"
          f"{(probed.coverage >= 0.80).mean():8.0%}"
          f"{(probed.coverage >= 0.50).mean():8.0%}"
          f"{(probed.coverage < 0.10).mean():7.0%}")
    print("  'Coverage' is the share of local days whose total can actually "
          "be reconstructed -\n  a day counts only if the accumulation "
          f"periods reaching into it leave under\n  {MAX_GAP_HOURS:.0f} hours "
          f"unaccounted for. Records flagged erroneous, and the missing\n  "
          f"sentinels, are dropped before counting.")

    print(f"\n--- 3. What that costs the shortfall ---")
    usable = probed[probed.coverage >= 0.80]
    print(f"  {'continent':30s}{'ISD cities':>12s}{'x usable':>10s}"
          f"{'effective':>11s}{'GHCN':>7s}")
    for _, r in census.iterrows():
        sub = probed[probed.continent == r.continent]
        rate = (sub.coverage >= 0.80).mean() if len(sub) else np.nan
        eff = r.isd_cities * rate if len(sub) else np.nan
        print(f"  {r.continent:30s}{int(r.isd_cities):12d}"
              f"{rate:10.0%}" if len(sub) else
              f"  {r.continent:30s}{int(r.isd_cities):12d}{'n/a':>10s}",
              end="")
        if len(sub):
            print(f"{eff:11.0f}{int(r.ghcn_cities):7d}")
        else:
            print(f"{'n/a':>11s}{int(r.ghcn_cities):7d}")
    print("  The usable rate is measured on the LARGEST city per station in "
          "each continent, which\n  is the most generous sample available: a "
          "provincial town does not report better\n  than a capital airport. "
          "So the effective column is an upper bound.")

    print(f"\n--- 4. The currency is countries, not cities ---")
    print(f"  {'continent':30s}{'countries':>11s}{'GHCN':>7s}{'ISD':>7s}"
          f"{'gain':>7s}")
    for _, r in census.iterrows():
        print(f"  {r.continent:30s}{int(r.countries):11d}"
              f"{int(r.ghcn_countries):7d}{int(r.isd_countries):7d}"
              f"{int(r.isd_countries) - int(r.ghcn_countries):+7d}")
    n_all = int(census.countries.sum())
    n_g = int(census.ghcn_countries.sum())
    n_i = int(census.isd_countries.sum())
    print(f"  {'ALL':30s}{n_all:11d}{n_g:7d}{n_i:7d}{n_i - n_g:+7d}")
    print(f"  E28 measured a design effect of 4.3 between cities of the same "
          f"country, so the\n  eighth German city buys almost nothing and a "
          f"first Kenyan one buys a great deal.\n  On that currency ISD takes "
          f"the panel from {n_g} countries to {n_i} of the {n_all} with a city "
          f"this size -\n  which would be the largest gain available to this "
          f"study, and the one thing GHCN\n  could not supply at any budget. "
          f"It is an upper bound: a country counts here\n  if one of its "
          f"cities has a station, not if that station reports.")
    # The probe visited one station in each of a limited set of countries, so
    # its confirmed count is not comparable to the census figure directly -
    # what transfers is the RATE, and saying so is the difference between a
    # bracket and a category error.
    seen = probed.country.nunique()
    conf = probed.loc[probed.coverage >= 0.50, "country"].nunique()
    rate = conf / seen
    print(f"  That bound has to be discounted by section 2, and carefully: "
          f"the probe visited\n  only {seen} countries, so its {conf} "
          f"confirmed ones are not a floor under {n_i} - a\n  country ISD "
          f"reaches but the probe never opened is neither confirmed nor "
          f"denied.\n  What transfers is the rate. {conf} of {seen} visited "
          f"countries had a station\n  reporting on at least half the days, "
          f"and {rate:.0%} of {n_i} is about {round(n_i * rate):d} - still "
          f"{round(n_i * rate) / n_g:.1f} times\n  the {n_g} GHCN reaches, "
          f"and the probe took the largest cities, so even that\n  is "
          f"generous.")
    print(f"  It does not rescue the headline. G20 needs ~612 countries for "
          f"the Brier\n  comparison to resolve and the planet has 195, so the "
          f"precision claim stays\n  out of reach - this widens the study's "
          f"reach, not its resolution.")

    print("\n--- Caveats ---")
    print(f"  1. One probe year ({PROBE_YEAR}) and {PER_CONTINENT} stations a "
          f"continent. That is enough to separate\n     'reports rain' from "
          f"'does not', which is the question; it is not enough to rank\n"
          f"     continents against each other by a few percent.")
    print("  2. Coverage here means a daily total can be RECONSTRUCTED, not "
          "that it is correct.\n     Nothing in this stage validates the "
          "values themselves - that is what the\n     GHCN-pair machinery in "
          "truth_uncertainty.py would have to be pointed at next.")
    print("  3. ISD's day attribution is not GHCN's. E24 showed the recording "
          "convention moves\n     the answer by 0.09 Brier, and an ISD-based "
          "panel would need that measurement\n     redone against timestamps "
          "rather than metadata.")


# ---------------------------------------------------------------------------
def main() -> None:
    args = set(sys.argv[1:])
    run_checks()
    if "check" in args:
        return

    stations = station_table()
    act, horizon = active(stations)
    if pd.Timestamp(horizon) < pd.Timestamp(f"{PROBE_YEAR}-12-31"):
        die(f"the archive stops at {pd.Timestamp(horizon).date()}, so "
            f"{PROBE_YEAR} is incomplete and every coverage figure would be "
            f"divided by more days than the year can supply")
    print(f"ISD history: {len(stations):,} stations, {len(act):,} spanning "
          f"{PROBE_YEAR} (file horizon {pd.Timestamp(horizon).date()})")

    grace = grace_sensitivity(stations)
    census, big = city_census(act)
    if "census" in args:
        print()
        print(census.to_string(index=False))
        return

    targets = sample_targets(big)
    print(f"probing {len(targets)} stations across "
          f"{targets.continent.nunique()} continents for {PROBE_YEAR}")
    probed = probe(targets)

    print()
    report(census, horizon, probed, grace)

    census.to_parquet(CENSUS_TABLE, index=False)
    probed.to_parquet(STATION_TABLE, index=False)
    print(f"\nwrote {CENSUS_TABLE.name} and {STATION_TABLE.name}")


if __name__ == "__main__":
    main()
