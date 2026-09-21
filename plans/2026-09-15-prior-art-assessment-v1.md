# Prior-Art Assessment — First Pass

**Date:** 2026-09-15. **Closes:** partially, Task 3 of `plans/2026-09-15-global-forecast-trust-journal-strategy-v3.md:190`. **Status:** incomplete — see §6.

Source PDFs are in `literature/`, which is git-ignored (≈120 MB of publisher PDFs, most not redistributable). DOIs below allow refetching.

---

## 1. Method used, and its limits

Seven PDFs were assessed by extracting text with `pdftotext`, reading front matter, and running targeted term-frequency scans plus line-level greps on the hits. **No paper was read in full.** No supplementary information was examined.

This is a screening method, not a literature review. It is adequate for deciding *"does a paper with our headline already exist"* and inadequate for *"what exactly did they do"*. Two failures actually occurred during this pass and are recorded here rather than quietly fixed, because the study's own central contribution is an audit of stated-versus-actual provenance and the same standard must apply to its literature work:

**Failure 1 — wrong keyword, wrong conclusion.** The first pass used `Brier = 0` as a proxy for *"no probabilistic verification"* and concluded that none of the papers verify forecasts probabilistically. **This was false.** Brier is a binary-event score; the ensemble and ML verification community uses **CRPS** and **SEEPS**. WeatherBench 2 contains CRPS ×24, "probabilistic" ×22, spread-skill ×8, SEEPS ×11 and a rank histogram. The corrected claim is much narrower and appears in §4.

**Failure 2 — contaminated regex.** Case-insensitive `ROC` matches "p**roc**ess". The reported "ROC ×30" in WeatherBench 2 was almost entirely noise. Any ROC/AUC counts in the table below are unreliable and were discarded.

Text extraction was sanity-checked (WeatherBench 2: 94,086 chars / 25 pp; MAUSAM: 126,508 / 39 pp; Linsenmeier & Shrader: 69,947 / 36 pp), so absence of a term is not an artefact of failed extraction.

---

## 2. Evidence table

Term counts, case-insensitive, whole document, `ROC` excluded as unreliable.

| | Brier | CRPS | rank hist. | spread-skill | "probabilistic" | SEEPS | "reliab" | rain gauge | ERA5 |
|---|---|---|---|---|---|---|---|---|---|
| Rasp 2024 — WeatherBench 2 | 0 | **24** | 1 | 8 | **22** | **11** | 2 | 2 | **73** |
| Gupta 2026 — MAUSAM | 0 | 3 | 0 | 0 | 13 | 0 | 4 | 22 | **93** |
| Linsenmeier & Shrader 2025 | 0 | 0 | 0 | 0 | **0** | 0 | 0 | — | — |
| Hewson & Pillosu 2021 | 2 | — | — | — | — | — | — | 3 | 2 |
| Huang 2023 | 0 | — | — | — | — | — | — | 1 | 0 |

---

## 3. Per-paper findings

### 3.1 Linsenmeier, M. & Shrader, J. (2025). *Global inequalities in weather forecasts.* Working paper, 25 Nov 2025, Columbia Climate School / Princeton HMEI / Goethe. 36 pp. No DOI captured.

**Verdict: DIRECT OVERLAP with claim C4. Treat C4 as scooped. Boundary confirmed exhaustively — see "Confirmation pass" below.**

What they do:

- Global analysis of **numerical weather prediction accuracy** related to economic inequality.
- Primary variable **2 m air temperature**; ECMWF digital archive **1985–2020**; GFS as a robustness check.
- Truth is **land-based station observations** from NOAA, not reanalysis. Precipitation verification uses **GSOD daily 0 UTC–0 UTC totals** from rain gauges.
- Observations are **population-weighted** — they verify where people live.
- They **attribute** the rich/poor gap: ~7% to in-situ observation infrastructure (station and radiosonde density, reporting rates), the larger remainder to geography and inherent predictability.
- Robustness includes verifying against model analysis instead of stations, continuous-reporting station subsets, and alternative outlier handling.

Headline: *a seven-day forecast in a high-income country is on average more accurate than a one-day forecast in a low-income country*, with the gap persistent since 1985.

What they do **not** do — verified by zero hits on every probabilistic term across the full 36 pages:

- **No probabilistic verification of any kind.** No Brier, no CRPS, no reliability, no calibration, no rank histogram. "Ensemble" appears twice, both times describing per-member deterministic accuracy averaged across members for *seasonal* forecasts.
- **No precipitation probability.** Precipitation is a secondary "external validity exercise" (their Figure 2, panel G), deterministic.
- **No consumer-facing or vendor-served forecasts.** They verify raw ECMWF/GFS archive output.
- **No ML forecast systems** — the record ends 2020.
- **No economic value computation.** Economic value is motivation, and their discussion explicitly calls for *"further work demonstrating the economic value of weather forecasts."*
- **No per-station observation-time handling** — daily precipitation totals are taken as fixed 0–24 UTC, the convention trap that G11 exists to prevent.

#### Confirmation pass (2026-09-15, Task 3a)

The supplementary information **is bundled in the same PDF** (begins at line 1846 of 2,802 extracted lines), so the earlier scan already covered it. A deliberately broad re-scan across the full document including SI returned **zero occurrences** of every one of: `brier`, `crps`, `continuous ranked`, `reliabilit`, `calibrat`, `probabilistic`, **`probability`**, `rank histogram`, `sharpness`, `spread`, `tercile`, `quantile`, `percentile`, `AUC`, `ROC`, `skill score`, **`uncertaint`**. Only `ensemble` (×2) and `anomaly correlation` (×3) occur.

The word "probability" does not appear once in the paper. **C4's boundary is settled: this is a deterministic-accuracy study with no probabilistic content whatsoever.**

Methods, now read directly (lines 1136–1200 of the extracted text):

- **Forecast source:** ECMWF **deterministic** archive, 1985–2020, validity 12 UTC, initialised 0/24/…/168 h ahead. GFS as a robustness check. Validity 0 UTC gives "essentially identical results".
- **Accuracy metric: anomaly correlation coefficient (ACC)** — stated at line 471 as *"our main measure of forecast accuracy"*. Not MAE, not RMSE. Bias is analysed separately and **explicitly excluded** from the headline measure.
- **Truth:** NOAA **Integrated Surface Database** stations for temperature; rain gauges for precipitation. The infrastructure analysis uses stations reporting sea-level pressure, chosen because pressure *"has always been assimilated"* whereas temperature has not.
- **Precipitation:** an *additional* analysis. Initialised 0 UTC, validity 0 UTC, 24-hour cumulative totals verified against observed daily totals.
- **Ensembles appear only for seasonal forecasts**, handled as: *"We process and verify each ensemble member separately and then average forecast accuracy across the ensemble."*

Three methodological openings follow, each of which this study can legitimately occupy:

1. **Member-wise score averaging discards the ensemble.** Averaging a deterministic score across members measures the average member, not the ensemble — capturing neither the ensemble mean's error reduction nor any distributional information. A probabilistic score is the standard treatment.
2. **ACC is a poor fit for precipitation.** Anomaly correlation presumes a continuous, roughly Gaussian anomaly field. Daily precipitation is zero-inflated, heavy-tailed and discontinuous at the wet/dry boundary. Applying ACC to it is defensible only as the rough external-validity check they label it as. Precipitation demands categorical or probabilistic verification — which is this study's entire object.
3. **Fixed 0–24 UTC accumulation** with no per-station observation-time reconciliation (G11).

Two passages are directly useful for positioning. They note that **national agencies translate global model predictions into local forecasts** — and do not study that translation layer, which is precisely the provenance audit. And their call for economic-value work is an open invitation to Task 23.

### 3.2 Rasp, S. et al. (2024). *WeatherBench 2.* JAMES. `10.1029/2023MS004019`

**Verdict: ADJACENT, but more important than first assessed. The standard this study deliberately deviates from.**

The field's flagship benchmark (Google Research, Google DeepMind, ECMWF). It **does** perform probabilistic ensemble verification — CRPS, spread-skill ratio, rank histograms. My first-pass claim that no paper here does probabilistic verification was wrong because of it.

Three findings that matter more than the correction:

1. **Ground truth is ERA5** (×73), stated at line 482: *"for precipitation accumulations we use ERA5 as a precipitation ground truth for all models."*
2. **The authors document their own limitation.** Line 406: *"for precipitation, ERA5 sometimes shows large differences to rain gauge measurements."* Line 1286: precipitation *"is not directly assimilated into ERA5… and often show large differences to rain gauges or radar precipitation estimates."*
3. **Precipitation evaluation is deterministic.** SEEPS is described at line 772 as ECMWF's *"routine deterministic precipitation evaluation"* — a three-class dry/light/heavy score. The probabilistic machinery is applied to continuous fields.

Net: the benchmark defining how the ML weather community measures precipitation skill uses a reanalysis that does not assimilate precipitation, and says so. **This is the strongest available justification for station truth, and it comes from the benchmark's own authors rather than from us.**

Also relevant: they report IFS HRES as most skilful for precipitation by SEEPS — a model ranking exists, from raw output against ERA5, which partially addresses C5.

### 3.3 Gupta, A., Sheshadri, A. & Suri, D. (2026). *MAUSAM: An Observations-Focused Assessment of Global AI Weather Prediction Models During the South Asian Monsoon.* JAMES 18, e2025MS005568. `10.1029/2025MS005568`

**Verdict: ADJACENT, closest competitor for C3. Watch closely.**

Billed as the *"first evaluation of AI weather predictions against observations during monsoon"*, reporting 15–45% prediction errors with AIFS lowest. Rain gauges ×22 — genuinely observation-focused — but ERA5 still ×93.

Bounded by: **regional** (South Asian monsoon), and largely deterministic — CRPS ×3, "probabilistic" ×13, no rank histogram, no spread-skill, no Brier, no PoP. My first-pass description of it as purely deterministic was overstated and is corrected here.

The existence of this paper is evidence the "AI models versus real observations" niche is being actively worked. **C3 survives as probabilistic calibration, globally — but the window to claim it is closing.**

### 3.4 Hewson, T. D. & Pillosu, F. M. (2021). *A low-cost post-processing technique improves weather forecasts around the world.* Communications Earth & Environment 2:132. `10.1038/s43247-021-00185-9`

**Verdict: ADJACENT — closest methodological neighbour; essential citation.**

ecPoint: statistical post-processing of ensemble forecasts accounting for sub-gridbox variation, bias, and weather-dependence, calibrated globally rather than locally. Calibration ×29, Brier ×2, PoP ×1. Claims useful extreme-rainfall forecasts extend to 5 days versus under 1 day unprocessed.

Directly relevant to **G3** (point-versus-gridbox representativeness) and **G17** (quantisation). But it is a method that **improves** forecasts, not an audit of what is **served** to users. Distinct contribution.

Secondary value: it establishes that *Communications Earth & Environment* publishes exactly this class of work — supporting the v3 venue ranking.

### 3.5 Mistry, M. N. et al. (2022). *Comparison of weather station and climate reanalysis data for modelling temperature-related mortality.* Scientific Reports. `10.1038/s41598-022-09049-4`

**Verdict: SUPPORTING.** Multi-region assessment of reanalysis versus station data for impact assessment. Cite in the methods to justify station truth over reanalysis; it makes the argument in an applied-impact setting.

### 3.6 Huang, Z. et al. (2023). *Reliability of Ensemble Climatological Forecasts.* Water Resources Research. DOI not captured.

**Verdict: SUPPORTING, niche.** Calibration ×6, no Brier, no CRPS hits. Relevant to the climatological-baseline work in G8 / Task 18.

### 3.7 Bossa, Y. A. & Hounkpè, J. (2026). *Evaluation and Post-Processing of Precipitation Forecast Skills at Short Lead Times for Hydrological Applications over the Ouémé Basin.* Climate (MDPI) 14, 146.

**Verdict: SUPPORTING, exemplar of the literature this study contrasts with.** Six NWP models, leads 1–7 days, 1985–2015, six sub-basins in Benin, KGE and event-based metrics, bias correction. ECMWF and UKMO best.

Useful as a concrete instance of the single-basin, deterministic, regional study that dominates precipitation verification — the contrast that motivates global scope. Also a rare Global South data point.

---

## 4. Consequences for the study's novelty claims

| Claim | Status after this pass |
|---|---|
| **C1** Global station-verified calibration of served PoP | ~~**Survives.**~~ **NARROWED 2026-09-21 (Task 3c).** No *paper* does this, but ForecastWatch does it commercially at larger scale and has shipped PoP reliability diagrams since July 2026. Claim must become the open-and-reproducible one. See `plans/2026-09-21-grey-literature-assessment-v1.md` §2, §6. |
| **C2** Provenance: vendor-served versus native ensemble probability | **Survives, untouched.** No paper in this set examines the vendor/post-processing layer. Linsenmeier & Shrader explicitly note it exists and do not study it. |
| **C3** Physics versus ML probabilistic calibration | **Survives, narrowed and time-limited.** Gupta 2026 did AI-versus-observations, regionally and deterministically. Claim must be *probabilistic calibration, global, gauge-verified*. |
| **C4** Forecast inequality | **SCOOPED** by Linsenmeier & Shrader 2025, including the gauge-density control. Do not pursue as written. Boundary confirmed: their measure is deterministic ACC on temperature, so *calibration* inequality — and precipitation specifically — remain open. |
| **C5** Multi-model league table | **Partially anticipated.** WeatherBench 2 ranks models on precipitation via SEEPS against ERA5. Ours differs by truth source and by ranking *served* forecasts. Not a headline. **Confirmed 2026-09-21:** ForecastWatch ranks 25 providers over 2,100 locations on 84 metrics. Do not lead with the league table. |
| **C6** Methods — economic value, seasonal weighting, representativeness | **Survives, re-worded 2026-09-21.** No economic value computation in any paper here; Linsenmeier's discussion asks for it, and the commercial corpus has zero hits for it either. But relative economic value is textbook (CAWCR portal, Richardson 2000), so the novelty is application and bounded truth error, not method. |

**Corrected framing.** The defensible statement is **not** "nobody does probabilistic verification" — WeatherBench 2 does, extensively. It is:

> Continuous ensemble verification against reanalysis is well established. **Binary-event reliability of publicly served precipitation probabilities, verified against station gauges, at global scale, is not.**

**Strategic consequence.** Stop competing on inequality of *accuracy*; pivot to inequality of *trustworthiness*. Linsenmeier & Shrader established that forecasts are less accurate where people are poorer. The open question is whether the probabilities people are actually served also **misrepresent their own reliability**, and whether that miscalibration compounds the accuracy gap in decision terms. This cites them as foundation rather than competing, preserves the equity dimension, and lands on ground no paper in this set occupies.

**Risk.** Linsenmeier & Shrader is a working paper four months old, likely under review at a high-profile venue. If it lands in Nature or Nature Communications, an adjacent submission there becomes harder — a further reason to pivot away from their claim rather than toward it.

---

## 5. Citation base established

For the literature-positioning section (Task 5), beyond the standards already identified (Brier 1950; Murphy 1973 decomposition; WGNE precipitation verification guidance; CAWCR/WWRP verification portal; Richardson relative economic value; ECMWF Forecast User Guide §12.B):

- **Rasp et al. 2024** — the evaluation standard, and the source of the ERA5-precipitation caveat justifying station truth.
- **Hewson & Pillosu 2021** — point-versus-gridbox representativeness and global calibration.
- **Linsenmeier & Shrader 2025** — the accuracy-inequality fact this study extends into the probabilistic domain.
- **Gupta et al. 2026** — AI evaluation against observations; the regional, deterministic precedent.
- **Mistry et al. 2022** — station-versus-reanalysis truth in applied assessment.
- **Huang et al. 2023** — ensemble climatological reliability, for the baseline work.
- **Bossa & Hounkpè 2026** — exemplar regional precipitation verification.

---

## 6. What remains unverified

Task 3 is **not closed**. Outstanding:

1. **No full methods read** of Rasp or Gupta — term frequencies and targeted lines only. Linsenmeier's methods have now been read.
2. ~~No supplementary information examined.~~ **CLOSED 2026-09-15 (Task 3a).** Linsenmeier's SI is bundled in the same PDF and was covered; a 17-term probabilistic scan over all 2,802 lines returned zero hits, and the methods were read directly. See §3.1, "Confirmation pass".
3. **Term counts remain a proxy** for the other six papers. A paper can perform reliability analysis under vocabulary not searched — though for Linsenmeier the term list is now broad enough that this risk is negligible.
4. **Seven opportunistically downloaded papers are not a literature search.** The C1–C6 queries against Google Scholar Labs, Web of Science, ECMWF eLibrary and AMS journals are still required.
5. ~~**Grey literature untouched** — ForecastWatch, EUMETNET, WMO/WWRP working-group reports. These are the most likely home of anything resembling C1 or C5 and are invisible to academic indexes.~~ **CLOSED 2026-09-21 (Task 3c).** The prediction was correct: ForecastWatch computes Brier scores for served PoP against METAR/SYNOP truth over 2,100 locations and ships an eleven-bin reliability diagram, none of it visible to an academic index. EUMETNET is observation infrastructure only. WWRP's verification working group is behind a Google sign-in and remains **unsearched rather than cleared**. Full assessment: `plans/2026-09-21-grey-literature-assessment-v1.md`.
6. **No forward-citation chase** on the WGNE guidance or the CAWCR/WWRP portal. Needs the same accounts as item 4.
