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

**E4. The triangulation result — the headline, and it is unresolved rather than a tie.** `src/triangulation.py`, 8,572 city-days, 15 capitals, set-identical sample enforced per (boundary mode, lead) cell. **Qualified by E10 below: the lead-1 calibration comparison is not a demonstrated tie, it is a comparison this sample cannot make. E22 closes the question for good: it is also a comparison this *truth* cannot make — the gap is 0.2× the amount gauge siting alone moves it, and flips sign in 45% of alternative worlds.**

*Divergence*, vendor `gfs_seamless` minus GEFS-derived, pro-rata:

| lead | bias | mean abs | median abs | Pearson r | share >0.2 |
|---|---|---|---|---|---|
| 1 | −0.078 | 0.134 | 0.032 | 0.861 | 24% |
| 3 | −0.068 | 0.178 | 0.097 | 0.786 | 33% |
| 5 | −0.061 | 0.245 | 0.194 | 0.648 | 47% |
| 7 | −0.069 | 0.301 | 0.258 | 0.510 | 60% |

Vendor PoP is systematically **drier** than the raw ensemble supports, by 0.06–0.08 at every lead.

*Calibration at lead 1 — the only same-lead comparison:* Brier **vendor 0.1669 vs GEFS 0.1672**. The gap, 0.0003, is **smaller than the 0.0015 the ensemble's own Brier moves under the day-boundary rule alone**. ~~This is a tie, and is reported as one.~~ **Superseded by E10: it is an *unresolved* comparison, not a demonstrated tie, and is reported that way both in the paper and on the site.** Vendor has better reliability (0.0151 vs 0.0214 — the raw 31-member frequency over-forecasts); GEFS has better discrimination (AUC 0.862 vs 0.846) and resolution. The ensemble's any-step variant beats both (Brier 0.1579, BSS 0.322). Vendor wins in 63% of 105 city × lead cells.

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

**E9. Two vendor models are not independent — now measured properly.** E12 supersedes the aggregate-level detection: the check is hour by hour, every pair, every city.

**E12. Duplicate detection across the full model set. — NEW.** Task 26a, `src/duplicates.py`. 1,453 comparable (city × channel × pair) comparisons on the raw served hourly values. Two duplications, both exact to the last digit:

| pair | channel | scope | evidence |
|---|---|---|---|
| `ecmwf_ifs025` = `metno_seamless` | probability | **all 19 cities** | 20,760 h, 100% exact |
| `ukmo_seamless` = `ukmo_uk_deterministic_2km` | probability **and** amount | 5 of 5 comparable cities | 2,272 h / 20,880 h, 100% exact |

**The duplication is channel-specific, and that is the finding.** MET Norway serves ECMWF's *probability* with its own *amounts* (84% exact, differences to 10.9 mm). A test demanding both channels match would have called the pair distinct and missed the duplication in the one field this study scores. Duplication is a property of the field being read, not of the model id.

**E12a. The threshold is not deciding anything.** Every flagged pair agrees on exactly 100% of hours and **nothing** falls in [0.99, 0.999); the closest unflagged pair is at 0.9837. Any cut inside that empty band gives the same answer. The control is falsifiable and fires on a synthetic pair placed inside the band.

**E12b. Close relatives are kept and disclosed.** `meteofrance_arome_france` vs `_hd` agree on up to 97.6% of amount hours but differ by up to 23 mm; `icon_d2` vs `ukmo_seamless` agree on 98.4% of probability hours at Monaco. These are two forecasts and stay in the table — but a reader counting them as two independent votes is still wrong, so the numbers are published.

**E12c. The league table was double-counting, and its rank intervals were broken.** 16 rows collapsed across 16 cities. Separately, `league.py` was ranking each model's 600 bootstrap replicates against *each other* instead of ranking models within each replicate, so every published row carried an identical, meaningless interval of 16–585 — a range wider than the number of models. Both are fixed; an assertion now makes the rank error fatal rather than printable. Threshold robustness inherits the collapse: the "rankings move" tally falls from 57/85 to 44/69.

**E10. The lead-1 "tie" does not survive testing — and neither does its negation. — NEW, and it changes the paper's central sentence.** `src/significance.py`, Tasks 25 and 26. Paired block bootstrap resampling whole calendar days (all 15 cities together, blocks of 14 days chosen from the measured decorrelation time τ = 1.9 days), 2,000 replicates:

| statistic, vendor − GEFS | estimate | 95% CI | p |
|---|---|---|---|
| Brier | −0.0003 | −0.0063, +0.0049 | 0.91 |
| reliability | −0.0063 | −0.0103, −0.0023 | 0.003 |
| resolution | −0.0045 | −0.0082, −0.0018 | 0.019 |
| AUC | −0.0160 | −0.0207, −0.0095 | 0.001 |
| dry bias | −0.0778 | −0.0871, −0.0698 | 0.001 |
| V at α = 0.05 | −0.542 | −0.733, −0.402 | 0.001 |
| V at α = 0.10 | −0.204 | −0.301, −0.126 | 0.001 |
| V at α = 0.50 | +0.017 | −0.000, +0.037 | 0.13 |

The Brier difference is not significant, **and TOST at the 0.0015 boundary-rule margin does not establish equivalence either (p = 0.34)**. The minimum detectable difference at 80% power is **0.0081 — five times the margin** — so no sample of this size and correlation could ever have demonstrated the tie. "The served probability is as well calibrated as the ensemble" is therefore **not a finding of this study**; what is a finding is that any difference is smaller than 0.006 Brier. The verdict is stable across block lengths 7–28 and both boundary rules.

**E10a. Everything else the paper wants to say IS resolved by the same sample.** The dry bias, the reliability/resolution trade (D10), the discrimination gap and the entire low-α decision-value gap (E4a) all clear significance comfortably on the identical days that cannot separate two Brier scores. The asymmetry is itself the argument for D12: the choice of metric, not the quantity of data, decides whether a reader sees the harm.

**E10b. The dependence handling is not a formality.** Against a synthetic panel with the study's own dependence structure and a known truth, the naive independent-sample interval covers **28%** of the time at a nominal 95% and rejects true nulls **72%** of the time; the day-block bootstrap covers 94% and rejects 3%. On the real data the naive standard error understates the dry-bias error by 1.8×. Any published verdict on these data that does not resample whole days is wrong by about that factor.

**E11. The per-city panel is a direction, not 105 findings. — NEW.** Task 26. Of 105 city × lead cells, 34 reach p < 0.05 uncorrected, **27 survive Benjamini-Hochberg at q = 0.10 and 19 survive Benjamini-Yekutieli** (valid under arbitrary dependence). Every surviving cell is at lead 5–7, i.e. in the region where the comparison is not like-for-like. The "vendor wins 63% of cells" figure in E4 must therefore be quoted as a direction only.

**E13. ML beats physics on daily rain probability — in direction everywhere, in significance nowhere once the family is counted. — NEW.** `src/physics_ml.py`, Task 30. ECMWF's IFS ENS and AIFS ENS, 51 members each, one grid, one initialisation, one republisher, so the forecast *method* is very nearly the only difference. 4,655 city-days × 7 leads over 15 capitals, 2025-07-09 .. 2026-05-31:

| lead | IFS Brier | AIFS Brier | diff (ML − physics) | 95% CI | p | q(BH) |
|---|---|---|---|---|---|---|
| 1 | 0.1783 | 0.1715 | −0.0068 | −0.0115, −0.0007 | 0.009 | 0.063 |
| 4 | 0.1823 | 0.1775 | −0.0048 | −0.0091, −0.0010 | 0.032 | 0.112 |
| 2,3,5,6,7 | — | — | −0.0016 to −0.0033 | straddle 0 | 0.14–0.54 | ≥0.34 |

All seven leads favour the ML system and AIFS wins on BSS and AUC at every lead, but **no lead survives Benjamini-Hochberg across the seven**, so the claim the data support is a consistent direction, not an established per-lead difference. The mechanism at lead 1 is *reliability* (−0.0055, p = 0.002) and a wetter mean probability (+0.0099 relative to the physics system's dry side, p = 0.003); resolution (+0.0007, p = 0.89) and discrimination (AUC +0.0044, p = 0.11) are flat. **AIFS is better calibrated, not more discriminating** — the opposite shape to the vendor-versus-ensemble result in E10, where post-processing bought reliability by giving up resolution.

**E13a. The accumulation ladder is worth more than half the effect, and nobody reports it. — NEW, and it is a methodological finding in its own right.** IFS ENS publishes 3-hourly accumulation steps and AIFS ENS 6-hourly ones. Scored each on its own native ladder, the ML advantage at lead 1 is **−0.0027 (p = 0.19)**; put both on one 6-hourly ladder, it is **−0.0068 (p = 0.009)**. The finer ladder hands the physics system a more accurate local-day boundary that has nothing whatever to do with forecast quality, and it is worth **0.0041 Brier — 60% of the measured difference**. Any physics-versus-ML comparison on local calendar days that does not match the ladder has inherited that artefact silently.

**E13b. The window is bounded by the gauges, not the archive.** Both member archives are collected to 2026-08-31, but the station record ends 2026-05-31, so the comparison runs 11 months rather than 14. The minimum detectable difference at lead 1 is 0.0079 — the observed effect sits just under it, which is why the direction is consistent and the individual verdicts are not. Per city, 11 of 15 capitals favour AIFS (median −0.0053, range −0.0417 at Dublin to +0.0535 at Monaco), so the effect is not carried by one station.

**E14. The headline window was not a fair year, and fixing it is nearly free. — NEW.** `src/window.py`, Task 17. The record runs 2024-04-26 to 2026-05-31, so pooling its days weights the seasons by how many of each happened to land in it. Imbalance — the share of days that would have to move months to make the sample a calendar year — is **0.050 on the pinned panel and 0.042 on the world panel**; trimming to whole years (**2024-06-01 .. 2026-05-31**) takes it to 0.037 and **0.009**, and costs only **5% of the days**. Of 6 panel × model series, **1 moves its BSS significantly** when the window is balanced (`served_world`/`best_match`, +0.0018, p = 0.013); `gfs_seamless` moves +0.0066 at p = 0.09. The balanced numbers are the ones to quote and the full record stays on disk as the sensitivity run.

**E14a. Trimming the calendar is necessary and not sufficient.** What survives trimming is gauge outage, not calendar shape, and it is not uniform across seasons. Re-weighting each series' seasonal Brier by the calendar year rather than by its own surviving day counts moves the median BSS by only +0.0003 — but **16 of 154 series move by more than 0.01 BSS** inside the already-balanced window. A checked artefact: on a synthetic series where the true annual Brier is 0.200, pooling the sample's days returns 0.157, a **+0.043** error recovered exactly by the weighting.

**E14b. Skill is constant for every pinned provider — and the one shift found is in the served series, not in a model. — NEW.** Task 19. Binary segmentation on daily pooled skill anomalies, with the p-value taken from the tail of the *maximum* statistic over all candidate dates under a null carrying the series' own annual cycle and memory, minimum segment 90 days, BH across the family. **Zero change points** in `ecmwf_ifs025`, `gfs_seamless`, `icon_d2`, `icon_eu`; no HAC trend significant among them (largest −0.0084/yr, p = 0.24). One shift survives: `served_world`/`best_match` at **2024-08-27, +0.0247 adjusted (p = 0.012, q = 0.012)**, against a naive raw jump of +0.0169 — the difference between the two is season, not system. The panel's city set is identical either side (105 cities both ways), so it is not a composition artefact. **It falls inside the balanced window, owning 12% of it**: skill is 0.0435 before and 0.0773 after, so the quoted balanced mean for that series is a blend of two regimes. A shift in the served series with none in any pinned provider points at the collection pipeline or the truth source rather than at a model upgrade.

**E14c. The change-point null is deliberately over-conservative, and the cost is measured.** On 60 stationary seasonal series the test fires **0%** of the time at a nominal 5% while still detecting a planted 0.06 step on **100%** of cases, and locating it to within 0 days. The obvious alternatives fail: drop the month effects and it fires **95%**; assume independent days and it fires 8%; resample only the residual around a fitted annual cycle — better-powered and the more natural choice — and it fires **15%**. An over-conservative change-point test reports fewer things; an over-confident one reports wrong ones.

**E20. The truth has a noise floor, and it is large. — NEW.** `src/truth_uncertainty.py`, Task 13 (G3). 192,934 GHCN gauge pairs within 60 km of the 175 study cities, ≥200 shared days each, scored at the study's own 0.2 mm threshold. Disagreement about whether it rained rises smoothly with separation — 0.083 under 1 km, 0.093 at 3–5 km, 0.112 at 10–15 km, 0.142 at 45–60 km — and the fit q(d) = 0.0925 + 0.0747(1 − e^(−d/49 km)) has R² = 0.986. **The intercept is the finding.** At zero separation nothing spatial is left, so the residual 0.083 is not representativeness error: it is bucket against bucket, reader against reader, hour against hour. No forecast can score past it. At the study's typical gauge-to-grid distance of 3.3 km the total is q = 0.093 (asymmetric: 0.151 wet-here-dry-there, 0.068 the mirror). Intervals resample **gauges**, not pairs, because one bad gauge enters dozens of pairs — a checked distinction: pair resampling covers 18% at a nominal 95%, gauge resampling 98%.

**E21. Truth noise does not cancel out of a paired difference — but the textbook correction is wrong anyway. — NEW, and it is the methodological result.** The label-noise identity leaves a residue 2q₁₀E[(f₁−f₂)y] − 2q₀₁E[(f₁−f₂)(1−y)], which vanishes only for rivals differing equally on wet and dry days. A *drier* forecast does not, so for E4 the residue is **−0.0055, eighteen times the difference being measured**. Applying it would have been a disaster: against real neighbouring gauges the same shift is **+0.0003**, agreeing on the spread (0.0017 vs 0.0016) and disagreeing on the bias by a factor of dozens. Independent flips turn a confidently forecast wet day dry as readily as a marginal one; a real gauge 5 km away saw the same weather system and agrees with the forecast on exactly the days the forecast was right about. **The published correction for label noise is inapplicable to spatially correlated truth, and this study can show it rather than assert it.**

**E22. Re-siting the gauge kills E4 and leaves E4a standing. — NEW, and it settles the headline.** The experiment holds every forecast and every day fixed and swaps in a real alternative bucket near each city (8 of 15 cities have one within 25 km; a draw swaps 5.8 on average):

| statistic | study value | siting sd | 95% range | sign flips | ratio |
|---|---|---|---|---|---|
| E4 Brier difference | −0.0003 | 0.0017 | −0.0029, +0.0038 | **45%** | **0.2** |
| vendor bias | −0.0031 | 0.0057 | −0.0191, +0.0027 | 15% | 0.5 |
| ensemble dry bias | +0.0747 | 0.0057 | +0.0587, +0.0804 | 0% | 13.2 |
| E4a value at α = 0.05 | −0.5422 | 0.0422 | −0.6479, −0.4876 | **0%** | **12.8** |
| E4a value at α = 0.10 | −0.2043 | 0.0185 | −0.2594, −0.1816 | 0% | 11.0 |

The Brier comparison is *smaller than the amount gauge siting alone moves it* and changes sign in nearly half of all alternative worlds — E10 said the sample could not resolve it, and E22 says the truth cannot either. The low-cost-loss decision gap is 13× its siting spread and never flips. **Same days, same gauges, same forecasts: the choice of metric decides whether truth error matters.** This is the strongest form of D12 yet available. Caveat: the 7 cities with no alternative gauge contribute no spread, and gauges failing the day-convention scan are dropped, so the quoted spread is a **lower bound**.

**E23. Task 14 returns a negative result, and the negative result is the honest one.** The two halves of pair disagreement — wet here / dry there against its mirror — stay within 0.0087 of each other at every separation, so there is no directional catch difference to find *between neighbouring gauges*. What a pair method cannot see is the part every bucket gets wrong together: wind loss, wetting and evaporation push all gauges the same way and are invisible to it by construction. That residue is **not corrected and not bounded here**, and the paper must say so rather than import a literature factor it has not measured. Separately, changing gauge moves a city's measured rain-day frequency by a median 0.023 and up to 0.184 — climatology is the reference every skill score is quoted against, which is why E22 re-scores rather than merely relabelling.

---

## Design Decisions

D1–D8 carry over from v4 unchanged. Four additions:

**D9. The tie is published as the result — but the tie is in the average, not in the decision. — REVISED BY E10.** At the only clean same-lead comparison the served probability is as well calibrated as the raw ensemble frequency (E4), and the study does **not** reframe to preserve the original hypothesis. **E10 sharpens this further and in the study's disfavour: the lead-1 comparison is not a demonstrated tie but an unresolved one, and the paper must say so.** The honest finding is: *the served number is drier than the ensemble supports and carries no lead axis at all; whether it is less trustworthy at day one by average score is a question this sample cannot answer, and the answer would have to be larger than 0.008 Brier for it to have been able to.* **E4a qualifies this decisively:** the average-score comparison is unresolved, but the decision-value gap at low cost-loss ratios is not — it is significant at p = 0.001. The paper's claim is therefore *conditional on the user*, not global.

**D12. The headline claim is user-conditional. — NEW.** No single verdict on "is the served probability trustworthy" is defensible, and the study should not attempt one. The defensible statement is that trustworthiness **depends on the decision being made**: for the median user acting near α ≈ 0.3–0.5 the served probability is fine; for the low-cost-ratio user it is not, and the mechanism is the documented dry bias. This is both more honest and more useful than a verdict, and it makes relative economic value — not Brier — the paper's organising metric.

**D10. Reliability-versus-resolution is the mechanism, not a detail. — NEW, and now tested.** The Brier decomposition explains why the two scores land together: vendor wins reliability (−0.0063, p = 0.003), ensemble wins resolution (−0.0045, p = 0.019) and discrimination (AUC −0.0160, p = 0.001), and they cancel. **Both legs of the trade are individually significant under E10's test, so the mechanism is measured rather than inferred from a cancellation.** This is the analytically interesting core and should be the paper's central figure, since it says post-processing is doing real work — it is trading sharpness for honesty — which is a defensible, quantified statement about an undisclosed pipeline.

**D11. The binary/amount contrast is a result. — NEW.** The forecast beats climatology decisively on rain occurrence (BSS +0.293) and **fails to beat it on rain amount** (CRPSS −0.076). "Will it rain" is trustworthy; "how much" is not. This is directly decision-relevant, it is measured on identical samples, and it has no counterpart in the prior art reviewed.

**D13. The ladder is part of the comparison, not part of the plumbing. — NEW.** E13a shows a step-resolution mismatch is worth 60% of the physics-versus-ML difference at lead 1. Any comparison this study publishes between archives with different accumulation ladders must therefore report the matched result as primary and the native one as a sensitivity, with both numbers printed. This applies beyond Task 30: it is the same class of confound as the day-boundary rule (G17), and it argues the paper should carry a short "artefacts that outweigh the signal" section rather than a methods footnote.

---

## Journal Targets

Revised for E4. The findings paper loses its most dramatic possible headline and gains defensibility.

### Nature family

| Venue | Odds | Assessment |
|---|---|---|
| **Nature** | ~1% | Closed. |
| **Nature Communications** | ~10–15% | **Down from 15–20%.** The tie removes the dramatic claim. Would now need global scale plus a strong ML-calibration result to carry it. E13 is not that result: the direction is consistent but no lead survives FDR, and the honest headline is the ladder artefact (E13a), which is a methods contribution rather than a Nature-family one. |
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

**G3. Truth uncertainty — closed for the gauge-pair part** (`src/truth_uncertainty.py`, E20–E23). Measured rather than caveated: 193k GHCN pairs give disagreement against separation, an irreducible floor of 0.083 at zero separation, and a re-siting experiment that answers the question the gap was asking. The answer is that E4's Brier gap **does not survive** (0.18× its siting spread, sign flips in 45% of alternative worlds) while E4a's decision gap does (13×, no flips). Remaining: the common-mode part no pair test can see (wind loss, wetting, evaporation — E23), and the IMERG cross-check (Task 12) for low-density regions.

**G4. Explanatory model.** Unchanged.

**G5. Literature positioning — partially closed.** Tasks 3b–3c remain open.

### Blocking Tier B credibility

**G6. Window homogeneity — CLOSED.** E14, E14a, E14b. Balanced window chosen and costed, seasons re-weighted climatologically, and the change-point search run with the search itself paid for in the p-value.

**G7. Metric breadth — CLOSED.** CRPS, ROC/AUC, sharpness and economic value all implemented and reported (E5, E6).

**G8. Baselines — CLOSED.** Persistence in two forms, flat base rate, and smoothed day-of-year climatology, all on identical days (E6).

**G9. Multiple comparisons — CLOSED.** E11. BH and BY applied to the 105-cell panel; 27 and 19 cells survive respectively, all at leads 5–7.

**G10. Significance testing — CLOSED, with a consequence.** E10. The headline verdict is now tested and the answer is that the sample cannot resolve it: the minimum detectable Brier difference, 0.0081, is five times the margin at which a difference would matter. This does not reopen the gap — it converts it into G20 and G1, which is where it belongs. **The test that matters for the paper's actual claim, the low-α decision-value gap, is comfortably significant.**

### Supporting

**G11. Rain-day convention at scale.** Open; must precede G1 scale-up.

**G12. Observation latency.** `OBS_END = "2026-05-31"`. Note E4's sample ends 2026-05-31 despite GEFS reaching 2026-08-31 — **the truth source, not the ensemble, is now the binding constraint** on closing the balanced window.

**G13. ML systems absent from the registry.** Partially resolved.

**G14. Sampling-design defence.** Open.

**G15. Licence segregation.** Open — CC-BY-SA propagation from UKMO.

**G16. Third-party republication dependency — CLOSED.** E2. Remaining housekeeping: disclose the compression and the negative-value artefact, resolve URLs from STAC at run time (`data.dynamical.org` URLs retire 2026-09-30), archive extracted point data to Zenodo.

**G17. Temporal quantisation — CLOSED.** E3. Report distributionally, not as a mean.

**G18. Competitor-collision monitoring.** Open.

**G19. Non-independent vendor models — CLOSED.** E12. `src/duplicates.py` tests every pair at every city hour by hour on the raw served values; the league table and the threshold-robustness table both consume its verdict and collapse duplicates before ranking, with the alias named on the surviving row rather than deleted. The triangulation stage's aggregate-level check is retained as a smoke alarm and now says so.

**G20. Sample-size honesty on the headline. — NEW, and now quantified.** E4 is 15 European capitals over 21 months with serially correlated days. E10 puts a number on what that buys: differences below 0.008 Brier are invisible to it. Every statement derived from it must carry that scope explicitly, and the claim must be re-tested once G1 is closed. Risk: the tie is a European artefact — and the study cannot presently tell a European artefact from an absence of effect.

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
- [x] Task 13. Quantify representativeness error — **done** (`src/truth_uncertainty.py`), from GHCN gauge pairs rather than GEFS neighbourhood fields: the pairs measure the truth's own noise, which a model field cannot. E20–E22.
- [x] Task 14. Gauge undercatch — **done, with a negative result** (E23). A pair method is blind to common-mode catch loss by construction; the directional asymmetry it *can* see is under 0.009 at every separation. Reported as a bound on what the method sees, not as a correction.
- [ ] Task 15. Evaluate `asos-parquet` as the low-latency truth source. Rationale: G12; the truth source now binds the window, not the ensemble.
- [ ] Task 16. Formalise the sampling design; cap-sensitivity. Rationale: G14.

### Phase 4 — Methodological depth

- [x] Task 17. Balanced headline window with the full record as a sensitivity run; per-season metrics with climatological weighting. Rationale: D1, D2, G6. → `src/window.py`, E14/E14a.
- [x] Task 18. **Re-reference BSS to the smoothed day-of-year climatology.** → E7, with smoother sensitivity.
- [x] Task 19. Model-version change-point timeline; test skill stability. Rationale: G6. → `src/window.py`, E14b.
- [x] Task 22. **CRPS, ROC/AUC, sharpness.** → E6.
- [x] Task 23. **Relative economic value curves.** → E5, E8.
- [x] Task 24. **Persistence and smoothed-climatology baselines.** → E6.
- [x] Task 25. **Paired significance testing accounting for spatial and temporal dependence.** → E10, E10b. `src/significance.py`, day-block bootstrap with a measured block length, TOST equivalence, and closed-form cluster/Newey-West variances beside it.
- [x] Task 26. **FDR control.** → E11. Benjamini-Hochberg and Benjamini-Yekutieli across the 105-cell panel.
- [x] Task 26a. **Systematic duplicate-series detection across all models and cities.** → E12, E12a-c. `src/duplicates.py`; consumed by `league.py` and `league_robustness.py`.

### Phase 5 — The headline argument

- [ ] Task 27. Multivariate explanatory model of skill and calibration error. Rationale: G4.
- [ ] Task 28. Test whether *calibration* error varies with income group, gauge density and region. Rationale: D7.
- [ ] Task 29. Express any calibration gap in decision terms via the Task 23 curves. Rationale: D7. **Now partly answered**: E5 puts the price of miscalibration at a median 0.017 at the peak but shows it concentrated at low cost-loss ratios — the equity question becomes whether *that* burden is distributed unequally.
- [x] Task 30. **Physics-versus-ML calibration on the AIFS-ENS matched window.** → E13, E13a, E13b. `src/physics_ml.py`, pipeline stage 16. Both ECMWF ensembles collected from dynamical.org (`src/collect_members.py`, `src/ens_archive.py`, now source-agnostic), accumulated onto one matched 6 h ladder by `ensemble_pop.py --step-hours=native,6`, scored with the same day-block bootstrap as Task 25. The result is *direction without per-lead significance*, and the ladder sensitivity (E13a) is arguably the more publishable half.
- [ ] Task 31. Provenance audit as a standalone section. Rationale: most original contribution; now carries the systematic dry bias (E4) alongside the two nulls.
- [x] Task 31a. **Report the occurrence-versus-amount contrast as a named result.** → D11, surfaced in `src/report.py` `sec_served`. Published as two samples side by side rather than pooled — 65 series over 15 capitals (+0.293 / −0.075) and 104 single-provider world cities (+0.339 / −0.026) — so the contrast has to hold in both, and it does.
- [x] Task 31b. **Surface the triangulation and decision-value results on the site. — NEW.** → E4, E4a, E10, D11. `src/report.py` `sec_served`, reached from the top nav and rendered in the cross-city pane: the divergence-by-lead table, the lead-1 verdict stated as *unresolved* with its interval and minimum detectable difference, the cost-loss table where the served probability is worse than useless at α = 0.05, and the occurrence-versus-amount split. Until now every one of these lived only in parquet. The site says "we cannot tell" where the plan says unresolved; nothing on the page claims the tie.

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
- **Every comparative verdict — especially the E4 comparison — rests on a paired significance test with spatial-dependence handling and FDR control.** Satisfied by `src/significance.py`. No verdict may rest on a difference smaller than its own sensitivity to methodological choices, and no null may be reported as a tie without an equivalence test that could have failed.
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
