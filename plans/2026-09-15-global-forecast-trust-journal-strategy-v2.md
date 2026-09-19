# Global Forecast Trustworthiness: Journal Strategy, Gap Analysis, and Upgrade Roadmap

**Version 2.** Supersedes `plans/2026-09-15-global-forecast-trust-journal-strategy-v1.md`. Revised after the four-step decision sprint (prior art, ensemble availability, ML-model availability, licensing) and after two design decisions by the author: the deliberately short verification window, and multi-source probability triangulation.

## Objective

Publish a scientific study answering **how much weather forecasts can be trusted across the globe, and what methods establish that**. Secondary objective: a reusable, DOI-archived verification dataset and pipeline. This document defines the target journals, the evidence base, the remaining gaps, and the execution sequence.

---

## Evidence Established by the Decision Sprint

These findings are settled and should not be re-litigated.

### Data availability

- **Vendor PoP is already collected.** Hourly `precipitation_probability` and `precipitation` per city per model are on disk for 16 capitals × 15 models (`src/collect_archive.py:134`, `data/raw/provider_pop_*.parquet`), plus the unpinned `best_match` series (`historical_forecast_pop.parquet`). The study can be executed on existing data.
- **PoP begins 2024-04-25, not 2024-01-01.** The field is null in the archive before that date, established by bisection (`src/constants.py:3-5`). `ARCHIVE_START` (`src/config.py:43`) governs the deterministic tracks only.
- **Ensemble members are unavailable retrospectively.** Open-Meteo's Ensemble API retains individual members for three days; ensemble means and spreads exist only from March 2026. Self-computed PoP for 2024–2026 cannot come from Open-Meteo.
- **The Previous Runs archive starts January 2024** (GFS from March 2021, JMA from 2018) — so the window start is imposed by the data, not merely chosen.
- **Deterministic lead-time series carry amounts, not probabilities.** `collect_previous_runs` requests `temperature_2m_previous_dayN` and `precipitation_previous_dayN` (`src/collect_archive.py:73-74`). Whether the API also serves `precipitation_probability_previous_dayN` is **unresolved** and is the highest-value open probe.

### Machine-learning forecast systems are available

The Historical Forecast API serves ML systems with dated archive starts: **ECMWF AIFS 0.25° Single from 2025-02-20**, **NCEP AIGFS 0.25° from 2026-01-07**, **NCEP HGEFS from 2026-01-07**, and **Google WeatherNext 2** (64-member ensemble). The `PROVIDER_MODELS` registry (`src/config.py:245-318`) currently contains fifteen models and **no ML systems**. Whether `precipitation_probability` is served for AIFS is **unresolved** and requires a live probe.

### Licensing is cleared

- Open-Meteo data are **CC-BY 4.0**: redistribution and adaptation are expressly permitted with attribution. The dataset descriptor paper is viable.
- Open-Meteo encourages academic citation and supplies a DOI: Zippenfenig, P. (2023), `10.5281/ZENODO.7970649`.
- **UK Met Office upstream data are CC-BY-SA** — share-alike may propagate into a derived release. Segregate or licence accordingly.
- The free tier is non-commercial. With the consulting section removed from the project (author decision), disclosed public research is covered. Rate limits remain the practical constraint: 5,000/hour, 300,000/month, with some calls counted as multiples.

### The window design is vendor-documented

Open-Meteo's own Historical Forecast documentation states the archive is *"not suitable for long time series due to model version changes over time."* The provider documents the non-stationarity argument. Several models also **enter mid-window** (DMI and KNMI 2024-07-01, ItaliaMeteo 2025-04-13, MeteoSwiss 2025-07-29, AIFS 2025-02-20, AIGFS 2026-01-07), making matched-window comparison mandatory — already implemented in `src/probe_providers.py:10-15`.

### Prior art — partial

A completed search returned **methodology only, no competing study**. Encouraging but not conclusive; remaining searches were bot-blocked. References identified for citation:

- WGNE, *Suggested methods for the verification of precipitation forecasts* — treat as the compliance standard.
- CAWCR/WWRP verification portal (Ebert) — canonical PoP verification reference.
- ECMWF, *Probabilistic verification of ECMWF precipitation forecasts*; ECMWF Forecast User Guide §12.B.

Still required: Google Scholar, Web of Science, ECMWF eLibrary, AMS journals.

---

## Design Decisions (settled)

**D1. Verification window is deliberately short and homogeneous.** A longer archive would average over superseded model versions and measure systems that no longer run. The window is also the only period in which ML and physics systems coexist. Headline window: **2024-09-01 to 2026-08-31** (two complete years, inside the PoP archive), with the full record from 2024-04-25 published as a sensitivity run.

**D2. Seasonal imbalance is neutralised by weighting, not ignored.** Metrics are computed per season and pooled with climatological weights; BSS is referenced to a smoothed day-of-year climatology rather than the sample base rate (`src/calibration.py:85`, `:106`), which removes the regime-dependent bias that raw pooling would introduce across climate zones.

**D3. Probability provenance is addressed by triangulation, not avoidance.** Three independent estimates of the same city-day probability: (i) vendor PoP, already on disk; (ii) self-computed PoP from external ensemble archives (TIGGE, GEFS on AWS Open Data); (iii) forward-collected Open-Meteo members, bridging the two. **The divergence between (i) and (ii) is a primary result**, not a caveat. Existing evidence supports the design: mean PoP differs by a factor of five across models over identical hours (`src/pop_provenance.py:9-15`), ruling out a single shared field served under different labels.

**D4. The consulting material is excluded from the study entirely.**

---

## Journal Targets

Probabilities are post-Phase-2 estimates. They are higher than in v1 because D3 removes the objection previously rated fatal.

### Nature family

| Venue | Odds | Assessment |
|---|---|---|
| **Nature** | ~3% | Requires the global forecast-inequality claim tied to population and hazard exposure. Expect transfer down. |
| **Nature Communications** | ~15-20% | Genuine ceiling. Needs global coverage, the triangulation result, and a causal explanation of skill variation. The physics-versus-ML comparison strengthens this materially. |
| **Communications Earth & Environment** | ~35% | **Primary recommendation for the findings paper.** |
| **Scientific Data** | ~60% | **Submit first.** Licence cleared; judged on technical quality and reuse, not novelty. |
| **npj Climate and Atmospheric Science** | ~20% | Climate-skewed. Secondary. |

### High-impact domain and interdisciplinary

| Venue | Odds | Assessment |
|---|---|---|
| **BAMS** | ~30% | Best non-Nature high-impact target; receptive to the provenance and methods-survey dimensions. |
| **ESSD** | ~40% | Alternative to Scientific Data; open review stress-tests the methodology publicly. |
| **Environmental Research Letters** | ~35% | Visible fallback. |
| **Weather, Climate, and Society** | ~45% | The right home if the equity result is strongest — not a consolation prize. |
| **QJRMS** | ~40% | Safe harbour if the contribution is judged methodological. |

**Sequence:** preprint → dataset descriptor (Scientific Data or ESSD) → findings paper to Nature Communications with a pre-agreed cascade to Communications Earth & Environment, then BAMS or ERL.

---

## Gap Analysis

### Blocking Tier A

**G1. Geographic scope and Global South representation.** Currently 16 capitals plus ~109–300 cities, capped by `MAX_CITIES = 300` (`src/probe_cities.py:73`). Yet 8,509 cities worldwide already have a usable gauge (`src/probe_cities.py:8-13`) — the limit is fetch budget, not data. Required: ≥2,000 cities, all inhabited continents, coverage reported per region and income group, including where verification is impossible.

**G2. Probability provenance — now a contribution, not a blocker.** Resolved by D3. Required: external ensemble archive integrated, forward member collection running, divergence quantified and published.

**G3. Truth uncertainty quantified, not caveated.** Representativeness is acknowledged (`src/config.py:70-74`) but not propagated. Required: gauge undercatch correction, representativeness error from station pairs propagated into every interval, satellite QPE cross-check where gauges are sparse.

**G4. Explanatory model.** Single-variable correlations already collapsed once at scale. Required: multivariate model of skill and calibration error against regime, orography, resolution, latitude, station geometry.

**G5. Literature positioning.** Partially begun; must be completed against proper databases.

### Blocking Tier B credibility

**G6. Window homogeneity, not length.** Settled by D1 and D2. Required: document model-version change points within the window and demonstrate skill stability across them; publish the balanced-window and full-window results side by side.

**G7. Metric breadth.** Required additions: CRPS, ROC/AUC, sharpness, and **relative economic value curves** — the direct operationalisation of "trustworthy for a decision".

**G8. Baselines.** Add persistence and smoothed day-of-year climatology.

**G9. Multiple comparisons.** FDR control across the city × model × lead grid.

**G10. Significance testing.** Paired tests between models on matched samples, accounting for **spatial** dependence — since spatial replication is now the sole source of statistical power under D1.

### Supporting

**G11. Rain-day convention at scale.** The offset is empirically derived (`src/config.py:113-121`). Must be automated per station against GHCN observation-time metadata, failing loudly on disagreement, **before** expansion multiplies any error.

**G12. Observation latency.** `OBS_END = "2026-05-31"` reflects GHCN's ~3-month lag (`src/config.py:123-125`); `ISD_END = "2025-08-24"` (`src/config.py:110-111`) means the hourly track lags over a year. Closing the window at 2026-08-31 requires waiting until roughly December 2026 or adding a lower-latency source.

**G13. ML systems absent from the registry.** `PROVIDER_MODELS` (`src/config.py:245-318`) contains no ML models despite their availability.

**G14. Sampling-design defence.** State the target population implied by one-city-per-gauge and per-country caps (`src/probe_cities.py:16-27`); run sensitivity under alternative caps.

**G15. Licence segregation.** Handle CC-BY-SA propagation from UK Met Office data in any released dataset.

---

## Implementation Plan

### Phase 0 — Remaining decision probes (days)

- [ ] Task 1. Probe whether `precipitation_probability_previous_dayN` is served by the Previous Runs API. Rationale: determines whether the paper's central "trust versus lead time" axis is probabilistic or only deterministic. Highest-value open question.
- [ ] Task 2. Probe whether `precipitation_probability` is served for AIFS and the other ML models in the Historical Forecast archive, and from what date. Rationale: decides whether the physics-versus-ML comparison is available now or needs forward collection.
- [ ] Task 3. Complete the prior-art search against Google Scholar, Web of Science, ECMWF eLibrary and AMS journals. Rationale: closes G5; the only step that can still invalidate the framing.
- [ ] Task 4. Assess TIGGE and GEFS-on-AWS access, archive depth, variables and storage cost. Rationale: decides the feasibility of D3 route (ii) and therefore the Tier A ceiling.

### Phase 1 — Foundations

- [ ] Task 5. Write the literature positioning against Brier, Murphy, WGNE precipitation verification guidance, CAWCR/WWRP, Richardson's relative economic value, and existing national and commercial verification efforts. Rationale: G5; cheapest rejection risk.
- [ ] Task 6. Automate per-station observation-time detection from GHCN metadata; reconcile against the empirical lag scan and fail loudly on disagreement. Rationale: G11; must precede scale-up.
- [ ] Task 7. Add ML systems to `PROVIDER_MODELS` and to the collection loop. Rationale: G13.
- [ ] Task 8. Start forward collection of Open-Meteo ensemble members now, daily. Rationale: D3 route (iii); every day of delay is a day of data permanently lost to the three-day retention window.
- [ ] Task 9. Record licence provenance per model and segregate CC-BY-SA-derived outputs. Rationale: G15.

### Phase 2 — Global scale-up

- [ ] Task 10. Lift the city budget toward the full qualifying pool with resumable, rate-limit-aware, multi-day collection. Rationale: G1.
- [ ] Task 11. Integrate NOAA ISD globally alongside GHCN-Daily; report coverage per continent and income group. Rationale: G1.
- [ ] Task 12. Add satellite QPE cross-check where gauge density is low. Rationale: G3.
- [ ] Task 13. Quantify representativeness error from station pairs and propagate it into every interval. Rationale: G3.
- [ ] Task 14. Apply gauge undercatch correction; report with and without. Rationale: G3.
- [ ] Task 15. Add a lower-latency observation source to close the window at 2026-08-31 and to free the hourly track from the ISD lag. Rationale: G12.
- [ ] Task 16. Formalise the sampling design and run cap-sensitivity analyses. Rationale: G14.

### Phase 3 — Methodological depth

- [ ] Task 17. Implement the balanced headline window (2024-09-01 → 2026-08-31) with the full record as a published sensitivity run; report per-season metrics with climatological weighting. Rationale: D1, D2, G6.
- [ ] Task 18. Re-reference BSS to a smoothed day-of-year climatology in place of the sample base rate (`src/calibration.py:85`, `:106`). Rationale: G6 and G8 together.
- [ ] Task 19. Compile the model-version change-point timeline and test skill stability across each change. Rationale: G6; converts the homogeneity assumption into a measured result.
- [ ] Task 20. Integrate the external ensemble archive and compute independent PoP. Rationale: D3 route (ii), G2.
- [ ] Task 21. Publish the vendor-versus-ensemble PoP divergence as a primary result. Rationale: G2; the novel contribution.
- [ ] Task 22. Add CRPS, ROC/AUC and sharpness. Rationale: G7.
- [ ] Task 23. Implement relative economic value curves across cost-loss ratios. Rationale: G7; operationalises the research question.
- [ ] Task 24. Add persistence and smoothed-climatology baselines. Rationale: G8.
- [ ] Task 25. Add paired significance testing accounting for spatial and temporal dependence. Rationale: G10.
- [ ] Task 26. Apply FDR control; restate which claims are inferential versus descriptive. Rationale: G9.

### Phase 4 — The Nature-level argument

- [ ] Task 27. Build the multivariate explanatory model of skill and calibration error. Rationale: G4.
- [ ] Task 28. Join reliability to population, hazard exposure and early-warning coverage; derive the headline societal statement. Rationale: the claim that makes Tier A arguable.
- [ ] Task 29. Test the forecast-inequality hypothesis with gauge density controlled; publish the null if it does not hold. Rationale: potential headline; the null is itself publishable.
- [ ] Task 30. Elevate physics-versus-ML calibration to a primary result if Task 2 confirms availability. Rationale: strongest novelty hook, available only within this window.
- [ ] Task 31. Elevate the provenance audit — silent routing, undisclosed derivation — into a standalone section. Rationale: the most original contribution.

### Phase 5 — Publication

- [ ] Task 32. Archive code and processed data on Zenodo with a citable DOI.
- [ ] Task 33. Post the preprint to timestamp priority.
- [ ] Task 34. Draft and submit the dataset descriptor.
- [ ] Task 35. Draft the findings paper to Nature Communications format with the cascade pre-agreed.
- [ ] Task 36. Obtain pre-submission review from a forecast-verification specialist.

## Verification Criteria

- Coverage spans ≥2,000 cities across all inhabited continents, with per-region coverage tabulated including regions where verification is impossible.
- Every skill number carries an interval including representativeness uncertainty, not only sampling uncertainty.
- The vendor-versus-ensemble PoP divergence is quantified and published for at least the major global systems.
- Headline results are reported on the balanced window and shown to be unchanged on the full record.
- Every model-versus-model claim rests on a paired significance test with FDR control and spatial-dependence handling.
- Relative economic value curves are published for the primary threshold and lead times.
- Null findings are reported as prominently as positive findings.
- Code and data are DOI-archived; the pipeline reproduces published numbers from a cold start.
- Licence provenance is documented per model, with CC-BY-SA obligations respected.

## Potential Risks and Mitigations

1. **External ensemble archives prove too costly to integrate.**
   Mitigation: the study stands on vendor PoP alone as a "what consumers are served" paper; triangulation is an enhancement, not a dependency. Fall back to Tier B venues.
2. **Forward ensemble collection starts too late to be useful.**
   Mitigation: start immediately (Task 8); three-day retention means lost days are unrecoverable.
3. **PoP is unavailable at lead times.**
   Mitigation: state plainly that probabilistic results are day-ahead and lead-time results deterministic; consider deriving probabilities from the deterministic ensemble of models as a documented approximation.
4. **Scale multiplies a silent convention error.**
   Mitigation: Task 6 precedes Task 10 deliberately; extend the existing loud-failure join validation (`run_all.sh:56-57`).
5. **Rate limits throttle the global collection.**
   Mitigation: resumable cache (`run_all.sh:96-108`), multi-day scheduling, or a paid subscription for the collection period.
6. **Named providers contest the rankings.**
   Mitigation: matched windows, threshold sensitivity, held-fixed checks, provenance caveat in the abstract, never rank on one metric.
7. **Effort exceeds appetite and nothing is submitted.**
   Mitigation: the dataset descriptor is sequenced first — achievable from the current state plus Phase 1.

## Alternative Approaches

1. **Vendor-PoP-only paper, no ensemble work.** Faster, honest, caps at Tier B. The sensible choice if Task 4 shows TIGGE integration is expensive.
2. **Equity-first framing to Weather, Climate, and Society.** Much less methodological work, high acceptance odds, genuinely appropriate audience.
3. **Methodology paper to QJRMS.** Consistency bars, provenance-audit design and representativeness treatment stand alone at current scale.
4. **Institutional partnership with a national met service or academic verification group.** Highest-leverage single move toward Nature Communications: deeper archives, native model output, and a co-author who knows the reviewers.
