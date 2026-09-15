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

The globe's relief textures are baked here too, for the same reason and one
more: the sources are 55 MB of full-resolution raster that nothing downstream
ever needs. What the page loads is 1/100th of that, and it carries physical
quantities - metres, vegetation, snow, distance to the nearest coast - rather
than a picture, so the renderer can restyle the planet per theme instead of
tinting somebody else's JPEG.

Usage:  python src/vendor.py           (libraries, land geometry, relief)
        python src/vendor.py --relief  (relief textures only)
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).resolve().parent / "web"
VENDOR = WEB / "vendor"
GEO = WEB / "geo"
CACHE = ROOT / "data" / "cache" / "relief"

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

# The same coastline at five times the detail, for readers who zoom.
#
# 110m is a world map's coastline: correct at the scale of a continent and
# visibly polygonal by 8x, where Sardinia is a five-sided wedge. 50m is what a
# regional map uses, and it is twelve times the points - far too much to put in
# front of a reader who only wants to see which cities are in the archive, and
# well worth fetching for one who has zoomed in far enough to care.
LAND_DETAIL_URL = "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-50m.json"
LAND_DETAIL_NOTE = ("world-atlas 2.0.2 land-50m, derived from Natural Earth "
                    "(public domain). Converted TopoJSON -> GeoJSON at vendor "
                    "time. Loaded by the globe only above 2x zoom.")

# Coordinates are rounded to this many decimals. At 1e-2 degrees (~1 km) the
# error is far below what a globe a few hundred pixels across can resolve, and
# it cuts the payload by more than half.
COORD_DECIMALS = 2

# ---------------------------------------------------------------------------
# Relief sources. All three are public domain (NASA / NOAA), all three are
# plate-carree global rasters on exactly the same grid, which is why they can be
# combined pixel-for-pixel without any reprojection.
# ---------------------------------------------------------------------------
RELIEF_SRC = [
    ("gebco_elev.png",
     "https://eoimages.gsfc.nasa.gov/images/imagerecords/73000/73934/"
     "gebco_08_rev_elev_21600x10800.png",
     "NASA Visible Earth / Blue Marble: GEBCO_08 land topography, 21600x10800"),
    ("gebco_bath.png",
     "https://eoimages.gsfc.nasa.gov/images/imagerecords/73000/73963/"
     "gebco_08_rev_bath_21600x10800.png",
     "NASA Visible Earth / Blue Marble: GEBCO_08 ocean bathymetry, 21600x10800"),
    ("bluemarble.jpg",
     "https://eoimages.gsfc.nasa.gov/images/imagerecords/73000/73751/"
     "world.topo.bathy.200407.3x5400x2700.jpg",
     "NASA Visible Earth: Blue Marble Next Generation, July 2004, 5400x2700"),
]

# Output grids. The globe is at most 620 CSS px across, so half its
# circumference is ~620 px: at 1536 texels of longitude the terrain is already
# slightly oversampled at the opening view, and the file is half the size of the
# 2048 version. The biome layer is smooth by nature and gets half that again.
ELEV_W, ELEV_H = 1536, 768
BIOME_W, BIOME_H = 1024, 512

# Metres per unit in the two NASA ramps, measured by fitting the shipped rasters
# against known summits (Everest, Aconcagua, Denali, Mont Blanc, Kilimanjaro)
# and known basins. They are 8-bit visualisation products, not grids, so these
# are the honest numbers rather than a documented constant.
TOPO_M_PER_UNIT = 28.0
BATH_M_PER_UNIT = 38.0

# Encoding of the elevation texture, mirrored in globe.js. One byte per texel,
# 128 = sea level, land above, ocean below, each side on its own square-root
# ramp because the eye wants resolution near sea level and does not care whether
# an abyssal plain is 5.2 or 5.4 km down.
ELEV_SEA = 128
ELEV_MAX_M = 8500.0
DEPTH_MAX_M = 9000.0

# Cap on the coast-distance channel. Beyond ~600 km from land the shelf is gone
# and the shading has nothing left to say, so spending bits there is waste.
COAST_MAX_KM = 600.0

RELIEF_NOTE = (
    "geo/relief-elev.webp, geo/relief-biome.webp\n"
    "    Baked from the three NASA/NOAA public-domain rasters below.\n"
    "    elev : one byte per texel, 128 = sea level; land = 128 + 127*sqrt(h/8500),\n"
    "           ocean = 128 - 127*(d/9000)^0.65. Lossless.\n"
    "    biome: R = vegetation, G = snow/ice, B = distance to coast (0 = 600 km,\n"
    "           255 = shoreline). Lossy; all three are smooth fields.")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "weather-calibration-build"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.read()


def _cached(name: str, url: str) -> Path:
    """Download once into data/cache/. The relief sources are 55 MB together and
    never change; re-fetching them on every run would be rude to NASA and slow
    for us."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if not path.exists():
        print(f"fetching {url}")
        path.write_bytes(_get(url))
    else:
        print(f"cached   {path.relative_to(ROOT)}")
    return path


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


def _coast_distance_km(land, lat_deg):
    """Great-circle distance to the nearest shoreline, per texel, both ways.

    Two chamfer passes over the grid - forward then backward - propagate the
    distance from every already-visited neighbour, which is the standard linear
    approximation to a Euclidean distance transform and is quite accurate enough
    for a shading term. Doing it here rather than in the browser is the whole
    point of baking: the same field would cost a second of main-thread time on
    every page load, and it never changes.

    Distances are in kilometres on the sphere, not texels, so the shelf glow
    does not widen towards the poles where texels are narrow.
    """
    import numpy as np

    h, w = land.shape
    # Texel size in km. Longitude shrinks with the cosine of latitude; a column
    # step near Svalbard is a fifth of one at the equator.
    dy_km = np.full(h, 111.32 * 180.0 / h, np.float32)
    dx_km = (111.32 * 360.0 / w * np.cos(np.deg2rad(lat_deg))).astype(np.float32)

    INF = np.float32(1e6)
    # The shoreline is the *boundary*, so both sides start at zero: a coastal
    # sea texel and a coastal land texel are equally "at the coast".
    edge = np.zeros((h, w), bool)
    edge[:, :-1] |= land[:, :-1] != land[:, 1:]
    edge[:, 1:] |= land[:, :-1] != land[:, 1:]
    edge[:-1, :] |= land[:-1, :] != land[1:, :]
    edge[1:, :] |= land[:-1, :] != land[1:, :]
    d = np.where(edge, np.float32(0), INF)

    def sweep(rows, flip):
        for y in rows:
            row = d[y]
            if 0 <= y - 1 < h:
                row = np.minimum(row, d[y - 1] + dy_km[y])
            if flip:
                row = row[::-1]
            step = dx_km[y]
            # Sequential horizontal pass. np.minimum.accumulate does it in one
            # C loop: subtract the ramp, take the running minimum, add it back.
            ramp = np.arange(w, dtype=np.float32) * step
            row = np.minimum.accumulate(row - ramp) + ramp
            if flip:
                row = row[::-1]
            d[y] = row

    sweep(range(h), False)
    sweep(range(h - 1, -1, -1), True)
    sweep(range(h), False)
    return d


def bake_relief() -> list[str]:
    """Turn three NASA rasters into the two textures the globe samples.

    The output is deliberately *data*, not imagery: elevation in metres, a
    vegetation index, a snow index and distance to the coast. Colour is applied
    in the browser from the CSS tokens, so the same textures render an
    instrument-dark planet, a pale daylight one and a monochrome blueprint one
    without a second download.
    """
    import numpy as np
    from PIL import Image

    # Two of the sources are 233-megapixel PNGs. That is a legitimate decode,
    # not a decompression bomb, and Pillow's default guard refuses it.
    Image.MAX_IMAGE_PIXELS = None

    paths = {name: _cached(name, url) for name, url, _ in RELIEF_SRC}

    print("baking relief")
    topo_src = Image.open(paths["gebco_elev.png"]).convert("L")
    bath_src = Image.open(paths["gebco_bath.png"]).convert("L")

    # Land/sea comes from the bathymetry raster, where land is saturated at 255.
    # Taking it from the topography instead would drown every coastal plain
    # below 28 m - which is most of the Netherlands, the Nile delta and
    # Bangladesh - since the topography ramp cannot resolve them from the sea.
    #
    # The mask is reduced *before* thresholding, so a texel is land when most of
    # the ground under it is land. Thresholding after averaging heights would
    # instead have smeared a 10 km grey coastline all round the world.
    land_full = Image.fromarray((np.asarray(bath_src) == 255).astype(np.uint8) * 255)
    land_frac = np.asarray(land_full.resize((ELEV_W, ELEV_H), Image.BOX), np.float32) / 255.0
    land = land_frac >= 0.5

    topo_m = np.asarray(topo_src.resize((ELEV_W, ELEV_H), Image.BOX), np.float32) * TOPO_M_PER_UNIT
    depth_m = (255.0 - np.asarray(bath_src.resize((ELEV_W, ELEV_H), Image.BOX), np.float32)) * BATH_M_PER_UNIT

    code = np.empty((ELEV_H, ELEV_W), np.float32)
    code[land] = ELEV_SEA + 127.0 * np.sqrt(
        np.clip(topo_m[land], 0, ELEV_MAX_M) / ELEV_MAX_M)
    sea = ~land
    code[sea] = ELEV_SEA - 127.0 * np.power(
        np.clip(depth_m[sea], 0, DEPTH_MAX_M) / DEPTH_MAX_M, 0.65)
    # Land must never encode as sea and vice versa: the renderer decides which
    # palette a texel gets from this one comparison, and a rounding that put a
    # Dutch polder at 128 would paint a hole in the country.
    code[land] = np.maximum(code[land], ELEV_SEA + 1)
    code[sea] = np.minimum(code[sea], ELEV_SEA - 1)
    code = np.clip(np.round(code), 0, 255).astype(np.uint8)

    # Blue Marble carries what elevation cannot: where the vegetation is. July,
    # so the northern hemisphere is in leaf and the snow that remains is the
    # permanent kind rather than a seasonal sheet over Siberia.
    bm = np.asarray(Image.open(paths["bluemarble.jpg"]).convert("RGB")
                    .resize((BIOME_W, BIOME_H), Image.BOX), np.float32)
    r, g, b = bm[..., 0], bm[..., 1], bm[..., 2]
    # Excess green over the red/blue average: the cheap, robust form of a
    # vegetation index for an RGB composite with no infrared band.
    veg = np.clip((g - 0.5 * (r + b)) / 24.0, 0, 1)
    # Snow and ice are bright *and* neutral. Brightness alone is not enough: the
    # Sahara is one of the brightest surfaces on the planet, and a pure
    # brightness test put an ice sheet over Libya. Sand is strongly red-biased,
    # so the channel spread separates the two cleanly - snow has almost none.
    spread = np.max(bm, axis=-1) - np.min(bm, axis=-1)
    snow = (np.clip((np.min(bm, axis=-1) - 105.0) / 95.0, 0, 1) *
            np.clip(1.0 - (spread - 14.0) / 34.0, 0, 1))

    lat = 90.0 - (np.arange(ELEV_H, dtype=np.float32) + 0.5) * (180.0 / ELEV_H)
    coast_km = _coast_distance_km(land, lat)
    coast = 1.0 - np.clip(coast_km / COAST_MAX_KM, 0, 1)
    coast = np.asarray(Image.fromarray(np.round(coast * 255).astype(np.uint8), "L")
                       .resize((BIOME_W, BIOME_H), Image.BOX))

    GEO.mkdir(parents=True, exist_ok=True)
    elev_path = GEO / "relief-elev.webp"
    biome_path = GEO / "relief-biome.webp"
    # Lossless for elevation. Shading differentiates this channel, so a lossy
    # codec's ringing around a coastline arrives on screen as a ridge of
    # hillshade in the sea - compression artefacts promoted to terrain.
    Image.fromarray(code, "L").save(elev_path, "WEBP", lossless=True, method=6)
    # Lossy for the biome layer, which is only ever read as a smooth blend
    # weight and never differentiated.
    Image.fromarray(np.dstack([
        np.round(np.where(land_biome(land), veg, 0) * 255).astype(np.uint8),
        np.round(snow * 255).astype(np.uint8),
        coast,
    ]), "RGB").save(biome_path, "WEBP", quality=82, method=6)

    print(f"relief-elev.webp:  {elev_path.stat().st_size/1024:.0f} KB "
          f"({ELEV_W}x{ELEV_H}, lossless)")
    print(f"relief-biome.webp: {biome_path.stat().st_size/1024:.0f} KB "
          f"({BIOME_W}x{BIOME_H})")

    return [RELIEF_NOTE + "\n" +
            "\n".join(f"    {note}\n    {url}" for _, url, note in RELIEF_SRC)]


def land_biome(land):
    """The land mask on the biome grid.

    Vegetation is masked to land because the ocean's greenness is coastal
    turbidity, and letting it through paints an algal bloom along every shore.
    Snow is *not* masked: Arctic sea ice is genuinely white, and cutting it at
    the coastline would leave Greenland floating in open water.
    """
    from PIL import Image
    import numpy as np
    small = Image.fromarray(land.astype(np.uint8) * 255, "L").resize(
        (BIOME_W, BIOME_H), Image.BOX)
    return np.asarray(small, np.float32) / 255.0 >= 0.5


def main() -> None:
    only_relief = "--relief" in sys.argv[1:]

    VENDOR.mkdir(parents=True, exist_ok=True)
    GEO.mkdir(parents=True, exist_ok=True)

    if only_relief:
        bake_relief()
        print("relief textures rebuilt; LICENSES.txt left untouched")
        return

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

    print(f"fetching {LAND_DETAIL_URL}")
    detail = topo_to_geojson(json.loads(_get(LAND_DETAIL_URL)), "land")
    dtext = json.dumps(detail, separators=(",", ":"))
    (GEO / "land-detail.geo.json").write_text(dtext)
    notes.append(f"geo/land-detail.geo.json\n    {LAND_DETAIL_NOTE}\n"
                 f"    {LAND_DETAIL_URL}")

    notes.extend(bake_relief())

    (VENDOR / "LICENSES.txt").write_text(
        "Third-party assets vendored into this repository.\n\n" +
        "\n\n".join(notes) + "\n")

    n_rings = sum(len(p) for p in land["coordinates"])
    print(f"\nland.geo.json: {len(text)/1024:.0f} KB, "
          f"{len(land['coordinates'])} polygons, {n_rings} rings")
    print(f"land-detail.geo.json: {len(dtext)/1024:.0f} KB, "
          f"{len(detail['coordinates'])} polygons")
    print(f"vendored into {VENDOR} and {GEO}")


if __name__ == "__main__":
    sys.exit(main())
