# Global Forecast Trustworthiness: Journal Strategy, Gap Analysis, and Upgrade Roadmap

**Version 4.** Supersedes `plans/2026-09-15-global-forecast-trust-journal-strategy-v3.md`. Revised after the first prior-art pass (`plans/2026-09-15-prior-art-assessment-v1.md`), which scooped one headline claim, sharpened three others, and forced a change of framing.

## Objective — revised

Publish a scientific study answering **how far the precipitation probabilities people are actually served can be trusted, worldwide, and by what method that is established**.

The shift from v3 is deliberate. "How accurate are forecasts globally" is now occupied (see D7). "How *trustworthy* are the probabilities people are served" is not, and is the more decision-relevant question: a forecast can be accurate on average and still systematically misrepresent its own uncertainty, and it is calibration — not accuracy — that determines whether a probability can be acted upon.

---

## Evidence Established (Phase 0 + prior-art pass)

Settled; do not re-litigate. Full detail in v3 §Phase 0 Outcomes and in the prior-art assessment.

### Data availability

- **Vendor PoP is on disk**, hourly, per city per model (`src/collect_archive.py:134`); PoP begins **2024-04-25** (`src/constants.py:3-5`).
- **Vendor PoP does not exist at lead times.** `precipitation_probability_previous_day1…7` are accepted and return **entirely null** at every probed date and model. A `_previous_day99` negative control was also accepted and null, proving the API echoes arbitrary names — only non-null counts are evidence (`data/raw/pop_leads_and_ml_probe.json`, `q1_previous_runs_pop_at_lead`).
- **Vendor PoP does not exist for ML models.** Live probe, 2026-09-15: `ecmwf_ifs025` returns 144/144 non-null PoP; `ecmwf_aifs025_single`, `ncep_aigfs025` and `ncep_hgefs025_ensemble_mean` return 144/144 precipitation and **0/144 PoP**. Valid ids established; `aigfs025`, `hgefs025`, `ncep_hgefs025` are rejected by the API.
- **External ensembles are cheap and licence-clean.** TIGGE is struck — CC BY-**NC** plus a no-third-party-redistribution clause, fatal to the dataset descriptor. Replaced by `dynamical.org` Zarr: GEFS 31 members gapless from 2020 (**79 GB** for 2,000 cities), ECMWF IFS ENS 51 members from 2024-04-01 (361 GB), AIFS ENS 51 members from 2025-07-02. All **CC-BY-4.0**. 5–8 dev-days total. Full report: `plans/2026-09-15-external-ensemble-archive-feasibility-v1.md`.
- **Forward ensemble collection is live** — `src/collect_ensemble.py`, 11 systems × 19 capitals, ~1,490 billed calls/day, retention measured at ≈4.1 days.

### Prior art

- **Linsenmeier & Shrader (2025) scoops the forecast-inequality claim**, including the gauge-density control. Station-verified, population-weighted, ECMWF 1985–2020, ~7% of the rich/poor gap attributed to in-situ infrastructure. **Zero probabilistic content** across all 36 pages — no Brier, CRPS, reliability or calibration.
- **WeatherBench 2 does perform probabilistic verification** (CRPS, spread-skill, rank histograms). The v3-era assumption that the field does not was wrong. But its **precipitation ground truth is ERA5**, and its authors state that ERA5 precipitation *"often show large differences to rain gauges"* and is *"not directly assimilated"*. Its precipitation score, SEEPS, is explicitly deterministic.
- **Gupta et al. (2026)** evaluated AI models against observations during the South Asian monsoon — regional, largely deterministic. The AI-versus-observations niche is being actively worked.
- **Hewson & Pillosu (2021)**, Comms Earth & Env — ecPoint global point-rainfall calibration. Closest methodological neighbour; a method that *improves* forecasts rather than an audit of what is *served*.

---

## Design Decisions (settled)

**D1. Verification window is deliberately short and homogeneous.** Headline **2024-09-01 → 2026-08-31**; full record from 2024-04-25 as a published sensitivity run. Justified three ways: the Previous Runs archive begins January 2024; PoP begins 2024-04-25; and Open-Meteo documents that the archive is *"not suitable for long time series due to model version changes over time."*

**D2. Seasonal imbalance is neutralised by weighting.** Per-season metrics pooled with climatological weights; BSS referenced to a smoothed day-of-year climatology rather than the sample base rate (`src/calibration.py:85`, `:106`).

**D3. Probability provenance is addressed by triangulation.** (i) vendor PoP, on disk; (ii) self-computed PoP from `dynamical.org` GEFS and ECMWF IFS ENS — retrospective, full window, CC-BY-4.0; (iii) forward-collected Open-Meteo members, bridging and validating. **The divergence between (i) and (ii) is a primary result.**

**D4. The consulting material is excluded from the study entirely.**

**D5. The lead-time axis is ensemble-derived, not vendor-derived.** Forced by the null PoP-at-lead result. Better design regardless: lead-dependent skill decay is measured on a probability whose construction we document ourselves. The negative probe result is published as justification, not hidden.

**D6. Physics-versus-ML is member-derived and window-limited.** Forced by the null ML PoP result. Matched window bounded by AIFS ENS availability (2025-07-02 →), ~14 months. Reported as a matched-window sub-study, never pooled with full-window physics results.

**D7. The claim is calibration, not accuracy. — NEW.** Linsenmeier & Shrader own "forecasts are less accurate where people are poorer." This study asks the successor question: **do the probabilities people are served also misrepresent their own reliability, and does that miscalibration compound the accuracy gap in decision terms?** They are cited as foundation, never competed with. Their own discussion calls for the economic-value work that operationalises this.

**Confirmed 2026-09-15 (Task 3a).** Their supplementary information is bundled in the same PDF and contains nothing probabilistic: a 17-term scan across all 2,802 extracted lines returns zero hits for `brier`, `crps`, `reliabilit`, `calibrat`, `probabilistic`, `probability`, `quantile`, `spread`, `skill score` and `uncertaint` alike — the word "probability" never appears. Their measure is the **anomaly correlation coefficient** on 2 m temperature, with bias explicitly excluded; precipitation is a secondary ACC check on fixed 0–24 UTC totals; ensembles appear only for seasonal forecasts and are handled by averaging deterministic scores across members. D7 is therefore safe, and three of their methodological choices are openings this study can occupy legitimately — see `plans/2026-09-15-prior-art-assessment-v1.md` §3.1.

**D8. Station truth is a contribution, not a constraint. — NEW.** WeatherBench 2 verifies precipitation against ERA5 while its authors document that ERA5 precipitation diverges from gauges and is not assimilated. Gauge truth is therefore a deliberate, externally-justified deviation from the field standard and must be argued as such in the abstract — not buried as a methods detail.

---

## Journal Targets

Revised from v3. The dataset descriptor is unaffected and remains the highest-probability acceptance. The findings paper moves slightly *down* on raw novelty (C4 lost) but *up* on defensibility (framing now supported by competitors' own stated limitations).

### Nature family

| Venue | Odds | Assessment |
|---|---|---|
| **Nature** | ~2% | The inequality route is closed by Linsenmeier & Shrader. Not a realistic target. |
| **Nature Communications** | ~15–20% | Down from 20–25%. Needs global coverage, the triangulation result, economic value, and the ML calibration comparison. Watch whether Linsenmeier & Shrader lands here first. |
| **Communications Earth & Environment** | **~40%** | **Primary recommendation.** Hewson & Pillosu 2021 confirms the venue publishes exactly this class of work. |
| **Scientific Data** | **~60–65%** | **Submit first.** Licence clear on every input in scope. |
| **npj Climate and Atmospheric Science** | ~20% | Secondary. |

### High-impact domain and interdisciplinary

| Venue | Odds | Assessment |
|---|---|---|
| **BAMS** | ~35% | Up: the provenance audit and the "what consumers are served" register fit BAMS well, and it is unaffected by the inequality scoop. |
| **ESSD** | ~40–45% | Alternative to Scientific Data; open review stress-tests methods publicly. |
| **ERL** | ~35% | Visible fallback. |
| **Weather, Climate, and Society** | ~45% | Still strong — the trustworthiness-versus-accuracy distinction is native to this audience. |
| **QJRMS** | ~40% | Safe harbour if judged methodological. |

**Sequence:** preprint → dataset descriptor (Scientific Data or ESSD) → findings paper to Communications Earth & Environment, with Nature Communications attempted first only if the ML calibration result proves strong.

---

## Gap Analysis

### Blocking Tier A

**G1. Geographic scope.** 16 capitals plus ~109–300 cities, capped by `MAX_CITIES = 300` (`src/probe_cities.py:73`), against 8,509 qualifying gauged cities (`src/probe_cities.py:8-13`). Required: ≥2,000 cities, all inhabited continents, coverage reported per region and income group including where verification is impossible.

**G2. Probability provenance.** Route settled by D3. Required: GEFS then IFS ENS integrated, forward collection sustained, divergence quantified and published.

**G3. Truth uncertainty quantified, not caveated.** Gauge undercatch correction, representativeness error propagated into every interval, IMERG cross-check where gauges are sparse. Now doubly important under D8: if station truth is the contribution, its error budget must be the most rigorous part of the paper.

**G4. Explanatory model.** Multivariate model of skill and calibration error against regime, orography, resolution, latitude, station geometry. Single-variable correlations already collapsed once at scale.

**G5. Literature positioning — partially closed.** First pass done (`plans/2026-09-15-prior-art-assessment-v1.md`), and Task 3a closed the Linsenmeier boundary question. **Five checks remain open**; see Tasks 3b–3c.

### Blocking Tier B credibility

**G6. Window homogeneity.** Document model-version change points within the window; demonstrate skill stability across them.

**G7. Metric breadth.** CRPS, ROC/AUC, sharpness, and **relative economic value curves**. Elevated by D7: economic value is now the mechanism that converts calibration into the decision currency Linsenmeier & Shrader's audience already accepts, and their discussion explicitly asks for it.

**G8. Baselines.** Persistence and smoothed day-of-year climatology.

**G9. Multiple comparisons.** FDR control across the city × model × lead grid.

**G10. Significance testing.** Paired tests accounting for **spatial** dependence, since spatial replication is the sole source of power under D1.

### Supporting

**G11. Rain-day convention at scale.** Automate per-station observation-time detection from GHCN metadata, failing loudly on disagreement, **before** expansion multiplies the error (`src/config.py:113-121`). Note Linsenmeier & Shrader use fixed 0–24 UTC GSOD totals with no per-station handling — doing this properly is a genuine methodological improvement over the scooping paper and should be stated as one.

**G12. Observation latency.** `OBS_END = "2026-05-31"` reflects GHCN's ~3-month lag (`src/config.py:123-125`); `ISD_END = "2025-08-24"` (`src/config.py:110-111`). Closing at 2026-08-31 needs a wait until ~December 2026 or the `asos-parquet` route.

**G13. ML systems absent from the registry — partially resolved.** Valid ids known; vendor PoP does not exist for them, so registry addition serves the deterministic and member-derived tracks only.

**G14. Sampling-design defence.** State the target population implied by one-city-per-gauge and per-country caps (`src/probe_cities.py:16-27`); run cap-sensitivity.

**G15. Licence segregation.** CC-BY-SA propagation from UK Met Office data, in both the vendor `ukmo_*` models and the `ukmo_*_ensemble` forward collection.

**G16. Third-party republication dependency.** `dynamical.org` is a republisher. For a paper whose contribution is a provenance audit, depending on it unverified is self-undermining. Required: validate a sample against raw `noaa-gefs-pds` GRIB2 byte-ranges, disclose rounded-mantissa compression, resolve assets from STAC at run time (`data.dynamical.org` URLs retire 2026-09-30), archive extracted point data to Zenodo.

**G17. Temporal quantisation.** Ensemble steps are 3-hourly; vendor PoP is hourly. Local midnight is unreachable for non-multiple-of-3 UTC offsets. Required: pro-rata boundary apportionment or snapping with reported sensitivity, **plus** vendor PoP re-derived at 3-hourly quantisation as a control, so resolution mismatch is separated from the genuine provenance signal.

**G18. Competitor-collision monitoring. — NEW.** Linsenmeier & Shrader (Nov 2025) is likely under review at a high-profile venue; Gupta et al. (2026) shows the AI-versus-observations niche is active. Required: re-run the prior-art queries before submission, and avoid submitting adjacent to wherever Linsenmeier & Shrader lands.

---

## Implementation Plan

### Phase 0 — Decision probes (COMPLETE except literature)

- [x] Task 1. Probe `precipitation_probability_previous_dayN`. → **Null**; `src/probe_pop_leads_and_ml.py`.
- [x] Task 2. Probe ML-model PoP availability and archive starts. → **Null for PoP**; ids and amount-starts established.
- [x] Task 3. Prior-art first pass. → `plans/2026-09-15-prior-art-assessment-v1.md`. C4 scooped; C1/C2/C3/C6 survive; framing revised per D7/D8.
- [x] Task 4. External ensemble archive assessment. → TIGGE struck; `dynamical.org` recommended.
- [x] Task 8. Forward ensemble collection built and started.

### Phase 1 — Close the literature question, then harden

- [x] Task 3a. ~~Read Linsenmeier & Shrader's supplementary information and full methods.~~ **DONE 2026-09-15.** SI bundled in the same PDF; 17-term probabilistic scan over 2,802 lines returned zero hits; methods read. D7 confirmed safe. Metric is anomaly correlation coefficient on temperature, bias excluded. See `plans/2026-09-15-prior-art-assessment-v1.md` §3.1.
- [ ] Task 3b. Run the C1–C6 queries against Google Scholar Labs, Web of Science, ECMWF eLibrary and AMS journals; verify every returned citation by opening the paper. Rationale: G5; seven opportunistic PDFs are not a search.
- [ ] Task 3c. Search grey literature — ForecastWatch, EUMETNET, WMO/WWRP working groups — and forward-cite the WGNE guidance and CAWCR/WWRP portal. Rationale: G5; the likeliest home of anything resembling C1 or C5, invisible to academic indexes.
- [ ] Task 5. Write the literature positioning around the corrected framing: continuous ensemble verification against reanalysis is established; binary-event reliability of served probabilities against gauges is not. Rationale: G5, D7, D8.
- [ ] Task 6. Automate per-station observation-time detection from GHCN metadata; reconcile against the empirical lag scan; fail loudly on disagreement. Rationale: G11; must precede scale-up, and is an improvement over the scooping paper.
- [ ] Task 7. Add validated ML ids to `PROVIDER_MODELS` for the deterministic track. Rationale: G13, D6.
- [ ] Task 8a. Install the daily cron for `src/collect_ensemble.py collect` plus a staleness alarm. Rationale: D3(iii); ~4-day retention makes an unnoticed failure unrecoverable.
- [ ] Task 9. Record licence provenance per model; segregate CC-BY-SA-derived outputs. Rationale: G15.

### Phase 2 — External ensemble integration

- [ ] Task 20a. Integrate `noaa-gefs-forecast-35-day`; compute member-derived PoP at all leads for the headline window. Rationale: G2, D5. **Highest return per dev-day; 3–5 days.**
- [ ] Task 20b. Integrate `ecmwf-ifs-ens-forecast-15-day-0-25-degree`. Rationale: two independent centres. +1–2 days.
- [ ] Task 20c. Validate a sample against raw `noaa-gefs-pds` GRIB2 byte-ranges; report agreement. Rationale: G16.
- [ ] Task 20d. Local-day accumulation with documented boundary-step handling, plus the 3-hourly vendor-PoP control. Rationale: G17.

### Phase 3 — Global scale-up

- [ ] Task 10. Lift the city budget toward the full qualifying pool; resumable, rate-limit-aware, multi-day collection. Rationale: G1.
- [ ] Task 11. Integrate NOAA ISD globally alongside GHCN-Daily; report coverage per continent and income group. Rationale: G1.
- [ ] Task 12. IMERG satellite QPE cross-check where gauge density is low. Rationale: G3.
- [ ] Task 13. Quantify representativeness error from GEFS neighbourhood fields and station pairs; propagate into every interval. Rationale: G3, D8.
- [ ] Task 14. Gauge undercatch correction; report with and without. Rationale: G3, D8.
- [ ] Task 15. Evaluate `asos-parquet` as the low-latency truth source. Rationale: G12.
- [ ] Task 16. Formalise the sampling design; cap-sensitivity analyses. Rationale: G14.

### Phase 4 — Methodological depth

- [ ] Task 17. Balanced headline window with the full record as a published sensitivity run; per-season metrics with climatological weighting. Rationale: D1, D2, G6.
- [ ] Task 18. Re-reference BSS to a smoothed day-of-year climatology. Rationale: G6, G8.
- [ ] Task 19. Model-version change-point timeline; test skill stability across each change. Rationale: G6.
- [ ] Task 21. Publish the vendor-versus-ensemble PoP divergence as a primary result. Rationale: G2.
- [ ] Task 22. Add CRPS, ROC/AUC and sharpness. Rationale: G7. Note CRPS is the field-standard comparator used by WeatherBench 2 — reporting it makes the study legible to that community.
- [ ] Task 23. Relative economic value curves across cost-loss ratios. Rationale: G7, D7. **Promoted to a headline result**, not a supporting metric: it is the mechanism converting calibration into decision value, and the scooping paper explicitly calls for it.
- [ ] Task 24. Persistence and smoothed-climatology baselines. Rationale: G8.
- [ ] Task 25. Paired significance testing accounting for spatial and temporal dependence. Rationale: G10.
- [ ] Task 26. FDR control; restate which claims are inferential versus descriptive. Rationale: G9.

### Phase 5 — The headline argument (restructured)

- [ ] Task 27. Multivariate explanatory model of skill and calibration error. Rationale: G4.
- [ ] Task 28. **REPLACES the accuracy-inequality task.** Test whether *calibration* error — not accuracy — varies with income group, gauge density and region, citing Linsenmeier & Shrader for the established accuracy gap and asking whether the served probabilities additionally misrepresent their reliability. Note their gap is measured on **temperature by anomaly correlation**; precipitation is a secondary check in their work, so the precipitation-calibration question is doubly open. Rationale: D7; the surviving equity claim.
- [ ] Task 29. Express any calibration gap in decision terms via the Task 23 economic-value curves: how much value is lost by acting on a miscalibrated probability, and is that loss distributed unequally. Publish the null if no gap exists. Rationale: D7; the claim that could still carry a Nature-family submission.
- [ ] Task 30. Physics-versus-ML calibration on the AIFS-ENS matched window, member-derived. Rationale: D6. Time-sensitive — Gupta et al. shows the niche is active.
- [ ] Task 31. Elevate the provenance audit into a standalone section: silent `best_match` routing, undisclosed derivation, **no probability served beyond day one, and none at all for ML systems**. Rationale: the most original contribution; Tasks 1 and 2 are themselves publishable findings about what the public can and cannot obtain.

### Phase 6 — Publication

- [ ] Task 32. Archive code and extracted point data on Zenodo with a citable DOI. Rationale: also mitigates G16.
- [ ] Task 33. Post the preprint to timestamp priority.
- [ ] Task 34. Draft and submit the dataset descriptor.
- [ ] Task 35. Draft the findings paper for Communications Earth & Environment, cascade pre-agreed.
- [ ] Task 36. Pre-submission review from a forecast-verification specialist.
- [ ] Task 37. Re-run the prior-art queries immediately before submission; check where Linsenmeier & Shrader landed. Rationale: G18.

## Verification Criteria

- Coverage spans ≥2,000 cities across all inhabited continents, with per-region coverage tabulated including regions where verification is impossible.
- Every skill number carries an interval including representativeness uncertainty, not only sampling uncertainty.
- The vendor-versus-ensemble PoP divergence is quantified and published, with the quantisation control reported separately.
- Headline results are reported on the balanced window and shown to be unchanged on the full record.
- Every model-versus-model claim rests on a paired significance test with FDR control and spatial-dependence handling.
- Relative economic value curves are published for the primary threshold and lead times.
- CRPS is reported alongside Brier-family metrics so results are comparable to WeatherBench 2.
- Null findings — the PoP-at-lead null, the ML-PoP null, and any calibration-equity null — are reported as prominently as positive findings.
- Member-derived values are validated against raw GRIB for a documented sample.
- Station truth is defended explicitly against the reanalysis-truth standard, citing WeatherBench 2's own caveat.
- Code and data are DOI-archived; the pipeline reproduces published numbers from a cold start.
- Licence provenance is documented per model, with CC-BY-SA obligations respected.

## Potential Risks and Mitigations

1. **~~External ensemble archives too costly.~~** Retired: 5–8 dev-days, licence clean.
2. **Forward ensemble collection silently stops.** ~4-day retention makes unnoticed failure unrecoverable. Mitigation: Task 8a alarm; `status` names missing dates.
3. **~~PoP unavailable at lead times.~~** Confirmed true; resolved by D5 and published as a finding.
4. **Further scooping.** C4 is already lost; C3 is time-sensitive. Mitigation: Tasks 3b, 3c and 37; preprint early (Task 33); prioritise Task 30.
5. **Scale multiplies a silent convention error.** Mitigation: Task 6 precedes Task 10; extend the loud-failure join validation (`run_all.sh`).
6. **Rate limits throttle global collection.** Mitigation: resumable cache, multi-day scheduling; ensemble collection alone consumes ~15% of daily quota.
7. **`dynamical.org` changes or disappears.** Mitigation: G16 — STAC-resolved URLs, raw-GRIB fallback proven, own data archived.
8. **Named providers contest the rankings.** Mitigation: matched windows, threshold sensitivity, held-fixed checks, provenance caveat in the abstract, never rank on one metric.
9. **Effort exceeds appetite.** Mitigation: the dataset descriptor is sequenced first and is achievable from the current state plus Phase 1.
10. **The calibration-equity result is null.** Mitigation: a well-powered null on whether miscalibration compounds the accuracy gap is publishable and directly answers a question Linsenmeier & Shrader leave open. Plan to publish it either way.

## Alternative Approaches

1. **Provenance-audit paper alone**, to BAMS. Unaffected by the inequality scoop, shortest path, genuinely novel. The strongest fallback.
2. **Equity-of-calibration framing to Weather, Climate, and Society.** Native audience for the accuracy-versus-trustworthiness distinction.
3. **Methodology paper to QJRMS.** Consistency bars, provenance-audit design, representativeness treatment.
4. **Institutional partnership with a national met service or academic verification group.** Highest-leverage single move toward a Nature-family outcome.
