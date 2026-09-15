"""Task 30: freeze the set of European capitals the study can actually cover.

The forecast side of a multi-city comparison is free - Open-Meteo serves any
coordinate - so the binding constraint is observations. This probe measures that
constraint instead of assuming it, and writes the verdict (including every
exclusion and its reason) to data/raw/capital_coverage.json.

Inclusion rules, all checkable here without downloading a single daily record:

  1. a GHCN-Daily station reporting PRCP within MAX_STATION_KM of the city,
  2. whose inventory covers the whole evaluation window (2024 through 2026),
  3. and whose elevation is within MAX_ELEV_DIFF_M of the forecast grid point.

Rule 3 exists because rule 1 alone produces nonsense: the nearest qualifying
station to Andorra la Vella is Saloria at 2451 m and to Vaduz is Saentis at
2502 m. Verifying a valley city against a mountain-top gauge would not measure
forecast quality, it would measure the lapse rate.

The rules are applied to every station in range, not to the nearest one only.
That distinction is the whole of `pick_station` below, and it matters twice
over. Andorra la Vella was excluded because its *nearest* gauge is on a
mountain - but La Seu d'Urgell, 17 km down the same valley and 176 m from the
grid point, was there all along. And a gauge can satisfy every rule above while
filing 37 days of data in two years, because the inventory records only the
first and last year a station reported, never how often. Preferring the nearest
gauge that actually reports (MIN_STATION_DAYS) over the nearest gauge that
merely exists recovers eleven cities in the world run that were previously
dropped downstream for having too little data to verify against.

The binding rule - at least MIN_PAIRS usable forecast/observation pairs after
QC - is still enforced later, when the daily records are actually joined; the
reporting-day count is a close predictor of it (the two agree to within about
three days) but not a substitute.

Usage:  python src/probe_capitals.py
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import requests

from config import RAW

# --------------------------------------------------------------------------
# Inclusion rules
# --------------------------------------------------------------------------
MAX_STATION_KM = 25.0
MAX_ELEV_DIFF_M = 300.0
MIN_PAIRS = 500          # enforced downstream, recorded here for one-place rules

# A station is "reporting" if it filed at least this many days over the window.
# Set just above MIN_PAIRS: the join costs a handful of days to missing forecast
# hours and to the day-convention shift, so a gauge with 525 days reliably
# clears 500 pairs, and one with 500 does not. Across the cities that survive
# every other rule, pairs = reporting days - 3 with a correlation of 0.999.
MIN_STATION_DAYS = 525

WINDOW_START_YEAR = 2024
WINDOW_END_YEAR = 2026

GHCN_BASE = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/"
ELEVATION_API = "https://api.open-meteo.com/v1/elevation"

# --------------------------------------------------------------------------
# The 43 European capitals, with IANA time zones. City-hall coordinates; the
# forecast grid point is whatever Open-Meteo snaps these to.
# --------------------------------------------------------------------------
CAPITALS: dict[str, tuple[float, float, str, str]] = {
    "Amsterdam": (52.3676, 4.9041, "Europe/Amsterdam", "NL"),
    "Andorra la Vella": (42.5063, 1.5218, "Europe/Andorra", "AD"),
    "Athens": (37.9838, 23.7275, "Europe/Athens", "GR"),
    "Belgrade": (44.7866, 20.4489, "Europe/Belgrade", "RS"),
    "Berlin": (52.5200, 13.4050, "Europe/Berlin", "DE"),
    "Bern": (46.9480, 7.4474, "Europe/Zurich", "CH"),
    "Bratislava": (48.1486, 17.1077, "Europe/Bratislava", "SK"),
    "Brussels": (50.8503, 4.3517, "Europe/Brussels", "BE"),
    "Bucharest": (44.4268, 26.1025, "Europe/Bucharest", "RO"),
    "Budapest": (47.4979, 19.0402, "Europe/Budapest", "HU"),
    "Chisinau": (47.0105, 28.8638, "Europe/Chisinau", "MD"),
    "Copenhagen": (55.6761, 12.5683, "Europe/Copenhagen", "DK"),
    "Dublin": (53.3498, -6.2603, "Europe/Dublin", "IE"),
    "Helsinki": (60.1699, 24.9384, "Europe/Helsinki", "FI"),
    "Kyiv": (50.4501, 30.5234, "Europe/Kyiv", "UA"),
    "Lisbon": (38.7223, -9.1393, "Europe/Lisbon", "PT"),
    "Ljubljana": (46.0569, 14.5058, "Europe/Ljubljana", "SI"),
    "London": (51.5074, -0.1278, "Europe/London", "GB"),
    "Luxembourg": (49.6116, 6.1319, "Europe/Luxembourg", "LU"),
    "Madrid": (40.4168, -3.7038, "Europe/Madrid", "ES"),
    "Minsk": (53.9006, 27.5590, "Europe/Minsk", "BY"),
    "Monaco": (43.7384, 7.4246, "Europe/Monaco", "MC"),
    "Nicosia": (35.1856, 33.3823, "Asia/Nicosia", "CY"),
    "Oslo": (59.9139, 10.7522, "Europe/Oslo", "NO"),
    "Paris": (48.8566, 2.3522, "Europe/Paris", "FR"),
    "Podgorica": (42.4304, 19.2594, "Europe/Podgorica", "ME"),
    "Prague": (50.0755, 14.4378, "Europe/Prague", "CZ"),
    "Pristina": (42.6629, 21.1655, "Europe/Belgrade", "XK"),
    "Reykjavik": (64.1466, -21.9426, "Atlantic/Reykjavik", "IS"),
    "Riga": (56.9496, 24.1052, "Europe/Riga", "LV"),
    "Rome": (41.9028, 12.4964, "Europe/Rome", "IT"),
    "Sarajevo": (43.8563, 18.4131, "Europe/Sarajevo", "BA"),
    "Skopje": (41.9981, 21.4254, "Europe/Skopje", "MK"),
    "Sofia": (42.6977, 23.3219, "Europe/Sofia", "BG"),
    "Stockholm": (59.3293, 18.0686, "Europe/Stockholm", "SE"),
    "Tallinn": (59.4370, 24.7536, "Europe/Tallinn", "EE"),
    "Tirana": (41.3275, 19.8187, "Europe/Tirane", "AL"),
    "Vaduz": (47.1410, 9.5209, "Europe/Vaduz", "LI"),
    "Valletta": (35.8989, 14.5146, "Europe/Malta", "MT"),
    "Vienna": (48.2082, 16.3738, "Europe/Vienna", "AT"),
    "Vilnius": (54.6872, 25.2797, "Europe/Vilnius", "LT"),
    "Warsaw": (52.2297, 21.0122, "Europe/Warsaw", "PL"),
    "Zagreb": (45.8150, 15.9819, "Europe/Zagreb", "HR"),
}


# --------------------------------------------------------------------------
# GHCN metadata
# --------------------------------------------------------------------------
def _download(name: str) -> str:
    path = RAW / name
    if not path.exists():
        resp = requests.get(GHCN_BASE + name, timeout=120)
        resp.raise_for_status()
        path.write_text(resp.text)
    return path.read_text()


def load_stations() -> pd.DataFrame:
    """Fixed-width GHCN station list: id, lat, lon, elevation, name."""
    rows = []
    for line in _download("ghcnd-stations.txt").splitlines():
        if len(line) < 71:
            continue
        rows.append((line[0:11].strip(), float(line[12:20]), float(line[21:30]),
                     float(line[31:37]), line[41:71].strip()))
    return pd.DataFrame(rows, columns=["id", "lat", "lon", "elev_m", "name"])


def load_inventory() -> pd.DataFrame:
    """Which element each station reports, and over which years."""
    rows = []
    for line in _download("ghcnd-inventory.txt").splitlines():
        if len(line) < 45:
            continue
        rows.append((line[0:11].strip(), line[31:35].strip(),
                     int(line[36:40]), int(line[41:45])))
    return pd.DataFrame(rows, columns=["id", "elem", "year_first", "year_last"])


def grid_elevations(caps: dict) -> dict[str, float]:
    """Elevation of each forecast grid point, from Open-Meteo.

    The comparison that matters is station vs *grid point*, not station vs the
    city's nominal altitude: the grid point is what the forecast describes.
    """
    from fetch import fetch_json, is_error
    names = list(caps)
    payload = fetch_json(ELEVATION_API, {
        "latitude": ",".join(f"{caps[n][0]:.4f}" for n in names),
        "longitude": ",".join(f"{caps[n][1]:.4f}" for n in names),
    })
    if is_error(payload):
        return {}
    return dict(zip(names, payload["elevation"]))


def _km(lat1, lon1, lat2, lon2):
    """Equirectangular distance - accurate well inside 1% at these ranges."""
    x = (lon2 - lon1) * 111.320 * math.cos(math.radians((lat1 + lat2) / 2))
    y = (lat2 - lat1) * 110.574
    return math.hypot(x, y)


def _km_vec(frame: pd.DataFrame, lat: float, lon: float):
    """Distance from (lat, lon) to every row of `frame`, vectorised."""
    dy = (frame.lat.values - lat) * 110.574
    dx = (frame.lon.values - lon) * 111.320 * math.cos(math.radians(lat))
    return np.hypot(dx, dy)


def station_frame(stations: pd.DataFrame, ids: set[str], days: pd.Series
                  ) -> pd.DataFrame:
    """Stations qualifying on the inventory, carrying their reporting-day count."""
    f = stations[stations.id.isin(ids)].reset_index(drop=True)
    f["days"] = f.id.map(days).fillna(0).astype(int)
    return f


def pick_station(frame: pd.DataFrame, lat: float, lon: float,
                 ref_elev: float | None):
    """The best gauge for one location, or `(None, why)` if there is none.

    "Best" is not "nearest". Every station inside the distance and elevation
    envelope is a candidate; among those that actually report often enough the
    nearest wins, and only if none of them does are we forced back onto the
    fullest sparse record. Ranking by distance alone picked a gauge with 20
    days of data for Houston while a complete one sat 9.8 km away, and picked a
    mountain top for Andorra while a valley station sat 17 km away.
    """
    km = _km_vec(frame, lat, lon)
    near = km <= MAX_STATION_KM
    if not near.any():
        return None, (f"no station within {MAX_STATION_KM:.0f} km reporting "
                      f"through {WINDOW_END_YEAR}")

    ok = near
    if ref_elev is not None:
        ok = near & (abs(frame.elev_m.values - ref_elev) <= MAX_ELEV_DIFF_M)
    if not ok.any():
        return None, (f"every station within {MAX_STATION_KM:.0f} km differs "
                      f"from the forecast grid point by more than "
                      f"{MAX_ELEV_DIFF_M:.0f} m in elevation")

    cand = frame[ok].assign(km=km[ok])
    reporting = cand[cand.days >= MIN_STATION_DAYS]
    if len(reporting):
        return reporting.nsmallest(1, "km").iloc[0], ""
    # Nothing in range reports often enough. Keep the fullest record anyway and
    # let the downstream pair count exclude the city with a measured reason,
    # rather than guessing here from a count that is only a good predictor.
    return cand.sort_values(["days", "km"], ascending=[False, True]).iloc[0], ""


# --------------------------------------------------------------------------
def probe() -> dict:
    import ghcn_bulk

    st, inv = load_stations(), load_inventory()
    grid = grid_elevations(CAPITALS)
    cen = ghcn_bulk.census().set_index("station")

    covering = inv[(inv.year_first <= WINDOW_START_YEAR)
                   & (inv.year_last >= WINDOW_END_YEAR)]
    by_elem = {e: set(g.id) for e, g in covering.groupby("elem")}
    frames = {
        "PRCP": station_frame(st, by_elem.get("PRCP", set()), cen.prcp_days),
        "TMAX": station_frame(st, by_elem.get("TMAX", set()), cen.tmax_days),
    }

    included, excluded = {}, {}
    for city, (lat, lon, tz, country) in CAPITALS.items():
        gelev = grid.get(city)
        prcp, why = pick_station(frames["PRCP"], lat, lon, gelev)
        if prcp is None:
            nearest = frames["PRCP"].iloc[int(_km_vec(
                frames["PRCP"], lat, lon).argmin())]
            excluded[city] = {
                "reason": why,
                "nearest_km": round(float(_km(lat, lon, nearest.lat,
                                              nearest.lon)), 1),
                "station": nearest.id, "station_name": nearest["name"],
                "station_elev_m": float(nearest.elev_m),
                "grid_elev_m": None if gelev is None else float(gelev),
            }
            continue

        tmax, _ = pick_station(frames["TMAX"], lat, lon, gelev)
        included[city] = {
            "lat": lat, "lon": lon, "timezone": tz, "country": country,
            "grid_elev_m": None if gelev is None else float(gelev),
            "prcp_station": prcp.id, "prcp_station_name": prcp["name"],
            "prcp_km": round(float(prcp.km), 1),
            "prcp_elev_m": float(prcp.elev_m),
            "prcp_days": int(prcp.days),
            "tmax_station": tmax.id if tmax is not None else None,
            "tmax_station_name": tmax["name"] if tmax is not None else None,
            "tmax_km": round(float(tmax.km), 1) if tmax is not None else None,
            "tmax_days": int(tmax.days) if tmax is not None else None,
        }

    return {
        "probed": pd.Timestamp.now("UTC").strftime("%Y-%m-%d"),
        "rules": {
            "max_station_km": MAX_STATION_KM,
            "max_elev_diff_m": MAX_ELEV_DIFF_M,
            "min_pairs": MIN_PAIRS,
            "min_station_days": MIN_STATION_DAYS,
            "window_years": [WINDOW_START_YEAR, WINDOW_END_YEAR],
            "note": ("min_pairs is enforced downstream, when daily records are "
                     "joined; min_station_days is the inventory-time proxy for "
                     "it, and decides which of the gauges in range is used."),
        },
        "included": included,
        "excluded": excluded,
    }


def main() -> None:
    res = probe()
    out = RAW / "capital_coverage.json"
    out.write_text(json.dumps(res, indent=2, sort_keys=True))

    inc, exc = res["included"], res["excluded"]
    print(f"wrote {out}")
    print(f"\n{len(inc)} of {len(CAPITALS)} capitals usable "
          f"(PRCP <= {MAX_STATION_KM:.0f} km, elevation within "
          f"{MAX_ELEV_DIFF_M:.0f} m, reporting {WINDOW_START_YEAR}-{WINDOW_END_YEAR})")
    df = pd.DataFrame(inc).T
    if len(df):
        cols = ["prcp_km", "prcp_station_name", "prcp_elev_m", "grid_elev_m",
                "prcp_days", "tmax_km"]
        print(df[cols].sort_values("prcp_km").to_string())

    thin = {c: v for c, v in inc.items() if v["prcp_days"] < MIN_STATION_DAYS}
    if thin:
        print(f"\nno gauge in range reports {MIN_STATION_DAYS}+ days; kept the "
              f"fullest, expect these to fail the pair count downstream:")
        for city, v in sorted(thin.items()):
            print(f"  {city:20s} {v['prcp_station_name']} - "
                  f"{v['prcp_days']} days")

    print(f"\n{len(exc)} excluded:")
    for city, why in sorted(exc.items()):
        if "elevation" in why["reason"]:
            extra = (f" (nearest is {why['station_name']} at "
                     f"{why['station_elev_m']:.0f} m vs grid "
                     f"{why['grid_elev_m']:.0f} m)")
        else:
            extra = f" (nearest is {why['nearest_km']} km away)"
        print(f"  {city:20s} {why['reason']}{extra}")

    no_temp = [c for c, v in inc.items() if v["tmax_station"] is None]
    if no_temp:
        print(f"\nusable for rain but not temperature: {', '.join(sorted(no_temp))}")


if __name__ == "__main__":
    main()
