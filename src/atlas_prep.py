"""Reduce two raw inputs of the site to the small tables the build reads.

The site build (src/atlas.py) runs in CI from committed artefacts only, and
two of its inputs are raw files too large to commit:

    data/raw/cities15000.txt   GeoNames, 8 MB   -> one capital per country
    data/raw/hygdata_v41.csv   HYG v4.1, 34 MB  -> stars to magnitude 7.5

This step keeps only what the page draws, in data/processed/, like every
other analysis artefact. Run it locally (run_all.sh does) and commit the two
outputs; CI never needs the raw files.

Usage:  python src/atlas_prep.py
"""

from __future__ import annotations

import csv

import pandas as pd

from config import PROCESSED, RAW

# Past the naked-eye limit, so the Milky Way shows by density.
STAR_MAG_LIMIT = 7.5

CAPITALS = PROCESSED / "country_capitals.csv"
STARS = PROCESSED / "stars.parquet"


def capitals() -> int:
    """One point per country: its capital (GeoNames feature code PPLC)."""
    caps: dict[str, tuple[float, float]] = {}
    with open(RAW / "cities15000.txt", encoding="utf-8") as fh:
        for row in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            if row[7] == "PPLC":
                caps.setdefault(row[8], (float(row[4]), float(row[5])))
    d = pd.DataFrame([(k, *v) for k, v in sorted(caps.items())],
                     columns=["country", "lat", "lon"])
    d.to_csv(CAPITALS, index=False)
    return len(d)


def stars() -> int:
    """Every catalogue star to STAR_MAG_LIMIT: position, magnitude, colour index.

    Positions stay equatorial (RA/Dec). Turning RA into the longitude a star
    stands over depends on the data date, so that happens at build time."""
    d = pd.read_csv(RAW / "hygdata_v41.csv", usecols=["id", "ra", "dec", "mag", "ci"])
    d = d[(d.id > 0) & (d.mag <= STAR_MAG_LIMIT)].sort_values(["mag", "id"])
    # float32 is far finer than the int16 the page packs positions into.
    d = d[["ra", "dec", "mag", "ci"]].astype("float32").reset_index(drop=True)
    d.to_parquet(STARS, index=False)
    return len(d)


def main() -> None:
    print(f"wrote {CAPITALS.name} ({capitals()} capitals), "
          f"{STARS.name} ({stars()} stars)")


if __name__ == "__main__":
    main()
