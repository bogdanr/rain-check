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

A fourth rule - at least MIN_PAIRS usable forecast/observation pairs after QC -
cannot be tested from the inventory alone and is enforced later, when the daily
records are actually joined.

Usage:  python src/probe_capitals.py
"""

from __future__ import annotations

import json
import math

import pandas as pd
import requests

from config import RAW

# --------------------------------------------------------------------------
# Inclusion rules
# --------------------------------------------------------------------------
MAX_STATION_KM = 25.0
MAX_ELEV_DIFF_M = 300.0
MIN_PAIRS = 500          # enforced downstream, recorded here for one-place rules
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


# --------------------------------------------------------------------------
def probe() -> dict:
    st, inv = load_stations(), load_inventory()
    grid = grid_elevations(CAPITALS)

    covering = inv[(inv.year_first <= WINDOW_START_YEAR)
                   & (inv.year_last >= WINDOW_END_YEAR)]
    by_elem = {e: set(g.id) for e, g in covering.groupby("elem")}

    def nearest(elem: str, lat: float, lon: float):
        cand = st[st.id.isin(by_elem.get(elem, set()))].copy()
        if cand.empty:
            return None
        cand["km"] = [
            _km(lat, lon, a, b) for a, b in zip(cand.lat.values, cand.lon.values)]
        row = cand.nsmallest(1, "km").iloc[0]
        return row

    included, excluded = {}, {}
    for city, (lat, lon, tz, country) in CAPITALS.items():
        prcp = nearest("PRCP", lat, lon)
        if prcp is None or prcp.km > MAX_STATION_KM:
            excluded[city] = {
                "reason": "no PRCP station within "
                          f"{MAX_STATION_KM:.0f} km reporting through "
                          f"{WINDOW_END_YEAR}",
                "nearest_km": None if prcp is None else round(float(prcp.km), 1),
            }
            continue

        gelev = grid.get(city)
        if gelev is not None and abs(prcp.elev_m - gelev) > MAX_ELEV_DIFF_M:
            excluded[city] = {
                "reason": "station elevation differs from the forecast grid "
                          f"point by more than {MAX_ELEV_DIFF_M:.0f} m",
                "station": prcp.id, "station_name": prcp["name"],
                "station_elev_m": float(prcp.elev_m),
                "grid_elev_m": float(gelev),
                "nearest_km": round(float(prcp.km), 1),
            }
            continue

        tmax = nearest("TMAX", lat, lon)
        has_temp = tmax is not None and tmax.km <= MAX_STATION_KM
        included[city] = {
            "lat": lat, "lon": lon, "timezone": tz, "country": country,
            "grid_elev_m": None if gelev is None else float(gelev),
            "prcp_station": prcp.id, "prcp_station_name": prcp["name"],
            "prcp_km": round(float(prcp.km), 1),
            "prcp_elev_m": float(prcp.elev_m),
            "tmax_station": tmax.id if has_temp else None,
            "tmax_station_name": tmax["name"] if has_temp else None,
            "tmax_km": round(float(tmax.km), 1) if has_temp else None,
        }

    return {
        "probed": pd.Timestamp.now("UTC").strftime("%Y-%m-%d"),
        "rules": {
            "max_station_km": MAX_STATION_KM,
            "max_elev_diff_m": MAX_ELEV_DIFF_M,
            "min_pairs": MIN_PAIRS,
            "window_years": [WINDOW_START_YEAR, WINDOW_END_YEAR],
            "note": ("min_pairs is enforced downstream, when daily records are "
                     "joined; it cannot be checked from the inventory alone."),
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
                "tmax_km"]
        print(df[cols].sort_values("prcp_km").to_string())

    print(f"\n{len(exc)} excluded:")
    for city, why in sorted(exc.items()):
        extra = ""
        if why.get("station_elev_m") is not None:
            extra = (f" (station {why['station_name']} at "
                     f"{why['station_elev_m']:.0f} m vs grid "
                     f"{why['grid_elev_m']:.0f} m)")
        elif why.get("nearest_km") is not None:
            extra = f" (nearest is {why['nearest_km']} km)"
        print(f"  {city:20s} {why['reason']}{extra}")

    no_temp = [c for c, v in inc.items() if v["tmax_station"] is None]
    if no_temp:
        print(f"\nusable for rain but not temperature: {', '.join(sorted(no_temp))}")


if __name__ == "__main__":
    main()
