# Global Forecast Trustworthiness: Journal Strategy, Gap Analysis, and Upgrade Roadmap

**Version 3.** Supersedes `plans/2026-09-15-global-forecast-trust-journal-strategy-v2.md`. Revised after Phase 0 was executed: the two API probes were run live, the external-archive feasibility study was completed, and forward ensemble collection was built and started.

## Objective

Publish a scientific study answering **how much weather forecasts can be trusted across the globe, and what methods establish that**. Secondary objective: a reusable, DOI-archived verification dataset and pipeline.

---

## Phase 0 Outcomes — Settled Facts

Phase 0 closed three open questions and started the one time-critical job. The net effect is that **every route previously rated as blocking is now open**, but the route that opens them is not the one v2 assumed.

### P0-A. Vendor PoP is NOT available at lead times — confirmed negative

`precipitation_probability_previous_day1…7` are **accepted by the Previous Runs API and returned entirely null** at every probed date across 2024–2026, for `ecmwf_ifs025`, `icon_eu` and `gfs_seamless` (`data/raw/pop_leads_and_ml_probe.json`, key `q1_previous_runs_pop_at_lead`). The probe included a deliberate negative control, `precipitation_probability_previous_day99`, which was *also* accepted and returned null — proving the API echoes arbitrary variable names, so `served: true` is meaningless and only the non-null count is evidence. Lead-0 `precipitation_probability` is non-null 168/168 over the same requests, confirming the probe was well-formed.

A twelve-variable inventory at leads (`q1b_lead_variable_inventory`) shows the same for every model: amounts, temperature, cloud cover, CAPE and the rest are served at leads; **probability is not**.

**Consequence:** the vendor cannot supply the paper's central "trust versus lead time" axis in probabilistic form. Risk 3 of v2 has materialised. It is mitigated not by the fallback v2 proposed, but by P0-C below, which supplies member-derived PoP at all leads for the entire window.

### P0-B. ML systems serve amounts, not probabilities — confirmed negative, with a route around it

Valid Historical Forecast model ids are `ecmwf_aifs025`, `ecmwf_aifs025_single`, `ecmwf_aifs025_ensemble`, `ncep_aigfs025`, `ncep_hgefs025_ensemble_mean`. The ids `aigfs025`, `hgefs025` and `ncep_hgefs025` are rejected outright (`q2_ml_models_historical_forecast`).

Archive starts, established by bisection (`q2_archive_start_bisection`):

| Model | `precipitation` from | `precipitation_probability` from |
|---|---|---|
| `ecmwf_aifs025_single` | 2025-02-17 | **never** |
| `ecmwf_aifs025` | mixed/partial | **never** |
| `ncep_aigfs025` | 2026-01-06 | 2026-03-08 |
| `ncep_hgefs025_ensemble_mean` | 2026-01-21 | **never** |

A live forecast probe on 2026-09-15 (`q2d_live_forecast_pop`) confirms this is not an archive artefact: `ecmwf_ifs025` returns 144/144 non-null PoP today, while all three ML models return 144/144 non-null precipitation and **0/144 PoP**. The vendor does not derive probability for ML systems at all.

**Consequence:** the physics-versus-ML comparison cannot be run on vendor PoP. It must use member-derived PoP — available from `ecmwf-aifs-ens-forecast` (2025-07-02 →) and from the forward collection started under P0-D, which captures `ecmwf_aifs025` at 51 members. AIGFS PoP from 2026-03-08 is too short and too recent to carry a result and should be treated as a curiosity, not evidence.

### P0-C. External ensembles are cheap, not expensive — the decisive reversal

Full report: `plans/2026-09-15-external-ensemble-archive-feasibility-v1.md`. Every access claim was tested live.

**TIGGE is out.** Not on cost but on licence: ECMWF's TIGGE contribution is **CC BY-NC 4.0**, and all other centres add *"data must not be supplied… to any third party outside your organisation."* A CC-BY derived dataset containing TIGGE-derived PoP would breach both. Since the dataset descriptor is sequenced first and is the highest-probability acceptance in the plan, this disqualifies TIGGE. It is also 15–25 dev-days, weeks of MARS queueing, and has documented tape-loss gaps.

**`dynamical.org` analysis-ready Zarr archives replace it, at a fraction of the cost.** All opened and read anonymously, all **CC-BY-4.0**:

| Archive | Members | Coverage | Transfer, 2000 cities, 730 inits |
|---|---|---|---|
| `noaa-gefs-forecast-35-day` | 31 | 2020-10-01 → present, gapless daily | **79 GB** |
| `ecmwf-ifs-ens-forecast-15-day-0-25-degree` | 51 | 2024-04-01 → present, gapless daily | **361 GB** |
| `ecmwf-aifs-ens-forecast` | 51 | 2025-07-02 → present | same code path |

Chunk geometry is the reason it is cheap: one GEFS chunk carries **all 31 members and leads 0–189 h** for a 4.25°×4° tile, so a city-day costs exactly one read. Cities cluster, so 2,000 cities need 480 tiles and 8,509 cities need only 715 — **scaling to the full qualifying pool costs ~50% more, not 4× more.** Effort: **3–5 dev-days for GEFS, +1–2 for IFS ENS.**

Three consequential side-findings:
- ECMWF Open Data ensembles *do* exist historically on AWS (`s3://ecmwf-forecasts`, 2023-01-18 →), contradicting v2's assumption — but the bucket returned HTTP 503 `SlowDown` on every probed GET, so the Zarr route supersedes it.
- A GEFS chunk delivers the **17×16 grid-cell neighbourhood** around each city for free — direct raw material for G3 representativeness error.
- The same catalogue carries **IMERG satellite QPE** (G3 cross-check) and **`asos-parquet`**, ~2,500 global hourly METAR stations (candidate low-latency truth source for G12).

**Consequence:** self-computed PoP is available **retrospectively, for the full window, at all leads, from two independent centres, under a clean licence.** This simultaneously repairs P0-A and P0-B and converts G2 from a mitigated weakness into a fully-evidenced primary result.

### P0-D. Forward ensemble collection is live

`src/collect_ensemble.py` is built and the first day is on disk. Eleven ensemble systems validated across 19 capitals (`data/processed/ensemble_coverage.json`): `ecmwf_ifs025` (51), `ecmwf_aifs025` (51), `icon_global` (40), `icon_eu` (40), `gfs025` (31), `gem_global` (21), `meteoswiss_icon_ch2` (21), `icon_d2` (20), `ukmo_global_ensemble_20km` (18), `meteoswiss_icon_ch1` (11), `ukmo_uk_ensemble_2km` (3). Cost: 152 requests ≈ 1,490 billed calls per run, ~15% of the daily free-tier quota; 2.5 MB/day.

Recorded negative results worth citing in the methods: `bom_access_global_ensemble` returns **all 18 member columns fully null** — schema without data, which a naive collector would have archived silently. `ecmwf_ifs04` and `ecmwf_aifs025_single` return one member on an ensemble endpoint. Retention measured at **≈4.1 days**, slightly beyond the documented three.

Two design notes that matter for reproducibility: the ensemble URL is date-free, so it hashes to the same cache key daily — caching is disabled for this endpoint (`src/fetch.py:59`), because a cache hit would silently serve yesterday's forecast as today's. And the provider probe's relative non-null threshold had to become an absolute floor (`src/collect_ensemble.py:153`), because short-horizon models such as ICON-CH1 fail a fractional test purely by having a 33-hour horizon.

---

## Design Decisions (settled)

**D1. Verification window is deliberately short and homogeneous.** Headline window **2024-09-01 → 2026-08-31**; full record from 2024-04-25 as a published sensitivity run. Justified three ways: the Previous Runs archive begins January 2024; PoP begins 2024-04-25 (`src/constants.py:3-5`); and Open-Meteo's own documentation states the archive is *"not suitable for long time series due to model version changes over time."*

**D2. Seasonal imbalance is neutralised by weighting.** Per-season metrics pooled with climatological weights; BSS referenced to a smoothed day-of-year climatology rather than the sample base rate (`src/calibration.py:85`, `:106`).

**D3. Probability provenance is addressed by triangulation. — REVISED.** Three estimates of the same city-day probability: (i) vendor PoP, on disk; (ii) **self-computed PoP from `dynamical.org` GEFS and ECMWF IFS ENS** — retrospective, full window, CC-BY-4.0, ~5 dev-days — *replacing TIGGE, which is struck*; (iii) forward-collected Open-Meteo members, now running, bridging the two and validating that the method reproduces the vendor pipeline. **The divergence between (i) and (ii) is a primary result.**

**D4. The consulting material is excluded from the study entirely.**

**D5. The lead-time axis is ensemble-derived, not vendor-derived. — NEW.** Forced by P0-A. Vendor PoP exists only at lead ≈0; member-derived PoP exists at all leads for the whole window. This is arguably the better design — lead-dependent skill decay is measured on a system whose probability construction is fully known and documented by us — but it must be stated as a deliberate choice with the negative probe result as its justification, not discovered by a reviewer.

**D6. Physics-versus-ML is member-derived and window-limited. — NEW.** Forced by P0-B. Matched window is bounded by AIFS ENS availability (2025-07-02 →), i.e. roughly 14 months inside the headline window. Report it as a matched-window sub-study, never pooled with the full-window physics results.

---

## Journal Targets

Revised upward from v2: P0-C removes the cost/licence risk that capped the triangulation result, and supplies the representativeness and satellite-QPE inputs as a by-product.

### Nature family

| Venue | Odds | Assessment |
|---|---|---|
| **Nature** | ~3% | Requires the forecast-inequality claim tied to population and hazard exposure. Expect transfer down. |
| **Nature Communications** | **~20–25%** | Up from 15–20%. Needs global coverage, the triangulation result, and a causal explanation. |
| **Communications Earth & Environment** | **~40%** | **Primary recommendation for the findings paper.** |
| **Scientific Data** | **~60–65%** | **Submit first.** Licence clear on every input now in scope. |
| **npj Climate and Atmospheric Science** | ~20% | Climate-skewed. Secondary. |

### High-impact domain and interdisciplinary

| Venue | Odds | Assessment |
|---|---|---|
| **BAMS** | ~30% | Best non-Nature high-impact target; receptive to the provenance dimension. |
| **ESSD** | ~40–45% | Alternative to Scientific Data; open review stress-tests the methodology publicly. |
| **Environmental Research Letters** | ~35% | Visible fallback. |
| **Weather, Climate, and Society** | ~45% | The right home if the equity result is strongest — not a consolation prize. |
| **QJRMS** | ~40% | Safe harbour if judged methodological. |

**Sequence:** preprint → dataset descriptor (Scientific Data or ESSD) → findings paper to Nature Communications with a pre-agreed cascade to Communications Earth & Environment, then BAMS or ERL.

---

## Gap Analysis

### Blocking Tier A

**G1. Geographic scope and Global South representation.** 16 capitals plus ~109–300 cities, capped by `MAX_CITIES = 300` (`src/probe_cities.py:73`), against 8,509 qualifying gauged cities (`src/probe_cities.py:8-13`). Required: ≥2,000 cities, all inhabited continents, coverage reported per region and income group including where verification is impossible.

**G2. Probability provenance — now a fully-resourced contribution.** Route settled by D3. Required: GEFS then IFS ENS integrated, forward collection sustained, divergence quantified and published.

**G3. Truth uncertainty quantified, not caveated.** Required: gauge undercatch correction, representativeness error propagated into every interval (raw material now free from GEFS neighbourhood chunks), IMERG cross-check where gauges are sparse.

**G4. Explanatory model.** Single-variable correlations already collapsed once at scale. Required: multivariate model of skill and calibration error against regime, orography, resolution, latitude, station geometry.

**G5. Literature positioning.** Still the cheapest rejection risk. Partially begun; databases not yet searched.

### Blocking Tier B credibility

**G6. Window homogeneity, not length.** Settled by D1/D2. Required: document model-version change points within the window and demonstrate skill stability across them.

**G7. Metric breadth.** Add CRPS, ROC/AUC, sharpness, and **relative economic value curves**.

**G8. Baselines.** Add persistence and smoothed day-of-year climatology.

**G9. Multiple comparisons.** FDR control across the city × model × lead grid.

**G10. Significance testing.** Paired tests accounting for **spatial** dependence, since spatial replication is the sole source of power under D1.

### Supporting

**G11. Rain-day convention at scale.** Automate per-station observation-time detection from GHCN metadata, failing loudly on disagreement, **before** expansion multiplies any error (`src/config.py:113-121`).

**G12. Observation latency.** `OBS_END = "2026-05-31"` reflects GHCN's ~3-month lag (`src/config.py:123-125`); `ISD_END = "2025-08-24"` (`src/config.py:110-111`). Closing at 2026-08-31 needs either a wait until ~December 2026 or the `asos-parquet` route identified in P0-C.

**G13. ML systems absent from the registry — partially resolved.** Valid ids now known (P0-B); vendor PoP does not exist for them, so registry addition serves the deterministic and member-derived tracks only.

**G14. Sampling-design defence.** State the target population implied by one-city-per-gauge and per-country caps (`src/probe_cities.py:16-27`); run cap-sensitivity.

**G15. Licence segregation.** CC-BY-SA propagation from UK Met Office data — now in two places: the vendor `ukmo_*` models and the `ukmo_global_ensemble_20km` / `ukmo_uk_ensemble_2km` forward collection.

**G16. Third-party republication dependency. — NEW.** The `dynamical.org` archives are a republication. For a paper whose most original contribution is a *provenance audit*, depending on a re-publisher unverified would be self-undermining. Required: validate a sample of city-days against raw `noaa-gefs-pds` GRIB2 byte-ranges (proven working), report the agreement, disclose the rounded-mantissa compression, resolve asset URLs from the STAC collection at run time (the `data.dynamical.org` URLs retire 2026-09-30), and archive our own extracted point data to Zenodo.

**G17. Temporal quantisation of member-derived PoP. — NEW.** Ensemble steps are 3-hourly; vendor PoP is hourly. Local midnight cannot be hit exactly for UTC offsets that are not multiples of 3 h. Required: apportion boundary steps pro-rata or snap and report sensitivity, **and** re-derive vendor PoP at 3-hourly quantisation as a control, so the resolution mismatch is separated from the genuine provenance signal in the divergence result.

---

## Implementation Plan

### Phase 0 — COMPLETE

- [x] Task 1. Probe `precipitation_probability_previous_dayN`. → **Negative** (P0-A). `src/probe_pop_leads_and_ml.py`.
- [x] Task 2. Probe ML-model PoP availability and archive starts. → **Negative for PoP**, ids and amount-starts established (P0-B).
- [ ] Task 3. Complete the prior-art search against Google Scholar, Web of Science, ECMWF eLibrary and AMS journals. **Still open — now the only unclosed Phase 0 item, and the only step that can still redirect the framing.**
- [x] Task 4. Assess external ensemble archives. → TIGGE struck on licence; `dynamical.org` Zarr recommended (P0-C).
- [x] Task 8. Start forward ensemble collection. → Live, 11 systems, 19 capitals (P0-D). **Must be scheduled daily; retention is ~4 days.**

### Phase 1 — Foundations

- [ ] Task 3 (carried). Prior-art search. Rationale: G5.
- [ ] Task 5. Write the literature positioning against Brier, Murphy, WGNE precipitation verification guidance, CAWCR/WWRP, Richardson's relative economic value, and existing national and commercial verification efforts. Rationale: G5.
- [ ] Task 6. Automate per-station observation-time detection from GHCN metadata; reconcile against the empirical lag scan and fail loudly on disagreement. Rationale: G11; must precede scale-up.
- [ ] Task 7. Add the validated ML ids to `PROVIDER_MODELS` for the deterministic track. Rationale: G13, D6.
- [ ] Task 8a. Install the daily cron for `src/collect_ensemble.py collect` and add a staleness alarm. Rationale: D3(iii); unrecoverable data loss on failure.
- [ ] Task 9. Record licence provenance per model and segregate CC-BY-SA-derived outputs. Rationale: G15.

### Phase 2 — External ensemble integration (promoted; was Phase 3)

- [ ] Task 20a. Integrate `noaa-gefs-forecast-35-day` and compute member-derived PoP at all leads for the headline window. Rationale: G2, D5. **Highest return per dev-day in the plan; 3–5 days.**
- [ ] Task 20b. Integrate `ecmwf-ifs-ens-forecast-15-day-0-25-degree`. Rationale: upgrades the divergence result to two independent centres. +1–2 days.
- [ ] Task 20c. Validate a sample against raw `noaa-gefs-pds` GRIB2 byte-ranges and report agreement. Rationale: G16. 1 day.
- [ ] Task 20d. Implement local-day accumulation with documented boundary-step handling, plus the 3-hourly vendor-PoP control. Rationale: G17.

### Phase 3 — Global scale-up

- [ ] Task 10. Lift the city budget toward the full qualifying pool with resumable, rate-limit-aware, multi-day collection. Rationale: G1.
- [ ] Task 11. Integrate NOAA ISD globally alongside GHCN-Daily; report coverage per continent and income group. Rationale: G1.
- [ ] Task 12. Add IMERG satellite QPE cross-check where gauge density is low. Rationale: G3.
- [ ] Task 13. Quantify representativeness error using the GEFS neighbourhood fields and station pairs; propagate into every interval. Rationale: G3.
- [ ] Task 14. Apply gauge undercatch correction; report with and without. Rationale: G3.
- [ ] Task 15. Evaluate `asos-parquet` as the low-latency truth source. Rationale: G12.
- [ ] Task 16. Formalise the sampling design and run cap-sensitivity analyses. Rationale: G14.

### Phase 4 — Methodological depth

- [ ] Task 17. Implement the balanced headline window with the full record as a published sensitivity run; per-season metrics with climatological weighting. Rationale: D1, D2, G6.
- [ ] Task 18. Re-reference BSS to a smoothed day-of-year climatology. Rationale: G6, G8.
- [ ] Task 19. Compile the model-version change-point timeline and test skill stability across each change. Rationale: G6.
- [ ] Task 21. Publish the vendor-versus-ensemble PoP divergence as a primary result. Rationale: G2.
- [ ] Task 22. Add CRPS, ROC/AUC and sharpness. Rationale: G7.
- [ ] Task 23. Implement relative economic value curves across cost-loss ratios. Rationale: G7; operationalises the research question.
- [ ] Task 24. Add persistence and smoothed-climatology baselines. Rationale: G8.
- [ ] Task 25. Paired significance testing accounting for spatial and temporal dependence. Rationale: G10.
- [ ] Task 26. Apply FDR control; restate which claims are inferential versus descriptive. Rationale: G9.

### Phase 5 — The Nature-level argument

- [ ] Task 27. Build the multivariate explanatory model of skill and calibration error. Rationale: G4.
- [ ] Task 28. Join reliability to population, hazard exposure and early-warning coverage; derive the headline societal statement. Rationale: makes Tier A arguable.
- [ ] Task 29. Test the forecast-inequality hypothesis with gauge density controlled; publish the null if it does not hold. Rationale: the null is itself publishable.
- [ ] Task 30. Physics-versus-ML calibration on the AIFS-ENS matched window, member-derived. Rationale: D6; strongest novelty hook.
- [ ] Task 31. Elevate the provenance audit — silent routing, undisclosed derivation, and now **the vendor's complete absence of probability for ML systems and at lead times** — into a standalone section. Rationale: the most original contribution; P0-A and P0-B are themselves publishable findings about what consumers can and cannot obtain.

### Phase 6 — Publication

- [ ] Task 32. Archive code and extracted point data on Zenodo with a citable DOI. Rationale: also mitigates G16.
- [ ] Task 33. Post the preprint to timestamp priority.
- [ ] Task 34. Draft and submit the dataset descriptor.
- [ ] Task 35. Draft the findings paper to Nature Communications format with the cascade pre-agreed.
- [ ] Task 36. Obtain pre-submission review from a forecast-verification specialist.

## Verification Criteria

- Coverage spans ≥2,000 cities across all inhabited continents, with per-region coverage tabulated including regions where verification is impossible.
- Every skill number carries an interval including representativeness uncertainty, not only sampling uncertainty.
- The vendor-versus-ensemble PoP divergence is quantified and published for at least the major global systems, with the quantisation control reported separately.
- Headline results are reported on the balanced window and shown to be unchanged on the full record.
- Every model-versus-model claim rests on a paired significance test with FDR control and spatial-dependence handling.
- Relative economic value curves are published for the primary threshold and lead times.
- Null findings — including P0-A and P0-B — are reported as prominently as positive findings.
- Member-derived values are validated against raw GRIB for a documented sample.
- Code and data are DOI-archived; the pipeline reproduces published numbers from a cold start.
- Licence provenance is documented per model, with CC-BY-SA obligations respected.

## Potential Risks and Mitigations

1. **~~External ensemble archives prove too costly.~~** Retired by P0-C: 5–8 dev-days total, licence clean.
2. **Forward ensemble collection silently stops.** Retention is ~4 days, so an unnoticed failure is unrecoverable. Mitigation: Task 8a staleness alarm; `status` already names missing dates.
3. **~~PoP unavailable at lead times.~~** Confirmed true (P0-A) and resolved by D5 — the axis moves to member-derived PoP. The negative result is published rather than worked around.
4. **Scale multiplies a silent convention error.** Mitigation: Task 6 precedes Task 10 deliberately; extend the loud-failure join validation (`run_all.sh:56-57`).
5. **Rate limits throttle the global collection.** Mitigation: resumable cache, multi-day scheduling; note the ensemble collection alone consumes ~15% of daily quota.
6. **`dynamical.org` changes or disappears.** Mitigation: G16 — STAC-resolved URLs, raw-GRIB fallback proven, own data archived to Zenodo.
7. **Named providers contest the rankings.** Mitigation: matched windows, threshold sensitivity, held-fixed checks, provenance caveat in the abstract, never rank on one metric.
8. **Effort exceeds appetite and nothing is submitted.** Mitigation: the dataset descriptor is sequenced first — achievable from the current state plus Phase 1.
9. **Prior art invalidates the framing.** The last unclosed Phase 0 item. Mitigation: do Task 3 before Phase 2 spends real effort.

## Alternative Approaches

1. **Vendor-PoP-only paper.** Now strictly worse than before: P0-A and P0-B mean such a paper has no probabilistic lead-time axis and no ML comparison. Caps well below Tier B ambitions.
2. **Equity-first framing to Weather, Climate, and Society.** Less methodological work, high acceptance odds, genuinely appropriate audience.
3. **Methodology paper to QJRMS.** Consistency bars, provenance-audit design and representativeness treatment stand alone at current scale.
4. **Institutional partnership with a national met service or academic verification group.** Highest-leverage single move toward Nature Communications.
