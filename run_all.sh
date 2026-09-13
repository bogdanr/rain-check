#!/usr/bin/env bash
# Reproduce the whole study end to end. Safe to re-run: network responses are
# cached under data/cache, so a warm run does no network I/O at all.
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python

echo "== 1/8 collect archives (Tracks A and B, ERA5) =="
(cd src && ../$PY collect_archive.py all)
(cd src && ../$PY collect_archive.py previous_runs icon_eu)

echo "== 2/8 station observations (daily, GHCN) =="
(cd src && ../$PY observations.py)

echo "== 3/8 station observations (hourly present weather, NOAA ISD) =="
(cd src && ../$PY observations_hourly.py)

echo "== 4/8 build verification tables =="
(cd src && ../$PY build_dataset.py)

echo "== 5/8 validate the join (fails loudly on misalignment) =="
(cd src && ../$PY validate_join.py)

echo "== 6/8 analysis and robustness =="
(cd src && ../$PY analyze.py)
(cd src && ../$PY robustness.py)

echo "== 7/8 hourly track, derived-probability events, external benchmarks =="
(cd src && ../$PY hourly.py)
(cd src && ../$PY events.py)
(cd src && ../$PY benchmarks.py)

echo "== 8/10 European capitals: coverage probe, then the multi-city run =="
(cd src && ../$PY probe_capitals.py)
(cd src && ../$PY capitals.py)

echo "== 9/10 forecast provenance audit, then the like-for-like ranking =="
# Which model actually backs the unpinned probability series, per city and per
# month, and does the league table survive holding the forecaster fixed?
(cd src && ../$PY pop_provenance.py)
(cd src && ../$PY capitals.py pinned)

echo "== 10/11 beyond the capitals: probe every city with a usable gauge =="
# GHCN's per-year bulk files replace ~16 GB of per-station downloads, so the
# expanded set costs one 422 MB fetch rather than one request per station.
(cd src && ../$PY probe_cities.py)
(cd src && ../$PY ghcn_bulk.py)
# The Track A leg of this run is API-heavy and can trip Open-Meteo's hourly
# limit; `world metrics` recomputes everything else from cache if that happens.
(cd src && ../$PY capitals.py world)

echo "== 11/11 build the HTML report =="
(cd src && ../$PY report.py)

echo
echo "Done. Open dist/index.html in a browser."
echo "Figures in figures/, tables in data/processed/."
