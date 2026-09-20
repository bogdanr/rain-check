"""Extraction of ensemble members at city points (plan Tasks 20a, 30).

Route (ii) of the probability-triangulation design D3: a member-level
ensemble, independent of the vendor, covering the whole verification window
retrospectively. Unlike `src/collect_ensemble.py`, which races a three-day
vendor retention window and can never be rebuilt, these archives are
retrospective and gapless, so this collector is a batch job that can be
re-run, resumed and repointed at a different city set or a different ensemble
at will.

Three sources, identical treatment
----------------------------------
`gefs` (NOAA, 31 members) is the triangulation ensemble; `ifs_ens` and
`aifs_ens` (ECMWF, 51 members each) are the physics-versus-ML pair of Task 30.
Which one is being collected is a command-line argument and nothing else:
every per-archive fact lives in `config.ENSEMBLE_SOURCES`. For Task 30 that is
load-bearing rather than decorative, because the finding is a difference
between two of them and any step applied to only one would be an alternative
explanation for it.

The cost model, which is the whole design
-----------------------------------------
A chunk carries every member and a long run of leads for one tile - GEFS
`[1, 31, 64, 17, 16]`, the ECMWF stores `[1, all leads, 51, 32, 32]`. The unit
of work is therefore the (tile, init) pair, not the city: all cities inside a
tile are served by one read, which is why the collector groups by tile first
and only then indexes out the cities. At 2,000 cities that is 480 GEFS tiles
rather than 2,000 point queries.

Note the dimension ORDER above: GEFS puts member before lead and both ECMWF
archives put lead before member. Reads are transposed to one canonical order
before anything is indexed out of them, because positional indexing would
otherwise transpose AIFS silently and produce a plausible wrong answer.

A correction to the feasibility report's projection, measured here rather than
assumed. Local day k ends 24(k+1) - utc_offset hours after the 00Z init, so on
GEFS's 64-lead chunking:

  * local days 1-6 need at most 180 h -> lead index 60 -> ONE chunk, for every
    time zone on Earth;
  * local day 7 needs lead index 64 for any UTC offset below +3 h, which is a
    SECOND chunk fetched for the sake of one or two 3-hour steps.

So the report's "one chunk read per city-day" holds for days 1-6 and for
UTC+3 and east; elsewhere day 7 doubles the transfer. The ECMWF stores keep
every lead in one chunk and so never pay that second read - at the cost of a
chunk about ten times larger. The run prints measured chunks and megabytes per
(tile, init) so the scale-up projection is re-derived from what actually
happened, not quoted.

Output layout
-------------
    data/raw/<source>/<YYYY-MM>/<source>_<city>.parquet
    data/raw/<source>/<YYYY-MM>/manifest.json

One parquet per city per calendar month of initialisations, long form
(`init_time, lead_index, lead_hours, member, precip_rate`). Per city rather
than per tile because the tile is an implementation detail of the store: tile
membership changes the moment the city registry grows, which would make tile
files silently non-idempotent, whereas a per-city file stays valid forever.
Per month because the month is a unit small enough that an interrupted run
loses little and large enough that the file count stays manageable at
thousands of cities.

`lead_hours` is stored beside `lead_index` because the three archives do not
share a step ladder - GEFS 3-hourly, AIFS ENS 6-hourly, IFS ENS both - so an
index alone does not say what interval a value covers. Everything downstream
accumulates from the hours, never from the index.

Resumable and idempotent in the repo's usual sense (cf.
`collect_ensemble.collect_city_model`): the parquet is the unit of completion.
A month is skipped when its file exists, carries every initialisation date in
the requested window, and reaches at least the lead index the requested lead
days need. Re-running costs nothing for what is already on disk, and widening
the lead range or extending the window re-fetches only what is genuinely new.

Values are stored as the archive serves them - mean precipitation RATE in
kg m-2 s-1 over the preceding step - not as accumulations or probabilities.
The raw quantity is the archive of record here; thresholds, day conventions
and boundary handling are all decisions that will be revisited (see
src/ensemble_pop.py), and baking any of them into the stored data would force
a re-download to change one's mind.

Usage:
    python src/collect_members.py plan [src] [capitals|cities]
    python src/collect_members.py collect [src] [capitals|cities] \\
        [--start=] [--end=] [--months=N] [--force]
    python src/collect_members.py status [src]
    python src/collect_members.py sanity [src]      # the Bucharest check

`src` is a key of config.ENSEMBLE_SOURCES and defaults to `gefs`.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from config import (
    ENSEMBLE_SOURCES,
    GEFS_THREADS,
    LEAD_DAYS,
    City,
    load_capitals,
    load_cities,
)
from ens_archive import (
    grid_of,
    group_by_tile,
    open_dataset,
    rx_bytes,
    source,
)
from ensemble_pop import required_lead_index


def _slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def out_path(src, city_name: str, month: str):
    return src.raw / month / f"{src.key}_{_slug(city_name)}.parquet"


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------
def plan(src, cities: dict[str, City], inits: np.ndarray,
         lead_days=LEAD_DAYS) -> dict:
    """Tiles, lead depth and projected transfer. Reads no data.

    The lead depth is computed per TILE as the maximum its cities need,
    because the read is per tile: one city at UTC+0 in a tile forces the
    second lead chunk for everyone in it.

    The maximum is taken over every initialisation in the range, not over a
    sample. Daylight saving moves a city's UTC offset by an hour twice a year,
    which is enough to push local day 7 across a chunk boundary: a depth
    planned in July and applied in January would silently truncate the last
    day of every European city for half the record.
    """
    src = source(src)
    grid = grid_of(open_dataset(src), src)
    tiles = group_by_tile(cities, grid)

    # Reading one index past a chunk boundary costs a whole extra chunk, so
    # the requirement is rounded up to the store's own lead chunking and the
    # chunk count that implies is what gets reported as the true cost.
    lead_chunk = grid.chunk_lead
    per_tile = {}
    for tile, members in tiles.items():
        need = tile_need_index(cities, members, inits, lead_days,
                               grid.lead_hours)
        n_lead = min(int(np.ceil((need + 1) / lead_chunk) * lead_chunk),
                     len(grid.lead_hours))
        per_tile[tile] = {"cities": members, "need_index": need,
                          "n_lead": n_lead,
                          "chunks": int(np.ceil(n_lead / lead_chunk))}
    total_chunks = sum(t["chunks"] for t in per_tile.values()) * len(inits)
    return {"grid": grid, "tiles": per_tile, "inits": inits,
            "total_chunks": total_chunks, "src": src}


def tile_need_index(cities: dict[str, City], members, inits, lead_days,
                    lead_hours) -> int:
    """Deepest lead index any city in one tile needs, over all `inits`.

    Cached per (timezone, init-range) because a tile's answer depends only on
    its cities' time zones, and at thousands of cities the same handful of
    zones recurs constantly.
    """
    return max(_tz_need(cities[name].timezone, inits, tuple(lead_days),
                        lead_hours)
               for name, _, _ in members)


_TZ_NEED: dict = {}


def _tz_need(tz: str, inits, lead_days: tuple, lead_hours) -> int:
    key = (tz, len(inits), str(inits[0]), str(inits[-1]), lead_days,
           float(lead_hours[1]), len(lead_hours))
    if key not in _TZ_NEED:
        _TZ_NEED[key] = max(required_lead_index(pd.Timestamp(t), tz, lead_hours,
                                                lead_days)
                            for t in inits)
    return _TZ_NEED[key]


def _print_plan(p: dict, cities: dict[str, City], mb_per_chunk: float) -> None:
    counts = sorted({t["chunks"] for t in p["tiles"].values()})
    spread = ", ".join(
        f"{sum(1 for t in p['tiles'].values() if t['chunks'] == c)} tiles "
        f"need {c}" for c in counts)
    print(f"source            {p['src'].key} ({p['src'].label}, "
          f"{p['src'].members} members)")
    print(f"cities            {len(cities)}")
    print(f"chunk tiles       {len(p['tiles'])} "
          f"({len(cities) / len(p['tiles']):.1f} cities per tile)")
    print(f"initialisations   {len(p['inits'])}  "
          f"{pd.Timestamp(p['inits'][0]).date()} .. "
          f"{pd.Timestamp(p['inits'][-1]).date()}")
    print(f"lead chunks/tile  {spread} "
          f"(2 = a time zone west of UTC+3 needs local day 7)")
    print(f"chunk reads       {p['total_chunks']:,}")
    print(f"projected transfer ~{p['total_chunks'] * mb_per_chunk / 1024:.1f} "
          f"GB at {mb_per_chunk:.3f} MB per chunk (measured)")


# --------------------------------------------------------------------------
# Completion test
# --------------------------------------------------------------------------
def _month_complete(src, city_name: str, month: str,
                    want_inits: list[pd.Timestamp], need_index: int) -> bool:
    path = out_path(src, city_name, month)
    if not path.exists():
        return False
    try:
        df = pd.read_parquet(path, columns=["init_time", "lead_index"])
    except Exception:
        return False
    have = set(pd.to_datetime(df["init_time"]).unique())
    if not set(want_inits).issubset(have):
        return False
    # A widened lead range must re-fetch: silently serving a short file would
    # drop local day 7 for half the world without saying so.
    return int(df["lead_index"].max()) >= need_index


# --------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------
def collect(src=None, city_set: str = "capitals", start: str | None = None,
            end: str | None = None, lead_days=LEAD_DAYS,
            *, force: bool = False, max_months: int | None = None) -> None:
    src = source(src)
    start = start or src.window_start
    end = end or src.window_end
    cities = load_capitals() if city_set == "capitals" else load_cities()
    ds = open_dataset(src)
    var = ds[src.variable]

    all_inits = pd.to_datetime(ds.init_time.values)
    # Archives that publish more than one cycle a day (IFS ENS runs 00 and 12Z)
    # are filtered to the registered initialisation hours rather than taken
    # whole: mixing cycles would double the sample with forecasts of different
    # age and make "lead day 1" mean two things at once.
    keep = np.isin(all_inits.hour, np.array(src.init_hours))
    sel = keep & (all_inits >= pd.Timestamp(start)) & (all_inits <= pd.Timestamp(end))
    inits = all_inits[sel]
    if len(inits) == 0:
        raise SystemExit(f"no {src.key} initialisations in {start}..{end}")
    # Gaplessness is a stated property of these archives (D1/G6 depend on it),
    # so it is checked here rather than discovered as a hole in the metrics.
    gaps = pd.date_range(inits[0], inits[-1], freq="D").difference(inits)
    if len(gaps):
        print(f"WARNING: {len(gaps)} missing initialisation days in the "
              f"window, first {gaps[0].date()} - the ensemble series will "
              f"have holes the vendor series does not")

    p = plan(src, cities, inits.values, lead_days)
    _print_plan(p, cities, src.mb_per_chunk)
    init_index = {pd.Timestamp(t): i for i, t in enumerate(all_inits)}

    months = sorted({month_key(t.date()) for t in inits})
    if max_months:
        months = months[:max_months]
    print(f"\n== extracting {len(months)} month partitions ==")

    t_run, rx0, chunks_read = time.time(), rx_bytes(), 0
    for month in months:
        m_inits = [t for t in inits if month_key(t.date()) == month]
        pending = {}
        for tile, info in p["tiles"].items():
            todo = [(n, ila, ilo) for n, ila, ilo in info["cities"]
                    if force or not _month_complete(src, n, month, m_inits,
                                                    info["need_index"])]
            if todo:
                pending[tile] = (info, todo)
        if not pending:
            print(f"  {month}: complete ({len(p['tiles'])} tiles) - skipped")
            continue

        t0, rx_m0 = time.time(), rx_bytes()
        n_chunk_m = 0
        frames: dict[str, list[pd.DataFrame]] = {}
        for tile, (info, todo) in sorted(pending.items()):
            lat_sl, lon_sl = p["grid"].tile_slice(tile)
            n_lead = info["n_lead"]

            def read(t_init, n_lead=n_lead, lat_sl=lat_sl, lon_sl=lon_sl):
                # One .values call per (tile, init) -> exactly `chunks` chunk
                # fetches. Any slicing that crossed a tile boundary here would
                # silently multiply the cost, which is why the slices come
                # from Grid.tile_slice rather than from the city coordinates.
                #
                # The transpose is not cosmetic: GEFS stores
                # (init, member, lead, lat, lon) and both ECMWF archives store
                # (init, lead, member, lat, lon). Indexing positionally would
                # read 51 "members" of 61 "leads" from AIFS - transposed data
                # that still has the right shape and entirely plausible values.
                return (var.isel(init_time=init_index[t_init],
                                 lead_time=slice(0, n_lead),
                                 latitude=lat_sl, longitude=lon_sl)
                        .transpose("ensemble_member", "lead_time",
                                   "latitude", "longitude").values)

            with ThreadPoolExecutor(max_workers=GEFS_THREADS) as pool:
                blocks = list(pool.map(read, m_inits))
            n_chunk_m += len(m_inits) * info["chunks"]

            lead_h = p["grid"].lead_hours[:n_lead].astype("float32")
            for name, ila, ilo in todo:
                i, j = ila - lat_sl.start, ilo - lon_sl.start
                cube = np.stack([b[:, :, i, j] for b in blocks])  # init,mem,lead
                if cube.shape[1] != src.members:
                    raise RuntimeError(
                        f"{name}: {cube.shape[1]} members, expected "
                        f"{src.members} - PoP resolution and every sharpness "
                        f"comparison depend on the member count")
                n_i, n_m, n_l = cube.shape
                frames.setdefault(name, []).append(pd.DataFrame({
                    "init_time": np.repeat(np.asarray(m_inits, dtype="datetime64[ns]"),
                                           n_m * n_l),
                    "member": np.tile(np.repeat(np.arange(n_m, dtype="int16"), n_l), n_i),
                    "lead_index": np.tile(np.arange(n_l, dtype="int16"), n_i * n_m),
                    "lead_hours": np.tile(lead_h, n_i * n_m),
                    "precip_rate": cube.reshape(-1).astype("float32"),
                }))

        written = 0
        for name, fr in frames.items():
            df = pd.concat(fr, ignore_index=True).sort_values(
                ["init_time", "member", "lead_index"], ignore_index=True)
            path = out_path(src, name, month)
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(path, index=False, compression="zstd")
            written += 1

        rx_m = (rx_bytes() - rx_m0) / 1e6
        chunks_read += n_chunk_m
        _manifest(src, month, m_inits, pending, n_chunk_m, rx_m,
                  time.time() - t0, lead_days)
        print(f"  {month}: {len(m_inits)} inits x {len(pending)} tiles -> "
              f"{written} city files, {n_chunk_m} chunks, {rx_m:.1f} MB, "
              f"{time.time() - t0:.1f}s "
              f"({rx_m / max(1, n_chunk_m):.3f} MB/chunk)")

    rx = (rx_bytes() - rx0) / 1e6
    dt = time.time() - t_run
    print(f"\n{chunks_read:,} chunk reads, {rx:.1f} MB, {dt / 60:.1f} min")
    if chunks_read:
        print(f"measured cost: {rx / chunks_read:.3f} MB and "
              f"{dt / chunks_read:.3f} s per chunk "
              f"(registry expects {src.mb_per_chunk:.3f} MB)")
        _project(rx / chunks_read, dt / chunks_read, p, len(inits))


def _project(mb_per_chunk: float, s_per_chunk: float, p: dict, n_inits: int
             ) -> None:
    """What the same code costs at the scales G1 demands.

    Tile counts from the feasibility report's measurement over
    data/raw/cities15000.txt; the per-chunk cost is the one just measured on
    this machine, so the projection is grounded rather than quoted.
    """
    chunks_per_tile = (sum(t["chunks"] for t in p["tiles"].values())
                       / max(1, len(p["tiles"])))
    print("\nprojection at the measured per-chunk cost "
          f"({chunks_per_tile:.2f} lead chunks per tile, {n_inits} inits):")
    for label, tiles in (("2,000 cities", 480), ("3,000 cities", 560),
                         ("8,509 cities (G1 pool)", 715)):
        n = tiles * chunks_per_tile * n_inits
        print(f"  {label:24s} {tiles:4d} tiles -> {n:9,.0f} chunks, "
              f"{n * mb_per_chunk / 1024:6.1f} GB, "
              f"{n * s_per_chunk / 3600:5.1f} h at this thread count")


def _manifest(src, month: str, m_inits, pending, n_chunks, mb, secs, lead_days
              ) -> None:
    path = src.raw / month / "manifest.json"
    payload = {
        "source": src.key,
        "collection_id": src.collection_id,
        "month": month,
        "n_inits": len(m_inits),
        "first_init": str(m_inits[0]), "last_init": str(m_inits[-1]),
        "lead_days": list(lead_days),
        "tiles": {f"{t[0]},{t[1]}": {
            "cities": [n for n, _, _ in info["cities"]],
            "lead_chunks": info["chunks"], "n_lead": info["n_lead"],
            "need_index": info["need_index"],
        } for t, (info, _) in pending.items()},
        "chunk_reads": n_chunks,
        "megabytes": round(mb, 2),
        "seconds": round(secs, 1),
        "variable": src.variable,
        "units": "kg m-2 s-1 (mean rate over the preceding step)",
        "written_utc": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)


# --------------------------------------------------------------------------
# Status and the published sanity check
# --------------------------------------------------------------------------
def status(src=None) -> None:
    src = source(src)
    files = sorted(src.raw.glob(f"*/{src.key}_*.parquet"))
    if not files:
        print(f"no {src.key} extraction yet under {src.raw}")
        return
    rows = [{"month": f.parent.name, "city": f.stem.replace(f"{src.key}_", ""),
             "mb": f.stat().st_size / 1e6} for f in files]
    df = pd.DataFrame(rows)
    per = df.groupby("month").agg(cities=("city", "nunique"), mb=("mb", "sum"))
    print(per.to_string())
    print(f"\n{df.city.nunique()} cities, {df.month.nunique()} months, "
          f"{len(df)} files, {df.mb.sum():.1f} MB on disk")
    mans = sorted(src.raw.glob("*/manifest.json"))
    if mans:
        tot = [json.loads(m.read_text()) for m in mans]
        c = sum(t["chunk_reads"] for t in tot)
        mb = sum(t["megabytes"] for t in tot)
        s = sum(t["seconds"] for t in tot)
        print(f"transfer to date: {c:,} chunks, {mb / 1024:.2f} GB, "
              f"{s / 60:.1f} min ({mb / max(1, c):.3f} MB/chunk)")


def sanity() -> None:
    """Reproduce the feasibility report's Bucharest probe (its section 4.1).

    GEFS only, deliberately: the number being reproduced is a measurement made
    against that archive, and the point of the check is that the archive still
    serves what it served then.

    Reported there: 2024-09-01 00Z, 31 members x 64 leads, mean rate
    6.67e-06 kg m-2 s-1 = 0.072 mm per 3 h. The comparison is printed over the
    3x3 neighbourhood as well as at the nearest cell, because at 0.25 deg the
    choice of cell moves the answer by more than the quantity being checked -
    which is itself the representativeness point G3 has to quantify.
    """
    src = source("gefs")
    ds = open_dataset(src)
    grid = grid_of(ds, src)
    city = load_capitals()["Bucharest"]
    ila, ilo = grid.cell(city.latitude, city.longitude)
    it = int(np.where(pd.to_datetime(ds.init_time.values)
                      == pd.Timestamp("2024-09-01"))[0][0])
    blk = ds[src.variable].isel(init_time=it, lead_time=slice(0, 64),
                                latitude=slice(ila - 1, ila + 2),
                                longitude=slice(ilo - 1, ilo + 2)).values
    print(f"Bucharest {city.latitude},{city.longitude} -> nearest cell "
          f"{grid.latitude[ila]},{grid.longitude[ilo]} (tile {grid.tile(ila, ilo)})")
    print("init 2024-09-01 00Z, 31 members x 64 leads (lead 0 is NaN by "
          "construction: no accumulation interval)\n")
    print("  lat      lon       mean rate     mm per 3 h")
    for i in range(3):
        for j in range(3):
            m = float(np.nanmean(blk[:, :, i, j]))
            mark = "  <- nearest" if (i, j) == (1, 1) else ""
            print(f"  {grid.latitude[ila - 1 + i]:7.2f} {grid.longitude[ilo - 1 + j]:8.2f} "
                  f"  {m:.3e}   {m * GEFS_STEP_SECONDS:7.4f}{mark}")
    m = float(np.nanmean(blk[:, :, 1, 1]))
    print(f"\nfeasibility report: 6.670e-06 = 0.0720 mm/3h")
    print(f"reproduced here:    {m:.3e} = {m * GEFS_STEP_SECONDS:.4f} mm/3h")
    lo = float(np.nanmin([np.nanmean(blk[:, :, i, j])
                          for i in range(3) for j in range(3)]))
    hi = float(np.nanmax([np.nanmean(blk[:, :, i, j])
                          for i in range(3) for j in range(3)]))
    inside = lo <= 6.67e-06 <= hi
    print(f"3x3 neighbourhood spans {lo:.3e}..{hi:.3e}; the reported value is "
          f"{'INSIDE' if inside else 'OUTSIDE'} it")
    if not inside:
        raise SystemExit("sanity check FAILED - the archive no longer agrees "
                         "with the feasibility measurement even allowing for "
                         "grid-cell choice")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    args = sys.argv[2:]
    key = next((a for a in args if a in ENSEMBLE_SOURCES), None)
    src = source(key)
    positional = [a for a in args
                  if not a.startswith("-") and a not in ENSEMBLE_SOURCES]
    city_set = positional[0] if positional else "capitals"
    kw = {a.split("=")[0][2:]: a.split("=")[1] for a in args if "=" in a}
    if cmd == "plan":
        cs = load_capitals() if city_set == "capitals" else load_cities()
        ds = open_dataset(src)
        it = pd.to_datetime(ds.init_time.values)
        it = it[np.isin(it.hour, np.array(src.init_hours))]
        sel = it[(it >= pd.Timestamp(kw.get("start", src.window_start)))
                 & (it <= pd.Timestamp(kw.get("end", src.window_end)))]
        _print_plan(plan(src, cs, sel.values), cs, src.mb_per_chunk)
    elif cmd == "collect":
        collect(src, city_set, kw.get("start"), kw.get("end"),
                force="--force" in args,
                max_months=int(kw["months"]) if "months" in kw else None)
    elif cmd == "status":
        status(src)
    elif cmd == "sanity":
        sanity()
    else:
        raise SystemExit(f"unknown command: {cmd}")
