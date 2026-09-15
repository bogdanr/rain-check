"""Task 41: read daily observations for many stations without many downloads.

The capitals pipeline fetched one CSV per station from NCEI's per-station
endpoint. Each is the station's *entire* history - 3 to 8 MB for two years of
useful data. That is fine for 15 cities and impossible for hundreds: the 3,930
distinct gauges behind the qualifying city pool would be roughly 16 GB of
downloads to extract about 30 MB of relevant rows.

GHCN publishes the same data sliced the other way, one file per year containing
every station on Earth. Three files cover the study window and total 422 MB:

    2024.csv.gz  168 MB      2025.csv.gz  158 MB      2026.csv.gz   96 MB

So the cost of adding a city drops from "another multi-megabyte download" to
zero. This module extracts the elements we need, for the stations we selected,
into a single parquet.

One substantive difference from the per-station path, not just a performance
one: these files carry GHCN's quality-control flag, and rows that failed QC are
dropped here. The per-station CSVs were taken as given.

Usage:  python src/ghcn_bulk.py
"""

from __future__ import annotations

import gzip
import sys

import pandas as pd

from config import GHCN_SCALE, RAW

YEAR_DIR = RAW / "ghcn_year"
YEARS = [2024, 2025, 2026]
BASE = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/by_year/{year}.csv.gz"

ELEMENTS = ["PRCP", "TMAX", "TMIN"]
COLUMNS = ["id", "date", "element", "value", "m_flag", "q_flag", "s_flag",
           "obs_time"]
CHUNK = 2_000_000

STORE = RAW / "ghcn_bulk.parquet"
CENSUS = RAW / "ghcn_census.parquet"


def ensure_downloaded() -> None:
    import requests
    YEAR_DIR.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        path = YEAR_DIR / f"{year}.csv.gz"
        if path.exists():
            continue
        print(f"  downloading {year}.csv.gz ...")
        with requests.get(BASE.format(year=year), stream=True, timeout=600) as r:
            r.raise_for_status()
            with path.open("wb") as fh:
                for block in r.iter_content(chunk_size=1 << 20):
                    fh.write(block)


def wanted_stations() -> set[str]:
    """Every gauge referenced by either coverage file."""
    import json
    from config import CAPITAL_COVERAGE

    ids: set[str] = set()
    for path in (CAPITAL_COVERAGE, RAW / "city_coverage.json"):
        if not path.exists():
            continue
        payload = json.loads(path.read_text())
        for v in payload["included"].values():
            for key in ("prcp_station", "tmax_station"):
                if v.get(key):
                    ids.add(v[key])
    return ids


def extract(ids: set[str] | None = None) -> pd.DataFrame:
    ensure_downloaded()
    ids = ids or wanted_stations()
    print(f"  extracting {len(ids)} stations x {len(ELEMENTS)} elements")

    keep = []
    for year in YEARS:
        path = YEAR_DIR / f"{year}.csv.gz"
        rows = 0
        with gzip.open(path, "rt") as fh:
            for chunk in pd.read_csv(fh, names=COLUMNS, header=None,
                                     usecols=[0, 1, 2, 3, 5],
                                     dtype={"id": str, "date": str,
                                            "element": str, "value": float,
                                            "q_flag": str},
                                     chunksize=CHUNK, low_memory=False):
                sel = chunk[chunk.id.isin(ids) & chunk.element.isin(ELEMENTS)]
                # A non-blank quality flag means the value failed one of GHCN's
                # consistency checks. Keeping those would import known-bad data.
                sel = sel[sel.q_flag.isna()]
                if len(sel):
                    keep.append(sel.drop(columns="q_flag"))
                    rows += len(sel)
        print(f"    {year}: {rows:,} rows kept")

    long = pd.concat(keep, ignore_index=True)
    wide = long.pivot_table(index=["id", "date"], columns="element",
                            values="value", aggfunc="first").reset_index()
    wide["ghcn_date"] = pd.to_datetime(wide["date"], format="%Y%m%d")
    for el in ELEMENTS:
        if el not in wide:
            wide[el] = pd.NA
    out = pd.DataFrame({
        "station": wide["id"],
        "ghcn_date": wide["ghcn_date"],
        "prcp": wide["PRCP"] / GHCN_SCALE,
        "tmax": wide["TMAX"] / GHCN_SCALE,
        "tmin": wide["TMIN"] / GHCN_SCALE,
    }).sort_values(["station", "ghcn_date"])

    out.to_parquet(STORE, index=False)
    print(f"\nwrote {STORE}  rows={len(out):,}  stations={out.station.nunique()}")
    print(f"  span {out.ghcn_date.min().date()} .. {out.ghcn_date.max().date()}")
    return out


def load(station: str, store: pd.DataFrame | None = None) -> pd.DataFrame:
    """Daily record for one station from the bulk store, or empty if absent."""
    if store is None:
        if not STORE.exists():
            return pd.DataFrame()
        store = pd.read_parquet(STORE)
    return store[store.station == station].copy()


# --------------------------------------------------------------------------
# Station census: how many days each gauge actually reports
# --------------------------------------------------------------------------
# The GHCN inventory records only the first and last year a station reported an
# element. That is a much weaker statement than it looks: a gauge listed as
# covering 2024-2026 may have filed 37 days in the whole window. Every city
# dropped for "too few usable pairs" was dropped for exactly this reason, and
# the probe could not see it coming, because the fact is not in the inventory.
#
# It is, however, already on disk: the per-year files hold every station on
# Earth, so counting reporting days per station costs one pass and no network.
# With that count the probe can require a gauge that reports, not merely one
# that exists.
def census(force: bool = False) -> pd.DataFrame:
    """Reporting days per station within the evaluation window, all stations."""
    if CENSUS.exists() and not force:
        return pd.read_parquet(CENSUS)

    from config import OBS_END
    from constants import POP_ARCHIVE_START

    lo = POP_ARCHIVE_START.replace("-", "")
    hi = OBS_END.replace("-", "")
    ensure_downloaded()
    print(f"  counting reporting days per station over {lo}..{hi}")

    counts: dict[str, pd.Series] = {}
    for year in YEARS:
        path = YEAR_DIR / f"{year}.csv.gz"
        with gzip.open(path, "rt") as fh:
            for chunk in pd.read_csv(fh, names=COLUMNS, header=None,
                                     usecols=[0, 1, 2, 5],
                                     dtype={"id": str, "date": str,
                                            "element": str, "q_flag": str},
                                     chunksize=CHUNK, low_memory=False):
                sel = chunk[chunk.q_flag.isna()
                            & (chunk.date >= lo) & (chunk.date <= hi)]
                for elem in ("PRCP", "TMAX"):
                    part = sel.id[sel.element == elem].value_counts()
                    if not len(part):
                        continue
                    counts[elem] = (part if elem not in counts
                                    else counts[elem].add(part, fill_value=0))
        print(f"    {year}: "
              + ", ".join(f"{e} {len(counts.get(e, ())):,}" for e in
                          ("PRCP", "TMAX")) + " stations so far")

    out = pd.DataFrame({
        "prcp_days": counts.get("PRCP", pd.Series(dtype=float)),
        "tmax_days": counts.get("TMAX", pd.Series(dtype=float)),
    }).fillna(0).astype(int)
    out = out.rename_axis("station").reset_index()
    out.to_parquet(CENSUS, index=False)
    print(f"\nwrote {CENSUS}  stations={len(out):,}")
    return out


def dense_stations(min_days: int) -> tuple[set[str], set[str]]:
    """Station ids reporting at least `min_days` of PRCP / of TMAX."""
    c = census()
    return (set(c.station[c.prcp_days >= min_days]),
            set(c.station[c.tmax_days >= min_days]))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "census":
        census(force=True)
    else:
        extract()
