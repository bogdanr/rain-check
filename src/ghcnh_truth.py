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
import re
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
# enough. Two was the first setting; it lost cities whose SYNOP record was the
# third id in range (a capital airport often has three: ICAO, WMO, and a
# legacy synoptic id), so four are tried now, within the same 25 km.
#
# The capital is always probed, ahead of the largest cities, so a country's
# anchor city is never the one left unexamined (2026-09-23 plan, Task 5).
PROBE_PER_COUNTRY = 5
STATIONS_PER_CITY = 4

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

# ---------------------------------------------------------------------------
# The zero rule (2026-09-23). WMO FM-12 lets a station leave the section-1
# precipitation group out when the amount is zero (indicator iR = 3), and the
# NOAA archives do not keep iR. A station following that practice files rain
# only when it rained: Mumbai 2025 has a SYNOP every 3 h all year but a rain
# value on 157 days, nearly all June-October, and under 1% of its non-trace
# values are zero. Read literally, "missing is not dry" then throws its dry
# days away, and the days that survive are the wet ones.
#
# The rule restores a zero only where every one of these holds:
#   * the station's practice is to omit zeros: it has at least
#     INFER_MIN_STATION_RECORDS valid amounts and at most INFER_MAX_ZERO_SHARE
#     of the non-trace ones are zero (a station that files zeros means
#     "not observed" by a blank);
#   * the report is a received FM-12 SYNOP at a (UTC time) slot where the
#     station's filed amounts use one period in >= INFER_MIN_KEY_SHARE of
#     INFER_MIN_KEY_RECORDS or more cases - that period is the zero's;
#   * every precipitation field of that report is blank, codes included;
#   * no report in the period (SYNOP or METAR, any weather group) shows
#     precipitation, a thunderstorm or precipitation in sight;
#   * no filed non-zero amount overlaps the period.
# Anything else stays missing. The rule is off unless the validation stage
# (`validate-zeros`) has passed, and each day built from a restored zero is
# flagged so every result can be recomputed without them.
# ---------------------------------------------------------------------------
INFER_MAX_ZERO_SHARE = 0.05
INFER_MIN_STATION_RECORDS = 20
INFER_MIN_KEY_RECORDS = 8
INFER_MIN_KEY_SHARE = 0.80
ZERO_VALIDATION = RAW / "ghcnh_zero_validation.json"

# Present-weather abbreviations that mean precipitation at or near the station.
WET_WX = re.compile(r"RA|DZ|SN|SH|TS|GR|GS|PL|UP|SG|IC")


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


def prefetch(station: str, years=None) -> None:
    """Download (cache) without reading. A thread pool mapping fetch_year
    over hundreds of stations holds every full-width frame at once - ~20 GB
    for the validation's 350 stations x 3 years, which Arrow did not survive
    (the process ended with status 0 and no traceback)."""
    for y in (years or YEARS):
        _get(YEAR_URL.format(s=station, y=y),
             YEAR_CACHE / f"{station}_{y}.parquet")


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
        trace = (mc.astype(str).str.strip().eq("T") if mc is not None
                 else pd.Series(False, index=df.index))
        if keep.any():
            out.append(pd.DataFrame({"end": when[keep], "hours": float(n),
                                     "mm": v[keep].astype(float),
                                     "trace": trace[keep].to_numpy(bool)}))
    if not out:
        return pd.DataFrame(columns=cols)
    rec = pd.concat(out, ignore_index=True)
    return (rec.drop_duplicates(["end", "hours", "mm"]).sort_values("end")
               .reset_index(drop=True))


def _precip_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith("precipitation")]


def wet_weather(df: pd.DataFrame) -> pd.Series:
    """Per row: does any present-weather group show precipitation near the
    station? MW is the manual ww table (14-19 in sight or thunder, 20-29
    recent, 50-99 at the station), AW the automatic one (20-29, 40-99), AU
    the METAR abbreviations; the abbreviation test covers all three."""
    df = df.reset_index(drop=True)
    wet = pd.Series(False, index=df.index)
    for c in df.columns:
        m = re.fullmatch(r"pres_wx_(MW|AW|AU)\d", c)
        if not m:
            continue
        s = df[c].dropna().astype(str).str.strip()
        s = s[s != ""]
        if s.empty:
            continue
        w = s.str.contains(WET_WX)
        num = pd.to_numeric(s.str.extract(r"(?:^|:)(\d{1,2})$")[0],
                            errors="coerce")
        if m.group(1) == "MW":
            w |= num.between(14, 29) | num.between(50, 99)
        elif m.group(1) == "AW":
            w |= num.between(20, 29) | num.between(40, 99)
        wet.loc[s.index] |= w.to_numpy(bool)
    return wet


def zero_practice(rec: pd.DataFrame) -> dict:
    """How a station files dry periods: n valid amounts, and the share of
    the non-trace ones that are exactly zero."""
    if rec.empty:
        return {"n": 0, "zero_share": None, "omits_zeros": False}
    nt = rec[~rec.get("trace", pd.Series(False, index=rec.index))
             .astype(bool)]
    share = float((nt.mm == 0).mean()) if len(nt) else None
    return {"n": int(len(rec)), "zero_share": share,
            "omits_zeros": bool(len(rec) >= INFER_MIN_STATION_RECORDS
                                and share is not None
                                and share <= INFER_MAX_ZERO_SHARE)}


def schedule(rec: pd.DataFrame) -> dict[int, tuple[float, ...]]:
    """UTC minute-of-day -> the accumulation periods the station files there.

    A SYNOP can carry two groups (section 1's 6 h and section 3's 3 h, or a
    24 h total at the main hour), so a slot keeps every period present in at
    least INFER_MIN_KEY_SHARE of the reports that filed an amount there, out
    of INFER_MIN_KEY_RECORDS or more such reports."""
    if rec.empty:
        return {}
    key = rec.end.dt.hour * 60 + rec.end.dt.minute
    out = {}
    d = pd.DataFrame({"k": key, "end": rec.end, "h": rec.hours})
    for k, g in d.groupby("k"):
        n = g.end.nunique()
        if n < INFER_MIN_KEY_RECORDS:
            continue
        share = g.drop_duplicates(["end", "h"]).h.value_counts() / n
        keep = tuple(sorted(float(h) for h, s in share.items()
                            if s >= INFER_MIN_KEY_SHARE))
        if keep:
            out[int(k)] = keep
    return out


def infer_zeros(df: pd.DataFrame, rec: pd.DataFrame, force: bool = False
                ) -> tuple[pd.DataFrame, dict]:
    """`rec` plus a zero for every SYNOP the rule above applies to.

    `force` skips the practice test only (validation V1 uses it on stations
    that do file zeros, after deleting them). Returns (records with an
    `inferred` column, a summary of what the rule did and why)."""
    rec = rec.copy()
    rec["inferred"] = False
    info = zero_practice(rec)
    info.update(candidates=0, inferred=0, blocked_wx=0, blocked_rain=0,
                slots=0)
    if df is None or df.empty or "DATE" not in df.columns:
        return rec, info
    if not (force or info["omits_zeros"]):
        return rec, info
    df = df.reset_index(drop=True)
    sched = schedule(rec)
    info["slots"] = len(sched)
    if not sched:
        return rec, info

    when = _when(df)
    rt = [c for c in df.columns if c.endswith("_Report_Type")]
    fm12 = pd.Series(False, index=df.index)
    for c in rt:
        fm12 |= df[c].astype(str).str.strip().eq("FM12")
    blank = pd.Series(True, index=df.index)
    for c in _precip_columns(df):
        if c.endswith(("_Report_Type", "_Source_Code", "_Source_Station_ID")):
            continue
        v = df[c]
        if not pd.api.types.is_numeric_dtype(v):
            v = v.where(~v.astype(str).str.strip()
                        .isin(["", "nan", "None", "<NA>"]))
        blank &= v.isna()
    key = when.dt.hour * 60 + when.dt.minute
    cand = fm12 & blank & when.notna() & key.isin(list(sched))
    c = pd.DataFrame({"end": when[cand], "k": key[cand]})
    # A slot with a filed amount under another row (duplicate reports of one
    # time from two sources) is not blank.
    c = c[~c.end.isin(set(rec.end))].drop_duplicates("end")
    c = (c.assign(hours=c.k.map(sched)).explode("hours")
          .astype({"hours": float}).drop(columns="k").reset_index(drop=True))
    info["candidates"] = int(len(c))
    if c.empty:
        return rec, info
    lo = (c.end - pd.to_timedelta(c.hours, unit="h")).to_numpy("datetime64[ns]")
    hi = c.end.to_numpy("datetime64[ns]")

    # Weather guard: any wet report in (lo, hi].
    wt = np.sort(when[wet_weather(df).to_numpy(bool) & when.notna().to_numpy()]
                 .to_numpy("datetime64[ns]"))
    n_wet = (np.searchsorted(wt, hi, side="right")
             - np.searchsorted(wt, lo, side="right"))
    by_wx = n_wet > 0
    # Rain guard: any filed non-zero amount overlapping (lo, hi).
    pos = rec[rec.mm > 0]
    by_rain = np.zeros(len(c), bool)
    if len(pos):
        phi = pos.end.to_numpy("datetime64[ns]")
        plo = (pos.end - pd.to_timedelta(pos.hours, unit="h")
               ).to_numpy("datetime64[ns]")
        for i in range(0, len(c), 2000):
            a, b = lo[i:i + 2000, None], hi[i:i + 2000, None]
            by_rain[i:i + 2000] = ((plo[None, :] < b)
                                   & (phi[None, :] > a)).any(axis=1)
    ok = ~by_wx & ~by_rain
    info.update(blocked_wx=int(by_wx.sum()),
                blocked_rain=int((by_rain & ~by_wx).sum()),
                inferred=int(ok.sum()))
    add = pd.DataFrame({"end": c.end[ok].values, "hours": c.hours[ok].values,
                        "mm": 0.0, "trace": False, "inferred": True})
    add["end"] = pd.to_datetime(add.end, utc=True)
    out = pd.concat([rec, add], ignore_index=True).sort_values("end")
    return out.reset_index(drop=True), info


def zero_rule_on() -> bool:
    """The rule applies only once validation has passed (and been kept)."""
    if not ZERO_VALIDATION.exists():
        return False
    return bool(json.loads(ZERO_VALIDATION.read_text()).get("passed"))


def precip_records(df: pd.DataFrame, infer: bool | None = None
                   ) -> tuple[pd.DataFrame, dict]:
    rec = parse_precip(df)
    if infer is None:
        infer = zero_rule_on()
    if not infer:
        rec = rec.copy()
        rec["inferred"] = False
        return rec, zero_practice(rec)
    return infer_zeros(df, rec)


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


def reconstruct_station(frames: list[pd.DataFrame], timezone: str,
                        infer: bool | None = None, info: dict | None = None
                        ) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """(daily precip, daily tmax, window end minute) for one station.

    `infer` None follows the validation gate; `info`, if given, receives the
    zero rule's summary for the station."""
    frames = [f for f in frames if f is not None and len(f)] if frames else []
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    rec, zi = precip_records(raw, infer)
    if info is not None:
        info.update(zi)
    # The day boundary is the station's own, read from what it filed.
    end, _ = choose_end_hour(rec[~rec.inferred], timezone)
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
    """Up to STATIONS_PER_CITY usable stations for the capital and largest
    cities of every country the GHCN-Daily pool cannot verify."""
    import probe_cities
    from probe_capitals import MAX_ELEV_DIFF_M, MAX_STATION_KM

    cities = probe_cities.load_geonames()
    cities = cities[(cities.population >= probe_cities.MIN_POPULATION)
                    | cities.capital]
    pool = probe_cities.candidate_pool()
    # Only GHCN-Daily rows decide which countries are "unreached": the pool
    # also carries GHCNh and ISD rows written by earlier runs of these stages.
    # And only rows that can meet the gate: a country whose best GHCN gauge
    # files 380 days is not reached by GHCN, whatever the inventory says.
    ghcn_countries = probe_cities.covered_countries(pool[pool.truth == "GHCN"])
    todo = cities[~cities.country.isin(ghcn_countries)].reset_index(drop=True)
    todo = (todo.sort_values(["capital", "population"], ascending=False)
                .groupby("country", group_keys=False).head(PROBE_PER_COUNTRY)
                .reset_index(drop=True))

    st = stations.reset_index(drop=True)
    tree = cKDTree(_xyz(st.lat, st.lon))
    k = min(4 * STATIONS_PER_CITY, len(st))
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
    out = pd.DataFrame(rows)
    # Every probed country, including those with no station in range, so the
    # country log can name that as the step where they stopped.
    out.attrs["probed"] = todo.country.value_counts().to_dict()
    return out


def _json_default(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    raise TypeError(type(v))


def screen(cands: pd.DataFrame) -> pd.DataFrame:
    """Days reconstructable in SCREEN_YEAR, per station."""
    ids = sorted(set(cands.station))
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(lambda s: prefetch(s, [SCREEN_YEAR]), ids))
    days = 366 if SCREEN_YEAR % 4 == 0 else 365
    tz = cands.drop_duplicates("station").set_index("station").timezone
    cov = {}
    for s in ids:
        df = fetch_year(s, SCREEN_YEAR)
        if df is None:
            cov[s] = 0.0
            continue
        rec, _ = precip_records(df)
        end, _ = choose_end_hour(rec[~rec.inferred], tz[s])
        cov[s] = len(daily_totals(rec, tz[s], end)) / days
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


def without_inferred() -> bool:
    import os
    return os.environ.get("RAINCHECK_NO_INFERRED", "") not in ("", "0")


def load_prcp(station_id: str) -> pd.DataFrame:
    """Same shape as `capitals.load_ghcn`: ghcn_date, prcp."""
    s = _store()
    if s is None:
        return pd.DataFrame()
    g = s[(s.station == station_id) & s.prcp.notna()]
    # Sensitivity run: every result recomputed without restored-zero days.
    if without_inferred() and "inferred" in g:
        g = g[~g.inferred.fillna(False).astype(bool)]
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


def country_log() -> dict:
    """Per country: how far GHCNh got and the best pair count it reached
    (written by `main`; empty if this stage predates the log)."""
    if not CANDIDATES.exists():
        return {}
    return json.loads(CANDIDATES.read_text()).get("countries", {})


def stage_log(todo_countries, cands, scr_all, best, meta, keep,
              gate: int) -> dict:
    """The step at which each probed country stopped, and its best record.

    Printed step counts cannot say why India is missing; this can, and it is
    the baseline every later change to the rules is measured against.
    """
    out = {}
    m = best.merge(meta, on="station", how="left") if len(meta) else best
    for cc in sorted(todo_countries):
        e = {"cities_probed": int(todo_countries[cc])}
        c = cands[cands.country == cc] if len(cands) else cands
        s = scr_all[scr_all.country == cc] if len(scr_all) else scr_all
        b = m[m.country == cc] if len(m) else m
        k = keep[keep.country == cc] if len(keep) else keep
        e["stations_in_range"] = int(c.station.nunique()) if len(c) else 0
        e["best_screen"] = (round(float(s.screen_coverage.max()), 3)
                            if len(s) else 0.0)
        e["best_pairs"] = (int(b.pair_days.fillna(0).max())
                           if len(b) and "pair_days" in b else 0)
        e["included"] = int(len(k))
        if len(k):
            e["step"] = f"{len(k)} cities pass"
        elif not e["stations_in_range"]:
            e["step"] = ("no active station within 25 km and 300 m of a "
                         "probed city")
        elif not len(b):
            e["step"] = (f"no station reconstructs {SCREEN_MIN_COVERAGE:.0%} "
                         f"of {SCREEN_YEAR} (best {e['best_screen']:.0%})")
        else:
            e["step"] = f"best record {e['best_pairs']} rain days < {gate}"
        out[cc] = e
    return out


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

    # 6. The zero rule. A zero-omitting station: 24 h group at 00 UTC, filed
    #    only when it rained (every 5th day). Blank SYNOPs become zeros; a
    #    blank SYNOP with rain in the present weather, a METAR, or an
    #    overlapping filed amount does not; a zero-filing station is left
    #    alone.
    rows = []
    days = pd.date_range("2025-01-01", periods=100, freq="D", tz="UTC")
    for i, day in enumerate(days):
        wet = i % 5 == 0
        rows.append({"DATE": day.isoformat(),
                     "precipitation_24_hour": 4.0 if wet else np.nan,
                     "precipitation_24_hour_Quality_Code": "1" if wet else None,
                     "temperature": 20.0, "temperature_Report_Type": "FM12",
                     "pres_wx_MW1": "RA:61" if i == 7 else None})
    rows.append({"DATE": (days[11] - pd.Timedelta(hours=5)).isoformat(),
                 "temperature": 20.0, "temperature_Report_Type": "FM15",
                 "pres_wx_AU1": "-RA:02"})
    df = pd.DataFrame(rows)
    rec, info = infer_zeros(df, parse_precip(df))
    got = set(rec[rec.inferred].end)
    want = {d for i, d in enumerate(days) if i % 5 and i not in (7, 11)}
    if not info["omits_zeros"] or got != want or \
            (rec[rec.inferred].hours != 24).any():
        die(f"zero rule restored {len(got)} (wanted {len(want)}): "
            f"{sorted(got ^ want)[:5]}; {info}")
    p = daily_totals(rec, "UTC", 0)
    if p.inferred.sum() != len(want) or (p[p.inferred].prcp != 0).any():
        die(f"restored days not flagged: {p}")
    filing = df.copy()
    filing.loc[filing.precipitation_24_hour.isna() & filing.DATE.str.endswith(
        "00:00:00+00:00") & (filing.index % 2 == 0), "precipitation_24_hour"] = 0.0
    rec2, info2 = infer_zeros(filing, parse_precip(filing))
    if info2["omits_zeros"] or rec2.inferred.any():
        die(f"zero rule touched a zero-filing station: {info2}")
    say("zero rule: blank scheduled SYNOPs become flagged zeros; a wet "
        "SYNOP, a wet METAR inside the period and a zero-filing station are "
        "left missing")
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
        list(ex.map(lambda a: prefetch(a[1]), pairs))

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
        list(ex.map(lambda s: prefetch(pairs[s]), pick.id))
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
# 6. Validation of the zero rule (run before the rule may be used)
#
# The risk is one: a blank that meant "not observed" read as "dry". The
# archive cannot say which a blank was, so the rule is judged by what it
# produces. Three tests, criteria fixed here before the first run:
#
#   V1  withheld zeros. At stations that DO file zeros, delete every zero,
#       apply the rule (practice test bypassed), rebuild, and compare with the
#       untouched record. Tests the schedule, the tiling and the guards: a
#       rule that invents a dry day where the station filed rain fails here.
#   V3  independent gauge. At zero-omitting GHCNh stations that GHCN-Daily
#       also carries (same id or WMO number), how often is a restored dry day
#       wet in the daily gauge? Compared with the same rate for FILED dry days
#       at zero-filing stations, which is what two pipes disagree by anyway.
#       And the wet-day rate the rule produces against the daily gauge's.
#   V2  satellite. The same question against CHIRP (no gauges blended in),
#       on sampled days, at the stations the probe would actually use.
#
# `passed` requires all three. The detail goes to
# processed/ghcnh_zero_validation.parquet, the verdict to ZERO_VALIDATION.
# ---------------------------------------------------------------------------
ZV_CRITERIA = {
    "v1_min_agree": 0.99,             # wet/dry agreement, rebuilt vs original
    "v1_max_false_dry": 0.01,         # original wet, rebuilt dry
    "v1_min_recovered": 0.80,         # original days rebuilt at all (pooled)
    "v3_max_excess_false_wet": 0.03,  # over filed-dry days at filing stations
    "v3_max_abs_wet_bias": 0.05,      # median |wet rate rule - wet rate gauge|
    "v3_min_inferred_days": 1000,
    "v3_min_stations": 10,
    "v2_max_excess_sat_wet": 0.05,    # over filed-dry days at filing stations
    "v2_min_inferred_days": 500,
}
ZV_PER_COUNTRY = 6
ZV_MAX = 320
ZV_MIN_OVERLAP = 150          # GHCN-Daily days in the window, per gauge
ZV_FILING_MIN_ZERO = 0.30     # zero share that marks a zero-filing station
ZV_SAT_BLOCKS = 12            # x satellite_truth.BLOCK_LEN days of CHIRP
ZV_SAT_CACHE = CACHE / "chirp_zero"


def _tz_from_lon(lon: float) -> str:
    off = int(round(lon / 15))
    return f"Etc/GMT{-off:+d}" if off else "UTC"   # Etc/ signs are inverted


def _ghcn_pairs(gauges: set[str], st: pd.DataFrame, act: set[str]
                ) -> dict[str, str]:
    """GHCN-Daily id -> GHCNh id for the same station: by id where GHCNh
    reuses it, by WMO number where the GHCN id encodes one (xxM000NNNNN)."""
    wmo = (st[st.wmo.notna() & (st.wmo.astype(str).str.len() > 0)]
           .reset_index().assign(w=lambda d: d.wmo.astype(str).str.zfill(5)))
    wmo = wmo[wmo.id.isin(act)].drop_duplicates("w").set_index("w").id
    pairs = {}
    for g in gauges:
        if g in act and g in st.index:
            pairs[g] = g
        elif len(g) == 11 and g[2:6] == "M000" and g[6:] in wmo.index:
            pairs[g] = wmo[g[6:]]
    return pairs


def _rate(x: pd.Series) -> float:
    return float(x.mean()) if len(x) else float("nan")


def _v1(stations: list[tuple[str, str]], thr: float) -> tuple[dict, list]:
    """Withheld zeros at zero-filing stations."""
    rows = []
    for sid, tz in stations:
        frames = [f for f in (fetch_year(sid, y) for y in YEARS)
                  if f is not None and len(f)]
        if not frames:
            continue
        raw = pd.concat(frames, ignore_index=True)
        ref, _, end = reconstruct_station([raw], tz, infer=False)
        if len(ref) < 100:
            continue
        cut = raw.copy()
        for n in SYNOP_PERIODS:
            c = f"precipitation_{n}_hour"
            if c not in cut:
                continue
            mc = cut.get(f"{c}_Measurement_Code")
            trace = (mc.astype(str).str.strip().eq("T") if mc is not None
                     else pd.Series(False, index=cut.index))
            z = (pd.to_numeric(cut[c], errors="coerce") == 0) & ~trace
            for col in [x for x in cut.columns if x.startswith(c)]:
                cut.loc[z, col] = np.nan
        if "precipitation" in cut:
            z = pd.to_numeric(cut["precipitation"], errors="coerce") == 0
            for col in [x for x in cut.columns
                        if x == "precipitation"
                        or x.startswith("precipitation_") and not
                        x.split("_")[1].isdigit()]:
                cut.loc[z, col] = np.nan
        rec, info = infer_zeros(cut, parse_precip(cut), force=True)
        test = daily_totals(rec, tz, end)
        test["obs_date"] = pd.to_datetime(test.obs_date)
        j = ref.merge(test, on="obs_date", suffixes=("_r", "_t"))
        wr, wt = j.prcp_r >= thr, j.prcp_t >= thr
        rows.append({"test": "V1", "station": sid, "days_ref": len(ref),
                     "days_back": len(j), "agree_n": int((wr == wt).sum()),
                     "false_dry_n": int((wr & ~wt).sum()),
                     "wet_ref_n": int(wr.sum()),
                     "zero_share": info.get("zero_share")})
    d = pd.DataFrame(rows)
    if d.empty:
        return {"evaluable": False, "passed": False}, rows
    out = {"stations": int(len(d)),
           "days": int(d.days_back.sum()),
           "recovered": float(d.days_back.sum() / d.days_ref.sum()),
           "agree": float(d.agree_n.sum() / d.days_back.sum()),
           "false_dry": float(d.false_dry_n.sum() / max(d.wet_ref_n.sum(), 1))}
    c = ZV_CRITERIA
    out["evaluable"] = len(d) >= 10
    out["passed"] = bool(out["evaluable"]
                         and out["agree"] >= c["v1_min_agree"]
                         and out["false_dry"] <= c["v1_max_false_dry"]
                         and out["recovered"] >= c["v1_min_recovered"])
    return out, rows


def _v3(thr: float) -> tuple[dict, list, list]:
    """Independent co-located daily gauges. Returns (summary, detail rows,
    zero-filing stations seen, for V1)."""
    import ghcn_bulk

    st = station_table().set_index("id")
    act = active_ids()
    cen = ghcn_bulk.census()
    gauges = set(cen[cen.prcp_days >= ZV_MIN_OVERLAP].station)
    pairs = _ghcn_pairs(gauges, st, act)
    pick = (pd.DataFrame({"id": sorted(pairs)}).sample(frac=1, random_state=23)
              .assign(cc=lambda d: d.id.str[:2])
              .groupby("cc").head(ZV_PER_COUNTRY).head(ZV_MAX))
    print(f"  V3: {len(pairs)} GHCN-Daily gauges GHCNh also carries; "
          f"screening {len(pick)} in {pick.cc.nunique()} country codes")
    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(lambda s: prefetch(pairs[s], [SCREEN_YEAR]), pick.id))
    omit, filing = [], []
    for g in pick.id:
        pr = zero_practice(parse_precip(fetch_year(pairs[g], SCREEN_YEAR)))
        if pr["omits_zeros"]:
            omit.append(g)
        elif pr["zero_share"] is not None and pr["n"] >= 100 \
                and pr["zero_share"] >= ZV_FILING_MIN_ZERO:
            filing.append(g)
    filing = filing[:max(3 * len(omit), 40)]
    use = omit + filing
    print(f"  V3: {len(omit)} zero-omitting and {len(filing)} zero-filing "
          f"stations; fetching {len(YEARS)} years each")
    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(lambda s: prefetch(pairs[s]), use))
    daily = _ghcn_prcp_with_source(set(use))
    lo, hi = pd.Timestamp(POP_ARCHIVE_START), pd.Timestamp(OBS_END)

    rows = []
    for g in use:
        hid = pairs[g]
        tz = _tz_from_lon(float(st.loc[hid, "lon"]))
        p0, _, _ = reconstruct_station([fetch_year(hid, y) for y in YEARS],
                                       tz, infer=False)
        p1, _, _ = reconstruct_station([fetch_year(hid, y) for y in YEARS],
                                       tz, infer=g in omit)
        dg = daily[(daily.station == g) & (daily.ghcn_date >= lo)
                   & (daily.ghcn_date <= hi)].set_index("ghcn_date")
        d = dg.prcp.dropna()
        if p1.empty or d.empty:
            continue
        # Independence. GHCN-Daily source S is the Global Summary of the Day,
        # itself computed from the same SYNOP messages; anything else is a
        # separate national or network daily report.
        src = dg.s_flag.fillna("").astype(str).str.strip()
        indep = float((src != "S").mean())
        h1 = p1.set_index("obs_date")
        # The shift is fitted on filed days only, never on restored ones.
        filed = h1[~h1.inferred.astype(bool)].prcp
        best = None
        for shift in (-1, 0, 1):
            ff = filed.copy(); ff.index = ff.index - pd.Timedelta(days=shift)
            j = pd.concat([d.rename("g"), ff.rename("h")], axis=1).dropna()
            if len(j) < 30:
                continue
            ag = float(((j.g >= thr) == (j.h >= thr)).mean())
            if best is None or ag > best[0]:
                best = (ag, shift)
        if best is None:
            continue
        shift = best[1]
        a = h1.copy(); a.index = a.index - pd.Timedelta(days=shift)
        j = a.join(d.rename("g"), how="inner")
        b = p0.set_index("obs_date").prcp.copy()
        b.index = b.index - pd.Timedelta(days=shift)
        j0 = pd.concat([d.rename("g"), b.rename("h")], axis=1).dropna()
        inf = j.inferred.astype(bool)
        dry_inf = inf & (j.prcp < thr)
        dry_filed = ~inf & (j.prcp < thr)
        rows.append({
            "test": "V3", "station": g, "ghcnh": hid, "cc": g[:2],
            "omits_zeros": g in omit, "shift": shift, "n": len(j),
            "independent": indep >= 0.9, "share_not_gsod": indep,
            "n_inferred_dry": int(dry_inf.sum()),
            "fw_inferred_n": int((dry_inf & (j.g >= thr)).sum()),
            "n_filed_dry": int(dry_filed.sum()),
            "fw_filed_n": int((dry_filed & (j.g >= thr)).sum()),
            "wet_gauge": _rate(j.g >= thr),
            "wet_rule": _rate(j.prcp >= thr),
            "n_without": len(j0),
            "wet_without": _rate(j0.h >= thr),
            "wet_gauge_without": _rate(j0.g >= thr),
            "inferred_share": _rate(inf)})
    d = pd.DataFrame(rows)
    if d.empty:
        return {"evaluable": False, "passed": False}, rows, []
    out = _v3_summary(d)
    out["all_sources"] = _v3_summary(d, independent_only=False)
    return out, rows, [pairs[g] for g in filing]


def _v3_summary(d: pd.DataFrame, independent_only: bool = True) -> dict:
    """The V3 verdict. The criterion is judged on gauges independent of
    SYNOP (see above); the all-sources figure is reported beside it."""
    if independent_only:
        d = d[d.independent]
    om, fi = d[d.omits_zeros], d[~d.omits_zeros]
    out = {"independent_only": independent_only,
           "stations_omitting": int(len(om)), "stations_filing": int(len(fi)),
           "countries_omitting": sorted(om.cc.unique().tolist()),
           "inferred_dry_days": int(om.n_inferred_dry.sum()),
           "false_wet_inferred": float(om.fw_inferred_n.sum()
                                       / max(om.n_inferred_dry.sum(), 1)),
           "false_wet_filed_ref": float(fi.fw_filed_n.sum()
                                        / max(fi.n_filed_dry.sum(), 1)),
           "wet_bias_rule_median": float((om.wet_rule - om.wet_gauge)
                                         .median()) if len(om) else None,
           "wet_bias_without_median": float((om.wet_without
                                             - om.wet_gauge_without)
                                            .median()) if len(om) else None,
           "days_without_median": float(om.n_without.median())
           if len(om) else None,
           "days_rule_median": float(om.n.median()) if len(om) else None}
    c = ZV_CRITERIA
    out["evaluable"] = bool(out["inferred_dry_days"] >= c["v3_min_inferred_days"]
                            and len(om) >= c["v3_min_stations"]
                            and fi.n_filed_dry.sum() >= 1000)
    out["passed"] = bool(
        out["evaluable"]
        and out["false_wet_inferred"]
        <= out["false_wet_filed_ref"] + c["v3_max_excess_false_wet"]
        and abs(out["wet_bias_rule_median"]) <= c["v3_max_abs_wet_bias"])
    return out


def _ghcn_prcp_with_source(ids: set[str]) -> pd.DataFrame:
    """PRCP for `ids` from the GHCN-Daily year files, with the source flag
    (ghcn_bulk.extract drops it). QC-flagged values are dropped as there."""
    import gzip
    import ghcn_bulk

    rows = []
    for year in ghcn_bulk.YEARS:
        path = ghcn_bulk.YEAR_DIR / f"{year}.csv.gz"
        if not path.exists():
            continue
        with gzip.open(path, "rt") as fh:
            for line in fh:
                if line[:11] not in ids:
                    continue
                f = line.rstrip("\n").split(",")
                if f[2] != "PRCP" or f[5].strip():
                    continue
                rows.append((f[0], f[1], float(f[3]) / 10.0, f[6]))
    d = pd.DataFrame(rows, columns=["station", "date", "prcp", "s_flag"])
    d["ghcn_date"] = pd.to_datetime(d.date, format="%Y%m%d")
    return d.drop(columns="date")


def _v2(thr: float) -> tuple[dict, list, list]:
    """CHIRP at the stations the probe would use. Returns (summary, detail,
    zero-filing (station, tz) seen, for V1)."""
    import satellite_truth as sat

    st = station_table()
    act = active_ids()
    cands = candidates(st[st.id.isin(act)])
    cands = cands[cands.lat.abs() < sat.BAND]
    stn = cands.drop_duplicates("station")[["station", "lat", "lon",
                                            "timezone"]].reset_index(drop=True)
    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(prefetch, stn.station))
    days = sat.sampled_days()
    starts = np.linspace(0, len(days) // sat.BLOCK_LEN - 1,
                         ZV_SAT_BLOCKS).astype(int)
    days = pd.DatetimeIndex([days[i * sat.BLOCK_LEN + k] for i in starts
                             for k in range(sat.BLOCK_LEN)])
    days = days[days <= pd.Timestamp(OBS_END)]

    series, practice = {}, {}
    for r in stn.itertuples():
        frames = [fetch_year(r.station, y) for y in YEARS]
        info = {}
        p, _, _ = reconstruct_station(frames, r.timezone, infer=True,
                                      info=info)
        practice[r.station] = info
        if len(p):
            series[r.station] = p.set_index("obs_date")
    stn = stn[stn.station.isin(series)].reset_index(drop=True)
    omit = [s for s in stn.station if practice[s].get("omits_zeros")]
    filing = [s for s in stn.station
              if practice[s].get("zero_share") is not None
              and practice[s]["n"] >= 100
              and practice[s]["zero_share"] >= ZV_FILING_MIN_ZERO]
    print(f"  V2: {len(stn)} probe stations in the CHIRP band "
          f"({len(omit)} zero-omitting, {len(filing)} zero-filing); "
          f"{len(days)} sampled days")

    row, col = sat.grid_index(stn)
    real_cache = sat.GRID_CACHE
    import hashlib
    tag = hashlib.sha1(",".join(stn.station).encode()).hexdigest()[:10]
    sat.GRID_CACHE = ZV_SAT_CACHE / tag
    try:
        def one(day):
            v = sat.fetch_day("chirp", day, row, col)
            if v is None:
                return None
            v = v.astype(float)
            v[v < 0] = np.nan
            return pd.DataFrame({"station": stn.station.to_numpy(),
                                 "sat_date": day, "sat": v})
        with ThreadPoolExecutor(4) as ex:
            got = [g for g in ex.map(one, days) if g is not None]
    finally:
        sat.GRID_CACHE = real_cache
    if not got:
        return {"evaluable": False, "passed": False}, [], []
    s = pd.concat(got, ignore_index=True).dropna(subset=["sat"])
    print(f"  V2: CHIRP read for {s.sat_date.nunique()} days")

    def joined(lag: int) -> pd.DataFrame:
        out = []
        for sid, g in s.groupby("station"):
            p = series[sid]
            k = g.sat_date + pd.Timedelta(days=lag)
            m = p.reindex(k)
            out.append(pd.DataFrame({"station": sid, "sat": g.sat.to_numpy(),
                                     "prcp": m.prcp.to_numpy(),
                                     "inferred": m.inferred.to_numpy()}))
        return pd.concat(out, ignore_index=True).dropna(subset=["prcp"])

    # The lag is fitted on filed days only.
    best = None
    for lag in (-1, 0, 1):
        j = joined(lag)
        f = j[~j.inferred.astype(bool)]
        ag = float(((f.sat >= thr) == (f.prcp >= thr)).mean()) if len(f) else 0
        if best is None or ag > best[0]:
            best = (ag, lag, j)
    _, lag, j = best
    j["inferred"] = j.inferred.astype(bool)
    om = j[j.station.isin(omit)]
    fi = j[j.station.isin(filing)]
    dry_inf = om[om.inferred & (om.prcp < thr)]
    dry_filed = fi[~fi.inferred & (fi.prcp < thr)]
    wet_filed = j[~j.inferred & (j.prcp >= thr)]
    out = {"lag": int(lag), "stations_omitting": len(omit),
           "stations_filing": len(filing),
           "inferred_dry_days": int(len(dry_inf)),
           "sat_wet_inferred_dry": _rate(dry_inf.sat >= thr),
           "sat_wet_filed_dry_ref": _rate(dry_filed.sat >= thr),
           "sat_wet_filed_wet": _rate(wet_filed.sat >= thr)}
    c = ZV_CRITERIA
    out["evaluable"] = bool(len(dry_inf) >= c["v2_min_inferred_days"]
                            and len(dry_filed) >= 200)
    out["passed"] = bool(out["evaluable"]
                         and out["sat_wet_inferred_dry"]
                         <= out["sat_wet_filed_dry_ref"]
                         + c["v2_max_excess_sat_wet"])
    rows = [{"test": "V2", "station": sid,
             "omits_zeros": sid in omit,
             "n_inferred_dry": int(((g.inferred) & (g.prcp < thr)).sum()),
             "sat_wet_inferred_dry": _rate(g[g.inferred & (g.prcp < thr)].sat
                                           >= thr)}
            for sid, g in j.groupby("station")]
    tz = stn.set_index("station").timezone
    return out, rows, [(x, tz[x]) for x in filing]


def validate_zeros() -> dict:
    from config import PROCESSED, RAIN_THRESHOLD_MM as thr

    print("=== GHCNh zero rule: validation ===")
    v3, r3, filing3 = _v3(thr)
    print(f"  V3 {json.dumps(v3, default=_json_default)}")
    v2, r2, filing2 = _v2(thr)
    print(f"  V2 {json.dumps(v2, default=_json_default)}")
    st = station_table().set_index("id")
    v1_st = {s: _tz_from_lon(float(st.loc[s, "lon"])) for s in filing3
             if s in st.index}
    v1_st.update(dict(filing2))
    v1, r1 = _v1(sorted(v1_st.items()), thr)
    print(f"  V1 {json.dumps(v1, default=_json_default)}")
    verdict = {"rule": {"max_zero_share": INFER_MAX_ZERO_SHARE,
                        "min_station_records": INFER_MIN_STATION_RECORDS,
                        "min_slot_reports": INFER_MIN_KEY_RECORDS,
                        "min_slot_share": INFER_MIN_KEY_SHARE},
               "criteria": ZV_CRITERIA, "threshold_mm": thr,
               "run": pd.Timestamp.now("UTC").strftime("%Y-%m-%d"),
               "V1": v1, "V2": v2, "V3": v3,
               "passed": bool(v1["passed"] and v2["passed"] and v3["passed"])}
    ZERO_VALIDATION.write_text(json.dumps(verdict, indent=2,
                                          default=_json_default))
    pd.DataFrame(r1 + r2 + r3).to_parquet(
        PROCESSED / "ghcnh_zero_validation.parquet", index=False)
    print(f"\n  zero rule {'PASSED' if verdict['passed'] else 'NOT passed'}"
          f" - wrote {ZERO_VALIDATION.name}")
    return verdict


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
    if "validate-zeros" in sys.argv[1:]:
        validate_zeros()
        return
    zero_on = zero_rule_on()
    print(f"zero rule: {'ON (validation passed)' if zero_on else 'off'}")

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
    probed = cands.attrs.get("probed", {})

    scr_all = screen(cands)
    scr = scr_all[scr_all.screen_coverage >= SCREEN_MIN_COVERAGE]
    # The best-covered station per city, then one city per station - the
    # capital first, so a shared airport verifies the capital.
    best = (scr.sort_values(["screen_coverage", "km"], ascending=[False, True])
               .drop_duplicates(["city", "country"])
               .sort_values(["capital", "population"], ascending=False)
               .drop_duplicates("station"))
    print(f"screen ({SCREEN_YEAR}, >= {SCREEN_MIN_COVERAGE:.0%} of days "
          f"reconstructable): {len(best)} cities in "
          f"{best.country.nunique()} countries")

    jobs = [(s, y) for s in best.station for y in YEARS]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(lambda a: prefetch(a[0], [a[1]]), jobs))

    lo, hi = pd.Timestamp(POP_ARCHIVE_START), pd.Timestamp(OBS_END)
    daily, meta = [], []
    for r in best.itertuples(index=False):
        zi = {}
        p, t, end = reconstruct_station(
            [fetch_year(r.station, y) for y in YEARS], r.timezone, info=zi)
        d = p.merge(t, on="obs_date", how="outer")
        d["inferred"] = d.inferred.fillna(False).astype(bool)
        d["station"] = PREFIX + r.station
        daily.append(d)
        w = d[(d.obs_date >= lo) & (d.obs_date <= hi)]
        wp = w[w.prcp.notna()]
        meta.append({"station": r.station, "end_min": int(end),
                     "prcp_days": int(p.prcp.notna().sum()),
                     "tmax_days": int(len(t)),
                     "pair_days": int(len(wp)),
                     "tmax_pair_days": int(w.tmax.notna().sum()),
                     "inferred_days": int(wp.inferred.sum()),
                     "omits_zeros": bool(zi.get("omits_zeros")),
                     "last_prcp": str(p.obs_date.max().date()) if len(p)
                     else ""})
    meta = pd.DataFrame(meta)
    # Task 6: the gate is on RAIN. Rain probability is what this study
    # verifies, and the daily maximum was the documented reason most cities
    # short of the gate failed it (see below). A city whose temperature record
    # is short keeps its rain verification and simply has no temperature
    # track - `capitals.track_a` skips a city without a TMAX station.
    good = meta[meta.pair_days >= MIN_PAIRS]
    keep = best.merge(good, on="station")
    keep["has_tmax"] = keep.tmax_pair_days >= MIN_PAIRS
    print(f"pairable: {len(keep)} cities in {keep.country.nunique()} countries "
          f"with {MIN_PAIRS}+ in-window rain days (the full gate); "
          f"{int((~keep.has_tmax).sum())} of them rain-only; "
          f"{int((keep.inferred_days > 0).sum())} rely in part on restored "
          f"zeros")
    short = best.merge(meta, on="station").query("station not in @keep.station")
    if len(short):
        print("  short of the gate (rain days / daily-max days): " + ", ".join(
            f"{r.city} {r.pair_days}/{r.tmax_pair_days}" for r in
            short.sort_values("pair_days", ascending=False).head(8)
            .itertuples()))
    log = stage_log(probed, cands, scr_all, best, meta, keep, MIN_PAIRS)

    cols = ["station", "obs_date", "prcp", "tmax", "tmin", "gap_h",
            "inferred"]
    out = pd.concat(daily, ignore_index=True)[cols]
    out = out[out.station.isin(PREFIX + keep.station)]
    out.sort_values(["station", "obs_date"]).reset_index(drop=True) \
       .to_parquet(DAILY, index=False)

    rows = [{"city": r.city, "country": r.country, "lat": float(r.lat),
             "lon": float(r.lon), "population": int(r.population),
             "dem": float(r.dem), "timezone": r.timezone,
             "capital": bool(r.capital),
             "prcp_station": PREFIX + r.station, "prcp_km": float(r.km),
             "prcp_elev_m": float(r.elev_m), "prcp_days": int(r.prcp_days),
             "tmax_station": PREFIX + r.station if r.has_tmax else None,
             "tmax_km": float(r.km) if r.has_tmax else None,
             "tmax_elev_m": float(r.elev_m) if r.has_tmax else None,
             "tmax_days": int(r.tmax_days) if r.has_tmax else None,
             "end_min": int(r.end_min), "pair_days": int(r.pair_days),
             "tmax_pair_days": int(r.tmax_pair_days),
             "last_prcp": r.last_prcp, "provisional": False,
             "omits_zeros": bool(r.omits_zeros),
             "inferred_days": int(r.inferred_days),
             "inferred_share": round(r.inferred_days / max(r.pair_days, 1),
                                     3),
             "screen_coverage": round(float(r.screen_coverage), 3)}
            for r in keep.itertuples(index=False)]
    CANDIDATES.write_text(json.dumps({
        "source": "NOAA GHCNh (public domain)", "base_url": BASE,
        "years": list(YEARS), "screen_year": SCREEN_YEAR,
        "screen_min_coverage": SCREEN_MIN_COVERAGE, "min_pairs": MIN_PAIRS,
        "gate": "rain pairs; temperature optional (rain-only cities carry "
                "tmax_station null)",
        "probe_per_country": PROBE_PER_COUNTRY,
        "stations_per_city": STATIONS_PER_CITY,
        "provisional": False,
        "zero_rule": zero_on,
        "cities": sorted(rows, key=lambda r: (r["country"], -r["population"])),
        "countries": log,
    }, indent=2, sort_keys=True, default=_json_default))
    print(f"\nwrote {CANDIDATES.name} ({len(rows)} cities) and {DAILY.name}")
    print("  by country: " + ", ".join(
        f"{k}:{v}" for k, v in keep.country.value_counts().items()))


if __name__ == "__main__":
    main()
