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
# data/raw/ensemble and data/raw/gefs are excluded from that input set. The
# forward ensemble collector (src/collect_ensemble.py) writes a new partition
# there EVERY day by design, and the GEFS archive collector
# (src/collect_gefs.py) likewise extends data/raw/gefs as new init_times are
# published. No step below consumes either, so counting them as inputs would
# mark the entire pipeline stale daily and turn every warm run into a full
# rebuild.
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
      newest=$(find src run_all.sh data/raw \
        \( -path data/raw/ensemble -o -path data/raw/gefs \) -prune -o \
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

echo "== 1/16 collect archives (Tracks A and B, ERA5) =="
S collect_archive.py all
S collect_archive.py previous_runs icon_eu

echo "== 2/16 station observations (daily, GHCN) =="
S observations.py

echo "== 3/16 station observations (hourly present weather, NOAA ISD) =="
S observations_hourly.py

echo "== 4/16 build verification tables =="
S build_dataset.py

echo "== 5/16 validate the join (fails loudly on misalignment) =="
S validate_join.py

echo "== 6/16 analysis and robustness =="
S analyze.py
S robustness.py

echo "== 7/16 hourly track, derived-probability events, external benchmarks =="
S hourly.py
S events.py
S benchmarks.py

echo "== 8/16 European capitals: coverage probe, then the multi-city run =="
S probe_capitals.py
run_fresh S capitals.py -- \
  data/processed/capitals_metrics.parquet \
  data/processed/capitals_pop.parquet \
  figures/capitals_reliability.png

echo "== 9/16 forecast provenance audit, then the like-for-like ranking =="
# Which model actually backs the unpinned probability series, per city and per
# month, and does the league table survive holding the forecaster fixed?
S pop_provenance.py
run_fresh S capitals.py pinned -- data/processed/capitals_pinned.parquet

echo "== 10/16 beyond the capitals: probe every city with a usable gauge =="
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

echo "== 11/16 multi-provider league: probe coverage, then collect =="
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

echo "== 12/16 per-provider verification, league table, robustness =="
# capitals.py providers and capitals.py pinned both write
# capitals_pinned.parquet: providers adds the wider model set, and whichever
# ran last owns the file. The report sections filter by model, so the
# skip-if-fresh ordering (pinned first, providers last) is safe.
run_fresh S capitals.py providers -- \
  data/processed/capitals_pinned.parquet \
  data/processed/capitals_pinned_daily.parquet
# Which model ids are one served series, before anything ranks them as two.
# Runs ahead of league.py because league.py consumes its verdict: a duplicate
# left in would take a rank slot, widen every other model's rank interval, and
# present one forecast's agreement with itself as two providers agreeing.
run_fresh S duplicates.py -- \
  data/processed/duplicate_pairs.parquet \
  data/processed/duplicate_groups.parquet \
  data/processed/duplicate_summary.json
run_fresh S league.py -- \
  data/processed/league_table.parquet \
  data/processed/league_summary.json
run_fresh S league_robustness.py -- \
  data/processed/league_robustness.parquet

echo "== 13/16 decision value, CRPS/ROC/sharpness, baselines (Tasks 18, 22-24) =="
# Runs entirely off the parquet tables written above - no network, no cache.
# The correctness checks (economic value of a perfect forecast is 1, of a
# climatology 0; AUC of a random forecast is 0.5; CRPS of a point forecast is
# its absolute error) run first and abort the step if any fails.
run_fresh S decision_metrics.py -- \
  data/processed/decision_metrics.parquet \
  data/processed/decision_value_curves.parquet \
  data/processed/decision_crps_amount.parquet \
  data/processed/decision_clim_sensitivity.parquet \
  figures/economic_value.png \
  figures/discrimination_sharpness.png \
  figures/bss_reference.png

echo "== 14/16 served vs member-derived probability (Task 21, triangulation) =="
# The headline contribution: how far the probability a consumer is SERVED
# (vendor PoP) sits from the probability the ENSEMBLE supports (our GEFS
# member-derived PoP), and which of the two is better calibrated against the
# gauge. Runs off gefs_pop.parquet, the vendor archives in data/raw, and the
# station truth already joined into capitals_pinned_daily.parquet - no network.
# Every series is scored on one identical sample of city-days; the module
# aborts loudly (stage-5 style) if that sample is not identical, and its
# selftest runs first.
run_fresh S triangulation.py -- \
  data/processed/triangulation.parquet \
  data/processed/triangulation_divergence.parquet \
  data/processed/triangulation_reliability.parquet \
  data/processed/triangulation_by_city.parquet

echo "== 15/16 paired significance and FDR control (Tasks 25-26) =="
# Whether the stage-14 verdicts survive the two dependencies in the sample:
# rain persists for days, and 15 capitals share the same weather systems. The
# resampling unit is therefore the calendar day carrying all its cities, drawn
# in blocks whose length is measured from the data rather than assumed. The
# correctness checks run first and abort the step: they demonstrate, against a
# known truth, that the naive independent-sample interval covers about a third
# of the time at a nominal 95% - which is the size of mistake this stage
# exists to prevent. Two minutes, no network.
run_fresh S significance.py -- \
  data/processed/significance_headline.parquet \
  data/processed/significance_cells.parquet \
  data/processed/significance_block_sensitivity.parquet

echo "== 16/16 build the HTML report =="
run_fresh S report.py -- dist/index.html

echo
echo "Done. Open dist/index.html in a browser."
echo "Figures in figures/, tables in data/processed/."
echo "Full rebuild from scratch: SKIP_UNCHANGED=0 ./run_all.sh"
