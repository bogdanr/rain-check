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

**E4. The triangulation result — the headline, now measured on 105 cities. — REVISED (Task 10a).** `src/triangulation.py`, **60,891 city-days, 105 cities in 25 countries**, set-identical sample enforced per (boundary mode, lead) cell. The earlier scope was 8,572 city-days over 15 capitals; those numbers are superseded throughout. **Qualified by E10 below: at this scope the lead-1 calibration comparison resolves, but its sign depends on the event definition. E22's warning still applies to any single-definition reading of it — the gap is small relative to what gauge siting alone moves.**

*Divergence*, vendor `gfs_seamless` minus GEFS-derived, pro-rata:

| lead | bias | mean abs | median abs | Pearson r | share >0.2 |
|---|---|---|---|---|---|
| 1 | −0.095 | 0.143 | 0.032 | 0.841 | 26% |
| 3 | −0.088 | 0.179 | 0.097 | 0.777 | 33% |
| 5 | −0.089 | 0.236 | 0.164 | 0.661 | 45% |
| 7 | −0.096 | 0.284 | 0.241 | 0.552 | 56% |

Vendor PoP is systematically **drier** than the raw ensemble supports, by 0.09–0.10 at every lead — a larger gap than the 0.06–0.08 seen at the capitals, and now resolved at p = 0.001.

*Calibration at lead 1 — the only same-lead comparison:* Brier **vendor 0.1546 vs GEFS 0.1607**, a gap of 0.0061 that E10 resolves at p = 0.001. Vendor has better reliability (0.0136 vs 0.0229 — the raw 31-member frequency over-forecasts); GEFS has better discrimination (AUC 0.8719 vs 0.8599) and marginally better resolution. **The ensemble's any-step variant beats both (Brier 0.1470, BSS 0.332), and scoring the vendor against that event reverses the verdict — see E10.** Vendor wins in 76% of 735 city × lead cells.

**E4a. The average score conceals a large decision-value gap at low cost-loss ratios. — IMPORTANT; REVISED at 105 cities.** At lead 1, on the identical paired sample, relative economic value:

| α | vendor | GEFS-derived |
|---|---|---|
| 0.05 | **−0.464** | **−0.018** |
| 0.10 | 0.060 | 0.239 |
| 0.20 | 0.370 | 0.446 |
| 0.30 | 0.515 | 0.545 |
| 0.50 | 0.459 | 0.433 |

For the **low-cost-ratio user — who acts cheaply and often to protect against a rare expensive loss — the served probability is worse than useless at α = 0.05 (−0.46), where the member-derived one is roughly break-even.** The vendor's dry bias (E4) is exactly what destroys value at low α: a systematically too-low probability fails to trigger action at low thresholds. The gap is −0.446 at α = 0.05 and −0.178 at α = 0.10, both at p = 0.001 (E10). At the capitals scope the corresponding figures were −0.748 vs −0.206 and −0.080 vs +0.124; the wider panel softens the levels and keeps the ordering and the significance. The honest verdict is *ahead on average accuracy under one event definition, behind under the other, and materially worse for an identifiable and decision-relevant class of user under both.* This is **invisible to Brier**, which is itself an argument for reporting economic value, per D7.

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

**E10. At 105 cities the lead-1 comparison resolves — and the verdict reverses with the definition of a rainy day. — REVISED at the wider panel (Task 10a); supersedes the 15-capital numbers below.** `src/significance.py`, Tasks 25 and 26. Paired block bootstrap resampling whole calendar days (all 105 cities together, blocks of 14 from the measured τ = 2.4 days), 2,000 replicates, 60,891 paired city-days over 631 days:

| statistic, vendor − GEFS | estimate | 95% CI | p |
|---|---|---|---|
| Brier, day-total event | **−0.0061** | −0.0093, −0.0030 | **0.001** |
| Brier, **any-step** event | **+0.0075** | +0.0053, +0.0097 | **0.001** |
| reliability | −0.0094 | −0.0126, −0.0063 | 0.001 |
| resolution | −0.0021 | −0.0055, −0.0003 | 0.32 |
| AUC | −0.0120 | −0.0149, −0.0091 | 0.001 |
| dry bias | −0.0951 | −0.1017, −0.0901 | 0.001 |
| V at α = 0.05 | −0.446 | −0.529, −0.384 | 0.001 |
| V at α = 0.10 | −0.178 | −0.218, −0.146 | 0.001 |
| V at α = 0.50 | +0.026 | +0.014, +0.038 | 0.002 |

Widening the panel from 15 capitals to 105 cities in 25 countries turns the unresolved comparison into a resolved one, exactly as E28's design effect predicts: the gain came from *countries*, not from days. But the win is definition-dependent and that is the stronger result. Against the day-total event the vendor is better by 0.0061; against the any-step event — the one the vendor's max-over-hours actually answers — the **ensemble** is better by 0.0075. **Both directions are firmly resolved (p = 0.001 each), so this is not ambiguity from thin data: which series is "better calibrated" is decided by an undisclosed definitional choice.** At 15 capitals the earlier reading was −0.0003 (CI −0.0063, +0.0049, p = 0.91) with TOST also failing at the 0.0015 margin — that null was a power limit, and it is superseded rather than contradicted.

**E10a. The metric still decides the verdict — now by sign rather than by resolvability.** At the 15-capital scope the argument for D12 was an asymmetry in power: the decision-value gap resolved on the same days that could not separate two Brier scores. At 105 cities both resolve, and they **disagree in sign** — Brier prefers the vendor by 0.0061 while α = 0.05 economic value prefers the ensemble by 0.446, on identical days. A reader told "better calibrated" and a reader who acts cheaply and often are given opposite advice from one sample. That is D12 in its strongest available form.

**E10b. The dependence handling is not a formality.** Against a synthetic panel with the study's own dependence structure and a known truth, the naive independent-sample interval covers **28%** of the time at a nominal 95% and rejects true nulls **72%** of the time; the day-block bootstrap covers 94% and rejects 3%. On the real data the naive standard error understates the dry-bias error by 1.8×. Any published verdict on these data that does not resample whole days is wrong by about that factor.

**E11. The per-city panel is a direction, not 735 findings. — REVISED at the wider panel.** Task 26. Of **735** city × lead cells, 365 reach p < 0.05 uncorrected, **364 survive Benjamini-Hochberg at q = 0.10 and 235 survive Benjamini-Yekutieli** (valid under arbitrary dependence). The vendor has the lower Brier in 557 of 735 cells (76%), but the surviving cells concentrate at the longer leads where the comparison is not like-for-like, so the cell-win share is a direction only.

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

**E24. The rain-day convention turns over in the late afternoon, not at noon — and that is a measurement of the diurnal cycle. — NEW.** `src/obs_time.py`, Task 6 (G11). A gauge read at hour H reports the 24 h ending at H, so by the clock the majority of it falls on the previous day only when H < 12. Solving 48,248 neighbour-pair lag measurements over 86,235 pairs within 25 km, anchored on the 75 gauges reporting a midnight reading, the measured shift against observation hour is:

| hour | stations | shift = 1 | decisive |
|---|---|---|---|
| 04–09 | 1,611 | 0.99 | yes |
| 09–15 | 25 | 1.00 | yes |
| **15–21** | **45** | **0.59 / 0.50** | **no** |
| 21–24 | 6 | 0.00 | yes |

The changeover sits between **15:00 and 21:00**. Rain does not arrive evenly over the day: the afternoon and evening convective peak lands in the *previous* day's window and outweighs the extra morning hours. Assuming noon would mis-date every gauge read between 12:00 and 15:00. Between 15:00 and 21:00 two conventions genuinely coexist and the metadata cannot separate them — reported as an undecided band rather than a rule awaiting more data. Mean graph residual 0.015 days, so the redundant pairwise measurements are consistent with each other and not merely with themselves.

**E25. GHCN metadata can date 42% of gauges with no network call, and agrees with the independent measurement 99.1% of the time where it matters. — NEW, and it is the gate Task 10 was waiting on.** 3,670 of 6,192 pool gauges carry an observation hour; 318 change their modal hour year to year and 985 disagree with their own mode on >10% of records, leaving 2,683 stable. Where metadata and neighbour measurement both have an answer they agree on **1,704 of 1,718 stations (99.2%)** — and **99.1% over the 1,636 where the rule predicts a non-zero shift**, which is the number that counts, since a rule is not useful for being right about the easy cases. The 14 disagreements are named in `obs_time_audit.parquet` and **excluded rather than resolved by preferring one route**. `capitals.scan_offset` therefore has a scalable replacement; the residual gate on G1 is gauge density within 25 km, which is thinnest exactly where the study most wants to go.

**E26. A one-day dating error costs 0.09 Brier — two orders of magnitude above anything this study reports.** Re-scoring the E4 panel against truth shifted by ±1 day: vendor Brier 0.1669 → 0.2381 (+1 day) or 0.2667 (−1 day); ensemble 0.1672 → 0.2392 / 0.2607. Against this, E4's unresolvable difference is 0.0003 and E13's resolved physics-versus-ML difference is 0.0068. **A wrong observation hour does not add noise to a league table, it reorders it** — which is why risk 7 made Task 6 a precondition for Task 10 and why the audit fails loudly rather than defaulting.

**E27. The 2,000-city target is not reachable from GHCN-Daily, and the shortfall is the deliverable. — NEW; this is the answer to G1, and it is "no".** At a 100,000 population floor the qualifying pool holds 1,061 cities: Europe 423, North America 409, Asia 186, Oceania 26, Central America & Caribbean 13, **Africa 1, South America 0**. Dropping the floor does not fix this — it buys American and German small towns, because the floor is the only lever and it pulls towards wherever gauges are dense. Against world *urban population*, Europe is over-represented by a ratio of 6.6 and Asia and Africa are the two that are missing. A designed 2,000-city sample allocated by the square root of urban population can supply **198 of 2,001**; the shortfall of 1,803 is almost entirely Africa, South America and Asia. That number is the size of the hole Tasks 11 (NOAA ISD) and 12 (IMERG) exist to fill, measured rather than asserted. `src/sampling.py`, `sampling_pool_census.parquet`, `sampling_design.parquet`.

*Update 2026-09-23:* E27 describes GHCN-Daily alone. With GHCNh as a second full-gate source (83 cities in 50 countries), world capitals exempt from the population floor, and a country counted as covered only when a record can meet the gate, the registry is 222 cities in 88 countries. India, Indonesia, Nigeria, South Africa, Egypt, Kenya, Ethiopia, Argentina and Peru remain out; the step that stops each is in `data/processed/country_coverage.parquet`. See `plans/2026-09-23-world-capitals-and-coverage-expansion-v1.md`.

**E28. Cities inside a country are near-interchangeable: the design effect is 4.3 against a mean country size of 3.8.** Resampling cities one at a time gives sd ∝ n^−0.50 (R² 0.999) — exactly the independent rate, because that draw *cannot see* that two cities are in the same country. Drawing whole countries instead gives **4.3× the variance at a matched 109 cities** (permutation p = 0.005; null median 0.92, 95% of shuffles 0.48–1.67). Since the mean country in the panel holds 3.8 cities, a design effect of 4.3 means the eighth German city buys close to nothing the first did not. The null is obtained by shuffling country labels across cities rather than assumed, because grouping cities at all shifts the variance (raw ratio 0.84 on a panel with no dependence) — dividing by a single null draw would report grouping noise as a finding.

Consequence for the required n: reaching the 0.0015 margin the day-boundary rule already moves things by needs **68,308 cities** drawn this way (naive arithmetic says 15,962) — more than the pool holds at any floor, so it is not a budget question. Reaching 0.0081, what the paired test achieves today, needs 2,300 cities or **612 countries**, and there are 195. **G20 is therefore closed in the negative: 2,000 cities is not a precision argument and the plan must stop justifying it as one.** It is a coverage argument, and the cheap axis is *countries*, not cities.

**E29. The country cap changes who is in the sample far more than what the sample says. — G14 closed, reassuringly.** Uncapped, one country supplies 34% of the qualifying pool and three supply 59%; at the cap of 8 the study uses, 4% and 12%, at a cost of 863 cities. Re-weighting the panel that exists to each cap's composition moves pooled BSS by at most **0.0118**, and by ≤0.0005 for every cap ≥ 2. The cap is a rule the paper must *describe*, not one it must defend. Caveat, stated in the stage: this re-weighting cannot see a country the panel has none of — it bounds the cap's effect *within* the sampled world, not the effect of the sampled world, which is E27's business.

**E30. NOAA ISD has a station almost everywhere GHCN does not, and most of those stations do not report rain. — Task 11; G1 partly answered.** (`src/isd_coverage.py`.) Two questions, asked in the order that matters, because the first one flatters ISD and is not the question.

*Stations exist.* Against every GeoNames city of 100,000+ — not the study's pool, which is already filtered to cities holding a GHCN gauge — a station sits within 25 km of 4,337 of 6,276 cities (69%) versus GHCN's 1,074 (17%). **Africa goes from 1 city to 495 and South America from 0 to 493.** On existence alone ISD closes G1 outright.

*They do not all report.* Reading one complete calendar year (2024) for 156 stations, 20 per continent at the largest cities — deliberately the most generous sample, since a provincial town does not report better than a capital airport — the share of local days whose daily total can actually be **reconstructed** has a median of 22% and reaches 80% for only 31% of stations. It splits hard by continent: North America 94% median, Asia 90%, Oceania 67%, against **Europe 28%, Central America 20%, Africa 8%, South America 3%**. Discounting the census by each continent's usable rate gives an upper bound of ~1,860 effective cities, of which Asia supplies 1,071 and Africa and South America 50 and 49.

*In the currency E28 established, the gain is real but smaller than it looks.* Counting countries rather than cities, ISD reaches 168 of the 174 with a city this size against GHCN's 45. That is the upper bound; discounted by the probe's rate (25 of 63 visited countries had a station reporting on at least half the days) it is about **67 countries, still 1.5× GHCN's 45** — and generous, because the probe took the largest cities. So ISD is worth integrating, and for Asia it is transformative, but it does not deliver a globally even panel: **Africa and South America remain unreached by any gauge network in this tree, which is now squarely Task 12's problem.**

Two traps, both of which produced a plausible wrong answer first. Requiring a station's last observation to carry the history file's exact horizon date left 152 of 28,095 stations, which is a measurement of NCEI's ingest lag and not of the network; a 30-day grace gives 12,069, and the count is flat from a fortnight to six months. And probing 2025, whose archive stops in late August, divided every station by 365 days — a perfectly reporting station scored 66% and the "above 80%" column read zero everywhere, which looked exactly like the finding. Both are now checks that abort the stage, the second by requiring a synthetic station that reports every day of the probe year to score 100%.

This does not touch G20. The headline still needs ~612 countries to resolve and the planet has 195: ISD widens the study's **reach**, not its resolution.

**E31. The forecast is not measurably worse where people are poorer, and the reason it cannot be measured is the same inequality the question is about. — Tasks 28 and 29; D7 answered in the negative.** (`src/equity.py`.)

*Who can be asked.* The world panel holds 102 cities in 25 countries: 92 cities in 21 high-income countries, 9 in 3 upper-middle, 1 in 1 lower-middle, and **none in any low-income country**. That is not a sampling choice — E27 showed the GHCN pool has one African city and no South American ones — so the panel is as poor as the gauge network permits.

*The raw answer is a climate measurement.* It rains on 15% of days in the panel's poorer cities and 40% in its richer ones, and a Brier score is bounded by its base rate, so the naive comparison reports the forecast as **better** where people are poorer: +0.081 on Brier, +0.99 on value at α = 0.05. The obvious repair fails too, and this is worth recording: restricting both sides to their shared 0.08–0.30 base-rate band still leaves the poorer cities at 15% against 24%, and **68% of the raw Brier gap survives it**. Only per-city caliper matching — each poorer city against richer cities within 0.02 of its own rain frequency — closes it, at which point the Brier gap is **+0.0022, 3% of the raw figure, p = 0.67**. Nothing reaches significance on any of the six metrics. What looked like a large effect was entirely how dry Morocco is.

*What is left is also undetectable.* Reliability's matched gap is +0.0033 with an interval of [−0.005, +0.043]; intervals resample **countries**, because E28 measured a design effect of 4.3 and four non-high-income countries is four independent observations however many cities sit inside them. Task 29's framing does not rescue it: at a cost-loss ratio of 0.05 the matched gap is +0.080 of realisable value with an interval of [−0.406, +0.857], which admits the poorer cities losing 41% of the available benefit or gaining 86%. The decision units make the ignorance legible rather than removing it.

*The gauge-distance axis is a truth artefact, not a finding.* Brier rises with gauge distance at +3.2e-04/km (p = 0.67), and E20's separation curve predicts +6.9e-04/km from label noise alone — **217% of what is observed**, with no free parameters. With the predicted inflation removed the residual slope is −3.7e-04 (p = 0.66). There is no forecast degradation with gauge distance in this panel beyond what a noisier label explains.

*The bridge, and the reason the question is unanswerable here.* Calibration cannot be measured where there is no gauge, but absence can be measured everywhere: across all 6,244 classifiable cities of 100,000+, a city in a high-income country has a GHCN gauge within 25 km **73%** of the time and a median 7.9 km away; in a low-income country, **26%** and 127.6 km. D7 cannot be answered *because of* that row, not despite it — the inequality is simultaneously the hypothesis and the obstacle to testing it.

Two controls make the null interpretable rather than merely empty. On a synthetic panel differing **only** in rain frequency, the raw comparison reports a spurious +0.0384, the shared-range repair leaves +0.0188, and the caliper removes it to +0.0013; loosening the caliper until the confound leaks back (+0.0070) is caught by the published artefact floor (0.0071), so a residual gap has to clear its own climate remainder and not just zero. And on a climate-balanced panel with a genuinely wet-biased forecast on the poorer side, the caliper still finds it (−0.0138, p < 0.01) — the null is not the matching throwing the panel away. Both abort the stage.

**E32. A satellite cannot stand in for the gauge that is not there: its floor sits above the entire score being compared. — Task 12; G1's second half answered in the negative.** (`src/satellite_truth.py`.)

*Why the test is possible at all.* The 408 GHCN gauges reporting in Africa and South America are far too sparse to score cities — that is E27 and it does not change. They are ample to score a **satellite**, because a satellite needs no gauge near a city, only enough gauges somewhere to establish how far it can be trusted. 649 stations inside the 50°S–50°N band, 200 days in 40 blocks across the balanced window, two products read as global daily rasters.

*The headline number.* Once the definitional mismatch is removed — a 5 km cell is wet whenever anything fell anywhere inside it, so it is re-cut at the threshold that makes it rain as often as the gauge — the station-free product disagrees with the bucket on **0.259** of station-days (CI 0.250–0.268). A forecast that predicted the gauge *perfectly* would therefore score Brier 0.259 against this truth source. The vendor's actual lead-1 Brier is 0.167 and E20's gauge floor is 0.083. **The substitute's noise floor is larger than the whole quantity being measured**, against differences the study needs to resolve of 0.0068 and 0.0081. This is not a truth source that adds tolerable noise; it is one that cannot see the effect.

*The two products fail in opposite directions, so neither is a safe default.* At the study's 0.2 mm the station-free product calls rain on 0.580 of days against the gauge's 0.346, inventing rain on 43% of dry days; the blended product runs 0.122 dry and **cannot reach the gauge's rain frequency at any threshold**, topping out at 0.235 against 0.344. E13's finding is that the served forecast is systematically drier than the ensemble supports — scored against the blended product that finding would have come out smaller, and the study would have reported a forecast flattered by its own referee.

*The circularity control inverts the expected answer, which is the interesting part.* CHIRPS blends station reports and CHIRP is the same pipeline without that step, so the gap between them was meant to price the circularity. At a fixed threshold the blended product does look better, by 0.051. **Matched, the ordering reverses: the station-free product is 0.029 ahead, and ahead on every continent.** So the blending does not buy agreement about *which days* it rained; it buys a rain frequency nearer the gauge's, which at a fixed threshold is worth more than skill is. The circularity is real and it lives in the calibration, not the discrimination — and it would have been reported backwards without the base-rate control.

*And it degrades along the axis it was supposed to repair.* Central America & Caribbean 0.342 and South America 0.304 against North America 0.258 and Europe 0.248. The product is most trustworthy where there are already gauges to check it against.

Seven checks police the places a silent error could live and abort the stage: the coordinate continent rule must agree with the country-and-timezone rule used elsewhere (96.9%); arithmetic cell indexing must equal the raster's own affine transform (127 of 127 interior stations, with the 73 exactly on a cell edge named rather than silently resolved); the −9999 fill must be dropped rather than scored as a dry day; a planted one-day calendar offset must be found and undone (0.426 → 0.082); intervals must resample gauges, not station-days (92% coverage against 28%); and base-rate matching must remove a pure definition mismatch (0.420 → 0.014) while leaving a genuine disagreement standing (0.251 → 0.251), so the matched column cannot manufacture agreement.

*Consequence.* **G1's scope caveat is now a finding rather than a gap.** Africa and South America are not reachable by GHCN (E27), not by ISD (E30), and not by this satellite. The limit on where forecasts can be verified is a property of what exists today, not a collection budget waiting to be spent.

**E33. Nothing measurable about a city predicts how well it is forecast, except how often it rains there — and that is the metric's own base rate, not a mechanism. — Task 27; G4 answered in the negative.** (`src/explain.py`.)

*The design is what makes the null worth anything.* Seven city properties — rain frequency, seasonal amplitude, absolute latitude, distance to the nearest gauge, grid-versus-gauge elevation mismatch, terrain roughness within 50 km, log population — regressed on four responses across 102 cities in 25 countries. E28 measured the design effect at 4.3, so seven predictors are fitted against roughly **25** independent observations, every interval resamples **countries**, and validation is leave-one-country-out. Standardisation happens inside the fold: scaling the whole panel first is worth +0.073 of R², which on these numbers is the difference between a result and nothing.

*The coefficients.* Across the three primary responses, exactly one term clears Benjamini–Hochberg on its response's seven tests: `base_rate` on `v_cal_a10` (−0.385, CI −0.504 to −0.263, q = 0.01). Terrain roughness, gauge distance, elevation mismatch, latitude and population survive nothing anywhere.

*The out-of-sample table is the finding.* `bss_clim` fits at in-sample R² 0.18 and cross-validates at **−0.17**; `reliability` 0.15 and **−0.44**. A negative number means the predictors make the prediction *worse* than knowing nothing about the city. Only `v_cal_a10` transfers, at +0.52 — **and a model given rain frequency alone scores +0.57 on the same folds.** The six other predictors add nothing that survives leaving a country out. A value score is bounded by its base rate exactly as a Brier score is, so the one thing that predicts is the same artefact section 3 already flags on Brier.

*The Brier warning, stated in coefficients.* Rain frequency carries **+0.030** of Brier against **−0.079** of the skill score — opposite signs on the same panel. A regression run on Brier would have reported climate as the answer, because a drier city scores better without being forecast better. This is D12 expressed as a regression rather than as a decision curve.

*Two checks keep the null from being the method's.* The same leave-one-country-out procedure returns a median R² of −0.09 on pure noise and **+0.47 on a planted signal**, so an empty result is the panel's answer and not the design's; and country-block intervals cover 92% at a nominal 95% where city-level ones cover 70%, so the resampling unit is not a stylistic choice. A near-duplicate predictor is flagged at VIF 598, so the table can say which coefficients are not separately identified. All abort the stage.

*Consequence.* **The study can say how badly the forecast fails, who it fails and what it costs them; it cannot say why.** That closes G4 rather than leaving it open — and it removes the temptation the in-sample column represents, since a paper reporting R² 0.18 without the −0.17 beside it would have published a mechanism that does not exist. What the panel cannot rule out is separate and stated: latitude and rain frequency are entangled at r = +0.65 here, so a latitudinal effect could hide inside a climate one, and terrain is missing for 21 cities precisely where gauges are sparse — which is where orography would matter most.

**E34. One of nine input sources is share-alike, six published files inherit it, and no reported number does. — Task 9; G15 closed.** (`src/provenance.py`.)

*The sources.* Nine publishers read from their own licence statements on 2026-09-22 and recorded with the wording, the URL and the date. Open-Meteo states CC BY 4.0 across its API and requires a link beside any display of the data; its own upstream list nonetheless gives **UK Met Office data as CC BY-SA 4.0**, so the blanket statement cannot be relied on for that upstream and the stricter term is assumed. GHCN-Daily and ISD are US Government works — GHCN asks for a citation, ISD states no licence at all. CHIRPS is CC0. dynamical.org's *datasets* are CC BY 4.0 while its *website prose* is CC BY-NC-SA, so its validation reports cannot be quoted into a commercially published paper as they stand.

*The propagation.* 334 published files scanned for the model identifiers whose values reach them: 27 carry model-derived values and **6 inherit the share-alike term**. One of the six republishes Met Office output *as served values* rather than as a statistic computed from it — the larger obligation, and the one a scan of the forecast-API registry alone would have missed, because those ids live in the ensemble registry where the same organisations are spelled differently and a defaulting lookup filed them under CC BY.

*What is claimed, and what is checked.* The lineage is not clean — `league.py` reads the duplicate table, Met Office rows included — so the testable claim is the narrower one: no Met Office model is pinned in any league city, so those rows collapse nothing, and no headline table carries a Met Office series. The stage fails if that stops being true.

*A defect found and fixed.* Open-Meteo's licence requires a link, not just a name, beside displayed data. The audit found city pages naming Open-Meteo without linking it; `src/report.py` now emits the link, and the check fails the build on any page that names the source without it — 109 of 109 pages compliant.

*What this does not settle.* ISD's terms are recorded as **unresolved rather than cleared**: it aggregates foreign national reports, some under WMO Resolution 40, and this study publishes coverage counts rather than observations, so nothing is redistributed. None of it is legal advice; it is a record of what each publisher states and when it was read.

**E36. The dry bias is a property of the served forecast, not of the reference centre — and so is the decision-value harm. — Task 20b.** (`src/reference_centre.py`.) E13 measured the served probability as drier than the GEFS members that ought to support it, and the obvious objection is that this says something about NOAA rather than about the forecast. A second, independent centre answers it: ECMWF IFS ENS, 51 members, on the **identical** 15 cities × 327 days × 7 leads, at a matched 3-hourly ladder so E23's boundary artefact cannot leak in.

*The bias.* The served number is drier than **both** references at lead 1 — −0.0835 against GEFS and −0.1189 against IFS, both p = 0.001. The two references disagree with each other by only 0.0354, **30% of the larger vendor gap**, and that ratio stays between 30% and 42% across all seven leads. The dry bias is not mostly a statement about one centre. It is also the stronger of the two comparisons in the direction that matters: the vendor series is `gfs_seamless`, NOAA-derived, so being drier than an *ECMWF* ensemble too is the harder result to explain away.

*What does not resolve, and must be said so.* Under the day-total event neither Brier comparison resolves on this panel — −0.0011 (p = 0.895) against GEFS and +0.0003 (p = 0.727) against IFS. Those differ in sign, and **that is not a reversal; it is two nulls** on 15 cities, where E35 needed 105 to separate 0.006. The stage's verdict logic states this rather than reading a sign flip out of noise.

*What does resolve, and agrees across centres.* Under the any-step event the member-derived probability wins against **both** — +0.0096 and +0.0176, both p = 0.001. So the same days that cannot separate the two under one event definition separate them decisively under the other, and agree across centres when they do. **The event definition is doing more work here than the choice of centre**, which is E35's finding confirmed from a second direction.

*E4a survives.* The cheap-action user loses against both references: at α = 0.05, −0.523 against GEFS and −0.768 against IFS; at α = 0.10, −0.207 and −0.343. The paper's organising claim is not an artefact of which ensemble it is measured against.

*Two controls.* **Member count** — re-deriving IFS from 31 of its 51 members moves its probability by −0.0008 [−0.0017, +0.0001] against a vendor gap of 0.1189, explaining 0.6% of the gap it could have explained entirely; the distinct-value count matches GEFS's (32 against 32) so the coarseness is matched rather than assumed. **Window** — the second archive starts later, so this panel is shorter than E13's; the window alone moves the vendor–GEFS bias by −0.0057, against a reference-centre disagreement of 0.0354.

*What this does not settle.* Two centres are not a population of centres: two agreeing **bounds** the reference effect at this panel's size, it does not estimate it. And this is the 15-capital panel, not E35's 105-city one, because the second archive does not reach the others — widening it is collection, not analysis.

**E37. Part of the coverage gap came from the selection logic, not the gauge network. — G1.** (`src/probe_cities.py`, `src/ghcnh_truth.py`, `src/isd_truth.py`.) A country counted as "covered by GHCN-Daily" as soon as any sparse gauge existed there, even one that could never meet the 500-pair gate. That blocked the hourly source (GHCNh) for the whole country: Japan (8 listed cities at 350–475 days), Pakistan, Saudi Arabia and New Zealand were listed but never scored, and 51 registry cities could never get a page. The fix counts a country as covered only when a record can meet the gate. It also adds world capitals (GeoNames `PPLC`, exempt from the population floor and outside the per-country cap), probes capitals first with 4 stations per city instead of 2, and admits cities on rain alone (14 have no daily-max gauge). Every country now has a recorded reason for being in or out: `data/processed/country_coverage.parquet`. Registry: 221 cities in 74 countries → 222 in 88; GHCNh 47 → 83 cities. Also found: GeoNames code `NA` (Namibia) was parsed as missing.

**E38. NOAA's hourly archive silently drops dry days in much of the Global South, and that bias cannot yet be repaired defensibly.** (`ghcnh_truth.infer_zeros`, `validate-zeros`.) Stations in India, Indonesia, Egypt, Saudi Arabia, Peru, South Africa and elsewhere file a SYNOP every day but leave the rain group blank when nothing fell, as the SYNOP code permits. Mumbai 2025: 362 days reported, 157 with a rain value, 0.9% of values zero; from November to April there are none. The GHCNh record does not keep the indicator that separates "omitted because zero" from "not observed". Read as missing, the blanks fail the 60% reconstruction screen, and the fail rate tracks aridity (Saudi Arabia 0%, Egypt 5%, Peru 10%, South Africa 29%, India 42%, Indonesia 54%). Kept as "reported days only", the blanks would inflate rain frequency. **Any verification built on GHCNh in these countries is biased towards wet days unless it handles this.** A rule that restores the blanks as flagged zeros was tested against criteria fixed before the run:
- V1, zeros withheld at 226 zero-filing stations: 99.98% wet/dry agreement, no false dry days. Pass.
- V2, CHIRP satellite on 2,778 restored dry days: wet on 39%, against 44% of filed dry days. Pass.
- V3, co-located independent gauges: 485 restored days at 12 stations, almost all US Pacific territories. Not evaluable, and where measured, gauge-wet on 25% against 9% for filed dry days.

The rule therefore stays off. V3 cannot be run with 2024–26 gauges in the target countries, because the lack of independent gauges there is the gap itself. Older overlaps are ruled out by design: pre-2024 data invites the objection that observing practice has changed. The finding is publishable on its own; the repair is not, yet.

**E39. Japan is verified through its national agency, and the national feed agrees with NOAA's copy of it.** (`src/jma_truth.py`, prefix `JMA:`.) Japan's gauges are fine: they file zeros (70% of Tokyo's values). But GHCN-Daily's Japanese feed stops on 2025-08-31, and Tokyo Haneda's GHCNh record has no rain group. JMA's daily observatory pages cover about 156 stations to the present. Checked against GHCN-Daily's copy of the same gauges (JMA's own feed, not GSOD) over 1,917 days: 99.0% wet/dry agreement, 93% identical values, and the stage withdraws its cities below 97%. Tokyo's apparent 77% agreement was GHCN's GSOD-derived record, which uses its own day boundary. All 9 cities pass the downstream day scan at lag 0 without being told to. Licence: Public Data License 1.0, compatible with CC BY 4.0, recorded in `provenance.py`.

**E40. The day-matching scan chose the wrong calendar day at a quarter of the cities where the answer is known. — affects E26.** (`capitals.scan_offset`, `capitals.scan_benchmark`.) Every gauge is lined up against ERA5 at shifts of −2…+2 days. The old scan used Pearson correlation on raw millimetres with fixed thresholds (r ≥ 0.45, margin ≥ 0.04), so a few large storms decided the result, and in the tropics it rejected most cities as "flat". GHCNh and JMA cities have their calendar day fixed by timestamps, so the right answer for all 91 of them is 0, and they form a benchmark with a known answer. **The old scan was wrong at 23 of 91 (25%)**, and 8 published cities (Brasília, Dhaka, Dubai, Faisalabad, Lahore, Riga, Wellington, Zinder) had been shifted by a day. By E26, a one-day error costs 0.09 Brier.

The new scan uses Spearman rank correlation on the days common to all shifts, with a seeded 200-replicate block bootstrap. It accepts a shift that wins ≥ 90% of resamples at rank r ≥ 0.30. For known-day sources, 0 stands unless another shift wins ≥ 90%; in that case the city is excluded, never shifted. The threshold was chosen on the benchmark alone (90%: 72 accepted, 2 wrong; 95%: fewer accepted, 3.0% wrong). The benchmark is rewritten on every build (`cities_scan_benchmark.parquet`), and its two failures (Christchurch, Khulna) are excluded as "record contradicts its own dating".

*Independent confirmation.* The scan never sees forecasts. Yet 14 of the 15 cities it re-dated score better against the forecast afterwards: Dubai −0.09 → 0.32, Luxembourg 0.26 → 0.44, Riga 0.06 → 0.22, Mexicali −0.29 → 0.16. The exception is Isfahan (0.31 → 0.22), which leans towards a one-day shift at only 67% confidence.

*Effect.* Cities with metrics 175 → 213, countries 60 → 83. Gained: Kinshasa, Abidjan, Douala, Lomé, Libreville, Bangui, Niamey, Ouagadougou, N'Djamena, Bogotá, Medellín, Cali, Paramaribo, Kuala Lumpur, Ho Chi Minh City and others. Lost: 11 GHCN gauges with no confirmed day (Amsterdam, Córdoba, Iași, Mersin, Nador, Nassau, Nottingham, Preston, Saint Helier, Saipan, Zaragoza). European capitals: Dublin and Luxembourg re-dated to 0, Budapest added, Amsterdam dropped (58% confidence). Bucharest's BSS is unchanged at 0.409 and its hand-set −1 is independently confirmed; its rank moves from 6 to 7 of 16 only because Luxembourg rises.

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

**G1. Geographic scope — ANSWERED, in the negative, and the answer is a result.** E27, E30, E32. 19 capitals in the ensemble track, ~109 cities in the vendor track. The old requirement — ≥2,000 cities on all inhabited continents — cannot be met from GHCN-Daily at any population floor: the qualifying pool holds 1 African city and 0 South American ones. The gap was therefore re-specified from "collect more cities" to **"acquire a truth source outside Europe and North America"**, with the 1,803-city shortfall in `sampling_design.parquet` as its size, and E28 restating the target in *countries* — a country is the unit that carries information, and a city inside one already sampled carries about a quarter of one.

All three candidate sources have now been tested and all three fail for the two continents that matter. GHCN: 1 African city, 0 South American (E27). ISD: the stations exist near 495 and 493 cities respectively and are largely **silent on precipitation** — median usable coverage 7.7% and 3.4%, lifting Africa and South America by ~50 cities each while transforming Asia from 195 to ~1,071 (E30). CHIRPS/CHIRP: the satellite reaches everywhere and its **disagreement with a gauge, 0.259 matched, is larger than the entire Brier score being compared** (E32).

**So the scope limit stops being a caveat and becomes a finding.** The paper cannot report a globally even panel because no one can: verification of rainfall forecasts outside Europe, North America and now Asia is not budget-limited, it is impossible with today's public data. What remains as engineering is Asia via ISD, which widens reach and — per G20 — does nothing for resolution.

**G2. Probability provenance — largely closed.** GEFS integrated, validated, divergence quantified (E4). Remaining: ECMWF IFS ENS for a second centre, and sustained forward collection.

**G3. Truth uncertainty — closed for the gauge-pair part** (`src/truth_uncertainty.py`, E20–E23). Measured rather than caveated: 193k GHCN pairs give disagreement against separation, an irreducible floor of 0.083 at zero separation, and a re-siting experiment that answers the question the gap was asking. The answer is that E4's Brier gap **does not survive** (0.18× its siting spread, sign flips in 45% of alternative worlds) while E4a's decision gap does (13×, no flips). Remaining: the common-mode part no pair test can see (wind loss, wetting, evaporation — E23), and the IMERG cross-check (Task 12) for low-density regions.

**G4. Explanatory model — closed, in the negative** (`src/explain.py`, E33). Seven city properties against four responses, countries as the resampling unit and leave-one-country-out as the test. Skill and reliability do not transfer at all (CV R² −0.17 and −0.44 against in-sample 0.18 and 0.15); the one response that does transfer is matched by rain frequency alone, which is the metric's base-rate dependence rather than a mechanism. The gap between the in-sample and cross-validated columns is the size of the claim that would otherwise have been made. The study can say how badly the forecast fails and who it fails, and not why.

**G5. Literature positioning — grey-literature half closed, Task 3b remains.** Task 3c done 2026-09-21 (`plans/2026-09-21-grey-literature-assessment-v1.md`), and it cost the study a claim. **C1 is narrowed, not lost:** ForecastWatch computes Brier scores for served PoP against METAR/SYNOP truth over 2,100 locations and 25 providers, and has shipped an eleven-bin PoP reliability diagram with Brier/BSS on an API since July 2026. No academic index shows this. The defensible claim is now the *open and reproducible* one — a verification that states whether its differences are resolvable (E10), bounds the error in its own truth (E20–E22) and reports what the probability is worth to someone acting on it (E4a) — none of which that benchmark does.

Two things came back in the study's favour. Their **public** face is deterministic: the 2026 award categories are temperature and wind only, with no precipitation category at all, and the consumer Accuracy Score methodology contains zero occurrences of "probability", "Brier" or "reliability". That is D12 as a market behaviour rather than an argument, from the organisation with the most data. And their truth is the ISD family, which E30 has measured at 8% of days reconstructable in Africa and 3% in South America — while their own report puts its highest accuracies over those same tropics. That is a contribution to their benchmark, and the strongest thing this study can say to that audience.

Also recorded: the wet-bias precedent (their founder's own work, in Silver 2012) points opposite to E13 and must be addressed explicitly; relative economic value is textbook, so C6's novelty is application not method; EUMETNET runs no forecast-verification programme; and WWRP's verification working group sits behind a sign-in and is **unsearched rather than cleared**.

### Blocking Tier B credibility

**G6. Window homogeneity — CLOSED.** E14, E14a, E14b. Balanced window chosen and costed, seasons re-weighted climatologically, and the change-point search run with the search itself paid for in the p-value.

**G7. Metric breadth — CLOSED.** CRPS, ROC/AUC, sharpness and economic value all implemented and reported (E5, E6).

**G8. Baselines — CLOSED.** Persistence in two forms, flat base rate, and smoothed day-of-year climatology, all on identical days (E6).

**G9. Multiple comparisons — CLOSED.** E11. BH and BY applied to the 105-cell panel; 27 and 19 cells survive respectively, all at leads 5–7.

**G10. Significance testing — CLOSED, with a consequence.** E10. The headline verdict is now tested and the answer is that the sample cannot resolve it: the minimum detectable Brier difference, 0.0081, is five times the margin at which a difference would matter. This does not reopen the gap — it converts it into G20 and G1, which is where it belongs. **The test that matters for the paper's actual claim, the low-α decision-value gap, is comfortably significant.**

### Supporting

**G11. Rain-day convention at scale — closed** (`src/obs_time.py`, E24–E26). The convention is now settled from GHCN metadata plus a neighbour-graph measurement, with no reanalysis and no network: 42% of gauges from metadata alone, agreeing with the independent measurement 99.1% of the time where it predicts a non-zero shift. The changeover hour is measured, not assumed, and is **not** where arithmetic puts it. The residual gate on G1 is gauge density, not convention.

**G12. Observation latency.** `OBS_END = "2026-05-31"`. Note E4's sample ends 2026-05-31 despite GEFS reaching 2026-08-31 — **the truth source, not the ensemble, is now the binding constraint** on closing the balanced window.

**G13. ML systems absent from the registry.** Partially resolved.

**G14. Sampling-design defence — CLOSED.** E29. The cap is swept from 1 to uncapped and its effect on the answer bounded at 0.0118 BSS, ≤0.0005 for any cap ≥ 2. The design, its allocation rule and its shortfall are stated explicitly in `sampling_design.parquet` rather than left implicit.

**G15. Licence segregation.** ~~Closed by E34~~ — one share-alike input (UK Met Office via Open-Meteo), six inheriting files segregated under CC BY-SA 4.0, no reported number affected, checked rather than asserted. `NOTICE.md` generated from the audit.

**G16. Third-party republication dependency — CLOSED.** E2. Remaining housekeeping: disclose the compression and the negative-value artefact, resolve URLs from STAC at run time (`data.dynamical.org` URLs retire 2026-09-30), archive extracted point data to Zenodo.

**G17. Temporal quantisation — CLOSED.** E3. Report distributionally, not as a mean.

**G18. Competitor-collision monitoring.** Open.

**G19. Non-independent vendor models — CLOSED.** E12. `src/duplicates.py` tests every pair at every city hour by hour on the raw served values; the league table and the threshold-robustness table both consume its verdict and collapse duplicates before ranking, with the alias named on the surviving row rather than deleted. The triangulation stage's aggregate-level check is retained as a smoke alarm and now says so.

**G20. Sample-size honesty on the headline. — CLOSED, in the negative.** E4 is 15 European capitals over 21 months with serially correlated days; E10 puts the invisible-difference threshold at 0.008 Brier. E28 now prices the fix and finds it unaffordable: closing to the 0.0015 margin would take ~68,308 cities at the measured design effect of 4.3, more than the pool holds. **The headline can therefore never be a precision claim, at any budget.** Every statement derived from it must carry its scope explicitly, and the paper's weight must rest on the decision-value result (E4a), which the sample *does* resolve. Risk: the tie is a European artefact — and the study cannot presently tell a European artefact from an absence of effect, nor buy its way out.

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
- [x] ~~Task 3c. Grey literature — ForecastWatch, EUMETNET, WMO/WWRP; forward-cite WGNE and the CAWCR/WWRP portal.~~ **DONE 2026-09-21.** `plans/2026-09-21-grey-literature-assessment-v1.md`. ForecastWatch narrows C1 and confirms C5 should not lead; EUMETNET negative; WWRP inaccessible; forward-citation chase still needs the Scholar account and stays with Task 3b.
- [ ] Task 5. Write the literature positioning around the corrected framing. Rationale: G5, D7, D8.
- [x] Task 6. Per-station observation-time detection — **done** (`src/obs_time.py`). Metadata rule fitted to a measured curve, audited against an independent neighbour-graph measurement, 14 disagreeing stations named and excluded. E24–E26.
- [ ] Task 7. Add validated ML ids to `PROVIDER_MODELS` for the deterministic track. Rationale: G13, D6.
- [x] Task 9. Record licence provenance per model; segregate CC-BY-SA outputs. Rationale: G15. → E34.

### Phase 2 — External ensemble integration

- [x] Task 20a. **Integrate GEFS; member-derived PoP at all leads for the headline window.** → E1.
- [x] Task 20b. **Integrate `ecmwf-ifs-ens-forecast-15-day-0-25-degree` as a second reference centre.** → E36. The dry bias and the decision-value harm both hold against ECMWF as well as NOAA; the day-total Brier verdict resolves against neither on this panel, and the any-step verdict resolves against both.
- [x] Task 20c. **Validate against raw GRIB2 byte-ranges.** → E2.
- [x] Task 20d. **Local-day accumulation with boundary handling, plus the 3-hourly vendor control.** → E3.
- [x] Task 21. **Publish the vendor-versus-ensemble divergence as a primary result.** → E4, `src/triangulation.py`, stage 14/15.

### Phase 3 — Global scale-up

**Now the critical path.** E4 is a 15-city European result; nothing in the paper generalises until this phase lands.

- [ ] Task 10. **RE-SPECIFIED by E27/E28.** Not "lift the city budget toward the full qualifying pool" — that pool is 1,061 cities and structurally European. Lift the *country* count, and only as far as the truth sources of Tasks 11–12 allow. Rationale: G1.
- [x] Task 10a. **Extend GEFS extraction to the full city set.** → E35. 176 cities collected (94 tiles, 24 months); the triangulation now runs on the 105 that survive the set-identical rule, across four continents and 60,891 city-days. The capitals' PoP regenerates bit-identical, so the movement is the new cities and not the code.
- [x] Task 11. ~~Integrate NOAA ISD globally alongside GHCN-Daily; report coverage per continent and income group.~~ **Feasibility answered (E30): worth integrating for Asia, near-useless for Africa and South America.** The integration itself is now a scoped engineering task rather than an open question — and the case for doing it rests on countries, not cities.
- [x] Task 12. ~~IMERG cross-check where gauge density is low.~~ **Done, with a negative result (E32)** (`src/satellite_truth.py`), on CHIRPS/CHIRP rather than IMERG: both are daily, global, anonymous over HTTP with byte-range reads, and the pair differs only in whether station reports are blended in — which makes the circularity measurable instead of assumed. The satellite's own disagreement with a gauge (0.259 matched) exceeds the entire Brier score being compared, so it is not a usable substitute truth source at this study's effect sizes. G1's second half closed in the negative.
- [x] Task 13. Quantify representativeness error — **done** (`src/truth_uncertainty.py`), from GHCN gauge pairs rather than GEFS neighbourhood fields: the pairs measure the truth's own noise, which a model field cannot. E20–E22.
- [x] Task 14. Gauge undercatch — **done, with a negative result** (E23). A pair method is blind to common-mode catch loss by construction; the directional asymmetry it *can* see is under 0.009 at every separation. Reported as a bound on what the method sees, not as a correction.
- [ ] Task 15. Evaluate `asos-parquet` as the low-latency truth source. Rationale: G12; the truth source now binds the window, not the ensemble.
- [x] Task 16. Formalise the sampling design; cap-sensitivity. Rationale: G14. **DONE — E29, `src/sampling.py`.**

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

- [x] Task 27. **Multivariate explanatory model of skill and calibration error.** → E33. `src/explain.py`, pipeline stage 24. Seven city properties against four responses, 102 cities in 25 countries, countries as the resampling unit because E28 measured the design effect at 4.3, and leave-one-country-out as the test. Skill and reliability do not transfer at all (CV R² −0.17 and −0.44 against in-sample 0.18 and 0.15); `v_cal_a10` transfers at +0.52 and rain frequency alone reaches +0.57 on the same folds, so the one thing that predicts is the metric's base-rate dependence and not a mechanism. Checks require the same procedure to find a planted signal (+0.47) and return nothing on noise (−0.09), so the null is the panel's answer rather than the design's. Rationale: G4, closed in the negative.
- [x] Task 28. **Test whether *calibration* error varies with income group, gauge density and region.** → E31. `src/equity.py`, pipeline stage 22. The panel reaches 25 countries, 4 of them non-high-income and none low-income, so the honest answer is a bounded null rather than a gap. The raw comparison favours the poorer side on every metric — an artefact of a lower rain base rate, not a finding — and caliper matching on base rate removes it. What survives is the width of the interval, and the artefact floor is predicted from a locally fitted curve rather than assumed to be zero.
- [x] Task 29. **Express any calibration gap in decision terms via the Task 23 curves.** → E31a. The bound is carried onto the cost-loss axis: at α = 0.10 the interval admits a gap no larger than the amount E4a already attributes to the served probability's dry bias, so "no measurable inequity" cannot be read as "none that would matter". Rationale: D7.
- [x] Task 30. **Physics-versus-ML calibration on the AIFS-ENS matched window.** → E13, E13a, E13b. `src/physics_ml.py`, pipeline stage 16. Both ECMWF ensembles collected from dynamical.org (`src/collect_members.py`, `src/ens_archive.py`, now source-agnostic), accumulated onto one matched 6 h ladder by `ensemble_pop.py --step-hours=native,6`, scored with the same day-block bootstrap as Task 25. The result is *direction without per-lead significance*, and the ladder sensitivity (E13a) is arguably the more publishable half.
- [x] Task 31. **Provenance audit as a standalone section.** → E2, E9, E34. `src/report.py` `sec_provenance`, in the top nav and the cross-city pane: four audits that need no observation of the weather, presented as one argument about what the served number *is* before anyone scores it. Two names one forecast (354 probability comparisons; MET Norway serves ECMWF's probability to the last digit on 20,760 hours at all 19 cities while its amounts are its own, so a both-channels test would have missed it); the rounding control (101 distinct served values, re-derivation moves the daily figure by a median 0.00 and a 99th percentile 0.36, reported as a distribution); the republisher check against NOAA's own GRIB2 byte-ranges (180 comparisons, 0.0012 mm mean, 105 bit-exact, 0 event flips, the two negative raw values disclosed rather than clipped); and the licence trace (11 sources read from their own pages, 1 share-alike, one file republishing served values, and our own compliance defect found and fixed). Renders only if all four are on disk — any one alone reads as housekeeping.
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
5. **The tie is a European artefact.** New and material. 15 capitals, one continent. Mitigation: Tasks 11 and 12 to make a non-European panel possible at all — E27 shows Tasks 10/10a cannot do it alone — and G20 requires scope stated on every derived statement. E28 removes the fallback of buying precision instead.
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
