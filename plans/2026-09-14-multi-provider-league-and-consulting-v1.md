# Multi-Provider League Table, Plain-Language Provider Guide, and Consulting Section

## Objective

Extend the verification from two pinned models (ECMWF IFS, ICON-EU) to the full set of major forecast providers available through Open-Meteo, publish a cross-provider calibration league table, add a plain-language "who are these providers and what do they mean" explainer for non-expert readers, and add a small consulting-services section to the report. Every statistic remains computed in Python at render time.

## Current State Assessment

- `src/config.py:51-53` already defines `PRIMARY_MODEL = "ecmwf_ifs025"`, `COMPARATOR_MODEL = "icon_eu"`, and a `MODELS` list — the pipeline is parameterized by model, not hard-coded.
- `src/pop_provenance.py:57,221` already fetches and identifies `gfs_seamless`, `icon_d2`, and other candidate models, proving the Open-Meteo schema works for multiple models unchanged.
- `src/capitals.py:744` has `PIN_MODELS = ["icon_eu", "ecmwf_ifs025"]` — the pinned like-for-like run generalizes by extending this list.
- `run_all.sh:48-49` documents that the Track A leg already trips Open-Meteo's hourly rate limit; multi-provider collection must be rate-limit aware.
- The report renderer is section-based (`sec_*` functions in `src/report.py`); a provider guide and consulting section are additive sections, not refactors.

## Assumptions

- Providers are limited to models served by Open-Meteo in phase 1 (ECMWF IFS, GFS, ICON-EU, ICON-D2, Météo-France ARPEGE/AROME, UKMO, GEM, JMA — final set decided by archive availability probe).
- "Provider" is explained to readers as the organization whose model produces the number (ECMWF, NOAA, DWD, Météo-France, Met Office), with "model" as the specific run — the report must not use jargon without a glossary anchor.
- Consulting section is informational only (services, example deliverables, contact link); no backend, no forms.

## Implementation Plan

### Phase 1 — Multi-provider collection and metrics

- [x] Task 1. Extend `src/config.py` with a `PROVIDER_MODELS` registry: model id → display name, issuing organization, model description string, archive depth, and geographic domain (global vs. regional). Rationale: a single source of truth for both collection and the report's explainer.
- [x] Task 2. Probe archive availability per model per city (pattern: `probe_capitals.py`) before committing to the final model set; record which models have usable PoP archives at which lead times. Write results to a coverage JSON consumed by the report.
- [x] Task 3. Generalize `collect_archive.py` collection loop over `PROVIDER_MODELS` with polite spacing/backoff between models to stay under Open-Meteo's hourly limit; spread Track A collection across days if needed (cache makes this resumable).
- [x] Task 4. Extend `src/capitals.py` pinned-mode (`PIN_MODELS`) to run per-model verification for every city: BSS, ECE, reliability bins, wet bias, by lead time. Emit `capitals_pinned.parquet` with a model column (schema already close — add model dimension).
- [x] Task 5. Build the league table: per city, rank models by BSS (with bootstrap rank intervals, reusing the existing rank-interval machinery), and produce the "held-fixed" sanity check that rankings survive pinning (generalize `pop_provenance.py` verdict logic).
- [x] Task 6. Add robustness: recompute the league table under threshold sensitivity (0.1 / 0.2 / 1.0 mm) so no ranking claim depends on one threshold.

### Phase 2 — Plain-language provider guide (report section)

- [x] Task 7. New report section "Who makes these forecasts?" using the registry from Task 1: for each provider, one short paragraph in lay terms — who they are (e.g. ECMWF = a European intergovernmental center; NOAA/GFS = the US weather agency's global model), what makes each distinctive, and why forecasts differ between them. No jargon without a glossary anchor; reuse the existing 18-anchor glossary pattern.
- [x] Task 8. Explain the concepts readers need: what a "weather model" is, why different computers produce different rain probabilities for the same city, and what "verification" means. Include a small diagram (provider → model → app → your umbrella decision).
- [x] Task 9. Annotate every league-table row with the plain-language provider name and a link to the guide; add a one-line "how to read this table" caption (what BSS means in everyday terms, what higher means).
- [x] Task 10. Add a "which model is my weather app actually using?" callout — the pop-provenance finding that apps often don't disclose or silently switch providers is a reader-relevant hook.

### Phase 3 — Consulting section

- [x] Task 11. New report section "Work with us": 3–4 service cards grounded in what the project already demonstrates — (1) forecast verification & calibration audits for any prediction (weather, sales, churn, elections), (2) probability recalibration with honest out-of-sample evaluation (cite the isotonic finding as an example deliverable), (3) model benchmarking / provider selection studies, (4) verification infrastructure setup (reproducible pipelines like this one).
- [x] Task 12. Include one short case-study paragraph per card referencing actual project results (hourly reframing, out-of-sample isotonic null result, provenance audit) — evidence, not adjectives. Contact link (mailto or profile page), no forms.

### Phase 4 — Integration and QA

- [x] Task 13. Wire new steps into `run_all.sh` (probe → collect → pinned league → report) keeping the warm-cache no-network property.
- [x] Task 14. Verify: pipeline re-runs byte-identical on warm cache; all glossary anchors resolve; league table renders with rank intervals; rate-limit failures degrade gracefully to cached data with a visible notice.

## Verification Criteria

- League table shows ≥4 models per capital with per-model BSS, ECE, and rank intervals.
- A non-expert reader can, from the provider guide alone, explain what ECMWF vs. GFS means and why their rain probabilities differ (no undefined jargon in the section).
- Every consulting claim is backed by a concrete result from the project's own tables/figures.
- `./run_all.sh` completes end-to-end and is idempotent on warm cache.

## Potential Risks and Mitigations

1. **Open-Meteo rate limits during multi-model collection.**
   Mitigation: polite spacing, multi-day resumable collection via cache, degrade gracefully to cached subsets.
2. **Unequal archive depth across models biases rankings.**
   Mitigation: report sample sizes per model, compute rank intervals on matched windows, and show a matched-period-only variant.
3. **Composite/comparative claims invite scrutiny from providers.**
   Mitigation: publish methodology, threshold sensitivity, and the held-fixed check alongside every ranking; never rank on a single metric alone.
4. **Regional models (AROME, UKMO, ICON-EU) missing for non-European cities.**
   Mitigation: coverage probe decides per-city model sets; table marks unavailable models explicitly rather than hiding them.

## Alternative Approaches

1. **Direct ingestion from ECMWF/MERRA-2 instead of Open-Meteo**: full independence and deeper archives, but weeks of work and storage; deferred to phase 2 of the roadmap.
2. **Score-first (composite rain-check score) before providers**: faster to a "product," but a one-provider score is not sellable; providers first unlocks both the score and the API later.
3. **Consulting as a separate site**: keeps the report clean, but splits the credibility asset; an in-report section keeps evidence and offer on one page.
