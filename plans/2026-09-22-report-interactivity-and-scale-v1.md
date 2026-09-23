# Report Interactivity, Readability, Navigation, and 1000-City Scale

## Objective

Evolve the rain-check report (`dist/` site + standalone `report.html`) so that:
1. All multi-city graphs are dynamic, remain legible at ~1000 cities, and always highlight the currently selected city.
2. Non-critical sections fold away, making the report scannable.
3. Navigation is first-class: grouped, scroll-aware, and usable on mobile.
4. The verified city set grows from ~109 toward ~1000.
5. The report serves both professionals (methodology, metrics) and curious readers (plain-language layer) via progressive disclosure.

All changes must respect the project's two standing constraints: the front-end design rule "Python computes every coordinate, JavaScript only toggles a class" (`src/city_charts.py:15`), and the 410 KB first-load budget (`src/report.py:123`) whose comment instructs "move the content, not the number".

## Current-State Findings (sources)

- Three cross-city charts are already interactive SVG with per-city `data-city` groups and a `cc-top` highlight layer: reliability spaghetti, base-rate scatter, lead-MAE lines (`src/city_charts.py:91-234`). Selection highlighting already works for these.
- 27 figures remain static matplotlib PNGs, produced by `src/analyze.py:95-190`, `src/capitals.py`, `src/decision_metrics.py:504-629`, `src/events.py:248`, `src/hourly.py:125`. Several capitals PNGs duplicate the SVG charts (acknowledged in `src/city_charts.py:17-20`).
- Navigation is a flat 14-link rail (`src/report.py:1428-1433`, `src/report.py:1469-1471`) with no scrollspy, grouping, or mobile treatment.
- Only one section folds today: the dropped-places `<details>` (`src/report.py:1516-1544`).
- City count is bounded by selection budget, not coverage: pool of 8,150 usable cities; caps `MAX_CITIES = 300` (`src/probe_cities.py:73`), `MAX_PER_COUNTRY = 8` (`src/probe_cities.py:78`), `MIN_POPULATION = 100_000` (`src/probe_cities.py:82`). Fetch cost ~6 calls / ~2 s per city (`src/probe_cities.py:70-72`).
- Per-city data ships as a packed column table inside the eager `site-config` JSON (`src/report.py:1695-1763`) — this grows linearly with city count and presses on the first-load budget.
- Meta description says "109 European capitals", which is wrong (set is worldwide) — `report.html:6`.

## Assumptions

- "Dynamic" means interactive within the existing zero-dependency SVG + class-toggle architecture, not adopting Plotly/ECharts (which would break the byte budget and the project's stated design rule).
- Target scale is ~1000 cities in one probe/verify cycle; the API throttle makes this an hours-long batch job, which is acceptable for `run_all.sh`.
- The standalone `report.html` build stays supported but may carry a reduced interactive surface (its base64 path already differs).

## Implementation Plan

### Phase 1 — Chart scalability groundwork (must precede city expansion)

- [ ] Task 1. Introduce a density-plus-overlay rendering mode in `src/city_charts.py` for line charts: Python precomputes quantile ribbons (5–95 and 25–75 percentile bands plus the median polyline) across all cities, and ships only the ribbons, the median, and a byte-bounded set of individual polylines. Rationale: 1000 polylines is both visual mud and ~hundreds of KB; ribbons convey the population shape at constant byte cost regardless of city count.
- [ ] Task 2. Add per-city chart coordinates to the existing per-city JSON payloads (`dist/data/cities/`, built in `src/city_report.py`): each city's 5-bin reliability points, lead-MAE points, and scatter position. On selection, JS draws that one city's geometry into the `cc-top` group from data it already fetched. Rationale: the selected-city highlight then works even when the city's line is not among the eagerly shipped set — Python still computed every coordinate; JS only inserts precomputed points.
- [ ] Task 3. Define the eager-line selection policy in Python: always include the default city (Bucharest), the best/worst deciles, and a stratified sample (by precipitation regime or continent) up to a fixed byte allowance; annotate the chart caption with "showing N of M cities; select any city to add it". Rationale: keeps the spaghetti informative and the page within budget at any city count.
- [ ] Task 4. Scale the base-rate scatter for 1000 points: shrink dot radius, add Python-computed density shading (e.g., precomputed 2D-bin background rects) beneath the dots, keep one `data-city` circle per city (dots are cheap: ~70 bytes each), and raise the selected dot with a labelled callout into `cc-top`. Rationale: a scatter degrades gracefully at 1000 points if the selected city is unmistakable.
- [ ] Task 5. Extend the delegated tooltip and highlight logic in `src/web/app.js` to cover the new ribbon/overlay charts and ensure highlight-follows-selection is applied on initial load, on palette selection, and on globe clicks. Rationale: single code path for all charts avoids divergent behaviour.

### Phase 2 — Retire static multi-city PNGs

- [ ] Task 6. Remove the capitals/cities PNG figures that duplicate the interactive SVGs (`capitals_reliability.png`, `capitals_baserate.png`, `capitals_lead_mae.png`, and the `cities_*` equivalents from `src/capitals.py`), keeping matplotlib output only as the `<noscript>` fallback path that `lazy_chart` (`src/report.py:133-169`) already expects. Rationale: `src/city_charts.py:17-20` warns the pair must not drift; one canonical source removes the risk.
- [ ] Task 7. Convert the remaining reader-facing analysis PNGs to the interactive SVG pattern where multi-city or per-city selection matters: decision-metrics figures (`economic_value.png`, `value_by_lead.png`, `bss_reference.png`, `discrimination_sharpness.png` from `src/decision_metrics.py:504-629`) gain a selected-city overlay; Bucharest-only figures (`src/analyze.py`, `src/events.py`, `src/hourly.py`) may stay static since they are fenced single-city content by design (`src/report.py:16-27`). Rationale: convert where selection adds meaning; do not churn fenced content.
- [ ] Task 8. Add hover tooltips (city, value, n days) to every converted chart via the existing `data-tip` delegation, and verify keyboard/AT access (role, aria-label per `_open`, `src/city_charts.py:55-57`). Rationale: parity with the current charts' accessibility posture.

### Phase 3 — Readability: folding and the two-audience layer

- [ ] Task 9. Classify the 14 sections into tiers: Core (Verdict, Calibration, the globe/city panel), Analysis (Seasons, Scorecard, Hourly, Capitals, League), Methods (Providers, Served vs raw, Provenance, Limits), Reference (Frost & heat, Work with us, Glossary). Render Methods and Reference tiers inside `<details class="fold">` blocks, following the pattern already proven by `dropped_block` (`src/report.py:1516-1544`), open-able via URL hash so deep links still land correctly. Rationale: keeps everything on the page (honesty requirement) while shortening the default scroll dramatically.
- [ ] Task 10. Lazy-load the folded sections' heavy content: folded tier bodies become hashed HTML fragments fetched on first open (same mechanism as `lazy_chart`), with full content inlined in the `<noscript>`/standalone paths. Rationale: this is the "move the content" lever the budget comment demands, and it funds the byte cost of Phase 1's richer charts and Phase 5's larger city table.
- [ ] Task 11. Add a per-section one-line plain-language summary ("What this section shows") rendered above each section body, and a "For practitioners" `<details>` at the end of technical sections holding the metric definitions, formulas, and caveats currently interleaved with the prose. Rationale: this is the dual-audience mechanism — curious readers read the summaries and skip the folds; professionals open them.
- [ ] Task 12. Add a short "How to read this report" primer near the hero (3–5 sentences: what a calibration curve is, what skill score means, what the reader can conclude), linking terms to the glossary anchors. Rationale: the theory on-ramp the curious audience currently lacks.

### Phase 4 — Navigation

- [ ] Task 13. Restructure `NAV` (`src/report.py:1428-1433`) into the tier groups from Task 9, rendering group headings in the rail, and add scrollspy: an IntersectionObserver in `src/web/app.js` toggles an `active` class on the rail link for the section in view. Rationale: 14 flat links give no sense of place; grouping + scrollspy is the cheapest strong fix and stays within "JS toggles a class".
- [ ] Task 14. Extend the existing command palette (`src/web/app.js`) to also match section names alongside cities, jumping to (and auto-unfolding) the section on selection. Rationale: reuses a component readers already have; one search box for "go anywhere".
- [ ] Task 15. Add a mobile navigation affordance: a compact section menu (collapsed rail behind a topbar button) plus a reading-progress indicator; ensure every section heading is an anchor with a visible copy-link affordance. Rationale: the rail is desktop-only today; anchors make sections shareable, which matters for professional citation.

### Phase 5 — Scale to ~1000 cities

- [ ] Task 16. Raise the probe budget in `src/probe_cities.py`: `MAX_CITIES` 300 → ~1200 selected (yielding ~1000 verified after elevation/verification attrition, based on the current 300→109 ratio needing review — measure the actual attrition causes first), `MAX_PER_COUNTRY` 8 → ~25, and consider `MIN_POPULATION` 100k → 50k only if country diversity stalls. Keep the one-city-per-gauge and 30 km separation rules unchanged. Rationale: the pool (8,150) supports this; the caps are the only binding constraint, and the dedupe rules are what keep the sample statistically honest (`src/probe_cities.py:16-27`).
- [ ] Task 17. Audit pipeline cost and robustness at 1000 cities before running: fetch time (~6 calls × ~2 s ≈ 2 h of API time — acceptable but add resumability/checkpointing to the city fetch stage), GHCN bulk memory, and stage runtimes in `run_all.sh`; add a partial-failure policy so one city's fetch error does not abort the batch. Rationale: a 10× batch surfaces failure modes a 300-city run never hit.
- [ ] Task 18. Move the packed city table out of the eager `site-config` JSON (`src/report.py:1695-1763`) into a lazily fetched hashed asset (loaded when the palette or globe first needs it), keeping only the selected city + defaults inline. Rationale: at 1000 cities the inline table alone would likely breach the 410 KB budget.
- [ ] Task 19. Verify downstream surfaces at 1000 cities: globe dot clustering/`+n` badges (`src/report.py:1497-1500`), palette search responsiveness, league-table length (add client-side sort and a "top/bottom N + find your city" default view with full table folded), and per-city page count in `dist/` (~1000 pages — check build time and `check_site.py` still passes). Rationale: every list-of-cities surface must degrade gracefully, not just the charts.
- [ ] Task 20. Re-run the full pipeline and confirm statistical framing still holds: per-country cap changes alter the sample composition, so significance/FDR (stage 15) and equity analyses must be regenerated, and any prose citing "109 cities" or country counts updated — including the wrong meta description at `report.html:6`. Rationale: the report's claims are sample-dependent; expanding the sample without re-verifying the claims would be the fabrication the codebase explicitly guards against.

### Phase 6 — Cross-cutting polish (the "anything else")

- [ ] Task 21. Fix metadata and SEO: correct meta description, add per-city page titles/descriptions with the city's headline metric, and structured-data breadcrumbs. Rationale: 1000 city pages are a discoverability asset only if indexed correctly.
- [ ] Task 22. Add a city-comparison affordance: allow pinning a second city so both are highlighted on the interactive charts (two `cc-top` slots, two colours). Rationale: "is my city better than X" is the most natural professional and lay question the current single-selection model cannot answer; the per-city JSON from Task 2 makes it nearly free.
- [ ] Task 23. Keep the budget honest: after Phases 1–5, re-measure first load against the 410 KB gate (`src/report.py:1808-1812`); if over, extend Task 10's lazy fragments rather than raising the number, per the standing instruction at `src/report.py:112-123`. Update `check_site.py` with assertions for: scrollspy markup, folded-section hash-open behaviour, lazy city table, and selected-city overlay presence.
- [ ] Task 24. Decide the standalone `report.html` policy: either (a) keep it feature-reduced (static fallbacks, no lazy fragments — everything inline as today) and document that, or (b) drop it if `dist/` is the sole published artifact. Rationale: every lazy-loading feature added in Phases 1–5 forks the standalone path; an explicit policy prevents silent rot.

## Verification Criteria

- Every multi-city chart highlights the selected city within one interaction, at both 109 and a simulated 1000-city dataset, with no chart exceeding a fixed byte allowance.
- No reader-facing multi-city matplotlib PNG remains except as `<noscript>` fallback.
- Default page scroll length shrinks measurably (Methods/Reference tiers folded); deep links to folded sections auto-open them.
- Rail shows grouped sections with a visibly active current section while scrolling; palette finds both cities and sections.
- `probe_cities.py` output reports ≥ ~1000 selected cities; full pipeline completes; `check_site.py` passes; first load ≤ 410 KB.
- A non-specialist can state what the report concludes after reading only the hero, primer, and section summaries (spot-check with a fresh reader).

## Potential Risks and Mitigations

1. **Byte budget breach from richer charts + larger city table**
   Mitigation: ribbons-not-lines rendering (Task 1), lazy city table (Task 18), lazy folded fragments (Task 10); re-measure at the existing build gate.
2. **10× pipeline run exposes API throttling / partial failures**
   Mitigation: resumable fetch with checkpointing and per-city error isolation (Task 17) before launching the full batch; the sha256 disk cache (`src/fetch.py`) already makes re-runs cheap.
3. **Sample-composition change invalidates published claims**
   Mitigation: Task 20 mandates re-running significance/FDR and rewriting sample-dependent prose; treat the 1000-city run as a new edition, not a patch.
4. **Folding hides content search engines / no-JS readers need**
   Mitigation: `<details>` content remains in the DOM for the eager tiers; lazy fragments retain `<noscript>` inline fallback, matching the site's existing no-JS discipline.
5. **Standalone build divergence**
   Mitigation: explicit policy decision (Task 24) plus a build-time check that both modes render every section.

## Alternative Approaches

1. **Adopt a chart library (Plotly/ECharts/uPlot) with client-side rendering**: faster to build zoom/pan/filter UX, but violates the dependency-free design rule, adds 100+ KB against a 410 KB budget, and moves computation into the browser where the project deliberately keeps it out. Rejected as the default; uPlot (~10 KB) is the only viable fallback if hand-rolled SVG proves unmaintainable.
2. **Canvas rendering for the 1000-line spaghetti**: constant byte cost and fast paint, but loses per-element DOM highlighting/tooltips and the no-JS SVG fallback. The ribbon+overlay approach achieves the same legibility without the losses.
3. **Grow cities incrementally (300 → 500 → 1000)**: lower risk per run and validates pipeline scaling early, at the cost of multiple full re-verifications and repeated prose rewrites. Worth choosing if Task 17's audit finds serious scaling risk.
4. **Two separate reports (lay summary page + technical report)** instead of folding one report: cleaner audience split but duplicates maintenance and breaks the single-URL story; progressive disclosure in one document (Tasks 9–12) is preferred.
