"""Task 40: generalise the city set from 43 European capitals to the world.

The capitals probe (probe_capitals.py) answered "which capitals can we verify?".
That framing turned out to be the wrong constraint. Capitals are 43 places
chosen for politics; the real limit is where a rain gauge still reports, and
that admits far more of the world:

    GeoNames cities >= 15k population          34,136
    ... with a usable PRCP gauge                8,509
    ... with a usable PRCP *and* TMAX gauge     8,150

So coverage was never the binding constraint - the city list was. What binds
instead is (a) fetch budget, roughly six API calls per city, and (b) two
selection rules that exist to stop the sample being garbage:

  1. **One city per gauge.** 8,150 cities share only ~3,750 distinct stations.
     Two cities verified against the same rain gauge are not two samples; they
     are one sample counted twice, and averaging them would silently
     over-weight wherever gauges are dense. Deduplicating by station is the
     single most important rule here.

  2. **A cap per country.** The qualifying pool is 39% United States and 13%
     Germany, purely because those countries report densely to GHCN. Ranking
     "world cities" off that pool would produce a league table of American
     suburbs. The cap trades raw n for the regime spread the study actually
     needs - the Phase 7/8 results are all driven by precipitation regime, not
     by how many cities were included.

Elevation is checked in two stages: a cheap pre-filter against the GeoNames
terrain model to shortlist, then the authoritative check against the *forecast
grid point* elevation for the shortlist only. The grid point is what the
forecast describes, so that is the comparison that matters - and it is what
rejected Andorra la Vella and Vaduz in the capitals probe.

Usage:  python src/probe_cities.py
"""

from __future__ import annotations

import io
import json
import math
import zipfile

import numpy as np
import pandas as pd
import requests

from config import RAW
from probe_capitals import (
    CAPITALS,
    MAX_ELEV_DIFF_M,
    MAX_STATION_KM,
    MIN_PAIRS,
    MIN_STATION_DAYS,
    WINDOW_END_YEAR,
    WINDOW_START_YEAR,
    ELEVATION_API,
    load_inventory,
    load_stations,
    pick_station,
    station_frame,
)

GEONAMES_URL = "https://download.geonames.org/export/dump/cities15000.zip"

# --------------------------------------------------------------------------
# Selection budget
# --------------------------------------------------------------------------
# Each city costs ~5 requests for the probability archive plus 1 for ERA5. At
# the throttle in fetch.py that is roughly two seconds per city, so a few
# hundred is comfortable and a few thousand is not.
MAX_CITIES = 300

# Ceiling per country. Set against the observation that the US alone supplies
# 3,328 qualifying cities: without a cap the sample is a map of GHCN reporting
# density rather than of world climate.
MAX_PER_COUNTRY = 8

# Cities below this are unlikely to be anyone's daily forecast, and the pool is
# large enough that we can afford to prefer places people live.
MIN_POPULATION = 100_000

# Two cities this close share weather as well as, usually, a gauge. Applied
# after station dedupe to catch neighbouring metros with separate stations.
MIN_CITY_SEPARATION_KM = 30.0


def load_geonames() -> pd.DataFrame:
    """GeoNames cities >= 15k population, downloaded once and cached."""
    path = RAW / "cities15000.txt"
    if not path.exists():
        resp = requests.get(GEONAMES_URL, timeout=300)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            path.write_bytes(z.read("cities15000.txt"))
    cols = ["geonameid", "name", "asciiname", "alt", "lat", "lon", "fclass",
            "fcode", "country", "cc2", "a1", "a2", "a3", "a4", "population",
            "elevation", "dem", "timezone", "moddate"]
    df = pd.read_csv(path, sep="\t", names=cols, low_memory=False)
    return df[["asciiname", "country", "lat", "lon", "population", "dem",
               "timezone"]].rename(columns={"asciiname": "city"})


def candidate_pool() -> pd.DataFrame:
    """Every city with a gauge close enough, before any selection.

    The distance and elevation envelope is unchanged; what changed is that the
    gauge is chosen from inside it rather than assumed to be the nearest one.
    See `pick_station` - the inventory says only that a station reported in
    2024 and in 2026, which a gauge filing 37 days in between satisfies.
    """
    import ghcn_bulk

    cities = load_geonames()
    st, inv = load_stations(), load_inventory()
    cen = ghcn_bulk.census().set_index("station")
    covering = inv[(inv.year_first <= WINDOW_START_YEAR)
                   & (inv.year_last >= WINDOW_END_YEAR)]
    by_elem = {e: set(g.id) for e, g in covering.groupby("elem")}

    prcp = station_frame(st, by_elem.get("PRCP", set()), cen.prcp_days)
    tmax = station_frame(st, by_elem.get("TMAX", set()), cen.tmax_days)
    print(f"  stations reporting through {WINDOW_END_YEAR}: "
          f"PRCP {len(prcp)} ({int((prcp.days >= MIN_STATION_DAYS).sum())} of "
          f"them filing {MIN_STATION_DAYS}+ days), "
          f"TMAX {len(tmax)} ({int((tmax.days >= MIN_STATION_DAYS).sum())})")

    rows = []
    for r in cities.itertuples(index=False):
        # The pre-filter compares against GeoNames' terrain model; the shortlist
        # is re-checked against the forecast grid point in
        # confirm_grid_elevation(), which is the comparison that counts.
        p, _ = pick_station(prcp, r.lat, r.lon, r.dem)
        if p is None:
            continue
        t, _ = pick_station(tmax, r.lat, r.lon, r.dem)
        if t is None:
            continue
        rows.append((r.city, r.country, r.lat, r.lon, int(r.population),
                     r.dem, r.timezone,
                     p.id, float(p.km), float(p.elev_m), int(p.days),
                     t.id, float(t.km), float(t.elev_m), int(t.days)))
    return pd.DataFrame(rows, columns=[
        "city", "country", "lat", "lon", "population", "dem", "timezone",
        "prcp_station", "prcp_km", "prcp_elev_m", "prcp_days",
        "tmax_station", "tmax_km", "tmax_elev_m", "tmax_days"])


def select(pool: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Apply the three selection rules, recording what each one removed."""
    log = []
    n0 = len(pool)

    keep = pool[pool.population >= MIN_POPULATION].copy()
    log.append({"rule": f"population >= {MIN_POPULATION:,}",
                "removed": n0 - len(keep), "remaining": len(keep)})

    # Rule 1: one city per rain gauge, keeping the largest.
    n1 = len(keep)
    keep = (keep.sort_values("population", ascending=False)
                .drop_duplicates(subset="prcp_station", keep="first"))
    log.append({"rule": "one city per PRCP station",
                "removed": n1 - len(keep), "remaining": len(keep)})

    # Rule 2: drop near-neighbours that survived rule 1 on a separate gauge.
    n2 = len(keep)
    keep = keep.sort_values("population", ascending=False).reset_index(drop=True)
    chosen_lat: list[float] = []
    chosen_lon: list[float] = []
    mask = []
    for r in keep.itertuples(index=False):
        if chosen_lat:
            dy = (np.asarray(chosen_lat) - r.lat) * 110.574
            dx = ((np.asarray(chosen_lon) - r.lon) * 111.320
                  * math.cos(math.radians(r.lat)))
            far = float(np.hypot(dx, dy).min()) >= MIN_CITY_SEPARATION_KM
        else:
            far = True
        mask.append(far)
        if far:
            chosen_lat.append(r.lat); chosen_lon.append(r.lon)
    keep = keep[mask]
    log.append({"rule": f"cities >= {MIN_CITY_SEPARATION_KM:.0f} km apart",
                "removed": n2 - len(keep), "remaining": len(keep)})

    # Rule 3: cap per country, then take the largest remaining globally.
    n3 = len(keep)
    keep = (keep.sort_values("population", ascending=False)
                .groupby("country", group_keys=False).head(MAX_PER_COUNTRY))
    log.append({"rule": f"at most {MAX_PER_COUNTRY} per country",
                "removed": n3 - len(keep), "remaining": len(keep)})

    # Always retain the capitals already analysed, so the new run is a superset
    # of the published one and the two can be compared directly.
    caps = set(CAPITALS)
    forced = keep.city.isin(caps)
    rest = (keep[~forced].sort_values("population", ascending=False)
            .head(max(0, MAX_CITIES - int(forced.sum()))))
    keep = pd.concat([keep[forced], rest]).sort_values(
        "population", ascending=False).reset_index(drop=True)
    log.append({"rule": f"budget of {MAX_CITIES} (capitals retained)",
                "removed": n3 - len(keep), "remaining": len(keep)})
    return keep, log


def confirm_grid_elevation(sel: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Authoritative elevation check: station vs the forecast grid point.

    The pre-filter used GeoNames' terrain model, which is not what the forecast
    describes. This re-checks the shortlist against the grid point Open-Meteo
    actually serves, in batches, and drops anything that fails.
    """
    from fetch import fetch_json, is_error

    elevs: list[float] = []
    for i in range(0, len(sel), 100):
        chunk = sel.iloc[i:i + 100]
        payload = fetch_json(ELEVATION_API, {
            "latitude": ",".join(f"{v:.4f}" for v in chunk.lat),
            "longitude": ",".join(f"{v:.4f}" for v in chunk.lon),
        })
        if is_error(payload):
            elevs.extend([np.nan] * len(chunk))
            continue
        elevs.extend(payload["elevation"])

    sel = sel.copy()
    sel["grid_elev_m"] = elevs
    gap = (sel.prcp_elev_m - sel.grid_elev_m).abs()
    bad = gap > MAX_ELEV_DIFF_M
    dropped = {
        r.city: {"reason": "station elevation differs from the forecast grid "
                           f"point by more than {MAX_ELEV_DIFF_M:.0f} m",
                 "station_elev_m": float(r.prcp_elev_m),
                 "grid_elev_m": float(r.grid_elev_m),
                 "nearest_km": round(float(r.prcp_km), 1)}
        for r in sel[bad].itertuples(index=False)}
    return sel[~bad].copy(), dropped


def main() -> None:
    print("=== Task 40: how many cities can this study actually cover? ===\n")
    pool = candidate_pool()
    print(f"\n  cities with a usable PRCP and TMAX gauge: {len(pool):,}")
    print(f"  distinct PRCP stations behind them      : "
          f"{pool.prcp_station.nunique():,}")
    print(f"  top countries in the raw pool           : "
          + ", ".join(f"{k} {v}" for k, v in
                      pool.country.value_counts().head(5).items()))

    sel, log = select(pool)
    print("\n  Selection:\n")
    for step in log:
        print(f"    {step['rule']:42s} -{step['removed']:6,d}  "
              f"-> {step['remaining']:,}")

    sel, dropped = confirm_grid_elevation(sel)
    print(f"\n  grid-elevation check dropped {len(dropped)}: "
          + (", ".join(sorted(dropped)) if dropped else "none"))

    included = {}
    for r in sel.itertuples(index=False):
        included[r.city] = {
            "lat": float(r.lat), "lon": float(r.lon), "timezone": r.timezone,
            "country": r.country, "population": int(r.population),
            "grid_elev_m": float(r.grid_elev_m),
            "prcp_station": r.prcp_station,
            "prcp_km": round(float(r.prcp_km), 1),
            "prcp_elev_m": float(r.prcp_elev_m),
            "prcp_days": int(r.prcp_days),
            "tmax_station": r.tmax_station,
            "tmax_km": round(float(r.tmax_km), 1),
            "tmax_days": int(r.tmax_days),
            "is_capital": r.city in CAPITALS,
        }

    out = RAW / "city_coverage.json"
    out.write_text(json.dumps({
        "probed": pd.Timestamp.now("UTC").strftime("%Y-%m-%d"),
        "rules": {
            "max_station_km": MAX_STATION_KM,
            "max_elev_diff_m": MAX_ELEV_DIFF_M,
            "min_pairs": MIN_PAIRS,
            "min_station_days": MIN_STATION_DAYS,
            "min_population": MIN_POPULATION,
            "max_per_country": MAX_PER_COUNTRY,
            "min_city_separation_km": MIN_CITY_SEPARATION_KM,
            "max_cities": MAX_CITIES,
            "window_years": [WINDOW_START_YEAR, WINDOW_END_YEAR],
        },
        "pool_size": int(len(pool)),
        "included": included,
        "excluded": dropped,
    }, indent=2, sort_keys=True))

    print(f"\nwrote {out}  ({len(included)} cities)")
    print(f"\n  countries represented : {sel.country.nunique()}")
    print(f"  capitals retained     : {int(sel.city.isin(CAPITALS).sum())}")
    print(f"  population range      : {sel.population.min():,} .. "
          f"{sel.population.max():,}")
    print(f"  latitude range        : {sel.lat.min():.1f} .. {sel.lat.max():.1f}")
    print("\n  By country:")
    vc = sel.country.value_counts()
    print("    " + ", ".join(f"{k}:{v}" for k, v in vc.items()))


if __name__ == "__main__":
    main()
