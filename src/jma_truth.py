"""Japan's truth source: the Japan Meteorological Agency's own station records.

Why Japan needs one (2026-09-23). Japan's GHCN-Daily gauges file dry days as
zeros and are otherwise sound, but every Japanese record in the GHCN-Daily
year files stops on 2025-08-31 and nothing arrives for 2026, so no Japanese
city reaches the 500-pair gate (the closest, Takamatsu, stops at 495). GHCNh
is no substitute: Tokyo Haneda's hourly record carries no precipitation
group at all. JMA publishes the same observatories' daily values itself.

What is read. The "past weather data" pages (過去の気象データ検索, etrn), one
page per station per month, for the ~150 surface observatories (気象台 and
測候所, block numbers 47xxx - the WMO numbers, the same stations GHCN-Daily
carries as JA0000 47xxx). The daily precipitation total is the 00-24 JST sum
of the station's own gauge; the daily maximum and minimum are the station's.
AMeDAS points are not used: every city this study can take has an
observatory, and the observatories are the ones with full QC and a long
history.

Quality marks, as JMA documents them (etrn 値の説明):
    --        the phenomenon did not occur: precipitation 0 (a real zero)
    )         quasi-normal value (under 20% of the day's data missing): kept
    ]         insufficient data: dropped
    x / ×     missing: dropped
    ///       not observed: dropped
    #         doubtful: dropped
    blank     no data: dropped
So a dry day is filed as a dry day and the rule "missing is not dry" holds
without any inference.

Licence. JMA website content is published under the Public Data License
(公共データ利用規約) version 1.0, which the Government of Japan states is
compatible with CC BY 4.0; the required credit is "出典：気象庁ホームページ"
with the page URL (see provenance.py). The site has no robots.txt; requests
are cached, sequential and spaced by DELAY_S.

Usage:
    python src/jma_truth.py          # fetch, parse, write
    python src/jma_truth.py check    # correctness checks only
"""

from __future__ import annotations

import html as htmllib
import json
import re
import sys
import time

import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree

import truth_sources
from config import CACHE, OBS_END, RAW
from constants import POP_ARCHIVE_START
from isd_coverage import _xyz

BASE = "https://www.data.jma.go.jp/stats/etrn"
PREF_URL = BASE + "/select/prefecture.php?prec_no={p}"
DAILY_URL = (BASE + "/view/daily_s1.php?prec_no={p}&block_no={b}"
             "&year={y}&month={m}&day=&view=")
TERMS_URL = "https://www.jma.go.jp/jma/kishou/info/coment.html"

PREFIX = truth_sources.JMA_PREFIX
COUNTRY = "JP"
TIMEZONE = "Asia/Tokyo"
CACHE_DIR = CACHE / "jma"
CANDIDATES = RAW / "jma_candidates.json"
DAILY = RAW / "jma_daily.parquet"

# JMA's prefecture codes (prec_no) lie in 11..99; codes with no stations
# return a page without any, and are cached as such.
PREC_NOS = range(11, 100)
# Cities tried: the capital and the largest others. One city per station.
PROBE_CITIES = 16
DELAY_S = 1.0

UA = {"User-Agent": "rain-check research (forecast verification study)"}


def die(msg: str) -> None:
    print(f"\nFAILED: {msg}\n")
    sys.exit(1)


def say(msg: str) -> None:
    print(f"  ok  {msg}")


# ---------------------------------------------------------------------------
# 1. Downloads (cached)
# ---------------------------------------------------------------------------
_last = [0.0]


def _get(url: str, path, refresh: bool = False) -> str:
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        wait = DELAY_S - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(url, headers=UA, timeout=120)
            _last[0] = time.time()
            r.raise_for_status()
            r.encoding = "utf-8"
            tmp = path.with_suffix(".part")
            tmp.write_text(r.text, encoding="utf-8")
            tmp.replace(path)
            return r.text
        except requests.RequestException as e:
            if attempt == 3:
                print(f"  {url}: {e}")
                return ""
            time.sleep(5 * (attempt + 1))
    return ""


# ---------------------------------------------------------------------------
# 2. Parsing
# ---------------------------------------------------------------------------
_VIEWPOINT = re.compile(r"viewPoint\(([^)]*)\)")


def parse_stations(text: str, prec_no: int) -> list[dict]:
    """Stations from a prefecture page's viewPoint(...) calls: kind ('s'
    observatory, 'a' AMeDAS), block number, name, position in degrees and
    minutes, height, and the closing date (9999 = still open)."""
    out = []
    for args in _VIEWPOINT.findall(text):
        f = [a.strip().strip("'") for a in args.split(",")]
        if len(f) < 18 or f[0] not in ("s", "a"):
            continue
        try:
            lat = float(f[4]) + float(f[5]) / 60
            lon = float(f[6]) + float(f[7]) / 60
            elev = float(f[8])
        except ValueError:
            continue
        out.append({"prec_no": prec_no, "kind": f[0], "block_no": f[1],
                    "name": f[2], "kana": f[3], "lat": lat, "lon": lon,
                    "elev_m": elev, "active": f[15] == "9999"})
    return out


def station_table() -> pd.DataFrame:
    rows = []
    for p in PREC_NOS:
        text = _get(PREF_URL.format(p=p), CACHE_DIR / "pref" / f"{p}.html")
        rows += parse_stations(text, p)
    st = pd.DataFrame(rows)
    if st.empty:
        return st
    return st.drop_duplicates(["kind", "block_no"]).reset_index(drop=True)


_ROW = re.compile(r"<tr class=\"mtx\"[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")


def value(cell: str, zero_dash: bool) -> float:
    """A JMA table cell as a number, or NaN if its mark disqualifies it."""
    s = htmllib.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
    s = s.replace("\u3000", "").replace(" ", "")
    if not s or "]" in s or "#" in s or "×" in s or "x" in s.lower() \
            or "///" in s:
        return np.nan
    s = s.rstrip(")")
    if s == "--":
        return 0.0 if zero_dash else np.nan
    return float(s) if _NUM.match(s) else np.nan


def parse_month(text: str, year: int, month: int) -> pd.DataFrame:
    """daily_s1 rows: day, local and sea-level pressure, precipitation
    (total, max 1 h, max 10 min), temperature (mean, max, min), ..."""
    cols = ["obs_date", "prcp", "tmax", "tmin"]
    rows = []
    for tr in _ROW.findall(text):
        cells = _CELL.findall(tr)
        if len(cells) < 9:
            continue
        day = re.sub(r"<[^>]+>", "", cells[0]).strip()
        if not day.isdigit():
            continue
        try:
            date = pd.Timestamp(year=year, month=month, day=int(day))
        except ValueError:
            continue
        rows.append((date, value(cells[3], True), value(cells[7], False),
                     value(cells[8], False)))
    return pd.DataFrame(rows, columns=cols)


def header_ok(text: str) -> bool:
    """The column layout this parser assumes: pressure, then precipitation,
    then temperature, in the header of a daily_s1 page."""
    t = re.sub(r"<[^>]+>", " ", text)
    i, j, k = t.find("気圧"), t.find("降水量"), t.find("気温")
    return 0 <= i < j < k


def months() -> list[tuple[int, int]]:
    lo, hi = pd.Timestamp(POP_ARCHIVE_START), pd.Timestamp(OBS_END)
    return [(d.year, d.month) for d in
            pd.date_range(lo.replace(day=1), hi, freq="MS")]


def fetch_station(prec_no: int, block_no: str) -> pd.DataFrame:
    frames = []
    for y, m in months():
        path = CACHE_DIR / "daily" / block_no / f"{y}{m:02d}.html"
        # A month still open when fetched is fetched again next time.
        now = pd.Timestamp.now()
        refresh = path.exists() and (y, m) >= (now.year, now.month - 1)
        text = _get(DAILY_URL.format(p=prec_no, b=block_no, y=y, m=m), path,
                    refresh=refresh)
        if not text:
            continue
        if not header_ok(text):
            die(f"JMA page layout changed for {block_no} {y}-{m:02d}")
        frames.append(parse_month(text, y, m))
    if not frames:
        return pd.DataFrame(columns=["obs_date", "prcp", "tmax", "tmin"])
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# 3. Which cities
# ---------------------------------------------------------------------------
def candidates(st: pd.DataFrame) -> pd.DataFrame:
    import probe_cities
    from probe_capitals import MAX_ELEV_DIFF_M, MAX_STATION_KM

    cities = probe_cities.load_geonames()
    cities = cities[(cities.country == COUNTRY)
                    & ((cities.population >= probe_cities.MIN_POPULATION)
                       | cities.capital)]
    cities = (cities.sort_values(["capital", "population"], ascending=False)
                    .head(PROBE_CITIES).reset_index(drop=True))
    obs = st[(st.kind == "s") & st.active].reset_index(drop=True)
    if obs.empty or cities.empty:
        return pd.DataFrame()
    tree = cKDTree(_xyz(obs.lat, obs.lon))
    d, idx = tree.query(_xyz(cities.lat, cities.lon), k=min(3, len(obs)))
    rows = []
    for i, r in cities.iterrows():
        for km, j in zip(np.atleast_1d(d[i]), np.atleast_1d(idx[i])):
            if km > MAX_STATION_KM:
                break
            s = obs.iloc[j]
            if pd.notna(r.dem) and abs(s.elev_m - r.dem) > MAX_ELEV_DIFF_M:
                continue
            rows.append({**r.to_dict(), "prec_no": int(s.prec_no),
                         # s.name would be the row label, not the column.
                         "block_no": s.block_no, "station_name": s["name"],
                         "km": float(km), "elev_m": float(s.elev_m)})
            break
    out = pd.DataFrame(rows)
    # One city per station, the capital first.
    return out.drop_duplicates("block_no").reset_index(drop=True) \
        if len(out) else out


# ---------------------------------------------------------------------------
# 4. What the pipeline reads
# ---------------------------------------------------------------------------
def is_jma(station_id: str | None) -> bool:
    return truth_sources.source_of(station_id) == "JMA"


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


def country_log() -> dict:
    if not CANDIDATES.exists():
        return {}
    return json.loads(CANDIDATES.read_text()).get("countries", {})


# ---------------------------------------------------------------------------
# 5. Correctness checks
# ---------------------------------------------------------------------------
def run_checks() -> None:
    print("=== JMA correctness checks ===")

    # 1. The quality marks.
    cases = [("--", True, 0.0), ("--", False, None), ("12.5", True, 12.5),
             ("3.0 )", True, 3.0), ("7.5]", True, None), ("×", True, None),
             ("///", True, None), ("4.0#", True, None), ("", True, None),
             ("<span>0.5</span>", True, 0.5), ("-1.2", False, -1.2)]
    for cell, zd, want in cases:
        got = value(cell, zd)
        if (want is None and not np.isnan(got)) or \
                (want is not None and got != want):
            die(f"cell {cell!r}: got {got}, wanted {want}")
    say("'--' is a real zero for precipitation; quasi-normal ')' is kept; "
        "insufficient ']', missing, not observed and doubtful are dropped")

    # 2. A daily_s1 row, laid out as the live page lays it out.
    row = ("<tr class=\"mtx\" style=\"text-align:right;\">"
           + "".join(f"<td class=\"data_0_0\">{v}</td>" for v in
                     ["<div class=\"a_print\"><a href=\"x\">3</a></div>",
                      "1008.7", "1011.4", "15.5", "6.0", "2.5", "25.1",
                      "29.9 )", "21.0", "80"]) + "</tr>")
    head = "<th>気圧(hPa)</th><th>降水量(mm)</th><th>気温(℃)</th>"
    m = parse_month(head + row, 2025, 9)
    if len(m) != 1 or m.obs_date[0] != pd.Timestamp("2025-09-03") or \
            (m.prcp[0], m.tmax[0], m.tmin[0]) != (15.5, 29.9, 21.0):
        die(f"daily_s1 row misread: {m}")
    if not header_ok(head) or header_ok("<th>気温</th><th>降水量</th>"):
        die("header layout test wrong")
    say("daily_s1: day, then precipitation total in column 4 and max/min "
        "temperature in columns 8-9, behind a header check that aborts on a "
        "layout change")

    # 3. Station list.
    page = ("viewPoint('s','47662','東京','トウキョウ','35','41.5','139','45.0',"
            "'25.2','1','1','1','1','1','1','9999','99','99','','','','','')")
    st = parse_stations(page, 44)
    if len(st) != 1 or st[0]["block_no"] != "47662" or not st[0]["active"] \
            or abs(st[0]["lat"] - 35.6917) > 1e-3:
        die(f"station list misread: {st}")
    say("prefecture page stations read with degrees+minutes positions")

    # 4. Loader shape.
    global _store
    fake = pd.DataFrame({"station": [PREFIX + "x"] * 2,
                         "obs_date": pd.to_datetime(["2025-01-01",
                                                     "2025-01-02"]),
                         "prcp": [0.0, np.nan], "tmax": [5.0, 6.0],
                         "tmin": [1.0, 7.0]})
    real = _store
    _store = lambda: fake  # noqa: E731
    try:
        pp, tt = load_prcp(PREFIX + "x"), load_temp(PREFIX + "x")
    finally:
        _store = real
    if list(pp.columns) != ["ghcn_date", "prcp"] or len(pp) != 1 \
            or tt.qc_fail.tolist() != [False, True]:
        die(f"loader shapes wrong: {pp} {tt}")
    if truth_sources.load_prcp(PREFIX + "missing") is None:
        die("truth_sources does not dispatch JMA ids here")
    say("loaders speak the GHCN loaders' shape and truth_sources routes JMA: "
        "ids to them")
    print()


# ---------------------------------------------------------------------------
# 6. Cross-check against GHCN-Daily's copy of the same observatories
# ---------------------------------------------------------------------------
# GHCN-Daily carries these observatories as JA000 + block number until its
# Japanese feed stopped (2025-08-31). Over the overlap both should be the same
# gauge's 00-24 JST total, so wet/dry must agree almost always and amounts
# closely; a disagreement means the parser or the day definition is wrong.
XCHECK_MIN_AGREE = 0.97
XCHECK = RAW / "jma_crosscheck.json"


def crosscheck(stations: list[str]) -> dict:
    """Judged on GHCN-Daily values whose source flag is not S. Source S is
    the Global Summary of the Day, recomputed from SYNOP messages on its own
    day boundary: against it even a correct JST record agrees only ~93% on
    wet/dry (Tokyo's GHCN record is S only, 77%). The other GHCN-Daily
    values for these stations are JMA's own daily reports (source 2), the
    same numbers this module reads, so they are the test of the parser and
    the day definition. S agreement is reported beside it."""
    import ghcnh_truth
    from config import RAIN_THRESHOLD_MM as thr

    ids = {"JA0000" + s.removeprefix(PREFIX): s for s in stations}
    g = ghcnh_truth._ghcn_prcp_with_source(set(ids))
    if g.empty:
        return {"evaluable": False}
    val = "ghcn"
    g = pd.DataFrame({"station": g.station.map(ids), "obs_date": g.ghcn_date,
                      val: g.prcp, "s_flag": g.s_flag.fillna("").str.strip()})
    j_all = _store().merge(g, on=["station", "obs_date"]).dropna(
        subset=["prcp", val])
    gsod = j_all[j_all.s_flag == "S"]
    j = j_all[j_all.s_flag != "S"]
    if j.empty:
        return {"evaluable": False}
    per = {s: {"days": int(len(x)),
               "wet_agree": float(((x.prcp >= thr) == (x[val] >= thr)).mean()),
               "mae_mm": float((x.prcp - x[val]).abs().mean())}
           for s, x in j.groupby("station")}
    agree = float(((j.prcp >= thr) == (j[val] >= thr)).mean())
    return {"evaluable": len(j) >= 500, "days": int(len(j)),
            "stations": len(per), "wet_agree": agree,
            "exact_share": float(((j.prcp - j[val]).abs() < 0.05).mean()),
            "mae_mm": float((j.prcp - j[val]).abs().mean()),
            "overlap": [str(j.obs_date.min().date()),
                        str(j.obs_date.max().date())],
            "gsod_days": int(len(gsod)),
            "gsod_wet_agree": float(((gsod.prcp >= thr)
                                     == (gsod[val] >= thr)).mean())
            if len(gsod) else None,
            "passed": agree >= XCHECK_MIN_AGREE, "per_station": per}


# ---------------------------------------------------------------------------
def main() -> None:
    run_checks()
    if "check" in sys.argv[1:]:
        return
    from probe_capitals import MIN_PAIRS

    st = station_table()
    if st.empty:
        die("no JMA stations read")
    obs = st[(st.kind == "s") & st.active]
    print(f"JMA: {len(st):,} stations listed, {len(obs)} open observatories")
    cands = candidates(st)
    print(f"candidates: {len(cands)} cities with an observatory within range")

    lo, hi = pd.Timestamp(POP_ARCHIVE_START), pd.Timestamp(OBS_END)
    daily, rows = [], []
    for r in cands.itertuples(index=False):
        d = fetch_station(r.prec_no, r.block_no)
        d["station"] = PREFIX + r.block_no
        daily.append(d)
        w = d[(d.obs_date >= lo) & (d.obs_date <= hi)]
        pair = int(w.prcp.notna().sum())
        tpair = int(w.tmax.notna().sum())
        wet = float((w.prcp >= 0.2).mean()) if pair else float("nan")
        print(f"  {r.city:14s} {r.station_name:6s} {r.block_no} "
              f"{r.km:4.1f} km  rain days {pair}  tmax days {tpair}  "
              f"wet {wet:.2f}")
        if pair < MIN_PAIRS:
            continue
        has_t = tpair >= MIN_PAIRS
        sid = PREFIX + r.block_no
        rows.append({
            "city": r.city, "country": r.country, "lat": float(r.lat),
            "lon": float(r.lon), "population": int(r.population),
            "dem": float(r.dem), "timezone": r.timezone,
            "capital": bool(r.capital), "prcp_station": sid,
            "prcp_km": r.km, "prcp_elev_m": r.elev_m,
            "prcp_days": int(d.prcp.notna().sum()),
            "tmax_station": sid if has_t else None,
            "tmax_km": r.km if has_t else None,
            "tmax_elev_m": r.elev_m if has_t else None,
            "tmax_days": int(d.tmax.notna().sum()) if has_t else None,
            "pair_days": pair, "tmax_pair_days": tpair,
            "last_prcp": str(d[d.prcp.notna()].obs_date.max().date()),
            "provisional": False, "station_name": r.station_name})
    out = pd.concat(daily, ignore_index=True) if daily else pd.DataFrame()
    keep = {r["prcp_station"] for r in rows}
    if len(out):
        out = out[out.station.isin(keep)]
        out.sort_values(["station", "obs_date"]).reset_index(drop=True) \
           .to_parquet(DAILY, index=False)
    log = {COUNTRY: {
        "cities_probed": int(len(cands)), "included": len(rows),
        "best_pairs": max([r["pair_days"] for r in rows] or [0]),
        "step": f"{len(rows)} cities pass" if rows else
        "no observatory record reaches the gate"}}
    CANDIDATES.write_text(json.dumps({
        "source": "Japan Meteorological Agency, past weather data (etrn)",
        "licence": "Public Data License 1.0 (compatible with CC BY 4.0)",
        "attribution": "出典：気象庁ホームページ",
        "terms_url": TERMS_URL, "base_url": BASE, "min_pairs": MIN_PAIRS,
        "cities": sorted(rows, key=lambda r: -r["population"]),
        "countries": log}, indent=2, ensure_ascii=False, sort_keys=True))
    print(f"\nwrote {CANDIDATES.name} ({len(rows)} cities) and {DAILY.name}")

    xc = crosscheck(sorted(keep))
    XCHECK.write_text(json.dumps(xc, indent=2, ensure_ascii=False))
    print(f"cross-check vs GHCN-Daily: " + json.dumps(
        {k: v for k, v in xc.items() if k != "per_station"}))
    if xc.get("evaluable") and not xc["passed"]:
        CANDIDATES.unlink()
        die(f"JMA disagrees with GHCN-Daily on the same gauges "
            f"({xc['wet_agree']:.3f} < {XCHECK_MIN_AGREE}); candidates withdrawn")


if __name__ == "__main__":
    main()
