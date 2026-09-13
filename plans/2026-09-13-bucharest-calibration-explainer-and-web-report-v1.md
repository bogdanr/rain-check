# Bucharest Calibration — Plain-Language Explainer, Web Report, and Analysis Improvements

## STATUS: COMPLETE (2026-09-13)

All three deliverables built. `./run_all.sh` regenerates everything including
`report.html`. Verification: pipeline passes, output byte-identical on re-run,
no external resource references, Brier identity error exactly 0, all 18 glossary
anchors resolve.

### Findings that changed the conclusion

1. **Out-of-sample recalibration gains 0.16% — essentially nothing.** The
   in-sample isotonic mapping looked valuable; with time-blocked CV it is not.
   A correction learned from 731 days is itself too noisy to pay for itself.
2. **But the miscalibration is real.** Consistency bars show 2 of 6 bins fall
   outside what a perfect forecast would produce: the "0%" bin (rains 5%) and
   the "99%" bin (rains 82%). So overconfidence at the extremes is genuine,
   *and* not worth correcting yet. Both facts are reported.
3. **Seasonal split contradicted the stated expectation.** The plan predicted
   summer would be worst (convective rain). It is not — spring is (BSS 0.30 vs
   winter 0.50). Summer does have the weakest resolution, which is the
   convective signal, but its lower rain rate flatters its overall score.
4. **Representativeness floor is 7.4%, not the 6.6% previously quoted.** Now
   computed at render time from the two stations rather than carried as a
   constant.

Deferred: Tasks 18–26 improvements beyond the seasonal split and consistency
bars are documented in the report's roadmap but not implemented.

## ADDENDUM: hourly track (Task 19 / roadmap rank 1) — COMPLETE

Implemented after the above. `src/observations_hourly.py`, `src/hourly.py`,
pipeline now 8 steps.

**The feasibility gate failed as specified, and the task was reframed rather
than abandoned.** Bucharest stations do not report hourly precipitation
*amounts*: Filaret gives only 6/12-hour accumulations, the airports mostly
24-hour or missing — a full year of Baneasa data contained one hourly-period
record. What they do report is present weather (WMO 4677) roughly hourly,
covering 85% of hours, including explicit "no significant weather" codes. So the
hourly track verifies precipitation *occurrence*, which is arguably the better
match: PoP is a probability of an event, not of an amount.

Sample rose from 731 days to **10,696 hours** (effective n 363 → 1665).

### The headline changed

| | Daily | Hourly |
|---|---|---|
| Skill score | 0.409 | **0.467** |
| Average error (ECE) | 6.5% | **2.5%** |
| "Almost certain" really means | 82% | **97%** |

At hourly resolution the confident end is **honest** — 98% forecasts see
precipitation 97% of the time, inside the perfect-forecast range. The daily
"overconfidence at the top end" was therefore largely an artefact of the
0.2 mm threshold, not dishonesty. This is consistent with the existing
threshold sensitivity (top bin: 84% at 0.1 mm, 72% at 1.0 mm).

Caveat recorded on the page: the two tracks differ in event, station AND
measurement type simultaneously, so this is not a clean isolation of timescale.

The low end still misses at both timescales: hours forecast at 0.5% see
precipitation 2.4% of the time, and 13% hours see it 27% of the time. **Low
probabilities are understated at every timescale tested.**

### Second finding: observing practice, not weather

Detection rate tracks reporting frequency, not distance from the city:

| Station | Distance | Reports/hour | Hours with precipitation |
|---|---|---|---|
| Filaret | 1.1 km | 1.0 | 5.7% |
| Baneasa | 8.5 km | 1.3 | 10.2% |
| Henri Coanda | 16.1 km | 1.3 | 10.5% |

The *closest* station reports the *least* rain, because it files the fewest
reports and misses brief showers between them. This is a property of the
observer and sets a floor on measurement precision.

### Methodological notes

- Both sides are UTC-timestamped, so the hourly join needs no local-day
  conversion — removing the entire class of off-by-one error that corrupted the
  daily track. A shift scan asserts this rather than assuming it (peaks at 0).
- Equal-count binning **collapsed to 3 bins** because 77% of hours carry PoP
  exactly 0. The hourly track uses equal-WIDTH bins; `equal_count` is now
  threaded through `brier_decomposition` and `consistency_bars`.
- ISD station ids do not follow guessable WMO numbering (154220 is Filaret,
  154200 is Baneasa not Otopeni); resolved from the ISD history file.
- ISD data ends 2025-08-24, so the hourly window is shorter than the daily one.

## Objective

Make the existing calibration study understandable and shareable without prior
meteorology or statistics knowledge, and fix one methodological weakness found
while reviewing it. Three deliverables:

1. A **glossary/explainer** that defines every term used and — critically —
   states for each metric what counts as a good, mediocre, and bad value, with
   the study's own value placed on that scale.
2. A **self-contained HTML report** (`report.html`, no server, no CDN) that
   visually summarises the findings for a non-technical reader.
3. A **ranked list of concrete analysis improvements**, with the highest-value
   ones implemented.

### Correction this work must carry

The previously reported headline "**Brier score of 0.409**" was mislabelled.
`0.409` is the Brier **Skill** Score (higher is better, 1.0 is perfect). The
Brier **score** is `0.1171` (lower is better, 0.0 is perfect). The two run in
opposite directions. Every artefact produced here must name them distinctly and
never abbreviate both to "Brier score". This is precisely the confusion the
glossary exists to prevent, so it doubles as the explainer's worked example.

### Assumptions made

- No new data collection is required; the report reads existing artefacts in
  `data/processed/` and `figures/`.
- The page must work offline from `file://` — so no external JS/CSS, and images
  are base64-embedded rather than linked. This makes it trivially shareable as
  a single file.
- All numbers on the page are computed at render time from the parquet tables,
  never hardcoded, so the page cannot drift from the data.
- Qualitative bands ("good"/"poor") for metrics are conventions, not laws. They
  must be labelled on the page as rules of thumb with their reasoning shown,
  not presented as authoritative thresholds.

---

## Implementation Plan

### Phase 1 — Fix the in-sample recalibration weakness

- [ ] Task 1. Add `cross_validated_recalibration(dates, prob, event, n_folds)` to
      `src/calibration.py:163-168`, alongside the existing
      `isotonic_recalibration`. It must split into **contiguous time blocks**,
      not random folds, fit isotonic regression on the training folds, and
      predict on the held-out fold. Rationale: the current Task 20 recalibration
      is fitted and scored on the same days. Isotonic regression is flexible
      enough to absorb noise, so its in-sample improvement is guaranteed and
      therefore meaningless. Random folds would additionally leak information
      across the split, because adjacent days share a weather regime. Return
      raw vs out-of-sample Brier and BSS plus the delta.
- [ ] Task 2. Report the out-of-sample figure in `src/analyze.py:165-170` next to
      the existing stated-vs-calibrated mapping table, and label the in-sample
      mapping explicitly as a descriptive summary of miscalibration rather than
      a measured improvement. Rationale: the mapping is still the clearest
      one-line statement of *how* the forecast is biased; only the claimed
      *gain* was overstated.
- [ ] Task 3. Treat the size of the out-of-sample gain as a headline result in
      its own right. Rationale: if recalibration buys very little out of sample,
      that is strong evidence the forecast is already well calibrated and the
      visible S-shape is largely sampling noise — a genuinely different
      conclusion from "overconfident at the extremes", and the analysis should
      be able to distinguish them.

### Phase 2 — Glossary content as structured data

- [ ] Task 4. Create `src/report_content.py` holding the glossary as structured
      Python data, not prose embedded in HTML. Each entry: short name, full
      name, one-sentence plain definition, a "why you should care" note,
      direction (higher/lower is better), value range, qualitative bands with
      cutoffs, and reference anchors. Rationale: structuring it lets the page
      render a consistent visual scale per metric automatically, and keeps the
      wording reviewable in one place rather than scattered through markup.
- [ ] Task 5. Cover at minimum: PoP, calibration/reliability diagram, base rate
      (climatology), Brier score, Brier Skill Score, reliability term,
      resolution term, uncertainty term, ECE, sharpness, skill vs calibration,
      lead time, MAE, bias, block bootstrap, effective sample size, isotonic
      recalibration, representativeness, reanalysis/ERA5, ensemble.
      Rationale: these are exactly the terms that appear in the current outputs;
      any term on the page without an entry is a gap.
- [ ] Task 6. For each metric give at least three reference anchors that make
      the number interpretable — typically perfect, this study's value, and a
      "no better than guessing the long-run average" point. Rationale: a bare
      number like 0.117 is uninterpretable; a number shown between 0.0 (perfect)
      and 0.198 (climatology) answers the user's question directly.
- [ ] Task 7. Encode the Brier-score caveat that it is **not comparable across
      locations** — a desert where it rains 2% of days scores a low Brier score
      trivially. Rationale: this is the single most common misreading of the
      metric and the reason BSS exists; omitting it would let a reader draw a
      false conclusion from a correct number.

### Phase 3 — HTML report generator

- [ ] Task 8. Create `src/report_style.py` with the CSS as a string constant.
      Rationale: keeps `report.py` focused on data and structure, and previous
      sessions showed large single-file writes timing out — smaller modules are
      more reliable to author.
- [ ] Task 9. Create `src/report.py` which loads the verification tables,
      recomputes every metric via the existing `calibration.py` functions, and
      emits a single self-contained `report.html` at the project root.
      Rationale: reusing the analysis functions rather than re-implementing
      guarantees the page and the printed analysis can never disagree.
- [ ] Task 10. Lead the page with a plain-language verdict section that answers
      "is the Bucharest forecast trustworthy?" in one short paragraph plus a
      practical lookup table (stated PoP to what it actually means). Rationale:
      this is the only part most readers will read; the technical detail should
      be reachable but not blocking.
- [ ] Task 11. Render a "metric scorecard": one row per metric showing name,
      value, a horizontal scale with qualitative bands shaded, a marker for this
      study's value, and reference anchors. Rationale: directly answers "is
      0.409 good?" visually, for every metric at once, which prose cannot do
      compactly.
- [ ] Task 12. Render the calibration curve as **inline SVG generated from the
      reliability table**, with hover tooltips giving bin range, sample count,
      stated probability and observed frequency. Rationale: the PNG cannot show
      per-bin detail without clutter; SVG scales crisply and makes sample size
      inspectable, which is the main thing that qualifies the curve.
- [ ] Task 13. Embed the existing matplotlib PNGs (`figures/reliability_pop.png`,
      `figures/lead_diagnostics.png`) as base64 in a secondary "full technical
      figures" section. Rationale: preserves the rigorous versions without
      requiring the reader to parse them first, and keeps the file portable.
- [ ] Task 14. Include the robustness results as a table with an explicit
      "did the conclusion hold?" column. Rationale: robustness is the strongest
      part of the current result and is currently invisible outside the console.
- [ ] Task 15. Include a limitations section in plain language, covering the
      single-lead-time restriction, the 6.6% station-to-station
      representativeness floor, and effective n of ~363 versus raw n of 731.
      Rationale: the credibility of the headline depends on these being stated
      up front rather than discovered later.
- [ ] Task 16. Add the glossary as the final section, with anchor links so any
      jargon term used earlier in the page links directly to its definition.
      Rationale: lets a reader stay in the narrative and dip into definitions on
      demand, which is what makes the page usable for learning.
- [ ] Task 17. Add `report.py` as a final step in `run_all.sh:21-23` so the page
      regenerates with every full run. Rationale: an artefact that must be
      remembered separately will go stale.

### Phase 4 — Analysis improvements (ranked by value per unit effort)

- [ ] Task 18. **Seasonal and regime-conditional calibration for PoP.** Split
      the reliability diagram by season. Rationale: highest value for lowest
      effort — the data already exists, and summer convective rain is expected
      to be far less well calibrated than winter frontal rain. An annual average
      can hide two opposite errors, exactly as already observed for temperature
      bias.
- [ ] Task 19. **Hourly rather than daily verification.** Verify hourly PoP
      against hourly observations instead of collapsing to "any rain today".
      Rationale: multiplies sample size roughly 24-fold, which is the binding
      constraint on every error bar in the study, and answers the more useful
      question "will it rain at 6pm".
- [ ] Task 20. **Consistency bars on the reliability diagram.** Add resampling
      bars showing the deviation expected *under perfect calibration* given the
      bin's sample size. Rationale: currently a reader cannot tell whether the
      S-shape is real miscalibration or sampling noise; consistency bars answer
      that directly and are the standard companion to a reliability diagram.
- [ ] Task 21. **A formal calibration test.** Add a significance test for the
      null of perfect calibration. Rationale: converts "looks overconfident at
      the extremes" into a statement with a stated error rate.
- [ ] Task 22. **More events beyond rain.** Frost tonight, heatwave, high wind,
      snow. Rationale: reuses the whole pipeline for near-zero marginal cost and
      covers the forecasts people actually act on.
- [ ] Task 23. **Additional stations and neighbourhood verification.** Rationale:
      separates "the forecast was wrong" from "the rain missed the gauge",
      which is currently an acknowledged but unquantified confound beyond the
      two-station spread.
- [ ] Task 24. **Log a consumer-facing provider** alongside the model. Rationale:
      answers the question most people actually mean — "is the forecast on my
      phone honest?" — including any deliberate wet bias, which a raw model
      will not show.
- [ ] Task 25. **Extend the temperature record using GFS back to March 2021.**
      Rationale: triples the temperature sample at no analytical cost, enabling
      year-to-year comparison.
- [ ] Task 26. **Continue the daily logger to unlock per-lead reliability
      curves** (the deferred Task 17 of the original plan). Rationale: the single
      most valuable missing result — "how far ahead can I trust a probability" —
      but it is gated on elapsed time, not effort, so it stays last.

---

## Verification Criteria

- `report.html` opens correctly from `file://` with no network access, and
  contains no external resource references.
- Every numeric value on the page is traceable to a computation at render time;
  grepping the source for hardcoded result numbers returns nothing.
- The page distinguishes Brier score (0.1171, lower better) from Brier Skill
  Score (0.409, higher better) in separate scorecard rows with opposite
  direction indicators.
- Every jargon term appearing in the narrative has a glossary entry and an
  anchor link resolving to it.
- Each metric scorecard row shows at least three reference anchors, so no value
  is presented without context.
- Cross-validated recalibration reports an out-of-sample Brier that is stated
  separately from the in-sample mapping, and folds are verifiably contiguous in
  time.
- `./run_all.sh` regenerates the page end to end, and re-running produces an
  identical file given identical inputs.
- The existing Brier identity self-check still returns exactly zero after the
  `calibration.py` changes.

## Potential Risks and Mitigations

1. **Qualitative bands presented as authoritative.** Cutoffs like "ECE below 5
   percentage points is excellent" are conventions.
   Mitigation: label them as rules of thumb on the page, show the reasoning
   behind each cutoff, and always display the raw value alongside the band.
2. **Simplification introducing errors.** Plain-language rewording can quietly
   change a claim's meaning.
   Mitigation: keep the technical figures and exact numbers on the same page;
   have each plain statement sit adjacent to the number it summarises.
3. **Cross-validated recalibration showing near-zero gain, contradicting the
   "overconfident at the extremes" headline.**
   Mitigation: this is a finding, not a failure. If it occurs, revise the
   headline to reflect it rather than suppressing it, and use Task 20's
   consistency bars to determine whether the S-shape was ever significant.
4. **Large file writes timing out**, as happened repeatedly in the previous
   session around 7 KB.
   Mitigation: split into `report_content.py`, `report_style.py` and
   `report.py`, keeping each write under roughly 5 KB.
5. **Page drifting out of date as data accrues.**
   Mitigation: wire into `run_all.sh` and stamp the page with generation date,
   data window, and sample size.
6. **Overstating certainty to a non-expert audience.** A polished page reads as
   more authoritative than the sample supports.
   Mitigation: put effective sample size and the representativeness floor in the
   summary section, not only in limitations.

## Alternative Approaches

1. **Jupyter notebook instead of a generated page.** Cheaper to build and
   naturally interleaves code with narrative, but requires a Python environment
   to view and mixes implementation detail into a document aimed at a
   non-technical reader.
2. **Static site generator plus Plotly.** Richer interactivity out of the box,
   at the cost of a heavy dependency, external CDN assets, and losing the
   single-file portability that makes the report trivially shareable.
3. **Markdown report rendered to PDF.** Simplest and most portable of all, but
   forfeits hover detail on the calibration curve, which is where per-bin
   sample size becomes legible.
4. **Glossary as a separate document.** Keeps the report short, but splits the
   definition away from the number it explains — the opposite of what was asked
   for.

---

## Follow-on: roadmap rank 3 (more event types), completed 2026-09-13

Implemented in `src/events.py`, wired in as pipeline step 7 and rendered as the
"Frost, heat and rain" section of the report.

**Approach.** The archive attaches a probability to nothing but rain, so
probabilities for frost and heat were *derived* from the deterministic forecast
by fitting P(event | forecast value) on contiguous time-blocked folds. Every
scored day was predicted by a model that never saw it.

**What the output can and cannot claim.** Reliability is not evidence here — the
mapping is fitted to be calibrated. Resolution and skill cannot be manufactured,
and those are the reported findings.

**Result.** Temperature events remain skilful far longer than rain:

| Event | base rate | BSS day 1 | BSS day 7 |
|---|---|---|---|
| Hot day (>= 32 C) | 13% | 0.84 | 0.66 |
| Frost (< 0 C) | 25% | 0.70 | 0.56 |
| Extreme heat (>= 35 C) | 6% | 0.70 | 0.48 |
| Rain (>= 0.2 mm) | 27% | 0.46 | 0.12 |

This partially discharges rank 2 (per-lead curves) for *derived* probabilities;
the published-PoP version remains gated on the logger.

### Three errors caught by the study's own checks

1. **Bin-count heuristic was wrong.** Tying bin count to the positive count
   collapsed heatwave to 3 equal-count bins, understating its resolution more
   than fivefold (0.0070 vs 0.0394) because quantile edges pile up when 80% of
   probabilities sit near zero. Switched to equal-width bins and added a
   permanent `MAX_BINNING_LOSS` assertion comparing binned to raw Brier — the
   general detector for binning destroying the decomposition.
2. **Logistic link was misspecified.** It left rain's reliability at 0.0064 with
   3 of 6 solid bins outside their consistency bars. An isotonic fit gives
   0.0008, 0 of 5 outside, and a better Brier score. Isotonic is now primary,
   with logistic retained as a published sensitivity (max skill gap 0.063, so no
   conclusion depends on the choice).
3. **Figure contradicted its own caption.** The reliability panel zigzagged
   wildly while the caption asserted "near-diagonal is expected", because it
   plotted sparse bins excluded from every numerical claim. Only visible by
   rendering the page; text extraction could not have caught it.

### Residual, explained rather than hidden

17% of well-populated bins fall outside their consistency bars against ~5%
expected. Cause established by measurement, not assertion: fitting the same
mapping in-sample puts **zero** bins outside for every event, so the excess is
entirely generalisation error — a mapping learned on one stretch of time not
transferring perfectly to another. This is what out-of-sample scoring exists to
reveal.

### Not done

Wind and snow events: neither variable is in the collected forecast set nor in
the daily station record, so they were dropped rather than faked.

