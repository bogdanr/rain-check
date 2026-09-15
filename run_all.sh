#!/usr/bin/env bash
# Reproduce the whole study end to end. Safe to re-run: network responses are
# cached under data/cache, so a warm run does no network I/O at all.
#
# Warm runs are also fast: every compute step is skipped when its outputs are
# already newer than all of its inputs (src/, run_all.sh, data/raw). A step
# re-runs only if you edited code, new raw data landed, or an output is
# missing. Set SKIP_UNCHANGED=0 to force a full rebuild.
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
SKIP_UNCHANGED=${SKIP_UNCHANGED:-1}

# run_fresh <cmd...> -- <output...>
# Run CMD unless every OUTPUT exists and is newer than the newest file under
# src/, run_all.sh, or data/raw (code, pipeline definition, or raw data changed
# -> recompute; otherwise the outputs are already derived from exactly these
# inputs and recomputing them is pure waste).
#
# data/raw/ensemble is excluded from that input set. The forward ensemble
# collector (src/collect_ensemble.py) writes a new partition there EVERY day by
# design, and no step below consumes it, so counting it as an input would mark
# the entire pipeline stale daily and turn every warm run into a full rebuild.
run_fresh() {
  local -a cmd outs=()
  local seen=0
  for a in "$@"; do
    if [ "$a" = "--" ]; then seen=1; continue; fi
    if [ "$seen" = 0 ]; then cmd+=("$a"); else outs+=("$a"); fi
  done
  if [ "$SKIP_UNCHANGED" = "1" ] && [ ${#outs[@]} -gt 0 ]; then
    local stale=0 o oldest newest
    for o in "${outs[@]}"; do [ -e "$o" ] || stale=1; done
    if [ "$stale" = 0 ]; then
      oldest=$(stat -c %Y "${outs[@]}" | sort -n | head -1)
      newest=$(find src run_all.sh data/raw -path data/raw/ensemble -prune -o \
        -type f -newermt "@$oldest" -print -quit 2>/dev/null | head -1)
      if [ -z "$newest" ]; then
        echo "   (up to date, skipped: ${cmd[*]})"
        return
      fi
    fi
  fi
  "${cmd[@]}"
}

S() { (cd src && "../$PY" "$@"); }

echo "== 1/13 collect archives (Tracks A and B, ERA5) =="
S collect_archive.py all
S collect_archive.py previous_runs icon_eu

echo "== 2/13 station observations (daily, GHCN) =="
S observations.py

echo "== 3/13 station observations (hourly present weather, NOAA ISD) =="
S observations_hourly.py

echo "== 4/13 build verification tables =="
S build_dataset.py

echo "== 5/13 validate the join (fails loudly on misalignment) =="
S validate_join.py

echo "== 6/13 analysis and robustness =="
S analyze.py
S robustness.py

echo "== 7/13 hourly track, derived-probability events, external benchmarks =="
S hourly.py
S events.py
S benchmarks.py

echo "== 8/13 European capitals: coverage probe, then the multi-city run =="
S probe_capitals.py
run_fresh S capitals.py -- \
  data/processed/capitals_metrics.parquet \
  data/processed/capitals_pop.parquet \
  figures/capitals_reliability.png

echo "== 9/13 forecast provenance audit, then the like-for-like ranking =="
# Which model actually backs the unpinned probability series, per city and per
# month, and does the league table survive holding the forecaster fixed?
S pop_provenance.py
run_fresh S capitals.py pinned -- data/processed/capitals_pinned.parquet

echo "== 10/13 beyond the capitals: probe every city with a usable gauge =="
# GHCN's per-year bulk files replace ~16 GB of per-station downloads, so the
# expanded set costs one 422 MB fetch rather than one request per station.
S probe_cities.py
run_fresh S ghcn_bulk.py -- data/raw/ghcn_bulk.parquet
# The Track A leg of this run is API-heavy and can trip Open-Meteo's hourly
# limit; `world metrics` recomputes everything else from cache if that happens.
run_fresh S capitals.py world -- \
  data/processed/cities_metrics.parquet \
  data/processed/cities_pop.parquet \
  figures/cities_reliability.png

echo "== 11/13 multi-provider league: probe coverage, then collect =="
# Which models actually serve a usable PoP archive at which capitals, then the
# multi-model archive collection behind the cross-provider league table.
# Both legs are cache-resumable and rate-limit aware (src/fetch.py backs off on
# 429), but the collection is API-heavy and can still exhaust Open-Meteo's
# hourly limit. That must not kill the run: the report degrades to a visible
# 'Collection pending' notice for whatever is missing, so a failure here is a
# warning, not an error. Never skipped: it must pick up newly published
# archive data on every run.
if S probe_providers.py && S collect_archive.py all-providers; then
  echo "multi-provider collection complete"
else
  echo "WARNING: provider probe/collection failed or was rate-limited;"
  echo "         continuing from cache. Missing legs will show as"
  echo "         'Collection pending' in the report; re-run to resume."
fi

echo "== 12/13 per-provider verification, league table, robustness =="
# capitals.py providers and capitals.py pinned both write
# capitals_pinned.parquet: providers adds the wider model set, and whichever
# ran last owns the file. The report sections filter by model, so the
# skip-if-fresh ordering (pinned first, providers last) is safe.
run_fresh S capitals.py providers -- \
  data/processed/capitals_pinned.parquet \
  data/processed/capitals_pinned_daily.parquet
run_fresh S league.py -- \
  data/processed/league_table.parquet \
  data/processed/league_summary.json
run_fresh S league_robustness.py -- \
  data/processed/league_robustness.parquet

echo "== 13/13 build the HTML report =="
run_fresh S report.py -- dist/index.html

echo
echo "Done. Open dist/index.html in a browser."
echo "Figures in figures/, tables in data/processed/."
echo "Full rebuild from scratch: SKIP_UNCHANGED=0 ./run_all.sh"
