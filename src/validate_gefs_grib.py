"""Audit the dynamical.org GEFS republication against raw NOAA GRIB2 (Task 20c).

Why this module exists
----------------------
The study's most original contribution is a provenance audit: it asks where
the probabilities served to the public actually come from. Building that audit
on a third-party republication of NOAA data without checking the republication
would be self-undermining. G16 therefore requires a documented sample of
city-days to be validated against the primary source, `s3://noaa-gefs-pds`,
and the agreement to be reported - including if it is poor.

Two specific things are being tested, not one:

  1. **Fidelity.** Does `precipitation_surface` x 10800 s equal the 3-hourly
     APCP accumulation in the raw GRIB2 message, at the same grid cell,
     member, initialisation and step?
  2. **Compression loss.** dynamical.org stores values with rounded
     floating-point mantissas (Klower et al. 2021). That is a disclosed,
     deliberate deviation and it must be quantified at the thresholds this
     study uses (0.1 / 0.2 / 1.0 mm), not hand-waved as "immaterial".

How the raw source is read
--------------------------
No bulk download. The `pgrb2sp25` (0.25 deg) product carries a sibling `.idx`
file listing every GRIB message and its byte offset, so a single APCP field
costs one anonymous HTTP range GET of ~280 kB. Members are `gec00` (control)
plus `gep01`..`gep30`, matching the archive's `ensemble_member` 0..30.

The accumulation bucket resets every 6 hours, which is the one subtlety:

    f003 = 0-3 h    f006 = 0-6 h    f009 = 6-9 h    f012 = 6-12 h
    f021 = 18-21 h  f024 = 18-24 h  ...

So a step ending at an odd multiple of 3 h is already a 3-hourly total and
can be compared directly, while a step ending at a multiple of 6 h must be
recovered by differencing the two messages. Both paths are exercised
deliberately; the differencing path is the one that can go wrong, and it is
also the one that exposes GRIB's decimal packing - APCP is stored to a
limited number of decimal places, so a difference of two buckets can come out
slightly negative for a trace amount. That is a property of the raw source,
not of the republication, and is reported separately rather than hidden.

On the dependency
-----------------
This needs a GRIB2 decoder. `eccodes` now ships manylinux wheels with the
ECMWF library bundled (`pip install eccodes cfgrib`; 2.48 here), so it is a
one-command install with no system packages and no conda - the "heavy
dependency" objection to route A in the feasibility report no longer holds for
a validation-sized sample. It is still far too slow for bulk extraction
(~1 range GET per member per step), which is exactly why it is used for an
audit sample and not for collection.

If the decoder were unavailable, the fallback would be to compare the
republication against `noaa-gefs-analysis` or ERA5 instead - a weaker check,
because it confounds republication error with model error - and that
substitution would have to be declared. It is not needed.

Usage:
    python src/validate_gefs_grib.py sample [n_days] [city]
    python src/validate_gefs_grib.py report
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

import numpy as np
import pandas as pd

from config import (
    GEFS_MEMBERS,
    GEFS_STEP_SECONDS,
    GEFS_VARIABLE,
    GEFS_WINDOW_END,
    GEFS_WINDOW_START,
    PROCESSED,
    RAIN_THRESHOLD_VARIANTS_MM,
    RANDOM_SEED,
    load_capitals,
)
from ens_archive import grid_of, open_dataset

# The primary source. Anonymous, free egress (AWS Open Data sponsorship), no
# registration. Deliberately the S3 website endpoint rather than any mirror:
# this is the audit, so it goes to NOAA's own bucket.
PDS = "https://noaa-gefs-pds.s3.amazonaws.com"
PRODUCT = "pgrb2sp25"          # 0.25 deg, matching the archive's grid exactly
FIELD = ":APCP:surface:"

VALIDATION_TABLE = PROCESSED / "gefs_grib_validation.parquet"
VALIDATION_SUMMARY = PROCESSED / "gefs_grib_validation.json"


def member_file(init: date, member: int, fhour: int) -> str:
    name = "gec00" if member == 0 else f"gep{member:02d}"
    return (f"{PDS}/gefs.{init:%Y%m%d}/00/atmos/{PRODUCT}/"
            f"{name}.t00z.pgrb2s.0p25.f{fhour:03d}")


def _http(url: str, headers: dict | None = None, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout).read()


def apcp_field(init: date, member: int, fhour: int) -> np.ndarray | None:
    """One APCP:surface field, by byte range, decoded to a 721x1440 array.

    Returns None when the message or the file is absent, so a single missing
    lead does not abort an audit run; the caller records the absence.
    """
    url = member_file(init, member, fhour)
    try:
        lines = _http(url + ".idx", timeout=60).decode().strip().split("\n")
    except urllib.error.HTTPError as exc:
        print(f"    .idx {url.rsplit('/', 1)[-1]}: HTTP {exc.code}")
        return None
    offs = [int(ln.split(":")[1]) for ln in lines]
    hit = [i for i, ln in enumerate(lines) if FIELD in ln]
    if not hit:
        print(f"    {url.rsplit('/', 1)[-1]}: no {FIELD} message")
        return None
    i = hit[0]
    end = offs[i + 1] - 1 if i + 1 < len(offs) else ""
    raw = _http(url, {"Range": f"bytes={offs[i]}-{end}"})
    if raw[:4] != b"GRIB":
        raise RuntimeError(f"range GET did not return a GRIB message for {url}")

    import eccodes
    h = eccodes.codes_new_from_message(raw)
    try:
        # The raw grid is 0-360 in longitude and 90..-90 in latitude; the
        # archive is -180..180. Asserted rather than assumed, because a
        # silent product change would make every comparison below wrong by a
        # half-globe shift while still looking plausible.
        if (eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")) != (1440, 721):
            raise RuntimeError("raw GEFS grid is no longer 1440x721 @ 0.25 deg")
        if eccodes.codes_get(h, "perturbationNumber") != member:
            raise RuntimeError(
                f"{url}: GRIB perturbationNumber "
                f"{eccodes.codes_get(h, 'perturbationNumber')} != requested "
                f"member {member} - the member naming has changed")
        return eccodes.codes_get_values(h).reshape(721, 1440)
    finally:
        eccodes.codes_release(h)


def raw_step_mm(init: date, member: int, fhour: int, ilat: int, ilon_raw: int
                ) -> tuple[float | None, str]:
    """3-hourly accumulation ending at `fhour`, in mm, from the raw source.

    `fhour` must be a multiple of 3. Buckets reset every 6 h, so a step ending
    at an odd multiple of 3 is read directly and one ending at a multiple of 6
    is recovered by differencing. Returns (mm, how) where `how` records which
    path was taken, so the two can be reported separately.
    """
    if fhour % 3 or fhour <= 0:
        raise ValueError(f"fhour must be a positive multiple of 3, got {fhour}")
    if fhour % 6 == 3:
        f = apcp_field(init, member, fhour)
        return (None if f is None else float(f[ilat, ilon_raw])), "direct"
    a = apcp_field(init, member, fhour)
    b = apcp_field(init, member, fhour - 3)
    if a is None or b is None:
        return None, "difference"
    return float(a[ilat, ilon_raw] - b[ilat, ilon_raw]), "difference"


# --------------------------------------------------------------------------
# The audit sample
# --------------------------------------------------------------------------
def _fmt(r: dict) -> str:
    raw = "----" if r["raw_mm"] is None else f"{r['raw_mm']:.3f}"
    return f"f{r['fhour']:03d} raw={raw} zarr={r['zarr_mm']:.3f}"


def sample(n_days: int = 6, city_name: str = "Bucharest",
           members=(0, 1, 7, 15, 30), fhours=(3, 6, 21, 24, 51, 168)
           ) -> pd.DataFrame:
    """Compare a documented, reproducible sample of (init, member, step) cells.

    The sample is drawn with a fixed seed (config.RANDOM_SEED) so the audit is
    re-runnable and the exact cells cited in the paper can be regenerated.
    Members deliberately include the control (0), the first and last perturbed
    members, and two in between, because a member-indexing error - the most
    likely way a republication goes wrong - would show up as agreement on some
    members and not others.

    Lead hours deliberately mix the two bucket paths (3, 21, 51 are direct;
    6, 24, 168 need differencing) and span days 1-7.
    """
    ds = open_dataset()
    grid = grid_of(ds)
    city = load_capitals()[city_name]
    ilat, ilon = grid.cell(city.latitude, city.longitude)
    # Raw GRIB longitude runs 0..359.75; the archive runs -180..179.75.
    lon_deg = float(grid.longitude[ilon])
    ilon_raw = int(round((lon_deg % 360.0) / 0.25))

    # Positions on the DATASET's own init_time axis, not on a filtered copy.
    # Filtering first and then feeding the filtered position back to `isel`
    # silently offsets every comparison by the number of pre-window
    # initialisations (1,431 of them here) - which looks exactly like a
    # republication that disagrees with its source, so it is the one indexing
    # error this module must not make.
    all_inits = pd.to_datetime(ds.init_time.values)
    in_window = np.where((all_inits >= pd.Timestamp(GEFS_WINDOW_START))
                         & (all_inits <= pd.Timestamp(GEFS_WINDOW_END)))[0]
    rng = np.random.default_rng(RANDOM_SEED)
    # Bias the draw towards days with rain: agreement on a dry day is nearly
    # content-free, since both sources would report zero whatever they did.
    picks = rng.choice(in_window, size=min(n_days * 4, len(in_window)),
                       replace=False)
    wet = []
    for t in sorted(picks):
        blk = ds[GEFS_VARIABLE].isel(init_time=int(t), lead_time=slice(0, 64),
                                     latitude=ilat, longitude=ilon).values
        wet.append((float(np.nansum(blk)), int(t)))
    wet.sort(reverse=True)
    chosen = [t for _, t in wet[:n_days]]

    print(f"{city_name}: cell {grid.latitude[ilat]},{lon_deg} "
          f"(raw grid index [{ilat},{ilon_raw}])")
    print(f"{len(chosen)} initialisations x {len(members)} members x "
          f"{len(fhours)} steps = {len(chosen) * len(members) * len(fhours)} "
          f"comparisons\n")

    rows = []
    for t in chosen:
        init = all_inits[t].date()
        zarr_block = ds[GEFS_VARIABLE].isel(
            init_time=int(t), lead_time=slice(0, 64),
            latitude=ilat, longitude=ilon).values          # (member, lead)
        if zarr_block.shape[0] != GEFS_MEMBERS:
            raise RuntimeError("member count changed mid-audit")
        for m in members:
            for fh in fhours:
                raw, how = raw_step_mm(init, m, fh, ilat, ilon_raw)
                zar = float(zarr_block[m, fh // 3]) * GEFS_STEP_SECONDS
                rows.append({
                    "init": init, "member": m, "fhour": fh, "path": how,
                    "raw_mm": raw, "zarr_mm": zar,
                    "diff_mm": None if raw is None else zar - raw,
                })
            print(f"  {init} member {m:2d}: "
                  + "  ".join(_fmt(r) for r in rows[-len(fhours):]))
    df = pd.DataFrame(rows)
    VALIDATION_TABLE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(VALIDATION_TABLE, index=False)
    print(f"\nwrote {VALIDATION_TABLE}  rows={len(df)}")
    report(df)
    return df


def report(df: pd.DataFrame | None = None) -> dict:
    """Agreement statistics, in the terms the study actually depends on."""
    if df is None:
        if not VALIDATION_TABLE.exists():
            raise SystemExit("no validation sample yet - run `sample` first")
        df = pd.read_parquet(VALIDATION_TABLE)
    ok = df.dropna(subset=["diff_mm"])
    d = ok["diff_mm"].to_numpy()

    out = {
        "n_comparisons": int(len(df)),
        "n_resolved": int(len(ok)),
        "n_missing_raw": int(df["raw_mm"].isna().sum()),
        "max_abs_diff_mm": float(np.abs(d).max()) if len(d) else None,
        "mean_abs_diff_mm": float(np.abs(d).mean()) if len(d) else None,
        "mean_diff_mm": float(d.mean()) if len(d) else None,
        "n_exact": int((np.abs(d) < 1e-6).sum()),
        "n_wet_comparisons": int((ok[["raw_mm", "zarr_mm"]].max(axis=1) > 0).sum()),
        "source": f"{PDS}/gefs.YYYYMMDD/00/atmos/{PRODUCT}/",
        "archive_variable": GEFS_VARIABLE,
    }
    for path, grp in ok.groupby("path"):
        g = grp["diff_mm"].to_numpy()
        out[f"max_abs_diff_mm_{path}"] = float(np.abs(g).max())
        out[f"n_{path}"] = int(len(g))
    # The number that decides whether the republication is usable: how often
    # the two sources disagree about the binary event this study verifies.
    #
    # Reported twice, deliberately. The naive count is dominated by exact ties:
    # raw GRIB APCP is decimally packed so a value is exactly 0.1 mm, while the
    # archive stores a float32 rate whose reconstruction is 0.099778 mm. That
    # is a >= tie-break, not a disagreement about the weather, and counting it
    # as a flip would overstate the republication's error by an order of
    # magnitude. `_tol` applies a 0.005 mm tolerance -- half the raw source's
    # own 0.01 mm packing step, so it can never mask a real difference.
    TIE_TOL_MM = 0.005
    for thr in RAIN_THRESHOLD_VARIANTS_MM:
        raw_wet = ok["raw_mm"] >= thr
        zarr_wet = ok["zarr_mm"] >= thr
        flips = int((raw_wet != zarr_wet).sum())
        near_tie = (ok["raw_mm"] - thr).abs() <= TIE_TOL_MM
        out[f"event_flips_at_{thr}mm"] = flips
        out[f"event_flips_at_{thr}mm_tol"] = int(((raw_wet != zarr_wet)
                                                  & ~near_tie).sum())
    out["event_flip_tolerance_mm"] = TIE_TOL_MM
    # Raw GRIB APCP is decimally packed, so differencing two 6-hourly buckets
    # can yield a small negative. Recorded as a property of the SOURCE.
    out["n_negative_raw"] = int((ok["raw_mm"] < 0).sum())
    out["min_raw_mm"] = float(ok["raw_mm"].min()) if len(ok) else None

    VALIDATION_SUMMARY.write_text(json.dumps(out, indent=2, sort_keys=True,
                                             default=str))
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    print(f"\nwrote {VALIDATION_SUMMARY}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sample"
    args = [a for a in sys.argv[2:] if not a.startswith("-")]
    if cmd == "sample":
        sample(int(args[0]) if args else 6,
               args[1] if len(args) > 1 else "Bucharest")
    elif cmd == "report":
        report()
    else:
        raise SystemExit(f"unknown command: {cmd}")
