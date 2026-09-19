# Weather Forecast Section — UI/UX Redesign

## Objective

Turn the live forecast strip from a plain one-line card into the visual centrepiece of the report: a themed, animated "now-cast" panel that makes the study's headline finding — *what has this stated probability actually meant in this city?* — the thing the eye lands on first.

The redesign must respect four constraints that already govern this codebase, none of which are negotiable:

1. **Python computes, JavaScript displays** (`src/web/app.js:8-10`). The annotation is a lookup into `pop_map` (`src/city_report.py:354-360`, `src/web/app.js:205-213`). No new statistic may be derived in the browser. Vendor-supplied API values may be *displayed*; they may not be aggregated, averaged or rescaled.
2. **The weather layer may not touch text contrast** (`src/web/app.css:124-136`). `[data-sky]` drives backdrops and the globe ocean only. Any new weather-reactive surface must either avoid carrying text or guarantee its own contrast floor, as the hero already does (`src/web/app.css:239-255`).
3. **A third-party outage must not break the page** (`src/web/app.js:265-272`), and this is asserted with the network blocked (`src/check_site.py:238-239`).
4. **First-load budget is 400 KB and is a build failure** (`src/report.py:62-66`). No new libraries, no icon fonts, no Lottie.

---

## Assessment of the current state

### What exists

| Concern | Location |
|---|---|
| Empty container, emitted hidden below the globe | `src/report.py:1071` |
| Fetch, decode, render, failure path | `src/web/app.js:215-273` |
| WMO code → label / emoji / sky class table | `src/web/app.js:181-201` |
| `pop_map` lookup (the headline feature) | `src/web/app.js:203-213` |
| Card styling | `src/web/app.css:410-424` |
| Mobile reflow (one rule) | `src/web/app.css:646` |
| Sky tokens consumed by hero and globe only | `src/web/app.css:128-136`, `:246-249`, `:291` |
| Verification: outage, success path, tint, contrast | `src/check_site.py:238-239`, `:344-396` |
| API endpoint config | `src/config.py:227`, injected at `src/report.py:1251` |

### Ranked problems, worst first

1. **The headline insight is the smallest text on the card.** The observed-frequency annotation renders as 13px `--muted` (`src/web/app.css:421`) while the vendor's own number gets 19px bold (`:420`). The page's entire thesis is that the stated number needs qualifying, and the layout says the opposite. *Highest priority: this is a content-hierarchy bug, not a taste issue.*
2. **Emoji as the primary visual.** `'\u2600\uFE0F'` and friends (`src/web/app.js:182-189`) render as a different artwork on every OS, cannot be themed, cannot animate, and look nothing like the rest of a page that hand-draws its own SVG globe and reliability curves. This is the single largest "does not look stunning" contributor.
3. **No motion, anywhere on the card.** The only transition in the whole feature is the hero's background crossfade (`src/web/app.css:250`). The card appears instantly, fully formed, with no entrance, no number transition, no loading state beyond `opacity: .55` (`src/web/app.css:424`).
4. **Layout shift on load.** `#fc` starts `hidden` (`src/report.py:1071`) and is revealed by script, pushing the city panel down mid-load. Reserving space is both a UX and a Core-Web-Vitals fix.
5. **Thin information density.** One request already returns current conditions and daily extremes; the card shows six numbers and no shape of the day. There is no sense of *when* the rain is expected.
6. **The card is inert to the weather it reports.** The sky tint reaches the hero and the globe's ocean but stops short of the one element actually describing the weather — it sits on flat `--panel` (`src/web/app.css:411`).
7. **Accessibility gaps.** No landmark or accessible name on `#fc`, no `aria-live` for the asynchronous update, no `aria-busy` during load. A screen-reader user switching city is told nothing.
8. **Fragile responsive behaviour.** `margin-left: auto` plus `min-width: 230px` (`src/web/app.css:419`) is patched by one override at 700px (`:646`); there is no defined layout at intermediate widths.

---

## Design direction

**A "now-cast" panel in three zones**, replacing the flat flex row:

- **Scene** — an inline SVG micro-scene (sun / cloud / rain / snow / storm / fog / night) drawn from theme tokens and gently animated, sitting on a weather-reactive gradient. This zone carries *no text*, which is what makes it safe to tint freely under constraint 2.
- **Conditions** — city name, current temperature, condition label, daily high/low, and an "as of" time. Tokened text on `--panel`; untinted, so contrast is structurally unchanged.
- **The calibration verdict** — the centrepiece. Stated probability and the city's own observed frequency shown as a paired visual (a two-track arc or twin bars against an honest-diagonal reference), with the sentence beneath it, and the sample size and significance as supporting detail.

**Motion vocabulary** — restrained, meaningful, and entirely suppressible:

- Scene loops: sun ray rotation, cloud drift, staggered raindrop fall, snow drift-and-sway, lightning flash, fog band slide, star twinkle.
- Entrance: staggered fade-and-rise for the three zones on first render and on city switch.
- Data motion: temperature and probability count up; the verdict arc draws itself via `stroke-dasharray`.
- Loading: a skeleton in the final layout's shape with a shimmer sweep — not an opacity dim.

**Optional enrichment** — an hourly probability ribbon for the next 12 hours, added to the *same* request via `hourly=precipitation_probability`. This is display of vendor values, not computation, and it answers "when?" which the card currently cannot. Treat as a separate, droppable task.

---

## Implementation Plan

### Phase 1 — Structure and contract

- [ ] Task 1. **Freeze the DOM contract the verifier depends on before changing anything.** `src/check_site.py:369-382` asserts on `#fc .pop`, `#fc .means`, and the temperature text inside `#fc`. Decide explicitly whether the new markup preserves `.pop`/`.means` as the same semantic nodes or whether `check_site.py` is updated in the same change. Rationale: silently renaming these turns a green verification run into a false negative, and this harness exists precisely to catch the "Bucharest's table shown for every city" class of bug (`src/check_site.py:344-350`).

- [ ] Task 2. **Replace the empty `<div class="fc" id="fc" hidden>` at `src/report.py:1071` with a server-rendered skeleton** carrying the final layout's zones and dimensions, `aria-busy="true"`, `role="region"`, and an accessible name. Rationale: eliminates the load-time layout shift (problem 4), gives the strip a landmark, and keeps the no-JS story honest — the skeleton must be inert and visually silent when scripting is off, so pair it with a rule that hides it in the absence of a script-set class.

- [ ] Task 3. **Decide and document the strip's placement.** Currently it sits between the globe and the depth line (`src/report.py:1071-1072`). Evaluate promoting it into or immediately beneath the hero (`src/report.py:1146-1151`) so the first screenful carries the live hook. Rationale: the hero already owns the sky gradient; putting the weather card adjacent to it makes the tint read as intentional rather than incidental. Record the decision and its reasoning in the module docstring, as this codebase does elsewhere.

- [ ] Task 4. **Restructure `.fc` in `src/web/app.css:410-424` as a CSS grid with named areas** and explicit behaviour at three breakpoints (desktop three-zone, tablet two-zone, ≤700px stacked), replacing the `margin-left: auto` / `min-width` pairing and its single override at `:646`. Rationale: removes the intermediate-width failure (problem 8) and makes later additions — the hourly ribbon — a grid-area change rather than a flex rewrite.

### Phase 2 — The visual identity

- [ ] Task 5. **Design an inline SVG icon set replacing the emoji column in `src/web/app.js:181-201`**, one scene per existing sky class (`clear`, `cloud`, `rain`, `snow`, `storm`, `fog`, `night`). Constraints: built from theme tokens and `currentColor`, no raster assets, no external requests, total added weight measured against `FIRST_LOAD_BUDGET_KB` (`src/report.py:62-66`). Rationale: this is the highest-leverage change for perceived quality (problem 2) and it is the prerequisite for every scene animation.

- [ ] Task 6. **Keep the WMO mapping table as the single auditable source of truth.** Extend the existing rows at `src/web/app.js:181-190` with a scene key rather than introducing a second lookup elsewhere. Rationale: the table's comment states the mapping stays in one place so the visual claim remains auditable; splitting icon selection across two structures would void that.

- [ ] Task 7. **Give the scene zone a weather-reactive backdrop driven by the existing `--sky-1` / `--sky-2` tokens** (`src/web/app.css:128-134`), mirroring the hero's technique of mixing the light end toward a dark base (`src/web/app.css:239-249`). Restrict the tint to the scene zone only. Rationale: satisfies problem 6 without giving the weather layer any influence over a surface that carries text — the structural guarantee behind constraint 2.

- [ ] Task 8. **Verify all seven sky states against all three themes before proceeding.** Blueprint deliberately has no large colour fills (`src/web/app.css:104-107`); a saturated scene panel would contradict the theme's stated purpose. Define the Blueprint treatment explicitly — likely outline-only scenes with no gradient. Rationale: the theme system's whole premise is that components reference semantic tokens and never raw colours (`src/web/app.css:11-14`); a new decorative surface is exactly where that rule gets broken first.

### Phase 3 — The calibration verdict (the reason the section exists)

- [ ] Task 9. **Promote the stated-vs-observed comparison to the card's primary visual element**, replacing the current 13px muted sentence (`src/web/app.css:421`, built at `src/web/app.js:242-261`). Render the stated probability and the bin's observed frequency as a paired visual with an explicit honest reference, so the *gap* is what the reader perceives first. Rationale: directly fixes problem 1, and realises the intent recorded as the highest-value idea in `plans/2026-09-13-interactive-multi-city-report-globe-and-themes-v1.md` Task 34.

- [ ] Task 10. **Keep the annotation a pure lookup.** The visual consumes `bin.mean`, `bin.obs`, `bin.n` and `bin.sig` exactly as shipped by `src/city_report.py:357-360`; geometry may be derived from them, a probability may not. Rationale: constraint 1. A second, divergent implementation of the study's mathematics in JavaScript is the specific failure this codebase is built to prevent.

- [ ] Task 11. **Give significance a distinct visual treatment.** `bin.sig` currently only appends "more/less often than stated" to a sentence (`src/web/app.js:244-247`). Reuse the `tag good` / `tag bad` vocabulary already established in the city tables (`src/city_report.py:292-296`). Rationale: visual consistency with the report's own verdict language, and it makes the honest case legible at a glance rather than by reading.

- [ ] Task 12. **Preserve both existing empty states**: no matching bin ("No comparable group of days…", `src/web/app.js:249`) and no probability at all. Design a deliberate layout for each rather than letting the grid collapse. Rationale: these paths are real — several cities have thin records (`src/city_report.py:314-320`) — and an unstyled gap in a "stunning" card is worse than a plain one.

### Phase 4 — Motion

- [ ] Task 13. **Add looping scene animations** (sun rotation, cloud drift, staggered raindrops, snow sway, lightning flash, fog slide, star twinkle) as CSS keyframes on SVG child nodes. Keep each loop long, low-amplitude and GPU-cheap — `transform` and `opacity` only, no layout-affecting properties. Rationale: this is the "cool touch" asked for; constraining it to compositor-only properties keeps it from costing a frame budget the globe's drag interaction already spends (`src/web/globe.js`).

- [ ] Task 14. **Add a staggered entrance animation** for the three zones, triggered on first render and re-triggered on city switch (`src/web/app.js:154`, `:417`). Rationale: city switching is currently an instantaneous `innerHTML` swap (`src/web/app.js:253-261`) with no signal that anything changed; motion carries the causal link from the click to the update.

- [ ] Task 15. **Animate the numbers**: count up the temperature and the probability; draw the verdict arc via `stroke-dasharray`. Rationale: reinforces the shift in hierarchy from Task 9 — the number the reader should study is the one that takes a moment to arrive.

- [ ] Task 16. **Replace `.fc.loading { opacity: .55 }` (`src/web/app.css:424`) with a shimmer skeleton** matching the final layout, cleared on both success and failure paths (`src/web/app.js:252`, `:270`). Rationale: an opacity dim reads as "broken", a skeleton reads as "arriving", and the skeleton from Task 2 is already the right shape.

- [ ] Task 17. **Handle `prefers-reduced-motion` explicitly, not by inheritance.** The global rule at `src/web/app.css:655-660` collapses durations to `.001ms`, which for an *infinite* loop means a still-running animation at pathological speed rather than a stopped one. Add an explicit `animation: none` for the scene loops and skip the count-up in script. Rationale: the existing blanket rule was written for transitions, before any looping animation existed; relying on it here would produce exactly the flicker it was meant to prevent.

- [ ] Task 18. **Suppress all motion and the scene gradient in print** (`src/web/app.css:662+`). Rationale: the print block already forces a light palette because a dark page prints as a wall of toner; a gradient scene panel has the same problem.

### Phase 5 — Optional enrichment

- [ ] Task 19. **Add an hourly probability ribbon** for the next 12 hours by extending the existing request at `src/web/app.js:221-225` with `hourly=precipitation_probability` (no second round trip). Render as a compact bar ribbon in its own grid area. Rationale: answers "when?", which the card cannot currently address, at near-zero cost. Explicitly a display of vendor values — no aggregation, no smoothing, no derived peak.

- [ ] Task 20. **Wire the ribbon into the existing delegated tooltip** via `data-tip` (`src/web/app.js:87-102`) rather than adding a second tooltip mechanism. Rationale: the tooltip is already delegated specifically because city sections are replaced wholesale on switch; a per-element handler would die with the markup that carried it.

- [ ] Task 21. **Add an "as of" timestamp and explicit city name** to the conditions zone. Rationale: the card currently never says which city's weather it is showing or how fresh it is, while sitting directly beneath a globe whose selection can change.

### Phase 6 — Accessibility and verification

- [ ] Task 22. **Add `aria-live="polite"` to the verdict region and manage `aria-busy`** across load, success and failure. Ensure decorative scene SVGs are `aria-hidden` and that every number is reachable as text. Rationale: fixes problem 7; an animated card that announces nothing on city switch is a regression for non-visual users.

- [ ] Task 23. **Preserve the outage behaviour exactly.** The failure path must still hide the strip and clear the tint (`src/web/app.js:265-272`), and `src/check_site.py:238-239` must still pass unchanged. Rationale: constraint 3. The analysis is the product; a decorative card must never be able to hold it hostage.

- [ ] Task 24. **Extend `src/check_site.py` with checks for the new surface**: scene SVG present and matching the decoded condition; the observed frequency rendered at or above the stated probability's visual weight; skeleton cleared on both paths; no animation running under `prefers-reduced-motion`; card contrast sampled across seven sky states × three themes as the hero already is (`src/check_site.py:612-679`); no clipping at 390px (`src/check_site.py:548-556`). Rationale: this project's verification targets invisible failures, and a tinted, animated, absolutely-layered card is a rich source of them.

- [ ] Task 25. **Re-measure the first-load payload** and confirm the build still passes `FIRST_LOAD_BUDGET_KB` (`src/report.py:62-66`, `:1279-1283`). Rationale: the budget is a build failure by design; discovering the breach in CI rather than locally wastes a cycle.

- [ ] Task 26. **Confirm byte-determinism still holds** (`src/check_site.py:165-175`). The strip is script-rendered so it should not affect page bytes, but the skeleton from Task 2 becomes part of every static page. Rationale: determinism is the property that lets CI re-render from committed artefacts without re-running the study (`requirements-site.txt:3-7`).

---

## Verification Criteria

- The forecast card renders the city's own observed frequency as the visually dominant figure, verified by the existing known-probability mock (`src/check_site.py:344-382`) plus a new weight/size assertion.
- No emoji glyph remains in the forecast render path; every condition resolves to an inline SVG scene built from theme tokens.
- Body text contrast remains ≥ 7.0:1 (AAA) across all seven `[data-sky]` states in all three themes, and card text contrast meets the same floor.
- With `**://api.open-meteo.com/**` aborted, `#fc` is hidden, no console error escapes the existing filter, and the rest of the page is unaffected.
- Cumulative layout shift attributable to `#fc` is zero: the skeleton occupies the resolved card's height.
- Under `prefers-reduced-motion: reduce`, no element inside `#fc` has a running animation.
- The card has a defined, unclipped layout at 390px, 700px, 860px and 1280px.
- First-load payload stays under 400 KB and the build does not fail its own budget check.
- Rebuilding twice produces byte-identical HTML apart from the "Generated" date line.
- No statistic is computed in `src/web/app.js`; the annotation remains a bin lookup into `pop_map`.

---

## Potential Risks and Mitigations

1. **The weather layer leaks into text contrast.** A tinted card is the most likely place the `[data-sky]` discipline (`src/web/app.css:124-136`) breaks.
   Mitigation: confine the tint to the text-free scene zone; keep the conditions and verdict zones on `--panel`; add the card to the seven-sky × three-theme pixel sweep.

2. **Blueprint is visually contradicted.** A gradient scene panel undermines a theme that deliberately has no large colour fills (`src/web/app.css:104-107`).
   Mitigation: define a Blueprint-specific outline-only treatment as an explicit deliverable (Task 8), not as an afterthought.

3. **Renaming DOM nodes silently breaks the verifier.** `src/check_site.py:369-382` selects `#fc .pop` and `#fc .means`.
   Mitigation: Task 1 forces the decision up front and pairs any rename with the corresponding harness change in the same commit.

4. **Animation competes with the globe for frames.** Dragging the globe repaints a canvas continuously (`src/web/globe.js`).
   Mitigation: compositor-only properties (`transform`/`opacity`), long low-amplitude loops, no `filter` on the scene — the same reasoning that put the globe's atmosphere on a pseudo-element (`src/web/app.css:266-274`).

5. **Scope creep into computing statistics.** "Show the recalibrated probability" is the obvious next idea and is exactly the forbidden move.
   Mitigation: Task 10 states the boundary; anything not present in `pop_map` requires a change in `src/city_report.py`, in Python, with the number shipped precomputed.

6. **Payload growth from the icon set.** Seven inline scenes plus keyframes is real weight against a 400 KB budget.
   Mitigation: share geometry across scenes, keep paths coarse, measure in Task 25 rather than at the end.

7. **The skeleton appears for no-JS readers.** Task 2 puts markup in every static page that only script can resolve.
   Mitigation: hide the skeleton by default and reveal it from script, so the no-JS document (`src/check_site.py:403-424`) is unchanged.

---

## Alternative Approaches

1. **Polish-only.** Keep emoji, restyle the card, add an entrance animation and fix the hierarchy. Lowest risk, smallest diff, passes the existing verifier almost unchanged — but leaves the largest "does not look stunning" factor (platform-dependent emoji) untouched.
2. **Canvas particle scene.** Real falling rain and drifting snow behind the card. Highest visual impact; costs a render loop next to the globe's, a second palette-probing mechanism, and a much harder reduced-motion and battery story. Recommended only if Task 13's CSS scenes prove insufficient.
3. **Full-bleed weather hero.** Merge the card into the hero so the weather becomes the page's backdrop. Most dramatic, but puts the headline `<h1>` on a weather-driven surface — a direct collision with constraint 2 and with the reasoning recorded at `src/web/app.css:239-249`.
4. **Third-party animated icon library (Lottie / animated SVG packs).** Fastest route to polished artwork; rejected on budget, on the self-hosting/supply-chain stance behind `src/vendor.py:1-8`, and because token-driven theming across three palettes is not something a packaged icon set will do.

---

## Recommendation

Phases 1–4 constitute the redesign proper and are ordered so that each phase leaves the site shippable. Phase 3 (Tasks 9–12) delivers the most value per unit of work — it is the only part that changes what the reader *understands*, not merely what they see — and Task 5 delivers the most visible quality jump. Phase 5 is genuinely optional and can be dropped without leaving a gap in the layout if it is built as its own grid area from the start.
