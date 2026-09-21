# Grey-literature assessment (Task 3c)

**Date:** 2026-09-21
**Closes:** item 5 of `plans/2026-09-15-prior-art-assessment-v1.md` §6 ("Grey literature untouched — ForecastWatch, EUMETNET, WMO/WWRP working-group reports. These are the most likely home of anything resembling C1 or C5 and are invisible to academic indexes.")
**Does not close:** item 4 (Task 3b, Scholar/WoS/AMS queries — owner: user) or item 6 (forward-citation chase, which needs the same accounts).

The prediction in that sentence was correct. The most damaging prior art found anywhere in this project is here, and no academic index would have shown it.

---

## 1. Method, and the three ways it failed first

Sources were fetched with `curl`, stripped of markup, and screened by term frequency — the same method the prior-art pass used on PDFs. Applied to the live web it failed three times, and each failure produced output that looked like a finding.

**It fails on JavaScript-rendered sites.** Four ForecastWatch pages returned 183–309 KB of HTML that reduced to ~4,000 characters of navigation. A term scan over that returns near-nothing and reads as "no relevant content". The site is WordPress, so the fix was its REST API: `wp-json/wp/v2/{glossary,downloads,awards_2026,knowledgebase,blog,news,features,press,pages,industry-solutions}` yields **315 documents, 560,416 characters** of actual body text. Every ForecastWatch finding below comes from that corpus; none of it was reachable by fetching pages.

**It fails on 404 pages.** The EUMETNET URL carried in the plan returns 404 with a 266 KB body. Its navigation alone produced 8 hits for `calibrat` and 13 for `station`. A term count over a 404 page is indistinguishable from a term count over a real one unless the status code is checked.

**It produces false friends that survive into a reader's summary.** Two, both caught by reading context rather than trusting counts:

| term | apparent reading | actual |
|---|---|---|
| `equit` ×11, CAWCR portal | equity / forecast inequality | **Equitable Threat Score** throughout |
| `calibrat` ×3, ForecastWatch awards methodology | forecast calibration | **anchoring an award score's 0–100 scale** to historical percentiles |

Both are in exactly the vocabulary this study uses. A screen reporting counts without contexts would have recorded an equity treatment and a calibration treatment where there is neither.

---

## 2. ForecastWatch

### F1 — A commercial benchmark has been computing Brier scores for served PoP, globally, against station truth, for years

From the public FAQ:

> "We calculate accuracy using standard metrics such as Mean Absolute Error (MAE) for temperature, **Brier Scores for probability of precipitation**, and bias analysis. Our methodology is transparent and has been used in several peer-reviewed studies."

Scale, from the 2021–2024 overview: **over 600 million forecasts, 25 providers, more than 2,100 locations worldwide, 84 metrics, leads 1–14 days**, with probability of precipitation one of six forecast categories. Named results exist at provider level — "Foreca/Vaisala was the most accurate for probability of precipitation Brier score."

This is the single most consequential result of the pass. C1 as written — *global station-verified calibration of served PoP* — is not unoccupied ground.

### F2 — Since July 2026 they ship a reliability diagram for PoP, and an API for it

From the July 2026 release notes:

> "POP Calibration is back. Select 24h POP in Monthly Insights for a **reliability diagram across all eleven bins**: your curve against a pooled market curve, an anonymous market-range band, and the **perfect-calibration diagonal** — plus a commitment histogram and exact per-bin counts… `GET /v1/insights/pop-calibration/` returns the same per-bin counts, observed and market frequencies, and **Brier/BSS figures**."

Eleven bins, observed frequency against forecast probability, the diagonal, per-bin counts. That is this study's central instrument, sold as a product feature, two months ago.

### F3 — Their truth is the METAR/SYNOP family, which this study has measured

From the May 2026 release notes, describing the endpoint that exposes their scoring truth:

> `GET /v1/observations/?station_code=KORD…` — "the exact dataset ForecastWatch uses to score every provider in the public benchmark… **`station_code` accepts ICAO (`KORD`) or SYNOP**"

and from the FAQ, "quality-controlled observations from the National Climatic Data Center (NCDC) in the U.S. and equivalent international meteorological agencies." Their September 2026 notes discuss "SYNOP-verified countries (much of Europe, the UK, Japan, and Turkey)".

This is the ISD family, and **E30 measured what it reports**: a daily precipitation total can be reconstructed on a median **22%** of days globally — 94% in North America, 90% in Asia, 28% in Europe, **8% in Africa, 3% in South America**.

The tension is specific and checkable. Their *Best Places* report ranks "nearly 2000 locations worldwide" and puts its highest three-day accuracies over "the northern coastlines of South America… extreme western Africa, eastern Central Africa… In general, the most accurate combinations of forecasts and persistence were found in the tropics." Those are precisely the regions where E30 finds the underlying station network nearly silent on rain. This is a contribution *to* their benchmark, not a rebuttal of it, and it is the strongest thing this study can say to that audience.

### F4 — Their public face is deterministic, even though the platform is not

The 2026 award categories, enumerated from `awards_2026-sitemap.xml`:

- `enterprise-weather-intelligence-{region}-temp-24-hr-high`, `-temp-24-hr-low`, `-wind-24-hr-wind`
- `consumer-weather-{region}-all-metrics-{1-7, 8-14}`

**There is no precipitation award category at all**, and no probabilistic one. Consumer awards fold everything into a single 0–100 Accuracy Score whose methodology page contains **zero occurrences of "probability", "Brier", "reliability" or "gauge"**; precipitation enters once, as "precipitation skill", a higher-is-better scalar converted to a loss.

So the largest existing benchmark computes Brier behind a subscription and reports an accuracy percentage to the public. **That is D12 observed in the wild** — the metric a reader is shown is not the metric that would reveal whether the probability is honest — and it is a better piece of evidence for the paper's thesis than anything the literature offers.

### F5 — The wet-bias precedent, and it points the other way

ForecastWatch's founder Eric Floehr compared PoP forecasts against actual rainfall and found "commercial forecasts (The Weather Channel and AccuWeather) regularly predicted a **greater chance of rain than occurred**, while the National Weather Service predictions were more consistent" — the "wet bias", popularised in Silver's *The Signal and the Noise* (2012).

E13 reports the served probability is **drier** than the ensemble supports (−0.078 at lead 1). These are different quantities — vendor-against-ensemble, not vendor-against-observed-frequency — and different vendors, Open-Meteo's `gfs_seamless` rather than TWC or AccuWeather. But a reviewer will read them as contradictory, and the paper must say which comparison it is making and against whom before someone else does.

### F6 — What 315 documents do *not* contain

Zero hits, across the whole corpus, for: `cost-loss`, `economic value`, `rain gauge`, `GHCN`, `calibration curve`. Nothing anywhere compares a served probability against the ensemble it was derived from. Their own download, *"Why two weather APIs disagree about tomorrow"*, shows the provenance question is commercially live and unanswered.

---

## 3. EUMETNET — negative, cleanly

The Forecasting & Climate activity is **observation infrastructure**: OPERA radar, ceilometers, moored buoys, BUFR tooling, quality monitoring of observations. Across 55,422 characters: zero `Brier`, zero `probability of precipitation`, one `reliability` (of observational data, not forecasts). Its linked PDF corpus is radar practice — ODIM formats, wind-turbine interference, RLAN, composite quality.

EUMETNET does not run a verification programme of served forecasts. Nothing here touches C1–C6.

## 4. WMO / WWRP — not searched, and recorded as such

The WWRP activity page is 6,537 characters of navigation. The Joint Working Group on Forecast Verification Research site (`sites.google.com/view/jwgfvr`) returns 941 KB that is entirely a **Google sign-in page**; both `wmo.int` verification paths in the plan now 404.

This source is **unsearched, not absent**. It remains the most likely home of a systematic international comparison and should be treated as an open risk, not a cleared one.

## 5. CAWCR / WWRP verification portal — a methods reference, not a study

Rich in vocabulary and poor in results, as expected of a teaching resource: `Brier` ×20, `reliability diagram` ×11, `economic value` ×5, `SEEPS` ×5, and a sample PoP dataset from FMI 2003. It cites Richardson (2000) on relative economic value and Katz & Murphy (1997).

The consequence is for C6. **Relative economic value is textbook.** Nothing in this study's method inventory may be presented as a new metric; the novelty is in what the metrics are applied to and in what is bounded around them.

---

## 6. Consequences for the claim set

| Claim | Status before | Status after this pass |
|---|---|---|
| **C1** Global station-verified calibration of served PoP | Survives | **Materially narrowed.** A commercial benchmark does this at larger scale and now ships reliability diagrams (F1, F2). The defensible claim is the *public and reproducible* one — see below. |
| **C2** Provenance: served versus native ensemble probability | Survives, untouched | **Survives, strengthened.** Absent from 315 ForecastWatch documents; their own report title shows the question is live and unanswered (F6). |
| **C3** Physics versus ML probabilistic calibration | Survives, narrowed, time-limited | Untouched by this pass. |
| **C4** Forecast inequality | Scooped (Linsenmeier & Shrader) | Untouched by this pass. Now also bounded rather than open by E31. |
| **C5** Multi-model league table | Partially anticipated (WeatherBench 2) | **Do not lead with it.** 25 providers × 2,100 locations × 84 metrics × 600M forecasts is a larger league table, and it is a product. |
| **C6** Methods — economic value, seasonal weighting, representativeness | Survives | **Survives, re-worded.** The methods are standard (§5); the novelty is application plus bounded truth error. Supported from the other side: zero `cost-loss` and zero `economic value` in the commercial corpus (F6). |

**C1, restated so that it is true:**

> Brier scoring of served precipitation probabilities against station observations exists commercially at larger scale than this study. What does not exist is an **open, reproducible** verification that states whether its differences are resolvable, bounds the error in its own truth source, and reports what the probability is worth to someone acting on it.

Every clause there is a result already in the tree: E10 (the headline difference is unresolvable, and by how much), E20–E22 (truth error measured and propagated, the Brier claim failing re-siting where the decision claim survives at 13×), E4a and E5–E6 (decision value), E27/E30/E32 (the scope limit as a measured finding).

**Strategic consequence.** The positioning is no longer "nobody has measured this". It is: *the largest benchmark that measures it reports an accuracy percentage to the public while computing Brier privately, and even the private number cannot see the harm that the decision curve shows.* F4 is external evidence for D12 from the one organisation with the most data, and it is better than a citation because it is a market behaviour rather than an opinion.

---

## 7. What remains unverified

1. **Task 3b is still open** — C1–C6 against Google Scholar Labs, Web of Science, ECMWF eLibrary, AMS journals. Owner: user.
2. **WWRP/JWGFVR is behind a sign-in** (§4). Unsearched, not cleared.
3. **ForecastWatch's paid reports were not read.** Everything in §2 is from public marketing, release notes, news and FAQ text. The term counts are therefore a **lower bound** on what they do, not a census — and the direction of that bound matters: they may already do more of C1 than this records.
4. **No forward-citation chase** on WGNE guidance or the CAWCR portal; needs the same accounts as Task 3b.
5. The ForecastWatch corpus is a snapshot of 2026-09-21. The PoP-calibration feature is two months old, so this is a moving target and should be re-checked before submission.
