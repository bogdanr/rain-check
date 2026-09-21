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
# data/raw/ensemble and the member archives under data/raw/{gefs,aifs_ens,
# ifs_ens} are excluded from that input set. The forward ensemble collector
# (src/collect_ensemble.py) writes a new partition there EVERY day by design,
# and the archive collector (src/collect_members.py) likewise extends each
# member directory as new init_times are published. The stages below consume
# the DERIVED PoP tables in data/processed rather than these directories, so
# counting them as inputs would mark the entire pipeline stale daily and turn
# every warm run into a full rebuild.
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
        \( -path data/raw/ensemble -o -path data/raw/gefs \
           -o -path data/raw/aifs_ens -o -path data/raw/ifs_ens \) -prune -o \
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

echo "== 1/23 collect archives (Tracks A and B, ERA5) =="
S collect_archive.py all
S collect_archive.py previous_runs icon_eu

echo "== 2/23 station observations (daily, GHCN) =="
S observations.py

echo "== 3/23 station observations (hourly present weather, NOAA ISD) =="
S observations_hourly.py

echo "== 4/23 build verification tables =="
S build_dataset.py

echo "== 5/23 validate the join (fails loudly on misalignment) =="
S validate_join.py

echo "== 6/23 analysis and robustness =="
S analyze.py
S robustness.py

echo "== 7/23 hourly track, derived-probability events, external benchmarks =="
S hourly.py
S events.py
S benchmarks.py

echo "== 8/23 European capitals: coverage probe, then the multi-city run =="
S probe_capitals.py
run_fresh S capitals.py -- \
  data/processed/capitals_metrics.parquet \
  data/processed/capitals_pop.parquet \
  figures/capitals_reliability.png

echo "== 9/23 forecast provenance audit, then the like-for-like ranking =="
# Which model actually backs the unpinned probability series, per city and per
# month, and does the league table survive holding the forecaster fixed?
S pop_provenance.py
run_fresh S capitals.py pinned -- data/processed/capitals_pinned.parquet

echo "== 10/23 beyond the capitals: probe every city with a usable gauge =="
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

echo "== 11/23 multi-provider league: probe coverage, then collect =="
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

echo "== 12/23 per-provider verification, league table, robustness =="
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

echo "== 13/23 decision value, CRPS/ROC/sharpness, baselines (Tasks 18, 22-24) =="
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

echo "== 14/23 served vs member-derived probability (Task 21, triangulation) =="
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

echo "== 15/23 paired significance and FDR control (Tasks 25-26) =="
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

echo "== 16/23 physics against machine learning (Task 30) =="
# ECMWF runs IFS ENS and AIFS ENS side by side: 51 members each, one grid, one
# initialisation, one republisher. That makes the forecast METHOD very nearly
# the only difference, which no cross-centre comparison can claim. Both are
# accumulated onto a single 6 h ladder first - IFS publishes 3-hourly steps and
# AIFS 6-hourly ones, and leaving that unmatched would hand the physics system
# a finer local-day boundary and nothing to do with forecast quality. The
# native-ladder run is kept beside it so the size of that artefact is a
# published number. Runs off the PoP tables on disk; no network.
run_fresh S physics_ml.py -- \
  data/processed/physics_ml_scores.parquet \
  data/processed/physics_ml_significance.parquet \
  data/processed/physics_ml_by_city.parquet \
  data/processed/physics_ml_ladder_sensitivity.parquet

echo "== 17/23 window homogeneity and change points (Tasks 17, 19; G6) =="
# Whether the headline window is a fair year, and whether the skill inside it
# is even constant. The record starts and ends mid-month, so pooling its days
# weights seasons by how many of each happened to land in it - an artefact
# worth tens of Brier points at a city with a monsoon. This stage trims to
# whole years, re-weights the seasons by the calendar rather than by the
# sample, and then searches each provider's daily skill for a level shift with
# the search itself paid for in the p-value. A shift found INSIDE the balanced
# window is reported as such: the quoted mean is then a blend of two regimes,
# which trimming the calendar cannot fix. Checks run first and abort the step.
run_fresh S window.py -- \
  data/processed/window_census.parquet \
  data/processed/window_shift.parquet \
  data/processed/window_season.parquet \
  data/processed/window_changepoints.parquet

echo "== 18/23 how much of the verdict is where the bucket stands? (G3; Tasks 13-14) =="
# Every score in this study is measured against one rain gauge, and the paper
# has been caveating that rather than measuring it. This stage measures it from
# the gauges themselves: 193k GHCN pairs near the study cities, disagreement
# against separation, extrapolated to zero to separate representativeness error
# from the irreducible noise in the truth. Then it re-sites the gauge - swapping
# in a real alternative bucket near each city, every forecast and every day held
# fixed - and asks which published claims still hold. The checks run first and
# abort the step; one of them shows that the textbook independent-flip
# correction would have moved the headline by 18x its own size in a direction
# the real gauges do not support. No network.
run_fresh S truth_uncertainty.py -- \
  data/processed/truth_pairs.parquet \
  data/processed/truth_separation_curve.parquet \
  data/processed/truth_rescore.parquet \
  data/processed/truth_propagation.parquet

echo "== 19/23 which day does the gauge mean? (Task 6, G11) =="
# A gauge read at 07:00 reports the 24 hours that ENDED at 07:00, most of which
# fell on the day before the one stamped on the record. capitals.py settles
# that per city against reanalysis, which works at 19 cities and cannot work at
# 2,000. This stage asks whether GHCN's own metadata can do the job instead,
# and answers by measuring: the lag that makes two neighbouring gauges agree is
# the difference of their conventions, and solving the whole neighbour graph
# against the gauges that report a midnight reading turns that into an absolute
# shift per station. Then the measurement is compared against the metadata and
# disagreements are named rather than averaged away. The stage also prices the
# error - a single day of mis-dating costs ~0.09 Brier, two orders of magnitude
# above anything this study reports. Checks run first and abort. No network.
run_fresh S obs_time.py -- \
  data/processed/obs_time_stations.parquet \
  data/processed/obs_time_hour_curve.parquet \
  data/processed/obs_time_audit.parquet \
  data/processed/obs_time_cost.parquet

echo "== 20/23 what sample can this study have? (Tasks 10, 16; G1, G14, G20) =="
# Phase 3 wants 2,000 cities on every inhabited continent. Before spending the
# collection budget, this stage asks what that buys and whether the pool can
# supply it. Three answers, none of them the expected one: the country cap
# changes WHO is in the sample far more than WHAT it says; the pool cannot
# reach the target outside Europe and North America at any population floor;
# and cities inside a country are near-interchangeable, so the naive
# city-count arithmetic understates the required n several-fold. The design
# effect is measured against a shuffled-label null rather than assumed, and
# the checks that police it abort the stage. No network.
run_fresh S sampling.py -- \
  data/processed/sampling_pool_census.parquet \
  data/processed/sampling_cap_sensitivity.parquet \
  data/processed/sampling_saturation.parquet \
  data/processed/sampling_design.parquet

echo "== 21/23 can ISD reach where GHCN cannot? (Task 11; G1) =="
# E27 re-specified G1 from "collect more cities" to "find a truth source
# outside Europe and North America". ISD is the first candidate, and this
# stage asks the two questions in the order that matters: does a station
# EXIST near those cities (cheap, and the answer flatters ISD), and does it
# report PRECIPITATION (expensive, and the answer is where the study is).
# Downloads one calendar year for 156 stations; cached after the first run.
run_fresh S isd_coverage.py -- \
  data/processed/isd_city_census.parquet \
  data/processed/isd_station_probe.parquet

echo "== 22/23 is the forecast worse where people are poorer? (Tasks 28, 29; D7) =="
# The question the study's framing points at. Two confounds have to be held
# off before the estimate means anything: poorer cities in this panel are
# drier, which flatters any score bounded by its base rate, and they have
# more distant gauges, which inflates one. The first is handled by matching
# each poorer city to richer cities that rain as often, the second by E20's
# separation curve. Reads the parquet tables already on disk; no network.
run_fresh S equity.py -- \
  data/processed/equity_income.parquet \
  data/processed/equity_distance.parquet \
  data/processed/equity_gauge_reach.parquet

echo "== 23/23 build the HTML report =="
run_fresh S report.py -- dist/index.html

echo
echo "Done. Open dist/index.html in a browser."
echo "Figures in figures/, tables in data/processed/."
echo "Full rebuild from scratch: SKIP_UNCHANGED=0 ./run_all.sh"
