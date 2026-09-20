"""Access layer for the member-level ensemble Zarr archives (Tasks 20a, 30).

Why a separate module
---------------------
`src/collect_members.py` (extraction), `src/ensemble_pop.py` (PoP) and
`src/validate_gefs_grib.py` (the raw-GRIB audit) all need the same three
things: where the archive lives, what its grid is, and how a city maps onto a
chunk tile. Those belong in one place so the three cannot drift apart.

Three archives, one code path
-----------------------------
This began as GEFS alone and has since taken ECMWF's IFS ENS and AIFS ENS for
the physics-versus-ML comparison (Task 30). Every per-archive fact lives in
`config.ENSEMBLE_SOURCES` and nothing here knows which source it is holding.
That is not tidiness for its own sake: Task 30's claim is a *difference*
between two ensembles, so any step one of them goes through alone becomes a
candidate explanation for that difference, and the cheapest way to retire the
objection is for no such step to exist.

What this archive is, and why it was chosen
-------------------------------------------
`dynamical.org` republishes NOAA GEFS as a time-optimised Zarr v3 / Icechunk
store on AWS Open Data. The alternative routes were assessed in
`plans/2026-09-15-external-ensemble-archive-feasibility-v1.md`: TIGGE is
licence-blocked (CC BY-NC plus a no-redistribution clause), raw GRIB2 costs
355 GB and ~1.3 M range requests, and the ECMWF AWS replica returns HTTP 503
under any sustained load. This store costs one chunk read per city-day.

The decisive property is the chunk geometry. GEFS chunks `[1, 31, 64, 17, 16]`
and both ECMWF stores chunk `[1, 51, every lead, 32, 32]`: one chunk carries
ALL members and a long run of leads for a single grid tile. So the unit of
work is the (tile, init_time) pair, not the city - every city inside the same
tile is served by one read. That is the entire reason the approach is
affordable, and it is why the collector groups cities by tile rather than
looping over cities.

Step ladders are read, never assumed
------------------------------------
GEFS publishes 3-hourly steps, AIFS ENS 6-hourly, IFS ENS 3-hourly out to
144 h and 6-hourly beyond it. Code that assumed any one of those would be
wrong by a factor of two on the others while still producing entirely
plausible numbers, so `Grid.lead_hours` carries the real ladder and every
accumulation downstream takes its step durations from there.

G16: never hard-code the URL
----------------------------
`data.dynamical.org` URLs are retired on 2026-09-30, and this study's headline
contribution is a provenance audit - hard-coding a third-party endpoint would
be exactly the failure mode the paper criticises. The icechunk asset is
therefore resolved from the STAC catalogue at run time and cached with a
timestamp; the cache expires so a moved asset is picked up automatically
rather than failing months later. The resolved licence string is stored
alongside it, because the CC-BY-4.0 status is what makes the derived dataset
releasable and must be re-checked, not assumed.

Rounded mantissas
-----------------
dynamical.org stores values with rounded floating-point mantissas for
compression (Klower et al. 2021). Immaterial at a 0.2 mm threshold, but it is
a disclosed deviation from the raw source and is one of the reasons
`src/validate_gefs_grib.py` exists.

Usage:
    python src/ens_archive.py resolve [src] [--force]  # STAC -> cached asset
    python src/ens_archive.py info [src]               # dims, grid, licence
    python src/ens_archive.py tiles [src] [city_set]   # tiles for a city set

`src` is a key of config.ENSEMBLE_SOURCES; it defaults to `gefs`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from config import (
    ENSEMBLE_SOURCES,
    GEFS_STAC_CATALOG,
    GEFS_STAC_MAX_AGE_DAYS,
    City,
    EnsembleSource,
    load_capitals,
    load_cities,
)
from fetch import fetch_json, is_error, reason

GEFS = ENSEMBLE_SOURCES["gefs"]


def source(key: str | EnsembleSource | None) -> EnsembleSource:
    """Resolve a source key to its record; `None` means GEFS."""
    if key is None:
        return GEFS
    if isinstance(key, EnsembleSource):
        return key
    if key not in ENSEMBLE_SOURCES:
        raise SystemExit(f"unknown ensemble source {key!r}; known: "
                         f"{sorted(ENSEMBLE_SOURCES)}")
    return ENSEMBLE_SOURCES[key]

# --------------------------------------------------------------------------
# STAC resolution (G16)
# --------------------------------------------------------------------------


def _get_json(url: str) -> dict:
    """One STAC GET. Uses the project's fetch layer for throttling and backoff.

    The response is deliberately NOT put in the shared HTTP cache: this module
    keeps its own dated cache (GEFS_STAC_CACHE) so that the age policy is
    explicit and auditable, rather than being an invisible property of a hash
    directory.
    """
    payload = fetch_json(url, {}, use_cache=False, write_cache=False)
    if is_error(payload):
        raise RuntimeError(f"STAC fetch failed for {url}: {reason(payload)}")
    return payload


def resolve_icechunk_asset(src=None, *, force: bool = False) -> dict:
    """Resolve one source's icechunk asset from the STAC catalogue.

    Returns a dict with `bucket`, `prefix`, `region`, `anon`, `href`,
    `license`, `collection_version` and `resolved_utc`.

    Cached because the daily collector must not depend on stac.dynamical.org
    being up, but the cache EXPIRES (GEFS_STAC_MAX_AGE_DAYS) so a moved or
    re-versioned asset is picked up on its own. A stale hard-coded URL that
    silently keeps serving an old version is the specific risk G16 names.
    """
    src = source(src)
    cache = src.stac_cache
    if not force and cache.exists():
        cached = json.loads(cache.read_text())
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(cached["resolved_utc"])).days
        if age <= GEFS_STAC_MAX_AGE_DAYS:
            return cached
        print(f"  {src.key}: STAC asset cache is {age} days old - re-resolving")

    catalog = _get_json(GEFS_STAC_CATALOG)
    children = [ln for ln in catalog.get("links", [])
                if ln.get("rel") == "child"
                and f"/{src.collection_id}/" in ln.get("href", "")]
    if not children:
        raise RuntimeError(
            f"{src.collection_id} is no longer a child of "
            f"{GEFS_STAC_CATALOG}. The archive has moved or been renamed; "
            f"resolve the new collection id before collecting anything, and "
            f"do NOT fall back to a hard-coded URL (plan G16).")

    coll = _get_json(children[0]["href"])
    if coll.get("id") != src.collection_id:
        raise RuntimeError(f"collection id mismatch: asked for "
                           f"{src.collection_id}, got {coll.get('id')}")

    assets = coll.get("assets", {})
    asset = assets.get("icechunk")
    if asset is None:
        raise RuntimeError(
            f"no `icechunk` asset on {src.collection_id}; assets are "
            f"{sorted(assets)}. The store format changed - re-read the "
            f"collection before assuming a fallback works.")

    href = asset["href"]
    if not href.startswith("s3://"):
        raise RuntimeError(f"unexpected icechunk href scheme: {href}")
    bucket, _, prefix = href[len("s3://"):].partition("/")
    prefix = prefix.rstrip("/")

    opts = asset.get("xarray:storage_options", {})
    region = opts.get("client_kwargs", {}).get("region_name")
    if region is None:
        raise RuntimeError(f"no region in the asset's storage options: {opts}")

    lic = coll.get("license")
    if lic != "CC-BY-4.0":
        # The releasable derived dataset (Scientific Data / ESSD, sequenced
        # first) depends on this licence. A change must stop the pipeline, not
        # be discovered at submission.
        raise RuntimeError(
            f"{src.collection_id} licence is now {lic!r}, not CC-BY-4.0. "
            f"The derived-dataset release depends on CC-BY; stop and "
            f"re-assess (plan G15/G16) before collecting further.")

    out = {
        "source": src.key,
        "collection_id": coll["id"],
        "collection_version": coll.get("version"),
        "href": href,
        "bucket": bucket,
        "prefix": prefix,
        "region": region,
        "anon": bool(opts.get("anon", True)),
        "license": lic,
        "attribution": coll.get("attribution"),
        "resolved_utc": datetime.now(timezone.utc).isoformat(),
        "catalog": GEFS_STAC_CATALOG,
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, indent=2, sort_keys=True))
    return out


# --------------------------------------------------------------------------
# Opening the store
# --------------------------------------------------------------------------
_DATASETS: dict = {}


def open_dataset(src=None, *, force_resolve: bool = False):
    """Open one archive read-only and anonymously; memoised per source.

    Opening costs ~5 s (Icechunk manifest fetch), so a collector that reopened
    per tile would spend more time on handshakes than on data.
    """
    src = source(src)
    if src.key in _DATASETS:
        return _DATASETS[src.key]

    import icechunk
    import xarray as xr

    asset = resolve_icechunk_asset(src, force=force_resolve)
    if not asset["anon"]:
        raise RuntimeError("the STAC asset no longer advertises anonymous "
                           "access; credentials would be required")
    storage = icechunk.s3_storage(bucket=asset["bucket"],
                                  prefix=asset["prefix"],
                                  region=asset["region"],
                                  anonymous=True)
    repo = icechunk.Repository.open(storage)
    session = repo.readonly_session("main")
    # chunks=None: eager numpy reads. dask would add a scheduler between us and
    # a read pattern we already control exactly (one chunk per tile per init),
    # and would hide how many chunks each step actually fetches.
    ds = xr.open_zarr(session.store, consolidated=False, chunks=None,
                      decode_timedelta=True)

    if src.variable not in ds:
        raise RuntimeError(f"{src.variable} missing from {src.collection_id}; "
                           f"variables are {sorted(ds.data_vars)[:10]}...")
    _DATASETS[src.key] = ds
    return ds


# --------------------------------------------------------------------------
# Grid and chunk tiles
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Grid:
    """The archive's spatial grid and its chunk tiling, read from the store.

    Read rather than assumed: a silent regrid (0.25 -> 0.5 deg) or a rechunk
    would otherwise turn every extraction into a plausible-looking wrong
    answer. `validate` fails loudly instead.
    """

    latitude: np.ndarray
    longitude: np.ndarray
    chunk_lat: int
    chunk_lon: int
    chunk_lead: int
    n_members: int
    lead_hours: np.ndarray
    src: EnsembleSource

    @property
    def step_hours(self) -> np.ndarray:
        """Duration of the accumulation interval ending at each lead index.

        Index i covers (lead_hours[i-1], lead_hours[i]]. Index 0 is the
        initialisation instant and covers nothing, so its duration is zero -
        which is also why the archives store NaN there.
        """
        d = np.diff(self.lead_hours, prepend=self.lead_hours[0])
        d[0] = 0.0
        return d

    def validate(self) -> None:
        s = self.src
        exp = (s.chunk_lat, s.chunk_lon, s.chunk_lead, s.members)
        got = (self.chunk_lat, self.chunk_lon, self.chunk_lead, self.n_members)
        if got != exp:
            raise RuntimeError(
                f"{s.key}: archive geometry changed, chunk/member shape {got} "
                f"!= {exp}. The tile-grouping cost model in "
                f"plans/2026-09-15-external-ensemble-archive-feasibility-v1.md "
                f"is derived from the old shape - re-measure before trusting "
                f"any projection.")
        dlat = np.diff(self.latitude)
        dlon = np.diff(self.longitude)
        if not (np.allclose(dlat, -0.25) and np.allclose(dlon, 0.25)):
            raise RuntimeError(f"{s.key}: grid spacing is no longer 0.25 deg")

    def cell(self, lat: float, lon: float) -> tuple[int, int]:
        """Nearest grid-cell indices for a point.

        Nearest-cell, not interpolation: the study verifies what a grid box
        forecasts, and bilinear interpolation would smooth precipitation
        further, deepening the area-versus-point bias the feasibility report
        flags in its section 5.
        """
        lon = ((lon + 180.0) % 360.0) - 180.0
        return (int(np.abs(self.latitude - lat).argmin()),
                int(np.abs(self.longitude - lon).argmin()))

    def tile(self, ilat: int, ilon: int) -> tuple[int, int]:
        return ilat // self.chunk_lat, ilon // self.chunk_lon

    def tile_slice(self, tile: tuple[int, int]) -> tuple[slice, slice]:
        tlat, tlon = tile
        return (slice(tlat * self.chunk_lat,
                      min((tlat + 1) * self.chunk_lat, len(self.latitude))),
                slice(tlon * self.chunk_lon,
                      min((tlon + 1) * self.chunk_lon, len(self.longitude))))


def grid_of(ds, src=None) -> Grid:
    src = source(src)
    enc = ds[src.variable].encoding["preferred_chunks"]
    lead = ds.lead_time.values.astype("timedelta64[s]").astype(np.int64) / 3600.0
    g = Grid(latitude=ds.latitude.values, longitude=ds.longitude.values,
             chunk_lat=enc["latitude"], chunk_lon=enc["longitude"],
             chunk_lead=enc["lead_time"], n_members=ds.sizes["ensemble_member"],
             lead_hours=lead, src=src)
    g.validate()
    # The ladder is allowed to coarsen with lead - IFS ENS goes 3-hourly to
    # 144 h and 6-hourly after, GEFS 3-hourly to 240 h - but it must start
    # where the source says it does and it must stay monotonic, because the
    # accumulation maths downstream reads interval widths straight off it. A
    # ladder that silently doubled would halve every day total.
    step = np.diff(lead)
    if not np.all(step > 0):
        raise RuntimeError(f"{src.key}: lead_time is not increasing")
    if abs(step[0] - src.step_hint_h) > 1e-9:
        raise RuntimeError(
            f"{src.key}: first step is {step[0]} h, not the registered "
            f"{src.step_hint_h} h. Either the archive rebuilt its ladder or "
            f"the registry is stale; do not collect until it is resolved.")
    if lead[0] != 0.0:
        raise RuntimeError(f"{src.key}: lead_time does not start at 0 h")
    # Local day 7 ends at most 192 h after a 00Z init (UTC-12); anything
    # shorter cannot serve the study's lead range for every time zone.
    if lead[-1] < 192.0:
        raise RuntimeError(f"{src.key}: leads stop at {lead[-1]} h, which no "
                           f"longer covers local day 7 for every time zone")
    return g


def group_by_tile(cities: dict[str, City], grid: Grid
                  ) -> dict[tuple[int, int], list[tuple[str, int, int]]]:
    """Cities bucketed by chunk tile: {tile: [(name, ilat, ilon), ...]}.

    This is the whole cost model. Cities cluster hard, so the tile count grows
    far more slowly than the city count - 2,000 cities need 480 tiles, 8,509
    need 715 - which is why scale-up is roughly linear in *tiles*, not cities.
    """
    out: dict[tuple[int, int], list[tuple[str, int, int]]] = {}
    for name, city in sorted(cities.items()):
        ilat, ilon = grid.cell(city.latitude, city.longitude)
        out.setdefault(grid.tile(ilat, ilon), []).append((name, ilat, ilon))
    return out


# --------------------------------------------------------------------------
# Network accounting
# --------------------------------------------------------------------------
def rx_bytes() -> int:
    """Total bytes received by the machine, for measuring real transfer.

    The zarr/icechunk stack does not report how much it fetched, and the
    feasibility report's 0.226 MB per (tile, init) is the number the whole
    scale-up projection rests on, so it is re-measured on every run rather
    than quoted.
    """
    total = 0
    with open("/proc/net/dev") as fh:
        for line in fh.readlines()[2:]:
            iface, _, rest = line.partition(":")
            if iface.strip() == "lo":
                continue
            total += int(rest.split()[0])
    return total


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _ladder_segments(lead_hours: np.ndarray) -> list[tuple[float, float]]:
    """The lead ladder as (step length, hour it runs to) runs.

    Printed rather than assumed, because "3-hourly" is true of GEFS to 240 h
    and of IFS ENS only to 144 h, and that difference is exactly the kind of
    thing that is easy to carry an assumption about.
    """
    step = np.diff(lead_hours)
    out: list[tuple[float, float]] = []
    for i, s in enumerate(step):
        if out and abs(out[-1][0] - s) < 1e-9:
            out[-1] = (s, float(lead_hours[i + 1]))
        else:
            out.append((float(s), float(lead_hours[i + 1])))
    return out


def _info(src=None) -> None:
    src = source(src)
    asset = resolve_icechunk_asset(src)
    print(json.dumps(asset, indent=2, sort_keys=True))
    ds = open_dataset(src)
    grid = grid_of(ds, src)
    init = ds.init_time.values
    print(f"\ninit_time      {len(init)}  {init[0]} .. {init[-1]}")
    gaps = np.unique(np.diff(init).astype("timedelta64[h]").astype(int))
    print(f"init spacing   {gaps} hours (a single value means gapless)")
    print(f"members        {grid.n_members}")
    ladder = " then ".join(f"{s:.0f}-hourly to {h:.0f} h"
                           for s, h in _ladder_segments(grid.lead_hours))
    print(f"lead_time      {ds.sizes['lead_time']} steps, "
          f"{grid.lead_hours[0]:.0f}..{grid.lead_hours[-1]:.0f} h, {ladder}")
    print(f"grid           {len(grid.latitude)} x {len(grid.longitude)} "
          f"@ 0.25 deg")
    print(f"chunk          lead={grid.chunk_lead} lat={grid.chunk_lat} "
          f"lon={grid.chunk_lon}")
    v = ds[src.variable]
    print(f"{src.variable}: units={v.attrs.get('units')} "
          f"step_type={v.attrs.get('step_type')}")
    print(f"comment: {v.attrs.get('comment')}")


def _tiles(src=None, city_set: str = "capitals") -> None:
    cities = load_capitals() if city_set == "capitals" else load_cities()
    grid = grid_of(open_dataset(src), src)
    tiles = group_by_tile(cities, grid)
    for tile, members in sorted(tiles.items()):
        print(f"  tile {tile}: {len(members):3d} cities  "
              f"{', '.join(n for n, _, _ in members[:6])}"
              f"{' ...' if len(members) > 6 else ''}")
    print(f"\n{len(cities)} cities -> {len(tiles)} chunk tiles "
          f"({len(cities) / len(tiles):.1f} cities per tile)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "info"
    rest = [a for a in sys.argv[2:] if not a.startswith("-")]
    key = rest[0] if rest and rest[0] in ENSEMBLE_SOURCES else None
    rest = rest[1:] if key else rest
    if cmd == "resolve":
        print(json.dumps(resolve_icechunk_asset(key, force="--force" in sys.argv),
                         indent=2, sort_keys=True))
    elif cmd == "info":
        _info(key)
    elif cmd == "tiles":
        _tiles(key, rest[0] if rest else "capitals")
    else:
        raise SystemExit(f"unknown command: {cmd}")
