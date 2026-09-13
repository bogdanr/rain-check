# Bucharest Weather Forecast Calibration Study

**Date:** 2026-09-12
**Updated:** 2026-09-13 (prior-art survey added; Phase 6 positioning and
Phase 7 European-capitals comparison opened)
**Status:** In progress

## Objective

Quantify how well weather forecasts for Bucharest are calibrated, by building
reliability (calibration) diagrams for probability-of-precipitation at lead times
1-7 days, plus conditional-bias and rank-histogram diagnostics for temperature,
using station observations as ground truth. Deliver reproducible plots and a
Brier-score decomposition separating reliability, resolution and uncertainty.

Extended 2026-09-13: once the Bucharest result stands, run the same pipeline
over every European capital with usable station observations, so the finding is
reported as a position within a peer group rather than an isolated number.

## Background

A calibration curve requires **probabilistic** forecasts: bin forecasts by stated
probability, plot observed event frequency against mean forecast probability.
Perfect calibration = the 45-degree diagonal.

Only precipitation probability (PoP) is natively probabilistic in the common APIs.
Deterministic variables (temperature, precipitation amount) are handled with the
analogous diagnostics: conditional-bias plots and rank/PIT histograms.

### Data source findings (Open-Meteo)

Documented coverage, then **empirically probed on 2026-09-12** (`src/probe_apis.py`,
results in `data/raw/api_probe.json`). The probe overturned two assumptions, so the
measured column is authoritative.

| Endpoint | Documented | **Measured for Bucharest** | Role here |
|---|---|---|---|
| Previous Runs API | Fixed lead offsets `_previous_day1..day7`, Jan 2024+ | CONFIRMED: `temperature_2m` + `precipitation` fully populated at all 7 leads for `ecmwf_ifs025`. `precipitation_probability_previous_dayN` is accepted but returns **100% null**. Ensemble models (`ecmwf_ifs025_ensemble`, `ncep_gefs05`) return member *columns* (50 / 30) that are also **100% null** | **Deterministic lead-resolved backbone** |
| Single Runs API | `&run=`, ECMWF IFS Mar 2024+ | CONFIRMED on host `single-runs-api.open-meteo.com` with `models=ecmwf_ifs`: 240 h horizon, works back to 2024-04-01. Requesting `precipitation_probability` silently reroutes to `ecmwf_ifs025_ensemble`, which is **not archived** -> hard failure | Deterministic full-horizon backup |
| Historical Forecast API | Stitched first-hours series, 2021+ | CONFIRMED. Native `precipitation_probability` is null before **2024-04-25** and populated from that date (bisected) | **Native PoP source**, short lead only |
| Ensemble API | Members for derived probabilities | `past_days` capped at **93**, and past member values are mostly null; explicit historical dates rejected | **Dead end for history** |
| Historical Weather API | ERA5 / ERA5-Land, 1940+ | CONFIRMED, daily aggregates fine | Secondary ground truth |
| Live Forecast API | - | CONFIRMED: 16-day hourly PoP + daily aggregates, fully populated | **Forward logger source** |

**Consequence for the design.** There is no source of *native, lead-resolved* PoP
history. The study therefore runs on three complementary tracks rather than one:

- **Track A (deep, lead-resolved, deterministic).** Previous Runs, 2024-01 -> now,
  leads 1-7. Supports temperature conditional-bias and error-vs-lead-time, plus a
  calibration-style "observed rain frequency vs forecast precipitation amount" curve.
- **Track B (native PoP, single lead).** Historical Forecast PoP, 2024-04-25 -> now
  (~870 days). This is the real probability a consumer sees, and carries the
  headline reliability diagram - but at one short lead time only.
- **Track C (native PoP, all leads, forward-collecting).** The daily logger, leads
  1-16 from today. The only way to get lead-resolved native PoP; starts empty and
  accrues.

Track B answers the user's literal question now; Track A answers the lead-time
question now; Track C answers both properly, in a few months.

Ground truth should be **station observations** (Bucharest Otopeni / Baneasa via
NOAA ISD / GHCN-Daily / Meteostat). Verifying a model against a reanalysis from
the same centre flatters the forecast; ERA5 is a cross-check, not the baseline.

### Ranked challenges

1. **PoP archive depth is the binding constraint** - now measured, not assumed: no
   lead-resolved native PoP history exists. Mitigated by the three-track design
   (A/B/C above) and by starting the forward logger immediately.
2. **Sample size and serial correlation.** Adjacent days are correlated; naive
   binomial error bars are too narrow.
3. **Event definition ambiguity.** ">0.1mm vs >1.0mm", which window - verdicts flip.
4. **Point-vs-grid representativeness.** Summer convection is sub-grid.
5. **Model identity drift.** `best_match` silently changes backing model.
6. **Time alignment.** UTC vs Europe/Bucharest local-day aggregation.

## Prior art and external benchmarks

Surveyed 2026-09-13. **Nothing published gives a reliability diagram or Brier
decomposition for Bucharest precipitation probability**, for any provider or
model. The adjacent literature is either the wrong geography, the wrong metric
(categorical hit rate, not calibration), or paywalled. That makes this study
non-redundant, but it also supplies three external anchors worth using.

### What exists

| Source | What it publishes | Why it does not answer our question |
|---|---|---|
| ECMWF headline scores + annual *Evaluation of ECMWF forecasts* Tech Memo | SEEPS for 24 h precipitation, CRPSS for ENS precipitation, 2 m T CRPS - verified against **station** observations | Hemispheric / extra-tropical aggregates; no city, no consumer-facing PoP |
| WMO Lead Centres (deterministic / EPS verification) | Cross-centre comparison on a common framework | Region-level, upper-air-heavy |
| WeatherBench 2 | ML vs physical models, global | Gridded and ERA5-verified - the same-centre reanalysis comparison this plan deliberately avoids (see Ground truth, above) |
| **meteoblue monthly Forecast Accuracy Reports** (from 2026) | MAE/RMSE/ME for temperature, dew point, wind **by continent and by lead day 1-7**; July 2026 used 78,561 stations. Global mLM temperature MAE 1.04 C at day 1 -> 1.65 C at day 6; best *raw* global model 1.66 C at day 1 | Continental averages; **no precipitation, no PoP, no reliability** |
| **ForecastWatch** *Best Places to Live or Work in Europe 2025* | 593 European locations; accuracy = % of high/low temperature forecasts within 3 F, plus a precipitation accuracy rate, averaged with persistence. Free summary is country-level: Portugal / Malta / Greece >70%, Finland / Moldova / Sweden <58% | Romania not broken out; per-station data is paid; metric is a hit rate, not calibration |
| ForecastAdvisor (ForecastWatch's free front end) | Per-city provider accuracy | Browse is US-state-only; `Bucharest, Romania` and `Paris, France` lookups both return "not found" - **no free Bucharest number exists** |
| ANM / Meteo Romania | Operational VERMOD verification, MOS for 163 stations to 10 days, BIAS/RMSE maps, worked examples at **Bucharest Baneasa (WMO 15420)**; documented in the ECMWF *Application and verification of ECMWF products - Romania* country reports | Results live on a restricted internal site (`neptun.meteoromania.ro`); the public reports are summaries only |
| *Comparison of COSMO and ICON-LAM high-resolution numerical forecast for Romania* (Atmosfera, 2024, ANM authors) | 2.8 km models, summer 2020 + two severe cases, ~152 synoptic stations; ME/RMSE for T2M/TD2M/PS/FF to 30 h, POD/FAR for 12 h precipitation | Deterministic, national aggregate, short lead; no probabilistic calibration |
| Bickel & Kim (2008), *Verification of The Weather Channel Probability of Precipitation Forecasts*, Mon. Wea. Rev. | The canonical consumer **wet bias**: PoP over-forecast at the low end | US, one provider, 2000s - a hypothesis to test here, not a result to reuse |

### The three anchors worth using

1. **Temperature error magnitude.** meteoblue's continental curve (day-1 MAE ~1.0-1.7 C, day-6 ~1.65 C) is a sanity band for our Track A lead curve. Our 1.59 C (ECMWF IFS 0.25) and 1.26 C (ICON-EU) at lead 1 sit inside the *raw model* part of that band, as they should - we compare raw model output, meteoblue's headline number is post-processed.
2. **Accuracy-percentage comparability.** ForecastWatch's 58-70% European band is the only city-resolved public yardstick. It is a different metric, but we can compute it exactly from data already on disk.
3. **A directional hypothesis.** Bickel & Kim predict over-forecasting at low PoP. Our Track B result shows the opposite at the low end (stated 0.4% -> observed 4.9%) together with over-forecasting at the high end. If that holds under scrutiny it is the single most interesting finding here: Open-Meteo appears to serve raw model probability, not the hedged consumer probability the wet-bias literature describes.

## Implementation Plan

### Phase 1 - Scope and design decisions

- [x] Task 1. Study parameters fixed in `src/config.py`.
- [x] Task 2. Models pinned: `ecmwf_ifs025` primary, `icon_eu` comparator.
- [x] Task 3. Event defined: >= 0.2 mm/day primary, 0.1 / 1.0 mm variants.
- [x] Task 4. Ground truth: GHCN-Daily stations primary, ERA5 secondary.

### Phase 2 - Data acquisition

- [x] Task 5. Cached fetch layer in `src/fetch.py` (SHA-keyed on-disk JSON cache,
      retry/backoff, negative caching of deterministic 4xx refusals).
- [x] Task 6. Previous Runs archive collected: 23,640 hours, 2024-01-01 to
      2026-09-11, leads 1-7, for both `ecmwf_ifs025` and `icon_eu`.
- [x] Task 7. Establish PoP source. **DONE** - probed all candidates; see the
      measured table above. Outcome: Historical Forecast native PoP
      (2024-04-25+, short lead) is the headline source; ensemble-derived PoP is
      not viable for history; the logger covers lead-resolved PoP going forward.
- [x] Task 8. `src/logger.py` live and installed in cron (daily 05:15). First
      snapshot captured 2026-09-12; 16 lead days per run.
- [x] Task 9. GHCN-Daily observations ingested. **Otopeni is not in GHCN-Daily**;
      the usable Bucharest stations are Filaret (1.1 km, precipitation only) and
      Baneasa (10.1 km, precipitation + temperature). Records run to 2026-05-31
      (GHCN lags ~3 months), which sets the verification end date.

### Phase 3 - Alignment and dataset construction

- [x] Task 10. `src/build_dataset.py` produces `verification_pop.parquet`
      (731 usable days) and `verification_leads.parquet` (5,649 usable rows).
- [x] Task 11. QC flags implemented; 7 and 322 rows dropped respectively.
- [x] Task 12. `src/validate_join.py` - all checks pass. **This caught a real
      bug**: a shift scan showed precipitation correlation peaking at -1 day
      while temperature peaked correctly at 0. An independent station-vs-ERA5
      scan (no forecast code involved) confirmed the offset was station-side -
      these records use the morning-observation convention, so precipitation
      dated D fell on D-1. Corrected in `observations.py`; discrimination
      improved from 0.67/0.09 to 0.76/0.06 for high/low PoP days.

### Phase 4 - Calibration analysis

- [x] Task 13. Reliability diagram -> `figures/reliability_pop.png`.
- [x] Task 14. Block bootstrap (7-day blocks, 2000 resamples) per bin.
- [x] Task 15. Sharpness histogram rendered beneath the curve.
- [x] Task 16. Brier decomposition, `identity_error = 0.00000` exactly. Getting
      there required two fixes: the identity holds for the *binned* forecast
      (not the raw Brier, which differs by a within-bin covariance term), and
      bin membership had to be routed through a single `assign_bins` call - the
      float-tolerance edge masks were double-counting probabilities landing
      exactly on a bin edge (0.35 occurs six times).
- [ ] Task 17. **DEFERRED - data does not exist.** One reliability curve per lead
      day requires native PoP at leads 1-7, which no Open-Meteo archive serves
      (Task 7). Track C is accruing this now; revisit once the logger has ~6
      months of history.
- [x] Task 18. Temperature MAE and seasonal bias vs lead time, plus a
      deterministic amount-vs-rain-frequency curve -> `figures/lead_diagnostics.png`.
- [ ] Task 19. **DEFERRED - no ensemble source.** The Ensemble API caps history
      at 93 days and returns nulls for past members, so no rank histogram is
      possible from the archive.
- [x] Task 20. Isotonic recalibration fitted; mapping reported.

### Phase 5 - Robustness and reporting

- [x] Task 21. Sensitivity run across 3 thresholds, ERA5 truth and the second
      station -> `src/robustness.py`. Overconfidence at both ends holds in all
      five variants.
- [x] Task 22. ECMWF IFS 0.25 vs ICON-EU compared on a common sample.
- [x] Task 23. Results summarised below; limitations recorded.

### Phase 6 - Positioning against prior art

All five tasks run on data already collected; no new fetching, no new sources.

- [x] Task 24. **ForecastWatch-comparable metrics** -> `src/benchmarks.py`,
      `data/processed/benchmarks.parquet`. Raw lead-1 temperature accuracy is
      **45.3%**, *below* the published 58-70% band - but the cause is a single
      constant offset: the grid point runs **+2.5 C too warm overnight** against
      Baneasa while daytime highs are only -0.9 C off. A lag scan confirms both
      variables peak at zero shift (tmax r=0.986), so this is grid-vs-station
      representativeness, not a join error. Remove the constant and the rate is
      **64.4%, inside the band**. Both numbers are reported, because every
      provider in the ForecastWatch panel post-processes and the debiased column
      is the fairer comparison - though it is fitted in-sample, so it is a
      ceiling. Precipitation hit rate 82% at lead 1, POD 0.88, FAR 0.38.
      Original task text:
      **ForecastWatch-comparable metrics.** From the Track A archive,
      compute (a) % of high/low temperature forecasts within 3 F (1.67 C) and
      (b) precipitation hit rate, at leads 1-3, for both pinned models. Report
      against the published European band (58-70%, country-level, 593
      locations). State the metric caveats explicitly: their figure averages
      forecast with persistence and uses their own station set, so this is
      positioning, not a like-for-like ranking.
- [x] Task 25. **Persistence baseline** added as a third model. At lead 1 it is
      near-competitive on the tolerance metric (44.1% vs 45.3%) precisely
      *because* it carries no site bias; the model's advantage is unambiguous
      everywhere else - tmax MAE 1.59 vs 2.69 C, rain calls 82% vs 75% - and
      widens with lead (lead 3: 42.9% vs 27.5%). Also recorded: thresholding the
      PoP at 50% scores 84.5% correct, while always saying "dry" scores 72.8%,
      which is the clearest possible argument for not headlining a hit rate.
      Original task text:
      **Persistence baseline.** Add persistence (yesterday's observed
      value / yesterday's rain state) as a third "model" in the lead-time and
      hit-rate tables. Needed to make Task 24 interpretable, cheap to compute,
      and it also gives the Brier skill score a second reference point beyond
      climatology.
- [x] Task 26. **Wet-bias hypothesis tested and REJECTED for Bucharest.**
      Low-PoP days (<=20%, n=475) state 3.1% and rain **7.4%** of the time:
      gap **+4.3 pp [+2.1, +7.0]**, excludes zero. High-PoP days (>=60%, n=167)
      state 88.0% and rain 76.1%: gap **-11.9 pp [-18.8, -5.9]**. So Bucharest
      shows the *inverse* of the Bickel & Kim consumer wet bias - overconfidence
      at both ends, which is the signature of raw model output that nobody has
      nudged upward at the low end. CIs from the 7-day block bootstrap, so
      week-long dry spells are not counted as independent days.
      Original task text:
      **Wet-bias hypothesis test, stated explicitly.** Frame the
      reliability result against Bickel & Kim (2008): test low-PoP
      over-forecasting as a directional hypothesis with a block-bootstrap
      confidence interval on the low-bin gap, rather than reading it off the
      curve. Report whether Bucharest/Open-Meteo shows the classic consumer wet
      bias, the inverse, or neither.
- [x] Task 27. **Seasonal split of the reliability diagram** ->
      `analyze.plot_seasonal_reliability`, `figures/reliability_seasonal.png`,
      rendered in the report. Five bins per season rather than ten, given the
      quartered sample. The expected result (summer worst, because convective
      cells are sub-grid) did **not** hold: summer has the weakest resolution,
      which is the convective signal, but its low rain rate flatters the overall
      score. Original task text:
      **Seasonal split of the reliability diagram.** Warm season
      (Apr-Sep, convective) vs cold season, on the existing 731 days. This is
      where calibration is most likely to break and it is the one remaining
      conditioning variable the current sample can support. Report effective n
      per season and merge bins below ~30 rather than plotting noise.
- [x] Task 28. **References recorded** (see bottom of this file) so the write-up
      can cite rather than assert, and so the survey can be re-run later without
      repeating the search.

### Phase 7 - Bucharest vs the other European capitals

The prior-art survey found no city-resolved public calibration numbers anywhere
in Europe, and ForecastWatch's country-level band (58-70%) is the only
comparison available. Running the existing Track B pipeline over every European
capital with usable observations turns "Bucharest's PoP is overconfident at the
extremes" into "here is where Bucharest sits among its peers, and here is what
drives the spread". The forecast side is free - Open-Meteo serves any
coordinate. **Observations are the binding constraint, so that was measured
first, not assumed.**

#### Measured GHCN-Daily coverage (probed 2026-09-13)

43 European capitals tested against the GHCN-Daily station and inventory files,
requiring a station with the element reported across 2024-2026:

| Requirement | Capitals qualifying |
|---|---|
| PRCP station within 25 km, data through 2026 | **20 of 43** |
| same, relaxed to data through 2025 | 31 of 43 |
| PRCP **and** TMAX within 25 km, through 2026 | 20 of 43 |

Qualifying (station, km): Amsterdam 4.6, Andorra la Vella 12.9, Berlin 5.8,
**Bucharest 1.1**, Budapest 2.1, Chisinau 1.2, Dublin 4.2, Helsinki 0.7,
Luxembourg 5.4, Madrid 2.3, Monaco 20.0, Oslo 3.6, Paris 15.7, Podgorica 1.7,
Reykjavik 2.9, Sarajevo 1.5, Stockholm 1.3, Tallinn 9.6, Vaduz 17.7, Vienna 3.3.

Two must be dropped on altitude: Andorra la Vella's nearest station is Saloria
at **2451 m** and Vaduz's is Saentis at **2502 m**, against city elevations near
1000 m and 460 m. Monaco (Nice, 20 km, coastal) is borderline. That leaves a
clean working set of **~17 capitals**, which is enough for a spread but not for
fine ranking.

Notable absences - London, Rome, Warsaw, Sofia, Prague, Brussels, Lisbon,
Belgrade, Skopje, Zagreb - mostly report TMAX but **not** current PRCP to GHCN.
Widening precipitation coverage means a second observation source (NOAA ISD
hourly SYNOP, or Meteostat), which is a real piece of work, not a config change.

#### Tasks

- [x] Task 29. **Pipeline made multi-city** via a `City` dataclass and `CITIES`
      registry in `src/config.py:127-196`, plus `load_capitals()`. **Deviation
      from the plan, deliberately:** the single-city constants were kept as the
      canonical Bucharest definition and the registry built on top of them,
      rather than rewriting `observations.py` / `build_dataset.py` / `analyze.py`
      to take a city argument. The multi-city work lives in `src/capitals.py`.
      Threading a city through the existing modules would have risked the
      headline result for no analytic gain. Acceptance test **passed**: Brier
      0.11707, BSS 0.40910, reliability 0.00569, identity error 0.00000 - all
      unchanged.
      Original task text:
      **Make the pipeline multi-city.** Replace the module-level
      constants in `src/config.py` with a `CITIES` registry (lat, lon, tz,
      station ids, day-offset) and thread a city object through
      `observations.py`, `build_dataset.py`, `calibration.py`, `analyze.py`.
      Acceptance test: Bucharest reproduces the current headline numbers
      **exactly** (Brier 0.1171, BSS 0.409, reliability 0.0057, identity 0.0).
      A refactor that silently moves the existing result is a failed refactor.
- [x] Task 30. **Capital set frozen by a committed probe** -> 
      `src/probe_capitals.py`, `data/raw/capital_coverage.json`. 43 capitals
      probed, **18 included**, 25 excluded with the reason recorded. The
      elevation rule was measured against the *forecast grid point* (Open-Meteo
      elevation API) rather than a nominal city altitude, and it earned its
      place immediately: it is what rejects Andorra la Vella (Saloria, 2451 m vs
      grid 1025 m) and Vaduz (Saentis, 2502 m vs 464 m).
      Original task text:
      **Freeze the capital set with a committed probe.** Promote the
      coverage scan to `src/probe_capitals.py` writing
      `data/raw/capital_coverage.json`, with explicit inclusion rules: PRCP
      station <= 25 km, |station elevation - city elevation| <= 300 m, and >= 500
      valid forecast-observation pairs after QC. Record every exclusion and its
      reason; the dropped capitals are part of the result, not noise.
- [x] Task 31. **Day convention re-derived per city - and it does differ.**
      Station-vs-ERA5 lag scan per capital, offset stored per city, no clean
      peak means exclusion. Result: **-1 day** for Bucharest and Reykjavik,
      **+1 day** for Amsterdam, Dublin and Luxembourg, **0** for the other ten.
      So a third of the map would have been confidently wrong had Bucharest's
      convention been assumed to travel. Budapest (peak r=0.55, margin 0.02) and
      Sarajevo (peak r=0.35) were excluded for having no resolvable lag.
      Original task text:
      **Re-derive the precipitation day convention per city.** The
      Bucharest join hid a one-day station-side offset that only a shift scan
      caught (Task 12). That convention is national, not universal, so each city
      gets its own station-vs-ERA5 shift scan, the offset is stored per city,
      and a city whose scan does not peak cleanly at a single lag is excluded.
      **This is the single most likely source of a wrong answer in Phase 7.**
- [x] Task 32. **Multi-city Track B reliability** -> `src/capitals.py`,
      `data/processed/capitals_metrics.parquet`,
      `figures/capitals_reliability.png`. **15 capitals** survived (18 minus
      Budapest, Sarajevo, and Chisinau at 102 usable pairs). Bucharest BSS 0.409
      [0.316, 0.497], **rank 5 of 15**. Range: Podgorica 0.635 to Reykjavik
      **-0.069** - a capital where the forecast cannot beat its own climatology,
      because it rains on 77% of days.
      Original task text:
      **Multi-city Track B reliability.** Brier, full Murphy
      decomposition, ECE, BSS and effective n per capital, with the same
      equal-count binning and 7-day block bootstrap. Publish as a table plus
      small-multiple reliability curves with Bucharest highlighted.
- [x] Task 33. **Base-rate confound made visible** ->
      `figures/capitals_baserate.png`. Ranking is on BSS and reliability, with
      the base rate printed beside every row, and Brier plotted against base
      rate so the reader can see why raw Brier is not comparable.
      Original task text:
      **Compare the right quantity.** Base rates differ (Bucharest 27%,
      Reykjavik far wetter), and the uncertainty term of the Brier score moves
      with base rate, so raw Brier scores are not comparable across cities.
      Rank on **reliability and BSS**, publish the base rate beside every row,
      and show Brier vs base rate explicitly so the confound is visible rather
      than hidden.
- [x] Task 34. **Multiplicity controlled.** BSS pre-registered as the ranking
      metric; per-city block bootstrap; ranks recomputed per replicate (each
      city resampled independently, since the cities are independent samples).
      Bucharest's 95% **rank interval is 3-8 of 15** - the ordering is mostly
      unresolvable at two years per city, which is the honest headline, and the
      report prints a rank range rather than a winner.
      Original task text:
      **Control the multiplicity.** ~17 cities x several metrics will
      manufacture a "most/least calibrated capital" from noise. Pre-register the
      ranking metric before looking, carry simultaneous bootstrap intervals, and
      report rank uncertainty rather than a league table with a winner.
- [x] Task 35. **Spread explained, and the maritime claim tested.** Univariate
      correlations with permutation p-values (n=15 is far too small for a
      multivariate fit). **Base rate dominates**: BSS vs base rate r=-0.77
      (p=0.001), reliability vs base rate r=+0.81 (p<0.001). Station distance
      explains **nothing** (r=-0.01, p=0.98), which rules out the obvious
      artefact. **Deviation:** distance-to-coast was replaced by
      *continentality* - the annual swing in ERA5 daily mean temperature - which
      is the physical quantity the coast stands in for and needs no new dataset;
      it is weak and not significant (r=+0.44, p=0.10). Net finding: the
      maritime capitals are **harder**, not easier, once the base-rate advantage
      that flatters them on a categorical hit rate is removed - the opposite of
      the intuition behind ForecastWatch's framing.
      Original task text:
      **Explain the spread, don't just rank it.** Regress per-city
      reliability and BSS on base rate, distance to coast and station distance.
      This is a direct test of ForecastWatch's published claim that maritime
      locations are more predictable - the first such test at calibration level
      rather than hit-rate level.
- [x] Task 36. **Track A extended across the capitals** ->
      `figures/capitals_lead_mae.png`, `data/processed/capitals_lead_mae.parquet`.
      Leads 1/3/5/7 (four points define the curve; the full seven would have
      quadrupled the payload for no extra shape). Debiased daily-max MAE: median
      capital **1.03 C at lead 1 -> 2.14 C at lead 7**, against meteoblue's
      published 1.66 C for the best raw model at day 1 - neighbouring
      quantities, so the archive is where it should be. ICON-EU was not run
      across all capitals: the Bucharest comparison (Task 22) already answers
      the model-choice question and 15 more archives is a lot of fetching for a
      second copy of it. **New QC rule earned the hard way:** Sarajevo produced
      a 7.8 C MAE, and the record turned out to carry July days with a maximum
      of 2 C beside a minimum of 8 C - 25% of its days have tmax < tmin. Any
      station failing that check on more than 2% of days is now dropped
      (`capitals.MAX_QC_FAIL_RATE`).
      Original task text:
      **Extend Track A across the same capitals** (Previous Runs works
      at any coordinate): temperature MAE and bias vs lead 1-7 per city, ECMWF
      IFS 0.25 vs ICON-EU. Cheap once Task 29 lands, and it is the part directly
      comparable to meteoblue's published lead-time curve.

- [x] **Bonus, Task 26 generalised across the capitals.** The wet-bias test was
      cheap to repeat once the multi-city table existed, and it produced the
      strongest result in Phase 7: low-PoP days rain **more** often than stated
      in **15 of 15** capitals (all with CIs excluding zero) and high-PoP days
      rain **less** often in **14 of 15**. The inversion of the Bickel & Kim
      consumer wet bias is therefore a property of the forecast chain, not of
      Bucharest. Bucharest is among the **mildest** cases at the low end
      (+4.3 pp); Reykjavik is the most extreme (+48.6 pp), Paris next (+22.4 pp).
      -> `data/processed/capitals_wet_bias.parquet`.

### Phase 8 - Provenance audit: whose forecast is being measured?

Prompted by asking what the study was still missing. Phases 6-7 had made the
result *comparable*; nothing had yet checked that the thing being compared was
a single, identified forecaster. It was not being checked, and the gap was in
the study's own stated methodology: `src/config.py:47-52` declares that
`best_match` is avoided because it "silently changes which model backs the
forecast over time, which would confound model drift with calibration error" -
and Track A implements that (`src/collect_archive.py:72`), but Track B, the
headline, requested probability with **no `models` parameter**
(`src/collect_archive.py:82-85`), as did the capitals fetch
(`src/capitals.py:82`). The policy was written down and then not applied to the
one series the report leads with.

This mattered because PoP is strongly model-dependent. A ten-day probe at
Bucharest over identical hours: mean PoP **2.55%** (ICON-EU), **6.54%** (ECMWF
IFS), **12.56%** (GFS) - a factor of five.

#### Tasks

- [x] Task 37. **Identify the backing model, month by month** ->
      `src/pop_provenance.py`, `data/processed/pop_provenance_agreement.parquet`.
      Exact-equality test rather than correlation: PoP is reported in whole
      percent, so if `best_match` *is* a given model the series are identical.
      **Result: `best_match` = ICON-EU in all 30 months, agreement 1.000 in 29
      of them and 0.997 in one, zero switches.** So no published number was
      built from a spliced series - the defect was documentation, not data.
      But nothing except this check was guaranteeing that, and a re-run has no
      such guarantee.
- [x] Task 38. **Re-score the headline on pinned models**, same days, same
      observations, same binning (695 days common to all four series).
      **The headline is provider-dependent: BSS 0.399 (ICON, = as published),
      0.284 (GFS), 0.279 (ECMWF).** A 0.12 spread, roughly 30% of the headline,
      is available from the model choice alone. The honest statement is
      therefore "ICON-EU's PoP for Bucharest has BSS 0.41", not "the forecast
      for Bucharest has BSS 0.41". Note this also means the two tracks describe
      **different forecasters**: Track A is ECMWF, Track B is ICON-EU.
      The Task 26 wet-bias inversion is **not** provider-specific: low-PoP days
      rain more often than stated under all four series (+0.023 to +0.047), and
      high-PoP days rain less often under all four (-0.131 to -0.270).
- [x] Task 39. **Does the league table survive pinning?** -> `capitals.py pinned`,
      `data/processed/capitals_pinned.parquet`, `figures/capitals_pinned.png`.
      First, `best_match` is **not one forecaster across the map**: of the 18
      probed capitals it resolves to ICON-EU in 9 and ECMWF in 9 (7 and 8 among
      the 15 ranked), each identified at agreement 1.000 across three separated
      months, with no mid-archive switch anywhere. Phase 7 was therefore ranking
      two different forecasters against each other. The published grouping looked
      damning - ICON-served capitals averaged BSS 0.423 against ECMWF-served
      0.238, p = 0.040.
      **The confound was real but not the explanation.** Rebuilding all 15
      capitals twice, once per pinned model: the group gap is **+0.185 as
      published and +0.193 with ICON pinned everywhere** - unchanged. Model and
      geography were confounded because `best_match` routes ECMWF to the
      maritime north-west, and those capitals are genuinely harder. Bucharest
      ranks **6 of 15 under either pinned model against 5 as published**, and the
      top five are identical in all three orderings. The Phase 7 conclusion
      stands, and now it stands on a like-for-like comparison.
      **Unplanned finding worth more than the check itself:** neither model is
      better. ICON's advantage over ECMWF tracks rain-day frequency
      (r = -0.64, p = 0.010): the high-resolution regional model wins where rain
      is infrequent and convective (Vienna +0.165, Monaco +0.101, Bucharest
      +0.098, Podgorica +0.094) and loses where fronts come off the Atlantic
      (Dublin -0.089, Luxembourg -0.048, Amsterdam -0.041). Station distance
      again explains nothing (r = -0.008), which rules out the obvious artefact.



Rain = >= 0.2 mm/day at Bucharest Filaret. Forecast = Open-Meteo native PoP.

| Metric | Value | Reading |
|---|---|---|
| Base rate | 0.272 | 27% of days see rain |
| Brier score | 0.1171 | |
| Brier skill score | 0.409 | clearly better than climatology |
| Reliability (lower better) | 0.0057 | small - calibration is good overall |
| Resolution (higher better) | 0.0842 | genuinely discriminating, not hedging |
| ECE | 0.065 | average gap of ~6.5 percentage points |
| Identity check | 0.00000 | decomposition verified exact |
| Effective n | 363 (raw 731) | serial correlation halves the real sample |

**The forecast is well calibrated in the middle and overconfident at the
extremes** - the classic S-shape around the diagonal:

| Stated PoP | Observed frequency | n |
|---|---|---|
| 0.4% | 4.9% | 350 |
| 7% | 10.7% | 75 |
| 21% | 28.7% | 87 |
| 52% | 45.8% | 72 |
| 82% | 75.0% | 64 |
| 99% | 81.9% | 83 |

Isotonic recalibration: a stated 100% should be read as ~86%, 90% as ~83%,
0% as ~4%. Mid-range values need little adjustment.

Deterministic track (Track A, 2024-01-01 to 2026-05-31, ~810 days per lead):
temperature MAE grows from 1.59 C at lead 1 to 2.64 C at lead 7, with a
persistent cold bias (-0.88 C at lead 1) that shrinks with lead time. ICON-EU
beats ECMWF IFS 0.25 at every common lead (1.26 vs 1.59 C at lead 1), though
the gap narrows by lead 4.

### Limitations

- The headline reliability diagram is at **one short lead time only**. It does
  not answer "how far ahead can I trust the forecast" for probabilities; Track C
  will, in a few months.
- **Representativeness floor:** two stations 10 km apart disagree on rain/no-rain
  on 6.6% of days. No calibration claim finer than that is meaningful. ERA5
  captured only 6% of the station total on the wettest day in the sample.
- **Effective sample size is ~363, not 731.** Bins hold 64-350 days; the
  extreme-bin estimates carry visible bootstrap intervals.
- GHCN lags ~3 months, so June-September 2026 is not yet verifiable.
- The rain threshold matters: at 1.0 mm the reliability term quadruples
  (0.0057 -> 0.0210), so the forecast is better calibrated for "any rain" than
  for "meaningful rain".
- **External comparisons are positioning, not ranking.** ForecastWatch averages
  forecast with persistence over its own station set; meteoblue's headline MAE
  is post-processed multi-model output, not raw model. Numbers from Tasks 24-25
  are directionally comparable at best and must be labelled as such.
- **The capitals comparison is partial by construction, and it came out at 15.**
  43 capitals probed, 18 passed the coverage rules, 15 survived the data checks.
  London, Rome, Warsaw, Sofia, Prague, Brussels, Lisbon and Belgrade are absent
  because GHCN carries no current precipitation for them; Budapest and Sarajevo
  because their day convention could not be resolved; Chisinau for having 102
  usable days. Any "European picture" claim must carry that list.
- **Cross-city ranking is mostly not resolvable.** Bucharest's 95% rank interval
  spans 3-8 of 15. Two years per city is enough to place a city in a broad band,
  not to order it against its neighbours. Only the extremes (Podgorica high,
  Reykjavik below climatology) survive the uncertainty.
- **Continentality is a proxy for maritime influence, not a measurement of it.**
  Distance to coast was not computed; the annual temperature swing stands in for
  it. The maritime finding rests mainly on the base-rate correlation, which is
  strong and direct, rather than on the continentality term, which is not
  significant at n=15.
- **The multi-city temperature comparison is on daily maxima, meteoblue's is on
  hourly temperature.** The two curves are neighbours, useful as a sanity band,
  and not the same statistic.

## Verification Criteria

- Several hundred matched forecast-observation pairs per lead day, with
  missing-data counts reported.
- Hand-checked dates confirm correct alignment.
- Reliability diagram renders with per-bin counts and block-bootstrap intervals;
  bins under ~30 samples de-emphasised or merged.
- `reliability - resolution + uncertainty` reproduces total Brier score to
  numerical tolerance.
- End-to-end reproducible from a cold cache with a single command.
- Sensitivity runs reported side by side; conclusions that flip are flagged as
  not robust.
- Phase 6 comparisons state their metric definition and its difference from the
  external figure alongside every number quoted.
- Seasonal reliability curves report per-season effective n; no bin under ~30
  days is plotted unmerged.
- Phase 7: the multi-city refactor reproduces the Bucharest headline numbers bit
  for bit before any second city is added.
- Every included capital has its own shift-scan-derived precipitation day offset
  on record; cities with an ambiguous scan are excluded and listed.
- Cross-city rankings carry simultaneous bootstrap intervals and the base rate
  of each city; no ranking is reported on raw Brier score.
- Every station record entering a temperature comparison passes a physical
  consistency check (daily maximum not below daily minimum); stations failing on
  more than 2% of days are dropped and named.

## Risks and Mitigations

1. **Insufficient archived PoP history** - evaluate three sources; start the
   forward logger immediately; fall back to ensemble-derived probabilities.
2. **Sparse extreme bins** - equal-count binning, report counts, treat n<30 as
   indicative only.
3. **Autocorrelation inflating significance** - block bootstrap; report effective
   sample size.
4. **Grid vs station representativeness** - verify against both stations and
   report the spread as an irreducible floor.
5. **Time-zone / lead-offset misalignment** - UTC internally, single explicit
   conversion; Task 12 checks become regression tests.
6. **`best_match` drift** - pin explicit models.
7. **Conflating calibration with skill** - always publish sharpness and resolution
   alongside the reliability curve.
8. **False equivalence with published accuracy figures** - external numbers use
   different metrics, station sets and post-processing. Quote them as context
   with the definition attached; never present a head-to-head ranking.
9. **Seasonal split halves an already small effective sample** - treat Task 27
   as indicative, with bootstrap intervals and merged bins, not as a second
   headline result.
10. **Per-city day-convention errors** - the Bucharest offset was station-side
    and invisible until a shift scan was run. Assume nothing transfers; Task 31
    makes the scan mandatory per city and exclusion the default on ambiguity.
11. **Cross-city base-rate confound** - Brier score moves with climatology, so a
    drier capital looks "better" for free. Rank on reliability and BSS, always
    publish the base rate (Task 33).
12. **Multiple comparisons across ~17 cities** - a spurious best and worst is
    almost guaranteed. Pre-register the metric, report rank uncertainty, resist
    the league table (Task 34).
13. **Uneven station representativeness across cities** - station distance runs
    0.7 km (Helsinki) to 20 km (Monaco), and the Bucharest two-station spread
    already puts the disagreement floor at 6.6% of days. Include station
    distance as a covariate (Task 35) rather than treating all cities as equally
    well observed. **Outcome: station distance correlates -0.01 with skill, so
    this risk did not materialise** - but it was only knowable by testing it.
14. **Corrupt station records passing silently** - Sarajevo reported 25% of days
    with a daily maximum below its own minimum and would have entered the table
    as a "city with a 7.8 C forecast error". Physical consistency checks are now
    applied before a station is used, and a failing station is dropped rather
    than down-weighted.

## Alternative Approaches Considered

1. **Forward-only collection** - simplest and cleanest, but no results for months.
   Adopted *alongside* the archive approach (Task 8), not instead of it.
2. **Ensemble-derived probabilities** - most principled, deeper history, works for
   any variable; but it is a derived product, not the PoP a consumer sees.
3. **Deterministic-only study** - fastest to first result with a long record, but
   calibration-flavoured rather than a true reliability diagram.
4. **Verify a consumer-facing provider** - answers the socially interesting
   question, but ToS-dependent and no historical archive.
5. **Reuse `xskillscore` / `properscoring` / `climpred`** - tested metrics out of
   the box at the cost of a dependency.
6. **Widen the capitals set with NOAA ISD or Meteostat** instead of GHCN-Daily -
   would recover London, Rome, Warsaw, Sofia and roughly a dozen more, since ISD
   carries hourly SYNOP in near-real time and does not lag three months. Costs a
   second ingest path, a second QC regime and a second day-convention problem,
   so it is deferred until the GHCN-based comparison shows the spread is worth
   resolving more finely.
7. **Compare capitals on ERA5 instead of stations** - instant full coverage of
   all 43, no station hunting. Rejected for the headline: ERA5 captured only 6%
   of the station total on the wettest Bucharest day in the sample, and it is
   produced by the same centre as the primary forecast model. Usable only as the
   secondary cross-check, exactly as in the single-city study.

## References

Surveyed 2026-09-13; see *Prior art and external benchmarks* for what each one
does and does not answer.

- ECMWF, *Quality of our forecasts* (headline scores, annual evaluation Tech
  Memo): https://www.ecmwf.int/en/forecasts/quality-our-forecasts
- ECMWF verification chart catalogue:
  https://charts.ecmwf.int/catalogue/packages/verification/
- WeatherBench 2: https://sites.research.google/weatherbench/
- meteoblue, *Transparent Forecasting: New Monthly Accuracy Reports*, 2026-08-19:
  https://www.meteoblue.com/en/blog/article/show/40665
- ForecastWatch, *Best and worst places to live in Europe for reliable weather*
  (593 locations, 2025 edition):
  https://forecastwatch.com/news/best-and-worst-places-to-live-in-europe-for-reliable-weather/
- ForecastWatch, 2026 Most Accurate Forecast Awards (Europe, consumer days 1-7:
  Microsoft; days 8-14: Pelmorex): https://forecastwatch.com/awards/2026/
- ForecastAdvisor (free, US-only browse): https://www.forecastadvisor.com/
- ECMWF, *Application and verification of ECMWF products - Romania* (ANM VERMOD
  system, Bucharest Baneasa 15420 examples):
  https://www.ecmwf.int/sites/default/files/elibrary/2010/7606-romania.pdf
- *Comparison of COSMO and ICON-LAM high-resolution numerical forecast for
  Romania*, Atmosfera, 2024:
  https://www.scielo.org.mx/scielo.php?script=sci_arttext&pid=S0187-62362024000100025
- Bickel, J.E. & Kim, S.D. (2008), *Verification of The Weather Channel
  Probability of Precipitation Forecasts*, Mon. Wea. Rev. 136(12):
  https://journals.ametsoc.org/view/journals/mwre/136/12/2008mwr2547.1.xml
- BoM/CAWCR, *Verifying probability of precipitation* (method reference):
  https://www.cawcr.gov.au/projects/verification/POP3/POP3.html
