"""Vendor third-party web assets into the repo. Run manually, not in CI.

Everything the page loads is served from our own origin. Pages hosts a vendored
file just as happily as a CDN does, and self-hosting removes three liabilities
from an artefact whose entire selling point is reproducibility: a third-party
availability risk, a privacy leak, and a supply-chain surface. It also means the
CI build - which rebuilds the page from committed artefacts and must not touch
the network - has everything it needs in the tree.

The land geometry is converted from TopoJSON to GeoJSON *here*, at vendor time,
rather than in the browser. That removes a whole JS dependency
(topojson-client): the decoding is about thirty lines and only has to happen
once, so paying for it on every page load would be silly.

Usage:  python src/vendor.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"
VENDOR = WEB / "vendor"
GEO = WEB / "geo"

# Pinned exactly. An unpinned vendored asset is the worst of both worlds: you
# carry the file but still cannot say which version you are carrying.
LIBS = [
    ("d3-array.js", "https://cdn.jsdelivr.net/npm/d3-array@3.2.4/dist/d3-array.min.js",
     "d3-array 3.2.4 - ISC - (c) Mike Bostock"),
    # d3-geo's UMD bundle expects d3-array on the global, so load order matters.
    ("d3-geo.js", "https://cdn.jsdelivr.net/npm/d3-geo@3.1.1/dist/d3-geo.min.js",
     "d3-geo 3.1.1 - ISC - (c) Mike Bostock, Charles Karney"),
]

LAND_URL = "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-110m.json"
LAND_NOTE = ("world-atlas 2.0.2 land-110m, derived from Natural Earth "
             "(public domain). Converted TopoJSON -> GeoJSON at vendor time.")

# Coordinates are rounded to this many decimals. At 1e-2 degrees (~1 km) the
# error is far below what a globe a few hundred pixels across can resolve, and
# it cuts the payload by more than half.
COORD_DECIMALS = 2


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "weather-calibration-build"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


# ---------------------------------------------------------------------------
# Minimal TopoJSON decoder
# ---------------------------------------------------------------------------
def _decode_arcs(topo: dict) -> list[list[list[float]]]:
    """Undo TopoJSON's delta encoding and quantisation into absolute lon/lat."""
    tr = topo.get("transform")
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            if tr:
                pts.append([x * tr["scale"][0] + tr["translate"][0],
                            y * tr["scale"][1] + tr["translate"][1]])
            else:
                pts.append([float(x), float(y)])
        out.append(pts)
    return out


def _ring(arcs: list, idx: list[int]) -> list[list[float]]:
    """Stitch arc indices into a ring. Negative index means traverse backwards."""
    pts: list[list[float]] = []
    for i in idx:
        a = arcs[~i][::-1] if i < 0 else arcs[i]
        # Arcs share endpoints; dropping the duplicate keeps rings clean.
        pts.extend(a[1:] if pts else a)
    return pts


def _round(ring: list[list[float]]) -> list[list[float]]:
    """Round, then drop points that collapsed onto their neighbour."""
    out = []
    for x, y in ring:
        p = [round(x, COORD_DECIMALS), round(y, COORD_DECIMALS)]
        if not out or p != out[-1]:
            out.append(p)
    return out


def topo_to_geojson(topo: dict, name: str) -> dict:
    """Convert one named TopoJSON object into a GeoJSON geometry."""
    arcs = _decode_arcs(topo)
    obj = topo["objects"][name]
    polys: list[list[list[list[float]]]] = []

    def add(geom):
        t = geom.get("type")
        if t == "GeometryCollection":
            for g in geom["geometries"]:
                add(g)
        elif t == "Polygon":
            polys.append([_round(_ring(arcs, r)) for r in geom["arcs"]])
        elif t == "MultiPolygon":
            for poly in geom["arcs"]:
                polys.append([_round(_ring(arcs, r)) for r in poly])

    add(obj)
    # A ring with fewer than 4 points has no area once rounded; keeping it would
    # only give the renderer degenerate paths to trip over.
    polys = [[r for r in p if len(r) >= 4] for p in polys]
    polys = [p for p in polys if p]
    return {"type": "MultiPolygon", "coordinates": polys}


def main() -> None:
    VENDOR.mkdir(parents=True, exist_ok=True)
    GEO.mkdir(parents=True, exist_ok=True)

    notes = []
    for fname, url, note in LIBS:
        print(f"fetching {url}")
        (VENDOR / fname).write_bytes(_get(url))
        notes.append(f"{fname}\n    {note}\n    {url}")

    print(f"fetching {LAND_URL}")
    topo = json.loads(_get(LAND_URL))
    land = topo_to_geojson(topo, "land")
    text = json.dumps(land, separators=(",", ":"))
    (GEO / "land.geo.json").write_text(text)
    notes.append(f"geo/land.geo.json\n    {LAND_NOTE}\n    {LAND_URL}")

    (VENDOR / "LICENSES.txt").write_text(
        "Third-party assets vendored into this repository.\n\n" +
        "\n\n".join(notes) + "\n")

    n_rings = sum(len(p) for p in land["coordinates"])
    print(f"\nland.geo.json: {len(text)/1024:.0f} KB, "
          f"{len(land['coordinates'])} polygons, {n_rings} rings")
    print(f"vendored into {VENDOR} and {GEO}")


if __name__ == "__main__":
    sys.exit(main())
