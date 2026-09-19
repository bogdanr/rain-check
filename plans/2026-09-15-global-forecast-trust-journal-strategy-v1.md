# Global Forecast Trustworthiness: Journal Strategy, Gap Analysis, and Upgrade Roadmap

## Objective

Convert the rain-check pipeline into a publishable scientific study answering: **how much can we trust weather forecasts across the globe, and what methods establish that?** Define a ranked target list of ten journals including Nature-family venues, state precisely which gaps block each tier, and sequence the work that closes them.

## Current State Assessment

**Scientific assets already in place**

- Proper scoring rules with exact Murphy decomposition and an identity self-check (`src/calibration.py:67-109`).
- Consistency bars separating real miscalibration from sampling noise (`src/calibration.py:224-259`) — a technique rarely applied in consumer-forecast evaluation.
- Serial-correlation-aware uncertainty: moving block bootstrap and effective sample size (`src/calibration.py:114-164`).
- Honest out-of-sample recalibration with contiguous time-block folds, explicitly rejecting random folds as leakage (`src/calibration.py:180-221`).
- Confounder control: models pinned rather than `best_match` (`src/config.py:49-53`), with a held-fixed verdict showing city ordering survives (`data/processed/league_summary.json:2-8`).
- Sampling design that resists the obvious criticism: one city per gauge, per-country cap, elevation check against the forecast grid point (`src/probe_cities.py:16-33`).
- Fully reproducible, cache-deterministic pipeline (`run_all.sh:1-12`).

**The decisive finding for scale**

`src/probe_cities.py:8-13` establishes that 8,509 cities worldwide already have a usable precipitation gauge, and 8,150 have gauge plus temperature. The current 109-to-300-city sample is limited only by `MAX_CITIES = 300` (`src/probe_cities.py:73`) — an API fetch budget, not data availability. **Global scale is an engineering problem, not a data problem.** This is what makes a Nature-family submission arguable rather than fantasy.

**The decisive weakness**

Probability of precipitation is consumed as a vendor-derived field. `API_ENSEMBLE` is declared (`src/config.py:335`) but used nowhere outside a probe (`src/probe_apis.py:141-143`). Until PoP is computed from raw ensemble members, every ranking claim is about Open-Meteo's post-processing, not about ECMWF, NOAA or DWD. No top-tier venue will accept the stronger claim without this.

## Assumptions

- Target is a two-paper programme, not one paper: a dataset/resource descriptor and a findings paper. This doubles the Nature-family odds at less than double the cost.
- Nature (main journal) is treated as an aspirational stretch, assessed honestly rather than dismissed.
- The consulting material (`plans/2026-09-14-multi-provider-league-and-consulting-v1.md:39-42`) is severed from anything the paper cites.
- Global expansion uses NOAA ISD (already integrated, `src/config.py:102-111`) alongside GHCN-Daily, since ISD is genuinely global where GHCN-Daily thins out.

---

## Journal Assessment: Ten Targets

Ranked by strategic value, with an honest probability of acceptance **after the Phase 1–3 work below is complete**. "As-is" probabilities are near zero for everything above tier 3.

### Tier A — Nature family

**1. Nature** — *stretch, ~2%*
Needs a claim of broad significance beyond meteorology. The only version that qualifies: a global reliability map showing that forecast trustworthiness is systematically stratified by region, income and gauge density — i.e. the populations most exposed to weather hazards receive the least reliable probabilities — quantified against population and hazard exposure. Requires everything in Phases 1–4 plus the societal-exposure layer. Realistically this becomes a Nature Communications transfer.

**2. Nature Communications** — *plausible, ~12-18%*
The realistic ceiling. Accepts rigorous global-scale empirical studies with a clear novel claim. Requires: genuinely global coverage (≥2,000 cities, all inhabited continents, explicit Global South representation), native-ensemble PoP, ≥3 verifying years, an explanatory model for *why* skill varies, and the exposure framing. The provenance finding (silent model routing) is a strong secondary hook.

**3. Communications Earth & Environment** — *good, ~30-35%*
Nature Portfolio, explicitly for solid regional-to-global earth-system studies that lack the cross-field reach Nature Communications demands. **This is the highest-probability Nature-byline venue for the findings paper.** Primary recommendation.

**4. Scientific Data** — *high, ~55-65%*
Nature Portfolio dataset descriptor. The pipeline, the curated multi-model verification dataset, and the reproducibility guarantees are exactly what this venue publishes. Judged on technical quality and reuse potential, not novelty. **Fastest route to a Nature-family byline, and it creates the citable DOI the findings paper depends on.** Strongly recommended as submission #1.

**5. npj Climate and Atmospheric Science** — *moderate, ~20%*
Nature Portfolio, but skews climate over weather verification. Viable if the study emphasises multi-year systematic model behaviour rather than day-to-day forecast quality. Secondary option.

### Tier B — High-impact domain and interdisciplinary

**6. Bulletin of the American Meteorological Society (BAMS)** — *good, ~30%*
High impact and unusually receptive to community-relevant, method-surveying, "here is the state of what we can actually verify" papers. The methods-review dimension the study requires is a feature here, not padding. The consumer-provenance angle is precisely the kind of thing BAMS publishes to provoke the field. **Best non-Nature high-impact target.**

**7. Earth System Science Data (ESSD)** — *good, ~40%*
Copernicus, very high impact for a data journal, open review. Alternative or complement to Scientific Data for the dataset descriptor; open peer review also stress-tests the methodology publicly before the findings paper is judged.

**8. Environmental Research Letters** — *good, ~35%*
IOP, interdisciplinary, high visibility, comfortable with "societal relevance of geophysical skill" framing. A solid fallback if Communications Earth & Environment declines.

**9. Weather, Climate, and Society (AMS)** — *high, ~45%*
Purpose-built for the question "how much can people trust the forecasts they are given, and who is disadvantaged?". If the equity/exposure angle is the strongest result, this is arguably a better home than a general venue, and reviewers there will engage with it seriously rather than ask for more dynamics.

### Tier C — Core verification venues (safe harbours)

**10. Quarterly Journal of the Royal Meteorological Society** — *good, ~40%*
Home of forecast verification methodology. The right venue if the consistency-bar technique, the provenance-audit design and the representativeness treatment are positioned as the contribution.

**Also-rans, deliberately not in the ten but worth knowing:** *Weather and Forecasting* (AMS) and *Meteorological Applications* (RMetS) are the near-certain acceptances (~65%+) if the goal shifts from impact to publication; *Monthly Weather Review* is a poor fit (model dynamics focus); MDPI *Atmosphere* is fast but will not serve a Nature-level ambition and may harm perceived rigour.

### Recommended submission sequence

- [ ] Preprint to EGUsphere or ESS Open Archive immediately, to timestamp the provenance finding.
- [ ] Submission 1: dataset descriptor to **Scientific Data** or **ESSD** (achievable, creates the DOI).
- [ ] Submission 2: findings paper to **Nature Communications**, with a pre-agreed cascade to **Communications Earth & Environment**, then **BAMS** or **Environmental Research Letters**.
- [ ] Optional submission 3: methodology note to **QJRMS** if reviewers on submission 2 focus on the verification technique.

---

## Gap Analysis

Gaps are ranked by how much each one blocks the Nature-family tier. Each states the current evidence, the required state, and the venue tier it unlocks.

### Blocking gaps (Tier A is impossible without these)

**G1. Geographic scope and Global South representation.**
Current: 16 capitals plus ~109-300 cities, capped at `MAX_CITIES = 300` (`src/probe_cities.py:73`), with a per-country cap of 8 (`src/probe_cities.py:78`) that correctly prevents US/German dominance but also keeps the total small. A study titled "across the globe" that is largely European will be rejected on scope alone. Required: ≥2,000 cities, every inhabited continent, explicit reporting of coverage in Africa, South Asia, Southeast Asia, South America and small island states, plus honest documentation of where no verification is possible and why. Unlocks: Tiers A and B.

**G2. Probability provenance.**
Current: vendor-supplied PoP; ensemble endpoint unused (`src/config.py:335`, `src/probe_apis.py:141-143`). Required: PoP computed directly from ensemble members for at least the major global systems, with the vendor field retained as a comparison. This simultaneously removes the largest reviewer objection and creates a genuinely novel result — the divergence between native-ensemble probability and what consumers are actually served. Unlocks: Tiers A and C.

**G3. Truth uncertainty quantified, not caveated.**
Current: representativeness acknowledged via station spread (`src/config.py:70-74`) but not propagated. Required: gauge undercatch correction, a representativeness error term estimated from station pairs and propagated into every confidence interval, and satellite QPE (IMERG or CMORPH) as an independent cross-check in gauge-sparse regions. Without this, a global map is a map of gauge quality. Unlocks: Tiers A and C.

**G4. Explanatory model — the "why".**
Current: single-variable correlations that did not survive scale-up (r = −0.77 collapsing to r = +0.56 on calibration error). Required: a multivariate model of skill and calibration error against precipitation regime, convective fraction, orography, model resolution, station distance and latitude. Nature-family reviewers reject descriptive atlases; they accept studies that explain the pattern. Unlocks: Tier A.

**G5. Literature positioning.**
Current: no citations anywhere in the repository. Required: full positioning against Brier, Murphy's decomposition and value framework, WMO/WWRP verification guidance, Richardson's relative economic value, and the existing commercial verification landscape. Absent this, the submission is desk-rejected regardless of merit. Unlocks: all tiers.

### Major gaps (block Tier B credibility)

**G6. Record length.** `ARCHIVE_START = "2024-01-01"` with `OBS_END = "2026-05-31"` (`src/config.py:43-44`, `:125`) yields under two verifying years after the GHCN lag — two to three seasonal cycles. Required: ≥3 years, or explicit seasonal-stratification with a stated inability to separate regime from skill.

**G7. Metric breadth.** Current metrics are Brier, BSS, ECE, reliability. Required additions: CRPS for precipitation amount, ROC/AUC for discrimination, sharpness, and **relative economic value curves** — the last is the direct mathematical operationalisation of "how much can we trust this forecast for a decision," and is the single strongest addition for the societal framing.

**G8. Baselines.** BSS is currently referenced to climatology only. Required: persistence, smoothed day-of-year climatology, and ideally a simple MOS baseline, so that "skill" is defended against the obvious alternatives.

**G9. Multiple comparisons.** Thousands of city × model × lead combinations. Required: false discovery rate control, or explicit framing of city-level results as descriptive with only pooled claims treated as inferential. Rank intervals alone are insufficient at global scale.

**G10. Significance testing between models.** Required: Diebold-Mariano or equivalent paired tests on matched samples, accounting for spatial and temporal dependence, before any statement that one provider outperforms another.

### Supporting gaps (cheap, but each is a rejection risk if left open)

**G11. Rain-day convention provenance.** The offset is empirically derived from an ERA5 lag scan (`src/config.py:113-121`). The reasoning is sound and honestly documented, but reviewers will ask for the GHCN observation-time metadata field as corroboration. At global scale this must be automated per station, and a wrong offset silently corrupts results — this is the highest-consequence silent failure mode in the pipeline.

**G12. Data licence and redistribution.** Confirm Open-Meteo and GHCN terms permit redistribution of the derived archive; a dataset descriptor is impossible without this.

**G13. Competing interests.** Remove the consulting section from any artefact the paper references. Ranking named national meteorological services while advertising paid services is an unnecessary and fatal credibility risk.

**G14. Sampling-design defence.** The one-city-per-gauge and per-country-cap rules (`src/probe_cities.py:16-27`) are good science but change the estimand. Required: an explicit statement of what population the sample represents, plus a sensitivity analysis under alternative caps.

---

## Implementation Plan

### Phase 1 — Foundations (prerequisite for every venue)

- [ ] Task 1. Write the literature positioning: Brier, Murphy decomposition and value framework, WMO/WWRP verification guidance, Richardson relative economic value, existing commercial and national verification efforts. Rationale: closes G5, the single cheapest rejection risk.
- [ ] Task 2. Resolve and document the exact derivation of every consumed PoP field, quoting vendor documentation where it exists and measuring it where it does not. Rationale: partially closes G2 and determines what can be claimed in the abstract.
- [ ] Task 3. Confirm redistribution licensing for all upstream data sources and record the terms. Rationale: closes G12; blocks the dataset paper if it fails.
- [ ] Task 4. Sever the consulting material from the scientific artefact; produce a clean report build with no commercial content. Rationale: closes G13.
- [ ] Task 5. Automate per-station observation-time detection from GHCN metadata and reconcile it against the existing empirical lag scan; fail loudly on disagreement. Rationale: closes G11 and removes the highest-consequence silent corruption risk before scale-up multiplies it.

### Phase 2 — Global scale-up (unlocks Tier A/B scope)

- [ ] Task 6. Lift the city budget from 300 toward the full qualifying pool, redesigning collection for a multi-day, resumable, rate-limit-aware schedule. Rationale: closes G1; `src/probe_cities.py:8-13` shows the data already exists.
- [ ] Task 7. Integrate NOAA ISD globally alongside GHCN-Daily to recover regions where GHCN-Daily thins, and report coverage per continent and per income group. Rationale: G1; makes "across the globe" defensible rather than aspirational.
- [ ] Task 8. Add satellite QPE (IMERG or CMORPH) as an independent truth cross-check wherever gauge density is low, reported as agreement rather than substituted for gauges. Rationale: G3; also pre-empts "your map is a gauge-density map".
- [ ] Task 9. Quantify representativeness error from co-located station pairs and propagate it into every published interval. Rationale: G3.
- [ ] Task 10. Apply gauge undercatch correction and report results with and without it. Rationale: G3.
- [ ] Task 11. Extend the verification window as archives deepen; restructure results by season and by year. Rationale: G6.
- [ ] Task 12. Formalise the sampling design: state the target population, and run sensitivity analyses under alternative per-country caps and separation thresholds. Rationale: G14.

### Phase 3 — Methodological depth (unlocks Tier A/C rigour)

- [ ] Task 13. Compute PoP directly from ensemble members via the unused ensemble endpoint, and publish the divergence between native-ensemble probability and vendor-served probability. Rationale: closes G2 and is, on its own, a novel and publishable result.
- [ ] Task 14. Add CRPS, ROC/AUC and sharpness alongside the existing reliability metrics. Rationale: G7.
- [ ] Task 15. Implement relative economic value curves across cost-loss ratios. Rationale: G7; converts "calibration" into "trustworthiness for a decision", which is the study's actual question.
- [ ] Task 16. Add persistence and smoothed-climatology baselines to every skill statement. Rationale: G8.
- [ ] Task 17. Add paired significance testing between models on matched samples, respecting dependence. Rationale: G10.
- [ ] Task 18. Apply false discovery rate control across the city × model × lead grid, and restate which claims are inferential versus descriptive. Rationale: G9.

### Phase 4 — The Nature-level argument

- [ ] Task 19. Build the multivariate explanatory model of skill and calibration error against regime, orography, resolution, latitude and station geometry. Rationale: closes G4; supplies the "why" that separates a Nature-family paper from an atlas.
- [ ] Task 20. Join reliability results to population, hazard exposure and early-warning-system coverage, producing the headline societal statement (how many people live where probabilistic forecasts are unreliable). Rationale: the specific claim that makes Tier A arguable.
- [ ] Task 21. Test and report the forecast-inequality hypothesis — whether reliability is stratified by region and income once gauge density is controlled for — including the null result if it does not hold. Rationale: this is the paper's potential headline; publishing the null honestly is consistent with the project's existing standard.
- [ ] Task 22. Elevate the provenance audit into a standalone section: silent model routing, undisclosed post-processing, and what consumers are actually served. Rationale: the most genuinely novel contribution and the strongest hook for BAMS and Nature Communications alike.

### Phase 5 — Publication execution

- [ ] Task 23. Archive code and processed data on Zenodo with a citable DOI.
- [ ] Task 24. Post the preprint to timestamp priority before any journal decision.
- [ ] Task 25. Draft and submit the dataset descriptor (Scientific Data or ESSD).
- [ ] Task 26. Draft the findings paper against Nature Communications formatting, with the cascade order pre-agreed so a decline costs days, not months.
- [ ] Task 27. Solicit pre-submission review from a forecast-verification specialist; the field has entrenched conventions and an outsider submission is judged harshly on unfamiliar terminology alone.

## Verification Criteria

- Coverage spans ≥2,000 cities with every inhabited continent represented and per-region coverage tabulated, including regions where verification is impossible.
- Every published skill number carries an interval that includes representativeness uncertainty, not only sampling uncertainty.
- At least one headline probability series is computed from raw ensemble members independently of any vendor post-processing.
- Every model-versus-model claim is supported by a paired significance test on matched samples with FDR control applied.
- Relative economic value curves are published for at least the primary threshold and lead times.
- The explanatory model reports effect sizes with intervals, and null findings are stated as prominently as positive ones.
- All code and processed data are DOI-archived and the pipeline reproduces the published numbers from a cold start.
- No commercial or promotional content appears in any artefact cited by the manuscript.

## Potential Risks and Mitigations

1. **Vendor post-processing proves irreducible for some models.**
   Mitigation: restrict cross-provider ranking claims to models where native ensemble probability is obtainable; report the remainder as "as-served" with the distinction stated in the abstract, not the appendix.
2. **Global scale-up exhausts API budgets or trips rate limits for weeks.**
   Mitigation: the cache already makes collection resumable (`run_all.sh:96-108`); schedule collection over weeks and treat partial coverage as a reportable outcome rather than a blocker.
3. **The forecast-inequality hypothesis dissolves once gauge density is controlled.**
   Mitigation: pre-register the analysis and commit to publishing the null; the project's prior nulls are an asset, and a well-powered null on this question is itself Tier-B publishable.
4. **Scale multiplies a silent data-convention error across thousands of stations.**
   Mitigation: Task 5 precedes Task 6 deliberately; the existing loud-failure join validation (`run_all.sh:56-57`) must be extended to per-station convention checks before expansion.
5. **Reviewers from named national services contest the rankings.**
   Mitigation: publish matched-window comparisons, threshold sensitivity and the held-fixed check for every ranking; state the provenance caveat in the abstract; never rank on a single metric.
6. **Effort exceeds appetite and nothing is submitted.**
   Mitigation: the dataset descriptor is deliberately sequenced first — it is achievable from the current state plus Phase 1, delivers a Nature-family byline, and de-risks the larger paper.

## Alternative Approaches

1. **Findings paper first, dataset paper never.** Faster to the prestige target, but forfeits the achievable Nature-family byline and the citable DOI that strengthens the findings submission. Not recommended.
2. **Reframe entirely as a societal-equity study for Weather, Climate, and Society.** Substantially less methodological work — Phases 1, 2 and 4 without most of Phase 3 — with a high acceptance probability and a genuinely appropriate audience. The pragmatic choice if the ambition is impact-on-the-question rather than journal prestige.
3. **Methodology-only paper to QJRMS.** The consistency-bar technique, provenance audit design and representativeness treatment stand alone as a verification-methods contribution at current data scale. Cheapest credible publication, but abandons the global-trust question that motivates the study.
4. **Partner with a national meteorological service or academic verification group.** Adds institutional credibility, access to deeper archives and native model output, and a co-author who knows the reviewers. Costs control and time, but is the single highest-leverage move for a Nature Communications outcome.
