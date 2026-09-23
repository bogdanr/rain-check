"""Task 10: a second truth source, so the panel can add countries GHCN cannot.

E27 showed GHCN-Daily cannot supply a gauge outside Europe, North America and
a handful of Asian countries; E30 showed NOAA ISD has a station almost
everywhere and that in Asia and North America most of them report rain on
~90% of days. E28 fixed the currency: a first city in a new country is worth
about four more cities in a country already sampled. So this stage does one
narrow thing - it offers ISD-verified cities *only in countries GHCN cannot
reach at the study's population floor* - and it does it in a way that lets the
existing pipeline validate every one of them rather than trusting them.

Three rules shape it.

  1. **One truth source per country.** A country either has GHCN cities or ISD
     cities, never both. Mixing them inside a country would make a
     within-country difference partly a difference of instruments, and E24
     already measured the day convention alone at 0.09 Brier.

  2. **A daily total is reconstructed, never interpolated.** ISD files
     accumulation records of varying length (1, 3, 6, 12, 24 h), often several
     overlapping ones. A station-day is a 24-hour window; its total is the sum
     of a *non-overlapping* set of records lying wholly inside it, and the day
     is missing - not dry - if that set leaves more than MAX_GAP_HOURS
     uncovered. A record that straddles the window is never split, because
     splitting it would invent the timing of rain nobody recorded.

  3. **The window is the station's own, and alignment is checked downstream.**
     A Chinese synoptic station reports 12-hour totals at 00 and 12 UTC, which
     is 08:00 and 20:00 local: no window ending at local midnight can be tiled
     from those records. So each station's window ends at the reporting hour
     closest to local midnight that its records actually tile, and the day is
     labelled by the calendar date holding most of the window. That is the
     same situation as a GHCN observer reading the gauge at 07:00, and it gets
     the same treatment: `capitals.scan_offset` lines the series up against
     ERA5 and excludes any city without a clean single lag. An ISD station
     whose totals are nonsense fails that check; it does not enter the study.

Temperature is a supporting track here as everywhere: the daily maximum is the
highest *sampled* hourly reading, which sits below the true maximum by a few
tenths. `track_a` reports a debiased MAE alongside the raw one, and that
column is the comparable one for ISD cities.

Usage:
    python src/isd_truth.py          # screen, fetch, reconstruct, write
    python src/isd_truth.py check    # correctness checks only
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

import truth_sources
from config import RAW
from isd_coverage import (
    BAD_QUALITY,
    HISTORY_CACHE,
    MAX_GAP_HOURS,
    _xyz,
    active,
    covered_days,
    fetch_year,
    parse_precip,
    station_table,
)

ISD_PREFIX = truth_sources.ISD_PREFIX
CANDIDATES = RAW / "isd_candidates.json"
DAILY = RAW / "isd_daily.parquet"

# The study window, matching the GHCN rule in probe_capitals. 2026 is partial
# and that is fine: the day count below is what gates a station, not the year.
YEARS = (2024, 2025, 2026)

# Stations probed per new country, nearest-first for the largest cities. Four
# covers the per-country cap's worth of chances without reading every airport
# in a large country; the screen year decides which are worth the full window.
PROBE_PER_COUNTRY = 4

# A station must reconstruct a daily total on this share of the screen year to
# be worth downloading the rest of the window for. Set well below the 80%
# E30 called "usable" because the final gate is the day count, not this.
SCREEN_MIN_COVERAGE = 0.60
SCREEN_YEAR = 2024

# ISD AA condition codes that mean the depth is not an amount for the stated
# period: 1 measurement impossible or inaccurate; 3/4 begin/end of an
# accumulated period (the amount belongs to a longer, unstated span); 5/6
# begin/end of a deleted period; 7/8 begin/end of a missing period. Trace (2)
# is kept as a real, near-zero amount. E30's coverage probe did not filter
# these - it measured whether records exist - so the strict reading lives here
# and `isd_coverage` stays reproducible.
BAD_CONDITION = {"1", "3", "4", "5", "6", "7", "8"}

# A day's maximum needs readings spread across it, or a station reporting only
# at night returns a "maximum" hours before the afternoon peak.
MIN_TEMP_READINGS = 8


def die(msg: str) -> None:
    print(f"\nFAILED: {msg}\n")
    sys.exit(1)


def say(msg: str) -> None:
    print(f"  ok  {msg}")


# ---------------------------------------------------------------------------
# 1. Parsing
# ---------------------------------------------------------------------------
def parse_precip_strict(df: pd.DataFrame) -> pd.DataFrame:
    """`isd_coverage.parse_precip`, minus records whose amount is not for its
    stated period (see BAD_CONDITION)."""
    cols = [c for c in ("AA1", "AA2", "AA3", "AA4") if c in df.columns]
    if not cols or "DATE" not in df.columns:
        return pd.DataFrame(columns=["end", "hours", "mm"])
    keep = []
    for c in cols:
        parts = df[c].astype(str).str.split(",", expand=True)
        if parts.shape[1] < 4:
            continue
        bad = parts[2].astype(str).isin(BAD_CONDITION)
        sub = df[["DATE"]].copy()
        sub["AA1"] = df[c].where(~bad)
        keep.append(parse_precip(sub))
    # An empty slot comes back as an untyped frame, and concatenating it with
    # a real one turns the timestamps into plain objects that the day logic
    # cannot read. Only non-empty slots are joined.
    keep = [k for k in keep if len(k)]
    if not keep:
        return pd.DataFrame(columns=["end", "hours", "mm"])
    out = pd.concat(keep, ignore_index=True)
    # The same record filed in two slots is one record.
    return out.drop_duplicates().sort_values("end").reset_index(drop=True)


def parse_temp(df: pd.DataFrame) -> pd.DataFrame:
    """Hourly air temperature, C. "+0172,1" is 17.2 C at quality 1; +9999 is
    the missing sentinel and must never become a 999.9 C afternoon."""
    if "TMP" not in df.columns or "DATE" not in df.columns:
        return pd.DataFrame(columns=["when", "t"])
    parts = df["TMP"].astype(str).str.split(",", expand=True)
    if parts.shape[1] < 2:
        return pd.DataFrame(columns=["when", "t"])
    v = pd.to_numeric(parts[0], errors="coerce")
    q = parts[1].astype(str)
    when = pd.to_datetime(df["DATE"], errors="coerce", utc=True)
    ok = v.notna() & (v.abs() != 9999) & ~q.isin(BAD_QUALITY) & when.notna()
    return pd.DataFrame({"when": when[ok], "t": v[ok] / 10.0})


# ---------------------------------------------------------------------------
# 2. Reconstruction
# ---------------------------------------------------------------------------
def _local(rec: pd.DataFrame, timezone: str) -> tuple[pd.Series, pd.Series]:
    # Wall-clock local time with the offset dropped, for the reason given in
    # isd_coverage.covered_days: local midnight does not exist on DST days in
    # some zones, and tz-aware normalising throws there.
    hi = rec.end.dt.tz_convert(timezone).dt.tz_localize(None)
    lo = hi - pd.to_timedelta(rec.hours, unit="h")
    return lo, hi


def daily_totals(rec: pd.DataFrame, timezone: str, end_min: int) -> pd.DataFrame:
    """Station-day totals for windows ending `end_min` minutes after local
    midnight.

    Minutes, not hours: Tehran is UTC+3:30, Yangon +6:30 and Delhi +5:30, so a
    synoptic station there reports at half past the local hour and no
    whole-hour window can ever be tiled from its records. The first version
    took whole hours and scored Tehran, Isfahan and Mashhad at zero days.

    Window k runs [D + end, D + end + 24h). Records lying wholly
    inside a window are candidates; longest first, a record is taken if it
    does not overlap one already taken (touching is fine). Longest-first
    because the long records are the station's own summaries of the short
    ones, so preferring them leaves fewer holes where an hourly report went
    missing. The day stands if the taken records leave at most MAX_GAP_HOURS
    of the window uncovered.
    """
    cols = ["obs_date", "prcp", "gap_h"]
    if rec.empty:
        return pd.DataFrame(columns=cols)
    lo, hi = _local(rec, timezone)
    shift = pd.Timedelta(minutes=end_min)
    w0 = (lo - shift).dt.floor("D") + shift          # window start per record
    inside = hi <= w0 + pd.Timedelta(days=1)
    r = pd.DataFrame({"w0": w0, "lo": lo, "hi": hi, "hours": rec.hours.values,
                      "mm": rec.mm.values})[inside.values]
    if r.empty:
        return pd.DataFrame(columns=cols)
    r = r.sort_values(["w0", "hours", "hi"], ascending=[True, False, True])

    rows = []
    for w, g in r.groupby("w0", sort=True):
        taken: list[tuple] = []
        total = 0.0
        covered = 0.0
        for a, b, h, mm in zip(g.lo, g.hi, g.hours, g.mm):
            if any(a < tb and b > ta for ta, tb in taken):
                continue
            taken.append((a, b))
            total += mm
            covered += h
        gap = 24.0 - covered
        if gap <= MAX_GAP_HOURS:
            # Labelled by the date holding most of the window, so a 20:00-to-
            # 20:00 day is the day it mostly is, and 00:00-to-00:00 is itself.
            rows.append(((w + pd.Timedelta(hours=12)).normalize(), total, gap))
    return pd.DataFrame(rows, columns=cols)


def choose_end_hour(rec: pd.DataFrame, timezone: str) -> tuple[int, int]:
    """The window end the station's records tile best, near midnight on ties.

    Candidates are midnight plus the commonest local end times of the
    multi-hour records, which is where a station's own day boundaries are.
    Returns (minutes after local midnight, days reconstructed).
    """
    if rec.empty:
        return 0, 0
    _, hi = _local(rec, timezone)
    multi = hi[rec.hours.values >= 6]
    ends = multi.dt.hour * 60 + multi.dt.minute
    cands = {0} | set(ends.value_counts().head(3).index.tolist())
    best = None
    for m in sorted(cands):
        n = len(daily_totals(rec, timezone, m))
        key = (n, -min(m, 1440 - m))
        if best is None or key > best[0]:
            best = (key, m, n)
    return best[1], best[2]


def daily_tmax(temp: pd.DataFrame, timezone: str) -> pd.DataFrame:
    """Highest sampled reading per local calendar day, where the day was
    sampled in all four quarters and at least MIN_TEMP_READINGS times.

    Temperature is not shifted: the lag scan established that the observing
    convention moves precipitation, not the daily maximum (see
    capitals.load_ghcn_temp)."""
    cols = ["obs_date", "tmax", "tmin"]
    if temp.empty:
        return pd.DataFrame(columns=cols)
    local = temp.when.dt.tz_convert(timezone).dt.tz_localize(None)
    d = pd.DataFrame({"day": local.dt.normalize(), "q": local.dt.hour // 6,
                      "t": temp.t.values})
    g = d.groupby("day").agg(tmax=("t", "max"), tmin=("t", "min"),
                             n=("t", "size"), quarters=("q", "nunique"))
    g = g[(g.n >= MIN_TEMP_READINGS) & (g.quarters == 4)]
    return g.reset_index().rename(columns={"day": "obs_date"})[cols]


# ---------------------------------------------------------------------------
# 3. Which cities, which stations
# ---------------------------------------------------------------------------
def candidates(stations: pd.DataFrame) -> pd.DataFrame:
    """Nearest usable ISD station to the largest cities of every country the
    GHCN pool cannot reach at the floor.

    "Cannot reach" is judged on the GHCN candidate pool, not the final
    selection: a country with a single qualifying GHCN city is a GHCN country,
    and rule 1 keeps it that way.
    """
    import probe_cities
    from probe_capitals import MAX_ELEV_DIFF_M, MAX_STATION_KM

    cities = probe_cities.load_geonames()
    cities = cities[cities.population >= probe_cities.MIN_POPULATION]
    pool = probe_cities.candidate_pool()
    # Only the GHCN and GHCNh rows decide: the pool also carries this stage's
    # own output from a previous run, and counting it would make every ISD
    # country look covered on the second run and remove itself. GHCNh rows do
    # count - a country GHCNh covers at the full gate needs no provisional
    # city (truth_sources: one source per country, in priority order).
    pool = pool[pool.truth != "ISD"]
    ghcn_countries = set(pool[pool.population >= probe_cities.MIN_POPULATION]
                         .country)
    todo = cities[~cities.country.isin(ghcn_countries)].reset_index(drop=True)

    st = stations.reset_index(drop=True)
    tree = cKDTree(_xyz(st.lat, st.lon))
    d, idx = tree.query(_xyz(todo.lat, todo.lon), k=2)
    rows = []
    for i, r in todo.iterrows():
        for km, j in zip(d[i], idx[i]):
            if km > MAX_STATION_KM or j >= len(st):
                continue
            s = st.iloc[j]
            if (pd.notna(s.elev_m) and pd.notna(r.dem)
                    and abs(s.elev_m - r.dem) > MAX_ELEV_DIFF_M):
                continue
            rows.append({**r.to_dict(), "station": s.id, "km": float(km),
                         "elev_m": float(s.elev_m) if pd.notna(s.elev_m)
                         else np.nan})
            break
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = (out.sort_values("population", ascending=False)
              .drop_duplicates("station")
              .groupby("country", group_keys=False).head(PROBE_PER_COUNTRY))
    return out.reset_index(drop=True)


def _read(station: str, year: int) -> pd.DataFrame | None:
    try:
        return fetch_year(station, year)
    except Exception as e:  # network trouble costs the station, not the run
        print(f"  {station} {year}: {e}")
        return None


def screen(cands: pd.DataFrame) -> pd.DataFrame:
    """One complete year per station, measured the way E30 measured it."""
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda s: _read(s, SCREEN_YEAR), cands.station.tolist()))
    cov = []
    days = 366 if SCREEN_YEAR % 4 == 0 else 365
    for r in cands.itertuples(index=False):
        df = _read(r.station, SCREEN_YEAR)
        c = (covered_days(parse_precip_strict(df), r.timezone)["days_covered"]
             / days) if df is not None else 0.0
        cov.append(c)
    out = cands.copy()
    out["screen_coverage"] = cov
    return out


def reconstruct(passed: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Full-window daily series for every station that passed the screen."""
    jobs = [(s, y) for s in passed.station for y in YEARS]
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda a: _read(*a), jobs))

    daily, meta = [], []
    for r in passed.itertuples(index=False):
        frames = [f for f in (_read(r.station, y) for y in YEARS)
                  if f is not None]
        if not frames:
            continue
        raw = pd.concat(frames, ignore_index=True)
        rec = parse_precip_strict(raw)
        hour, _ = choose_end_hour(rec, r.timezone)
        p = daily_totals(rec, r.timezone, hour)
        t = daily_tmax(parse_temp(raw), r.timezone)
        # Either side can be empty for a station, and an empty frame carries
        # an untyped date column that refuses to merge with a real one.
        p["obs_date"] = pd.to_datetime(p.obs_date)
        t["obs_date"] = pd.to_datetime(t.obs_date)
        d = p.merge(t, on="obs_date", how="outer")
        d["station"] = ISD_PREFIX + r.station
        daily.append(d)
        meta.append({"station": r.station, "end_min": hour,
                     "prcp_days": int(p.prcp.notna().sum()) if len(p) else 0,
                     "tmax_days": int(len(t))})
    cols = ["station", "obs_date", "prcp", "tmax", "tmin", "gap_h"]
    out = (pd.concat(daily, ignore_index=True)[cols] if daily
           else pd.DataFrame(columns=cols))
    return out, pd.DataFrame(meta)


# ---------------------------------------------------------------------------
# 4. What the pipeline reads
# ---------------------------------------------------------------------------
def is_isd(station_id: str | None) -> bool:
    return bool(station_id) and str(station_id).startswith(ISD_PREFIX)


def _store() -> pd.DataFrame | None:
    return pd.read_parquet(DAILY) if DAILY.exists() else None


def load_prcp(station_id: str) -> pd.DataFrame:
    """Same shape as `capitals.load_ghcn`: ghcn_date, prcp. The column keeps
    the GHCN name so `scan_offset` and `build` need no second code path."""
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
    """ISD candidates in `probe_cities.candidate_pool`'s column layout, or an
    empty frame if this stage has not been run."""
    if not CANDIDATES.exists():
        return pd.DataFrame()
    return pd.DataFrame(json.loads(CANDIDATES.read_text())["cities"])


# ---------------------------------------------------------------------------
# 5. Correctness checks
# ---------------------------------------------------------------------------
def _rec(rows) -> pd.DataFrame:
    return pd.DataFrame([{"end": pd.Timestamp(e, tz="UTC"), "hours": float(h),
                          "mm": float(m)} for e, h, m in rows])


def run_checks() -> None:
    print("=== correctness checks ===")

    # 1. Twenty-four hourly records sum to their day, and the same day filed
    #    again as a 6-hour and a 24-hour summary is not counted three times.
    hourly = [(f"2025-03-02T{h:02d}:00:00", 1, 0.5) for h in range(1, 24)]
    hourly.append(("2025-03-03T00:00:00", 1, 0.5))
    got = daily_totals(_rec(hourly), "UTC", 0)
    if len(got) != 1 or abs(got.prcp.iloc[0] - 12.0) > 1e-9:
        die(f"24 hourly 0.5 mm records did not sum to 12 mm: {got}")
    dup = hourly + [("2025-03-02T06:00:00", 6, 3.0),
                    ("2025-03-03T00:00:00", 24, 12.0)]
    got = daily_totals(_rec(dup), "UTC", 0)
    if len(got) != 1 or abs(got.prcp.iloc[0] - 12.0) > 1e-9:
        die(f"overlapping summaries were double-counted: {got}")
    say("hourly records sum to their day, and the same rain filed again as "
        "6 h and 24 h summaries is counted once, not three times")

    # 2. A day with a hole is missing, not dry. This is the failure that would
    #    bias every base rate low: a lost afternoon of reports read as zero.
    holed = [r for r in hourly if not r[0].endswith(("T12:00:00", "T13:00:00",
                                                     "T14:00:00", "T15:00:00"))]
    if len(daily_totals(_rec(holed), "UTC", 0)):
        die("a day missing four hours of reports was scored as a total")
    say(f"a day with more than {MAX_GAP_HOURS:.0f} h unreported is dropped, "
        "so missing reports cannot pass as a dry day")

    # 3. A record straddling the window boundary is never split. A 24-hour
    #    total ending at 06:00 belongs to no midnight-to-midnight day.
    if len(daily_totals(_rec([("2025-03-03T06:00:00", 24, 9.0)]), "UTC", 0)):
        die("a record straddling midnight was credited to a midnight day")
    say("a record straddling the window is not split between two days, so "
        "no rain is given a timing nobody recorded")

    # 4. The Chinese synoptic case: 12-hour totals at 00 and 12 UTC in UTC+8
    #    tile only windows ending at 08:00 or 20:00 local. The chooser must
    #    find 20:00 (closer to midnight), and label the day it mostly is.
    syn = []
    for day in pd.date_range("2025-03-01", periods=30, freq="D"):
        syn += [((day).isoformat(), 12, 1.0),
                ((day + pd.Timedelta(hours=12)).isoformat(), 12, 2.0)]
    rec = _rec(syn)
    end, n = choose_end_hour(rec, "Asia/Shanghai")
    if end != 20 * 60 or n < 28:
        die(f"12-hourly records at 00/12 UTC in UTC+8 chose end minute {end} "
            f"with {n} days; expected 1200 (20:00) with ~29")
    first = daily_totals(rec, "Asia/Shanghai", end).iloc[0]
    if abs(first.prcp - 3.0) > 1e-9:
        die(f"a 20:00-20:00 day did not sum its two 12 h records: {first}")
    say("12-hourly synoptic totals in UTC+8 select the 20:00 local window and "
        "sum both halves of it - the station's own day is found, not assumed")

    # 4b. The same station in Tehran (UTC+3:30) reports at 03:30 and 15:30
    #     local. A whole-hour window can never tile that; the first full run
    #     scored Tehran, Isfahan and Mashhad at zero days for exactly this.
    end, n = choose_end_hour(rec, "Asia/Tehran")
    if n < 28 or end % 60 != 30:
        die(f"half-hour-offset station tiled {n} days at minute {end}; "
            f"expected ~29 at a :30 boundary")
    say("a synoptic station in a half-hour time zone tiles its days on the "
        ":30 boundary rather than scoring zero")

    # 5. Amounts whose period is not the stated one never enter a total.
    df = pd.DataFrame({"DATE": ["2025-03-01T06:00:00", "2025-03-01T07:00:00"],
                       "AA1": ["06,0120,3,1", "01,0008,9,1"]})
    got = parse_precip_strict(df)
    if len(got) != 1 or abs(got.mm.iloc[0] - 0.8) > 1e-9:
        die(f"a begin-accumulated record was accepted: {got}")
    say("records flagged as part of an accumulated, deleted or missing period "
        "are dropped, so a multi-day sum cannot pose as one period's rain")

    # 5b. A station whose second slot holds nothing usable must still parse to
    #     real timestamps. The first full run died here, on a live station.
    df = pd.DataFrame({"DATE": ["2025-03-01T06:00:00"],
                       "AA1": ["01,0008,9,1"], "AA2": ["99,9999,9,9"]})
    got = parse_precip_strict(df)
    if not pd.api.types.is_datetime64_any_dtype(got.end):
        die(f"an empty second slot turned timestamps into {got.end.dtype}")
    covered_days(got, "UTC")
    say("an empty AA2 alongside a real AA1 keeps timestamps typed, so one "
        "silent slot cannot take down the day logic for the whole station")

    # 6. Temperature: the sentinel is not a heatwave, and a day sampled only
    #    at night has no maximum.
    stamps = pd.date_range("2025-07-01", periods=24, freq="h").strftime(
        "%Y-%m-%dT%H:%M:%S")
    vals = [f"+{200 + h * 5:04d},1" for h in range(24)]
    vals[10] = "+9999,9"
    t = parse_temp(pd.DataFrame({"DATE": stamps, "TMP": vals}))
    mx = daily_tmax(t, "UTC")
    if len(mx) != 1 or abs(mx.tmax.iloc[0] - 31.5) > 1e-9:
        die(f"daily max was not the highest valid reading: {mx}")
    night = t[t.when.dt.hour < 6]
    if len(daily_tmax(night, "UTC")):
        die("a day sampled only overnight was given a daily maximum")
    say("+9999 is dropped rather than read as 999.9 C, and a day sampled in "
        "one quarter only gets no maximum")

    # 7. The loaders speak the GHCN loaders' shape, so capitals.build and
    #    scan_offset see no difference in kind between the two sources.
    fake = pd.DataFrame({"station": ["ISD:x"] * 2,
                         "obs_date": pd.to_datetime(["2025-01-01",
                                                     "2025-01-02"]),
                         "prcp": [1.0, np.nan], "tmax": [5.0, 6.0],
                         "tmin": [1.0, 7.0], "gap_h": [0.0, 0.0]})
    global _store
    real = _store
    _store = lambda: fake  # noqa: E731
    try:
        p, tt = load_prcp("ISD:x"), load_temp("ISD:x")
    finally:
        _store = real
    if list(p.columns) != ["ghcn_date", "prcp"] or len(p) != 1:
        die(f"load_prcp shape is not load_ghcn's: {p}")
    if list(tt.columns) != ["local_date", "obs_tmax", "obs_tmin", "qc_fail"] \
            or tt.qc_fail.tolist() != [False, True]:
        die(f"load_temp shape or QC flag wrong: {tt}")
    say("loaders return the GHCN loaders' columns and the tmax<tmin QC flag, "
        "so ISD cities flow through the same checks as every other city")
    print()


# ---------------------------------------------------------------------------
def main() -> None:
    run_checks()
    if "check" in sys.argv[1:]:
        return

    from config import OBS_END
    from constants import POP_ARCHIVE_START

    stations = station_table()
    # station_table() drops elevation; the elevation rule needs it back.
    hist = pd.read_csv(HISTORY_CACHE, dtype=str)
    hist.columns = [c.strip() for c in hist.columns]
    elev = pd.Series(pd.to_numeric(hist["ELEV(M)"], errors="coerce").values,
                     index=hist.USAF.str.zfill(6) + hist.WBAN.str.zfill(5))
    elev = elev[~elev.index.duplicated()]
    stations["elev_m"] = stations.id.map(elev)
    act, horizon = active(stations)
    print(f"ISD: {len(act):,} active stations (history to "
          f"{pd.Timestamp(horizon).date()})")

    cands = candidates(act)
    print(f"candidates: {len(cands)} stations in {cands.country.nunique()} "
          f"countries GHCN cannot reach")
    scr = screen(cands)
    passed = scr[scr.screen_coverage >= SCREEN_MIN_COVERAGE]
    print(f"screen ({SCREEN_YEAR}, >= {SCREEN_MIN_COVERAGE:.0%} of days "
          f"reconstructable): {len(passed)} stations in "
          f"{passed.country.nunique()} countries")

    daily, meta = reconstruct(passed)
    if daily.empty:
        die("no station produced a daily series")
    # The gate is the one capitals.build enforces for this source: ISD cities
    # are provisional and meet MIN_PAIRS_PROVISIONAL (see truth_sources), days
    # counted inside the window the forecast archive covers. Counting days
    # outside it (the first version did) admits stations that are then
    # rejected downstream after their forecasts have been fetched for nothing.
    gate = truth_sources.MIN_PAIRS_PROVISIONAL
    lo, hi = pd.Timestamp(POP_ARCHIVE_START), pd.Timestamp(OBS_END)
    inwin = daily[(daily.obs_date >= lo) & (daily.obs_date <= hi)]
    n_p = inwin[inwin.prcp.notna()].groupby("station").size()
    n_t = inwin[inwin.tmax.notna()].groupby("station").size()
    meta["pair_days"] = (ISD_PREFIX + meta.station).map(n_p).fillna(0).astype(int)
    meta["tmax_pair_days"] = (ISD_PREFIX + meta.station).map(n_t).fillna(0) \
        .astype(int)
    last = daily.obs_date.max()
    print(f"ISD data ends {last.date()}; the forecast archive starts "
          f"{lo.date()}, so at most {(min(last, hi) - lo).days + 1} days can "
          f"pair (the provisional gate is {gate})")
    good = meta[(meta.pair_days >= gate) & (meta.tmax_pair_days >= gate)]
    keep = passed.merge(good, on="station")
    print(f"pairable: {len(keep)} stations in {keep.country.nunique()} "
          f"countries with {gate}+ pairable days of both")
    best = meta.sort_values("pair_days", ascending=False).head(5)
    print("  closest: " + ", ".join(
        f"{passed.set_index('station').city.get(s, s)} {n}"
        for s, n in zip(best.station, best.pair_days)))

    daily = daily[daily.station.isin(ISD_PREFIX + keep.station)]
    daily = daily.sort_values(["station", "obs_date"]).reset_index(drop=True)
    daily.to_parquet(DAILY, index=False)

    rows = [{"city": r.city, "country": r.country, "lat": float(r.lat),
             "lon": float(r.lon), "population": int(r.population),
             "dem": float(r.dem), "timezone": r.timezone,
             "prcp_station": ISD_PREFIX + r.station, "prcp_km": float(r.km),
             "prcp_elev_m": float(r.elev_m), "prcp_days": int(r.prcp_days),
             "tmax_station": ISD_PREFIX + r.station, "tmax_km": float(r.km),
             "tmax_elev_m": float(r.elev_m), "tmax_days": int(r.tmax_days),
             "end_min": int(r.end_min), "pair_days": int(r.pair_days),
             "provisional": True,
             "screen_coverage": round(float(r.screen_coverage), 3)}
            for r in keep.itertuples(index=False)]
    CANDIDATES.write_text(json.dumps({
        "horizon": str(pd.Timestamp(horizon).date()),
        "data_end": str(last.date()),
        "min_pairs": gate, "provisional": True,
        "years": list(YEARS), "screen_year": SCREEN_YEAR,
        "screen_min_coverage": SCREEN_MIN_COVERAGE,
        "max_gap_hours": MAX_GAP_HOURS,
        "cities": sorted(rows, key=lambda r: (r["country"], -r["population"])),
    }, indent=2, sort_keys=True))
    print(f"\nwrote {CANDIDATES.name} ({len(rows)} cities) and {DAILY.name}")
    print("  by country: " + ", ".join(
        f"{k}:{v}" for k, v in keep.country.value_counts().items()))


if __name__ == "__main__":
    main()
