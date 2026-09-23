# Cross-City Chart Redesign

## Objective

Make the three interactive cross-city charts (reliability, base-rate scatter, lead-time error in `src/city_charts.py`) readable, engaging and self-explanatory for both professionals and curious readers, using space well, and provably legible at 1,000 cities. Keep the site's core principle: Python computes every coordinate, JavaScript only toggles classes / injects precomputed geometry.

## Design principles

- The population is the ribbon; individual lines are annotation, not data. Kill the spaghetti.
- Every chart answers one question in its title, states it in a computed headline, and labels its parts inside the plot.
- One consistent palette: population = cool neutral blue; median = strong blue; selected city = accent (orange); pinned comparison = second hue (teal); reference lines = dashed ink. All via CSS variables so dark mode keeps working.
- Charts fill the content column and keep a fixed aspect via viewBox; no more 560 px postage stamps.

## Implementation Plan

### Phase A - Chart design system (canvas, type, color, annotation)

- [ ] A1. Widen the chart canvas: viewBox ~ 860x430 (reliability can stay squarer, ~640x560 side-by-side with its companion chart on wide screens), `max-width` raised to the content width in `app.css`, font sizes rescaled (axis 12-13, labels 14, annotations 13). Rationale: fixes the poor-use-of-space complaint at its root.
- [ ] A2. Introduce chart color tokens in `app.css` (`--ch-pop`, `--ch-median`, `--ch-sel`, `--ch-cmp`, `--ch-anno`) with dark-mode variants; ribbons become a soft blue, median a saturated blue, selected city the site accent. Rationale: the gray-on-gray boredom is a palette problem, not a data problem.
- [ ] A3. Add an in-plot annotation helper in `city_charts.py`: short text callouts with leader lines, region labels (e.g. "forecast too optimistic" / "too cautious" either side of the reliability diagonal; "no better than climatology" under the zero-skill line), and a drawn-in ribbon key ("middle 50% / 80% of cities") placed inside the plot instead of a legend. Rationale: makes each chart self-explanatory for the curious reader.
- [ ] A4. Give every chart a computed headline takeaway rendered above it (e.g. "Below 30%, stated rain chances run ~N points too low in most cities"), derived in Python from the same tables, so the professional gets the number and the casual reader gets the sentence. Rationale: charts should state their finding, not just display data.

### Phase B - Replace encodings that don't scale

- [ ] B1. Reliability chart: remove the 150-line eager spaghetti entirely. Draw ribbons + median + ideal + hovered/selected/pinned city curves only (selected/pinned already arrive via per-city overlays; hover uses the same overlay for eager cities or none). Rationale: the sample lines are pure noise at any count; the byte budget freed pays for everything else in this plan.
- [ ] B2. Add a companion "calibration fingerprint" scatter: one dot per city positioned by low-end bias vs high-end bias (or reliability slope vs intercept), computed in Python from the same 5-bin tables. Every city stays individually visible, hoverable and selectable at 1,000+ cities; label the 4-6 most extreme outliers with their names. Rationale: restores the per-city visibility the spaghetti pretended to give, in a form that scales.
- [ ] B3. Base-rate scatter: replace the gray-rect density layer with dot styling that scales (smaller radius + opacity by count, or keep the histogram but as rounded soft-blue cells), color dots by region/continent, and label named outliers (top/bottom 3 by skill) with leader lines. Rationale: turns a smear into a map of who is where.
- [ ] B4. Lead-time chart: ribbons + median + selected/pinned only (drop eager sample lines as in B1); add a per-lead annotation of the median value at day 1 and the last day ("1.4 C at day 1 -> 2.6 C at day 7"). Rationale: the question this chart answers is about the shape, not 150 individual cities.
- [ ] B5. Add a "where is my city" readout under each chart: one computed sentence per city shipped in its JSON payload (e.g. "Bucharest: day-1 error 1.4 C - better than 71% of cities"), injected on selection. Rationale: the highlight shows where the city is; the percentile says what that means.

### Phase C - Interaction upgrades

- [ ] C1. Click-to-select on chart geometry: clicking a dot/curve selects that city site-wide (the groups already carry `data-city` and `cursor: pointer`; wire the click in `app.js`). Rationale: the chart becomes a navigation surface, not just a display.
- [ ] C2. Region filter chips above the scatter charts (Python assigns each city group a `data-region` class; JS toggles a class on the SVG to dim non-matching regions - no recomputation). Rationale: the main de-crowding gesture at 1,000 cities that stays within the class-toggle architecture.
- [ ] C3. Hover behaviour: raise hovered geometry with name label drawn at the curve end (not only the tooltip), consistent across all charts. Rationale: tooltips are invisible in screenshots and on touch; end-labels are not.

### Phase D - Layout and space

- [ ] D1. Full-width chart blocks: chart on the left, computed takeaway bullets + the B5 readout on the right at wide viewports, stacking on mobile. Rationale: uses the empty margins the 560 px charts currently waste.
- [ ] D2. Normalize the remaining static PNG figures' display widths to the same grid so the page stops alternating between narrow SVGs and wide PNGs. Rationale: visual coherence.

### Phase E - 1,000-city proofing and verification

- [ ] E1. Add a synthetic-fixture test: render all charts from a generated 1,000-city metrics frame and assert (a) SVG byte size under a threshold, (b) element counts bounded (no per-city polylines slip back in), (c) outlier labels don't overlap (simple bounding-box check). Rationale: the "horrible at 1000" failure mode becomes a failing test, not a surprise.
- [ ] E2. Rebuild, re-run `check_site.py` including the browser checks (selection highlight, pin overlay, region chips, click-to-select), and re-verify the 410 KB first-load budget - expected to drop because B1/B4 delete the eager line sample. Rationale: every visual addition must be paid for; here the deletions pay first.

## Verification Criteria

- No chart ships more than O(quantiles + outlier labels + selected overlays) geometry regardless of city count; synthetic 1,000-city render passes size/count assertions.
- Each chart has: computed headline, in-plot annotations, ribbon key, labeled outliers, and a selected-city highlight with name label.
- Charts occupy the full content column; axis text >= 12px effective.
- Click on any chart city selects it; region chips dim/restore without recomputation; all `check_site.py` checks pass and the first-load budget holds.

## Potential Risks and Mitigations

1. **Removing spaghetti loses the "every city is here" feeling.**
   Mitigation: the fingerprint scatter (B2) shows literally every city as a dot; the caption states the count.
2. **Outlier name labels collide at high density.**
   Mitigation: Python-side greedy label placement with a bounding-box test (E1 asserts it); drop labels before overlapping them.
3. **Region coloring adds bytes per dot.**
   Mitigation: encode region as a class on the existing group (one short attribute), colors live once in CSS.
4. **Headline sentences go stale against the data.**
   Mitigation: they are computed from the same tables at build time, never hand-written.

## Alternative Approaches

1. **Adopt a JS charting library (uPlot/Observable Plot):** richer interaction (zoom, brushing) but breaks the "Python computes, JS toggles" architecture, adds a vendor payload against the 410 KB budget, and makes determinism checks harder. Rejected.
2. **Canvas rendering for the scatter at 1,000 dots:** cheaper paint, but loses per-element hover/click/CSS-highlight, which is the whole point of the selection model. Rejected unless SVG dot counts prove slow (E1 can add a paint-time check).
3. **Keep spaghetti but with strong alpha fade:** minimal change, but still ships bytes for lines nobody can distinguish and still reads as mud in screenshots. Rejected.
