# Global Forecast Trustworthiness: Journal Strategy, Gap Analysis, and Upgrade Roadmap

**Version 5.** Supersedes `plans/2026-09-15-global-forecast-trust-journal-strategy-v4.md`. Revised after Phases 2 and 4 were substantially implemented and measured. Three gaps closed with evidence, one headline hypothesis **partially falsified**, and the framing tightened as a result.

## Objective — unchanged from v4

Publish a scientific study answering **how far the precipitation probabilities people are actually served can be trusted, worldwide, and by what method that is established**.

---

## What Changed in v5

v4 was a plan. v5 records **measurements**. The important development is that the study's central hypothesis — that vendor-served probabilities are meaningfully worse than the ensemble supports — **did not survive contact with the data at the only lead where a clean comparison is possible**. That is recorded here as the finding it is, and the framing is adjusted to match rather than to preserve the original claim.

---

## Evidence Established

Settled; do not re-litigate.

### Data availability (from v4, unchanged)

- **Vendor PoP is on disk**, hourly, per city per model (`src/collect_archive.py:134`); PoP begins **2024-04-25** (`src/constants.py:3-5`).
- **Vendor PoP does not exist at lead times.** `precipitation_probability_previous_day1…7` return entirely null; a `_previous_day99` negative control was also accepted and null, proving only non-null counts are evidence (`data/raw/pop_leads_and_ml_probe.json`).
- **Vendor PoP does not exist for ML models.** `ecmwf_aifs025_single`, `ncep_aigfs025`, `ncep_hgefs025_ensemble_mean` return 144/144 precipitation and **0/144 PoP**.
- **TIGGE struck on licence** (CC BY-NC plus no-redistribution); replaced by `dynamical.org` Zarr, all CC-BY-4.0.
- **Forward ensemble collection is live** — `src/collect_ensemble.py`, 11 systems × 19 capitals, retention ≈4.1 days.

### Prior art (from v4, unchanged)

- **Linsenmeier & Shrader (2025) scoops the forecast-inequality claim.** Zero probabilistic content across all 36 pages including bundled SI; measure is anomaly correlation coefficient on temperature, bias excluded.
- **WeatherBench 2 does perform probabilistic verification** — but its precipitation ground truth is ERA5, and its authors document the gauge divergence themselves. Its precipitation score, SEEPS, is deterministic.
- **Gupta et al. (2026)**: AI-versus-observations niche is active, regional and largely deterministic.
- **Hewson & Pillosu (2021)**, Comms Earth & Env: closest methodological neighbour; improves forecasts rather than auditing what is served.

### Measured results — NEW in v5

**E1. Member-derived PoP now exists for the full headline window.** `data/processed/gefs_pop.parquet` — 19 cities, **2024-09-01 → 2026-08-31 exactly**, leads 1–7, 31 members, 100% usable, both boundary modes. This is the balanced window of D1, covered end to end. Built by `src/collect_gefs.py`, `src/gefs_archive.py`, `src/ensemble_pop.py`.

**E2. G16 is closed — the republisher is faithful.** Validation of `dynamical.org` GEFS against raw `noaa-gefs-pds` GRIB2 (`data/processed/gefs_grib_validation.json`): 180 comparisons, **mean absolute difference 0.0012 mm, max 0.04 mm**, zero event flips at 0.1/0.2/1.0 mm within a 0.005 mm tolerance, 105 of 180 bit-exact, no missing values. Two mildly negative raw values observed (min −0.04 mm), an artefact of the rounded-mantissa compression, which must be disclosed.

**E3. G17 is closed — quantisation does not drive the divergence.** `data/processed/vendor_pop_quantisation.parquet`, 16,416 city-days: re-deriving vendor PoP at 3-hourly resolution shifts it by a **median of 0.00** and a mean of 0.0089 under snapping. A tail exists — 99th percentile 0.36 under pro-rata — so the control must be reported distributionally, not as a single mean. But resolution mismatch is ruled out as the explanation for the vendor-versus-ensemble gap.

**E4. The triangulation result — the headline, and it is a tie.** `src/triangulation.py`, 8,572 city-days, 15 capitals, set-identical sample enforced per (boundary mode, lead) cell.

*Divergence*, vendor `gfs_seamless` minus GEFS-derived, pro-rata:

| lead | bias | mean abs | median abs | Pearson r | share >0.2 |
|---|---|---|---|---|---|
| 1 | −0.078 | 0.134 | 0.032 | 0.861 | 24% |
| 3 | −0.068 | 0.178 | 0.097 | 0.786 | 33% |
| 5 | −0.061 | 0.245 | 0.194 | 0.648 | 47% |
| 7 | −0.069 | 0.301 | 0.258 | 0.510 | 60% |

Vendor PoP is systematically **drier** than the raw ensemble supports, by 0.06–0.08 at every lead.

*Calibration at lead 1 — the only same-lead comparison:* Brier **vendor 0.1669 vs GEFS 0.1672**. The gap, 0.0003, is **smaller than the 0.0015 the ensemble's own Brier moves under the day-boundary rule alone**. This is a tie, and is reported as one. Vendor has better reliability (0.0151 vs 0.0214 — the raw 31-member frequency over-forecasts); GEFS has better discrimination (AUC 0.862 vs 0.846) and resolution. The ensemble's any-step variant beats both (Brier 0.1579, BSS 0.322). Vendor wins in 63% of 105 city × lead cells.

**E4a. The Brier tie conceals a real decision-value gap at low cost-loss ratios. — IMPORTANT.** At lead 1, on the identical paired sample, relative economic value:

| α | vendor | GEFS-derived |
|---|---|---|
| 0.05 | **−0.748** | **−0.206** |
| 0.10 | **−0.080** | **+0.124** |
| 0.20 | 0.282 | 0.376 |
| 0.30 | 0.456 | 0.497 |
| 0.50 | 0.481 | 0.465 |
| peak | 0.571 | 0.574 |

Peak value and Brier are tied, but for the **low-cost-ratio user — who acts cheaply and often to protect against a rare expensive loss — the served probability is actively harmful where the member-derived one is not.** The vendor's dry bias (E4) is exactly what destroys value at low α: a systematically too-low probability fails to trigger action at low thresholds. The honest verdict is therefore *tied on average scores, materially worse for an identifiable and decision-relevant class of user.* This is a sharper claim than the original hypothesis, and it is **invisible to Brier** — which is itself an argument for reporting economic value, per D7.

**E5. Decision value is established.** `src/decision_metrics.py`, 65 capital × model series plus 104 world cities. Peak relative economic value **median 0.559 at α ≈ 0.40**; positive somewhere for **100%** of series; positive across a median 66% of the cost-loss range. Persistence peaks at 0.089. **The price of miscalibration — acting on the served number versus the best trigger — is a median of only 0.017 at the peak.** At α = 0.10 the acting rule gives median V = 0.092 and is positive for just 55% of series, against 0.196 for the best trigger: the low-cost-ratio user is where miscalibration actually bites.

**E6. Baselines and metric breadth are done.** Against the smoothed day-of-year climatology on identical days: forecast **+0.293** (beats it in 94% of series); 30-day persistence −0.001 (48%); yesterday-0/1 persistence −0.306 (0%); flat base rate +0.026 (82%). AUC median 0.845 versus 0.569 for persistence. **CRPS on precipitation amount is negative — CRPSS −0.076, positive in only 32% of series** — i.e. the forecast amount does not beat a leave-one-out seasonal climatology, in sharp contrast to the binary event. That contrast is a finding, not a defect.

**E7. The climatology reference matters and is regime-dependent.** Task 18: re-referencing BSS to the smoothed day-of-year climatology shifts it by a median **+0.0180**, range −0.0121 to +0.0723, with **18% of series losing skill**. The shift correlates with seasonality amplitude (r = −0.452) and rain-day frequency (r = +0.480) — so it is *not* noise, and it tracks the exact axis the cross-regime comparison runs along. Direction is stable across all six smoother settings; magnitude is not (spread 0.0717). The sample-referenced BSS cannot be used for cross-regime comparison.

**E8. Lead-time value decay is quantified.** Bucharest, derived probabilities: AUC 0.869 → 0.693 and peak value 0.681 → 0.356 across leads 1–7. At α = 0.10 value is **zero beyond lead 2** — the low-cost-ratio user gets nothing from a three-day-ahead rain probability.

**E9. Two vendor models are not independent.** `ecmwf_ifs025` and `metno_seamless` return **identical** series at these cities, detected automatically by the triangulation stage. Any cross-centre claim treating them as independent evidence is invalid.

---

## Design Decisions

D1–D8 carry over from v4 unchanged. Four additions:

**D9. The tie is published as the result — but the tie is in the average, not in the decision. — NEW.** At the only clean same-lead comparison the served probability is as well calibrated as the raw ensemble frequency (E4), and the study does **not** reframe to preserve the original hypothesis. The honest finding is: *the served number is drier than the ensemble supports and carries no lead axis at all, yet is not meaningfully less trustworthy at day one by average score — because the vendor's post-processing buys back in reliability what it loses in resolution.* The tie holds under both boundary rules. **E4a qualifies this decisively:** the average-score tie does not hold in decision terms at low cost-loss ratios, where the served number is actively harmful. The paper's claim is therefore *conditional on the user*, not global.

**D12. The headline claim is user-conditional. — NEW.** No single verdict on "is the served probability trustworthy" is defensible, and the study should not attempt one. The defensible statement is that trustworthiness **depends on the decision being made**: for the median user acting near α ≈ 0.3–0.5 the served probability is fine; for the low-cost-ratio user it is not, and the mechanism is the documented dry bias. This is both more honest and more useful than a verdict, and it makes relative economic value — not Brier — the paper's organising metric.

**D10. Reliability-versus-resolution is the mechanism, not a detail. — NEW.** The Brier decomposition explains the tie: vendor wins reliability, ensemble wins resolution and discrimination, and they cancel. This is the analytically interesting core and should be the paper's central figure, since it says post-processing is doing real work — it is trading sharpness for honesty — which is a defensible, quantified statement about an undisclosed pipeline.

**D11. The binary/amount contrast is a result. — NEW.** The forecast beats climatology decisively on rain occurrence (BSS +0.293) and **fails to beat it on rain amount** (CRPSS −0.076). "Will it rain" is trustworthy; "how much" is not. This is directly decision-relevant, it is measured on identical samples, and it has no counterpart in the prior art reviewed.

---

## Journal Targets

Revised for E4. The findings paper loses its most dramatic possible headline and gains defensibility.

### Nature family

| Venue | Odds | Assessment |
|---|---|---|
| **Nature** | ~1% | Closed. |
| **Nature Communications** | ~10–15% | **Down from 15–20%.** The tie removes the dramatic claim. Would now need global scale plus a strong ML-calibration result to carry it. |
| **Communications Earth & Environment** | **~40%** | **Primary recommendation, unchanged.** Hewson & Pillosu 2021 confirms the venue. The reliability/resolution mechanism (D10) suits it well. |
| **Scientific Data** | **~65%** | **Submit first. Up from 60–65%** — E1/E2 make the dataset materially stronger and its provenance now independently validated. |
| **npj Climate and Atmospheric Science** | ~20% | Secondary. |

### High-impact domain and interdisciplinary

| Venue | Odds | Assessment |
|---|---|---|
| **BAMS** | **~40%** | **Up.** The provenance audit is untouched by E4 — arguably strengthened, since "serves no probability beyond day one, none for ML systems, and a systematically drier number than its own ensemble" is a sharper story than a calibration indictment. |
| **ESSD** | ~45% | Up slightly with E2. |
| **ERL** | ~35% | Unchanged. |
| **Weather, Climate, and Society** | ~45% | Unchanged; E5/E8 are native to this audience. |
| **QJRMS** | ~40% | Unchanged. |

**Sequence:** preprint → dataset descriptor (Scientific Data or ESSD) → findings paper to Communications Earth & Environment. Nature Communications only if Task 30 (ML calibration) proves strong.

---

## Gap Analysis

### Blocking Tier A

**G1. Geographic scope.** Still the single largest gap. 19 capitals in the ensemble track, ~109 cities in the vendor track, against 8,509 qualifying gauged cities (`src/probe_cities.py:8-13`). E4 rests on **15 European capitals** — insufficient for any global claim. Required: ≥2,000 cities, all inhabited continents.

**G2. Probability provenance — largely closed.** GEFS integrated, validated, divergence quantified (E4). Remaining: ECMWF IFS ENS for a second centre, and sustained forward collection.

**G3. Truth uncertainty quantified, not caveated.** Unchanged and now more pressing: E4's 0.0003 Brier gap is far inside any plausible representativeness error, so the tie cannot be claimed as a tie until that error is quantified. Gauge undercatch, representativeness propagated into every interval, IMERG cross-check.

**G4. Explanatory model.** Unchanged.

**G5. Literature positioning — partially closed.** Tasks 3b–3c remain open.

### Blocking Tier B credibility

**G6. Window homogeneity.** Change-point timeline still outstanding.

**G7. Metric breadth — CLOSED.** CRPS, ROC/AUC, sharpness and economic value all implemented and reported (E5, E6).

**G8. Baselines — CLOSED.** Persistence in two forms, flat base rate, and smoothed day-of-year climatology, all on identical days (E6).

**G9. Multiple comparisons.** Open. Now urgent: E4 reports differences in the fourth decimal of Brier across 105 cells.

**G10. Significance testing.** Open and now the **most important single gap for the headline**. E4's verdict rests on differences smaller than the boundary-rule sensitivity; without a paired test with spatial-dependence handling, "tie" is an assertion rather than a result.

### Supporting

**G11. Rain-day convention at scale.** Open; must precede G1 scale-up.

**G12. Observation latency.** `OBS_END = "2026-05-31"`. Note E4's sample ends 2026-05-31 despite GEFS reaching 2026-08-31 — **the truth source, not the ensemble, is now the binding constraint** on closing the balanced window.

**G13. ML systems absent from the registry.** Partially resolved.

**G14. Sampling-design defence.** Open.

**G15. Licence segregation.** Open — CC-BY-SA propagation from UKMO.

**G16. Third-party republication dependency — CLOSED.** E2. Remaining housekeeping: disclose the compression and the negative-value artefact, resolve URLs from STAC at run time (`data.dynamical.org` URLs retire 2026-09-30), archive extracted point data to Zenodo.

**G17. Temporal quantisation — CLOSED.** E3. Report distributionally, not as a mean.

**G18. Competitor-collision monitoring.** Open.

**G19. Non-independent vendor models. — NEW.** `ecmwf_ifs025` and `metno_seamless` are identical at these cities (E9). Required: detect duplicate series systematically across the full city set, and exclude duplicates from any cross-centre or multi-model claim. The triangulation stage already detects this; the league table must inherit the check.

**G20. Sample-size honesty on the headline. — NEW.** E4 is 15 European capitals over 21 months with serially correlated days. Every statement derived from it must carry that scope explicitly, and the claim must be re-tested once G1 is closed. Risk: the tie is a European artefact.

---

## Implementation Plan

### Phase 0 — Decision probes (COMPLETE except literature)

- [x] Task 1. Probe PoP at lead. → **Null.**
- [x] Task 2. Probe ML-model PoP. → **Null for PoP.**
- [x] Task 3. Prior-art first pass. → C4 scooped; framing revised per D7/D8.
- [x] Task 3a. Linsenmeier & Shrader SI and methods. → D7 confirmed safe.
- [x] Task 4. External ensemble archive assessment. → TIGGE struck; `dynamical.org` adopted.
- [x] Task 8. Forward ensemble collection built and started.
- [x] Task 8a. **Daily collection scheduled.** Installed as a **systemd timer**, not cron — no cron daemon runs on this host, so the documented cron line would have silently never fired. `rain-check-ensemble.timer`, daily 05:40 with randomised delay, persistent across downtime; service verified by a manual start.

### Phase 1 — Close the literature question, then harden

- [ ] Task 3b. Run the C1–C6 queries against Google Scholar Labs, Web of Science, ECMWF eLibrary and AMS journals; verify every citation by opening the paper. Rationale: G5. **Owner: user** (requires Scholar account).
- [ ] Task 3c. Grey literature — ForecastWatch, EUMETNET, WMO/WWRP; forward-cite WGNE and the CAWCR/WWRP portal. Rationale: G5.
- [ ] Task 5. Write the literature positioning around the corrected framing. Rationale: G5, D7, D8.
- [ ] Task 6. Automate per-station observation-time detection from GHCN metadata; fail loudly on disagreement. Rationale: G11; must precede Task 10.
- [ ] Task 7. Add validated ML ids to `PROVIDER_MODELS` for the deterministic track. Rationale: G13, D6.
- [ ] Task 9. Record licence provenance per model; segregate CC-BY-SA outputs. Rationale: G15.

### Phase 2 — External ensemble integration

- [x] Task 20a. **Integrate GEFS; member-derived PoP at all leads for the headline window.** → E1.
- [ ] Task 20b. Integrate `ecmwf-ifs-ens-forecast-15-day-0-25-degree`. Rationale: two independent centres — now more important, since E9 shows the vendor track has fewer independent centres than it appears.
- [x] Task 20c. **Validate against raw GRIB2 byte-ranges.** → E2.
- [x] Task 20d. **Local-day accumulation with boundary handling, plus the 3-hourly vendor control.** → E3.
- [x] Task 21. **Publish the vendor-versus-ensemble divergence as a primary result.** → E4, `src/triangulation.py`, stage 14/15.

### Phase 3 — Global scale-up

**Now the critical path.** E4 is a 15-city European result; nothing in the paper generalises until this phase lands.

- [ ] Task 10. Lift the city budget toward the full qualifying pool. Rationale: G1, G20.
- [ ] Task 10a. **Extend GEFS extraction to the full city set. — NEW.** The ensemble track currently covers 19 capitals; the divergence result must span the same cities as the vendor track. Rationale: G1, G20.
- [ ] Task 11. Integrate NOAA ISD globally alongside GHCN-Daily; report coverage per continent and income group. Rationale: G1.
- [ ] Task 12. IMERG cross-check where gauge density is low. Rationale: G3.
- [ ] Task 13. Quantify representativeness error from GEFS neighbourhood fields. Rationale: G3, D8. **Promoted** — E4's verdict is unclaimable without it.
- [ ] Task 14. Gauge undercatch correction; report with and without. Rationale: G3, D8.
- [ ] Task 15. Evaluate `asos-parquet` as the low-latency truth source. Rationale: G12; the truth source now binds the window, not the ensemble.
- [ ] Task 16. Formalise the sampling design; cap-sensitivity. Rationale: G14.

### Phase 4 — Methodological depth

- [ ] Task 17. Balanced headline window with the full record as a sensitivity run; per-season metrics with climatological weighting. Rationale: D1, D2, G6.
- [x] Task 18. **Re-reference BSS to the smoothed day-of-year climatology.** → E7, with smoother sensitivity.
- [ ] Task 19. Model-version change-point timeline; test skill stability. Rationale: G6.
- [x] Task 22. **CRPS, ROC/AUC, sharpness.** → E6.
- [x] Task 23. **Relative economic value curves.** → E5, E8.
- [x] Task 24. **Persistence and smoothed-climatology baselines.** → E6.
- [ ] Task 25. Paired significance testing accounting for spatial and temporal dependence. Rationale: G10. **Promoted to the highest-priority analysis task** — E4's central verdict is unsupported without it.
- [ ] Task 26. FDR control. Rationale: G9.
- [ ] Task 26a. **Systematic duplicate-series detection across all models and cities. — NEW.** Rationale: G19.

### Phase 5 — The headline argument

- [ ] Task 27. Multivariate explanatory model of skill and calibration error. Rationale: G4.
- [ ] Task 28. Test whether *calibration* error varies with income group, gauge density and region. Rationale: D7.
- [ ] Task 29. Express any calibration gap in decision terms via the Task 23 curves. Rationale: D7. **Now partly answered**: E5 puts the price of miscalibration at a median 0.017 at the peak but shows it concentrated at low cost-loss ratios — the equity question becomes whether *that* burden is distributed unequally.
- [ ] Task 30. Physics-versus-ML calibration on the AIFS-ENS matched window. Rationale: D6. **Elevated** — with E4 a tie, this is now the most likely source of a Nature-family-grade result.
- [ ] Task 31. Provenance audit as a standalone section. Rationale: most original contribution; now carries the systematic dry bias (E4) alongside the two nulls.
- [ ] Task 31a. **Report the occurrence-versus-amount contrast as a named result. — NEW.** Rationale: D11.

### Phase 6 — Publication

- [ ] Task 32. Zenodo archive with DOI. Rationale: G16.
- [ ] Task 33. Preprint to timestamp priority.
- [ ] Task 34. Dataset descriptor.
- [ ] Task 35. Findings paper for Communications Earth & Environment.
- [ ] Task 36. Pre-submission review from a verification specialist.
- [ ] Task 37. Re-run prior-art queries before submission. Rationale: G18.

## Verification Criteria

- Coverage spans ≥2,000 cities across all inhabited continents, with per-region coverage tabulated including where verification is impossible.
- Every skill number carries an interval including representativeness uncertainty.
- **Every comparative verdict — especially the E4 tie — rests on a paired significance test with spatial-dependence handling and FDR control.** No verdict may rest on a difference smaller than its own sensitivity to methodological choices.
- The vendor-versus-ensemble divergence is published with the quantisation control reported distributionally.
- Headline results are reported on the balanced window and shown unchanged on the full record.
- Relative economic value curves are published for the primary threshold and all lead times.
- CRPS is reported alongside Brier-family metrics for comparability with WeatherBench 2.
- Null and tie findings are reported as prominently as positive ones.
- Member-derived values are validated against raw GRIB for a documented sample, with the compression artefact disclosed.
- Station truth is defended explicitly against the reanalysis-truth standard, citing WeatherBench 2's own caveat.
- Duplicate vendor series are detected and excluded from cross-centre claims.
- Code and data are DOI-archived; the pipeline reproduces published numbers from a cold start.

## Potential Risks and Mitigations

1. **~~External ensemble archives too costly.~~** Retired.
2. **Forward ensemble collection silently stops.** Mitigated: systemd timer installed and verified (Task 8a). Residual risk — no alarm on repeated failure; `status` must still be checked periodically.
3. **~~PoP unavailable at lead times.~~** Confirmed; resolved by D5 and published as a finding.
4. **~~Vendor-versus-ensemble divergence too small to matter.~~** Partly realised: the *calibration* gap is a tie (E4), though the systematic dry bias and the divergence growth with lead are real. Mitigated by D9/D10/D11 — the paper reports the mechanism rather than an indictment.
5. **The tie is a European artefact.** New and material. 15 capitals, one continent. Mitigation: Tasks 10 and 10a before any general claim; G20 requires scope stated on every derived statement.
6. **Further scooping.** C3 time-sensitive. Mitigation: Tasks 3b, 3c, 37; preprint early; prioritise Task 30.
7. **Scale multiplies a silent convention error.** Mitigation: Task 6 precedes Task 10.
8. **Rate limits throttle global collection.** Mitigation: resumable cache, multi-day scheduling.
9. **`dynamical.org` changes or disappears.** Mitigation: G16 closed on fidelity; STAC-resolved URLs and Zenodo archive still outstanding.
10. **Named providers contest the rankings.** Mitigation: matched windows, threshold sensitivity, held-fixed checks, duplicate detection (G19), provenance caveat in the abstract.
11. **Effort exceeds appetite.** Mitigation: the dataset descriptor is sequenced first and is now achievable from the current state plus Phase 1 — E1 and E2 make it stronger than when v4 was written.
12. **The calibration-equity result is null.** Publishable either way.

## Alternative Approaches

1. **Provenance-audit paper alone**, to BAMS. **Strengthened by v5**: two nulls, a systematic dry bias, divergence growing with lead, and duplicate series served under distinct model names. Unaffected by the inequality scoop. The strongest fallback and arguably now the strongest primary.
2. **Reliability-versus-resolution paper**, to QJRMS or Weather and Forecasting. D10 as the thesis: what vendor post-processing actually does to an ensemble, measured.
3. **Equity-of-calibration framing to Weather, Climate, and Society.**
4. **Institutional partnership with a national met service or academic verification group.** Highest-leverage single move toward a Nature-family outcome.
