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


if __name__ == "__main__":
    extract()
