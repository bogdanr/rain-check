# Site Redesign: "Atlas Noir", a Persistent Globe Stage on One Layered Page

## Objective

Redesign the rain-check report (`dist/` site, built by `src/report.py` with `src/web/{app.css,app.js,globe.js}`) on a separate branch so that it:
- looks modern and polished,
- is clear to a curious reader, and
- is detailed and citable for a professional.

## Decisions log

- **2026-09-24: the dark style is the default look.** The Noir palette (near-black ground, luminous data marks, cyan accent) is the primary design target, and it is the style the prototype, the screenshots and the globe materials are tuned for first. Daylight and Blueprint stay as secondary themes: Blueprint because it is the print and high-contrast theme, Daylight as a reader option. Both are re-tuned after the dark theme, not in parallel with it.
- **2026-09-24: one layered page, no Story / Lab switch.** A mode switch makes the reader choose before they know what the modes are, splits polish and testing across two layouts, and gives only half the visitors the full design. Instead the page deepens as the reader goes: a cinematic top (globe plus verdict), then the story chapters over the globe, then detail one click away everywhere (number popovers, method drawers, downloads), then folded reference sections. The globe docks to a mini view below the story so the dense sections get full width. Lab mode's useful parts (sortable league, compare, downloads) move into the main page (Phase 5). A later "compact" toggle may tighten spacing only; it must never change the layout.
- **2026-09-25: pure black ground, cyan accent.** Navy ground and amber accent were compared in the prototype and dropped.
- **2026-09-25: the WebGL globe keeps main's terrain-at-the-horizon.** Land near the limb is displaced with the same law as `src/web/globe.js:193-207` (20x at the summits, square root below, 100x ceiling), so mountains break the outline. The atmosphere is kept thin: a narrow halo hugging the limb, not a wide glow.

Constraints:
- Keep the shaded-relief terrain globe and make it the centrepiece.
- Keep the project's standing rules:
  - Python computes every coordinate, and JS only toggles or injects (`src/city_charts.py:26-29`).
  - Every number comes from the tables (`src/report.py:7-8`).
  - Decoration may never carry text contrast or data (`src/web/app.css:225-228`).
  - First-load budget (`src/report.py:123`).
  - The browser checks in `src/check_site.py` must still pass.

Inspiration taken from the RTR "Signal Noir" deck (`../../Work/Specure/RTR/slides/`):
- **One persistent 3D stage behind all the content.** Each slide declares a stage state, and the camera flies between states (`RTR/plans/2026-09-23-rtr-conference-slidev-deck-v2.md:12-21`).
- **Visual style** (`RTR/slides/styles/index.css:1-81`):
  - pure black ground
  - Inter Variable for text, JetBrains Mono for small uppercase "kicker" labels
  - huge tabular numbers
  - a soft 600 ms rise when items are revealed
  - a "data as of" stamp
- **Safety features:** automatic fallback, fps watchdog and a safe mode (same plan, `:23-33`).

On the web, the equivalent is **scrollytelling**: the page scrolls over a fixed globe stage, and each section declares where the camera should be.

## Current-state findings (sources)

- **Page assembly:** the whole page is Python f-strings in `document()` (`src/report.py:1759-1867`).
- **Structure:**
  - hero (`:1838-1843`), then primer, globe block, city panel
  - then cross-city sections, then the folded deep dive, Methods and Reference (`:1770-1801`)
  - navigation is a grouped rail (`NAV`, `:1475-1487`)
  - there is also a command palette (`:1725-1738`)
- **Design system:**
  - a semantic token contract with three themes, Observatory (default), Daylight and Blueprint (`src/web/app.css:17-223`)
  - reading column 940 px, system font stack, 16 px (`src/web/app.css:115-116,251`)
  - visually a competent document, not yet an experience
- **The globe** (`src/web/globe.js:1-45`):
  - d3-geo orthographic projection
  - a **software per-pixel relief shader** on a 2D canvas, reading vendored elevation and biome textures that hold physical quantities, not colours
  - SVG markers on top, for accessibility and hit-testing
  - materials and lighting are CSS tokens (`src/web/app.css:61-96`)
  - this is the asset to keep and upgrade: it is CPU-bound, so smooth camera flights and a full-screen stage are not realistic on it
- **Budget:** 410 KB, with a long history of raises and one reduction (`src/report.py:67-123`). Any new fonts, WebGL code or components must be paid for by moving content out.
- **The story the design must tell** (`plans/2026-09-22-session-state-v1.md:18-40`):
  - **D12**: the metric decides whether a reader sees the harm.
  - **E4a**: the served probability costs a cheap-action user real value while its average score looks fine. E4a survived three attacks: E35, E22 and E36.
  - Every calibration verdict must travel with its **event definition** (day-total vs any-step).
  - "Verification is not possible outside EU/NA/Asia" (G1) is itself a finding.
- **No CI workflow file** was found in the repo. Deployment is to GitHub Pages under `/rain-check/` (`src/report.py:112-122`).

## Design concepts (three directions, one recommended)

1. **Observatory Noir (mission control)**
   - A near-black instrument aesthetic: the globe full-bleed, with glass HUD panels, mono telemetry labels and glowing data marks.
   - Strongest "from the future" look.
   - Risk: it becomes a dashboard with no story.
2. **Scrollytelling Atlas (narrative)**
   - Chapters scroll over a fixed globe stage. Each chapter moves the camera, for example "Your city", then "Europe's capitals", then "The world, and where no one can check".
   - Closest to the RTR deck, and the best fit for curious readers.
   - Risk: professionals get slowed down.
3. **Instrument Lab (professional workspace)**
   - A dense, sortable, filterable layout: league table with rank intervals, compare mode, exports, method drawers.
   - Best fit for professionals. Least impressive at first glance.

**Decision: "Atlas Noir", concept 2's structure in concept 1's visual language, with concept 3's tools built into the same page.**
- One page, one layout, one data payload. Depth comes from progressive disclosure, not from a mode.
- This matches the dual-audience goal the project already set (`plans/2026-09-22-report-interactivity-and-scale-v1.md:10`).

## Signature ideas

### The stage (globe)
- **Globe v3: a WebGL2 port of the existing relief shader.**
  - Same textures and the same byte encoding as `src/vendor.py`.
  - Same CSS material tokens, so all three themes keep working.
  - Runs on the GPU instead of the CPU, which enables smooth 60 fps camera flights, a full-viewport stage and a proper atmosphere rim.
  - An optional real-time sun terminator, computed from the "as of" time, which is honest because it is astronomy, not data.
- **Fallback chain:** WebGL2, then the current canvas shader, then the vector basemap, then the no-JS directory. An fps watchdog and context-loss handler, as in RTR, drops a level automatically.
- **Stage states declared per section** (e.g. a `data-stage` attribute on each section):
  - `city`: the camera closes in on the selected city
  - `europe`: the capitals
  - `world`: all verified cities
  - `desert`: countries that cannot be verified, shaded by reason
  - `ensemble`: reference-centre comparison
  - An IntersectionObserver tweens between states (600–900 ms ease-in-out). With reduced motion, it jumps instantly.
- **Globe layers** (toggle chips, all values precomputed in Python):
  - skill (current)
  - bias, too dry vs too wet
  - distance to the gauge
  - served-vs-ensemble gap
  - coverage and "why missing", from `data/processed/country_coverage.parquet`
- **Markers:**
  - The visual layer is drawn in WebGL: a glow, with a pulse ring on the selected city.
  - The **SVG/DOM markers stay** as the focusable, labelled hit layer, so accessibility and `check_site` parity are unchanged.

### Clear data for curious readers
- **Verdict hero:**
  - One sentence answer, for example: "Honest? Slightly too dry. Useful? Yes."
  - A large skill number with count-up, a confidence bar and a plain-English scale: "can't beat the usual", "worth acting on".
  - The event-definition chip and a "data as of" stamp are always attached.
- **"The 40% promise" icon array:** for a chosen forecast bin, 10 or 100 day tiles, with the days it actually rained filled in, next to the promise. This makes calibration understandable at a glance. Counts are precomputed per city.
- **"How cheap is your umbrella?" slider:**
  - This is the D12/E4a thesis as an interaction. The reader drags the cost/loss ratio and sees the value the served forecast delivers against the ensemble.
  - Curves already exist in `decision_value_curves.parquet`. JS only selects a precomputed point.
  - This is the single most important new chart, because it *shows the harm* that the average score hides.
- **Reliability diagram redesign:**
  - Shaded "said too little rain" and "said too much rain" regions with words on them.
  - The perfect-honesty diagonal and the population ribbons (the current design) stay.
  - The selected city is drawn as a bright line with an end label.

### Depth for professionals
- **Evidence ledger:** a "claims that survived" panel. Each headline claim is shown with the attacks it withstood (sample size E35, truth E22, reference centre E36), and its effect size, interval and resampling unit. This directly signals rigour.
- **Number chips:** every headline number is a chip, and hover or focus opens a popover with:
  - n, the interval, the resampling unit and the event definition
  - the source table and the stage of `run_all.sh` that produced it
  - "copy citation"
- **Tools in the page (formerly "Lab mode"):**
  - sortable league table with rank-interval whiskers and duplicate-model annotations
  - two-city compare (already planned in the palette)
  - per-chart "download CSV", precomputed by Python as hashed assets
  - a "Method" drawer on each chart
- **Stateful deep links:** the URL carries city, compare city, section and globe layer, so any view can be cited.
- **Print:** the Blueprint theme becomes the print/PDF style.

### Visual language and modern web platform
- **Tokens:**
  - Evolve the existing contract and keep the component rule of no raw hex.
  - Add an OKLCH-based "Noir" palette to Observatory: ground about `#05070a`, a cyan data accent, and the existing skill ramp re-tuned for the darker ground.
  - Daylight and Blueprint are kept.
- **Type:** Inter Variable plus JetBrains Mono, self-hosted as Latin-subset woff2 files; mono uppercase kickers; tabular numerals for all figures.
- **Layout:** a full-bleed stage, content "cards" of frosted glass over the stage in the story chapters, a 68-character prose measure, and a wide grid for the dense sections once the globe has docked.
- **Native platform features, no framework:**
  - View Transitions API for city switching (the heading and numbers morph)
  - CSS scroll-driven animations (`animation-timeline: view()`) for reveals, with the RTR 600 ms rise
  - Popover API for the palette and chips
  - CSS anchor positioning for tooltips
  - container queries for cards, `:has()`, CSS nesting, `@property` for animated counters
  - each of these degrades gracefully where it is unsupported

## Assumptions

- The branch is `design/atlas-noir`, created from the current HEAD. `main` keeps publishing.
- "Modern technologies" means **modern browser features plus WebGL2**, not a SPA framework.
  - The Python-renders-HTML architecture, the no-JS path and the determinism check are real strengths.
  - An Astro/Svelte rewrite would discard `check_site.py`'s coverage for little visual gain (see Alternatives).
- Fonts and the WebGL module load after first paint. Like the terrain textures (`src/report.py:77-83`), they are excluded from the budget only if the page renders fully without them. Otherwise they are counted.
- All existing element ids that `check_site.py` relies on (`#globe`, `#city-card`, `#city-answer`, `#palette`, `#rail`, `#h1-city`, …) are preserved.
- The standalone `report.html` keeps a static fallback: no stage, the print theme, everything inline.

## Implementation Plan

### Phase 0: Branch, baseline, moodboard
- [ ] Task 0.1. Create the branch `design/atlas-noir`. Record the baseline: first-load KB, `check_site.py` pass count (128), and screenshots of the current site at desktop and mobile in all three themes. Rationale: every later change is judged against a measured before.
- [ ] Task 0.2. Build a throwaway static prototype of three screens using real Bucharest numbers: hero plus verdict, the umbrella slider, and the world "desert" stage. Build it outside the pipeline, e.g. `design/proto/`, git-ignored or on the branch only. Screenshot it with the Playwright set-up `check_site.py` already uses. Rationale: agree on the look before touching the generator. Prototype the dark Noir style only (decided 2026-09-24). Where there is a choice to make, compare dark variants, e.g. pure black vs deep navy ground, and cyan vs amber accent.
- [ ] Task 0.3. Choose the final dark palette and the type pairing from the prototype. Write the stage-state list and the section order down in this plan as v2.

### Phase 1: Design system
- [ ] Task 1.1. Extend the token contract in `src/web/app.css`:
  - an OKLCH Noir palette for Observatory
  - type-scale tokens (display, huge, big, kicker, body, mono)
  - motion tokens (600 ms / 900 ms, `cubic-bezier(.2,.8,.2,1)`)
  - glass surface tokens, and elevation and glow tokens that are off in Blueprint
  
  Rationale: the existing no-raw-colour rule means a redesign is mostly a token change.
- [ ] Task 1.2. Self-host Inter Variable and JetBrains Mono as Latin-subset woff2 with `font-display: swap`, emitted as hashed assets via `Site.add_file` (`src/sitebuild.py:95-97`). Decide whether they count in the budget, following the rule in the Assumptions. Rationale: typography does most of the "premium" work.
- [ ] Task 1.3. Build the core components as CSS plus Python render helpers:
  - kicker
  - huge number with count-up
  - number chip with provenance popover
  - event-definition badge
  - "as of" stamp
  - glass card
  - layer toggle chips
  - method drawer
  
  Put them in a single render module (e.g. extend `src/report_render.py`) so every section uses the same helper. Rationale: consistency, and the chip/badge enforce the "event definition travels with the verdict" rule in the markup.
- [ ] Task 1.4. Re-tune Daylight and Blueprint against the new components, and make Blueprint the `@media print` style. Rationale: professionals print and cite.

### Phase 2: Layout shell and navigation
- [ ] Task 2.1. Restructure `document()` (`src/report.py:1759-1867`):
  - a fixed full-viewport stage container behind the content
  - sections carrying the `data-stage` attributes
  - a new hero layout
  - a docked-globe state below the story chapters
  
  Keep every id `check_site.py` asserts on. Rationale: the stage-behind-content model is the RTR idea translated to scroll.
- [ ] Task 2.2. Redesign the top bar: brand, city button, theme menu (the segmented control becomes a compact menu), palette trigger. Turn the rail (`src/report.py:1608-1615`) into a slim chapter progress indicator with scrollspy that expands to the full outline on hover or focus. Rationale: a sense of place without clutter.
- [ ] Task 2.3. Upgrade the palette to "go anywhere / do anything": cities, sections, compare, switch theme, toggle a globe layer. Rationale: a fast path for power users.
- [ ] Task 2.4. Store state in the URL (city, compare, section, layer), and restore it on load and on history navigation. Rationale: citable views.

### Phase 3: Globe v3 (the stage)
- [ ] Task 3.1. Port the relief shader from `src/web/globe.js` to a WebGL2 fragment shader in a new module:
  - same texture decoding constants (`src/web/globe.js:129-200`)
  - same exaggeration and limb-displacement laws
  - materials read from the same CSS tokens
  
  Rationale: the GPU enables the full-bleed stage and camera flights; reusing the encoding keeps one source of truth with `src/vendor.py`.
- [ ] Task 3.2. Build a camera-state engine: named states, tweened orthographic rotation and zoom, driven by an IntersectionObserver over `data-stage` sections and by city selection. Reduced motion jumps instantly. Rationale: the continuous "flight" feel.
- [ ] Task 3.3. Hybrid markers:
  - glow and pulse drawn in WebGL
  - the existing SVG/DOM markers kept as transparent, focusable hit targets, re-projected with the same d3-geo projection
  - the `+n` clustering kept
  
  Rationale: keeps accessibility and parity checks intact.
- [ ] Task 3.4. Globe layers: Python precomputes a per-city value and a tier for each layer, and a per-country coverage status for the "desert" choropleth. They ship in the lazily loaded city table. JS only switches classes or uniforms. Rationale: G1 becomes visible, and the "JS derives nothing" rule is kept.
- [ ] Task 3.5. Build the safety layer:
  - WebGL2 capability detection
  - context-loss handling
  - an fps watchdog (below ~30 fps for 3 s, drop to the canvas renderer)
  - pause when the tab is hidden or the stage is off-screen
  - device pixel ratio capped at 1.5
  - a `?safe=1` flag and a palette action
  
  Rationale: the same protection as the RTR stage.
- [ ] Task 3.6. Optional: an astronomical sun terminator at the "as of" timestamp, subtle, and disabled in Blueprint. Rationale: a "live planet" feel with no fake data.

### Phase 4: Data storytelling components
- [ ] Task 4.1. Verdict hero: a one-sentence answer computed in Python from the city's scores, the huge skill number, a confidence bar, the plain-scale labels, the event chip and the as-of stamp. Rationale: the answer comes first.
- [ ] Task 4.2. "40% promise" icon array: Python emits the per-bin day counts for each city into its JSON (`dist/data/cities/`), and JS swaps the precomputed tile states. Rationale: the most intuitive explanation of calibration.
- [ ] Task 4.3. "How cheap is your umbrella?" slider: Python emits the served and ensemble value curves at fixed cost/loss steps from `decision_value_curves.parquet`, plus one pre-written sentence per step. The slider only indexes into them. Rationale: this is the paper's thesis (D12/E4a) made tangible.
- [ ] Task 4.4. Restyle the reliability chart in `src/city_charts.py`: labelled too-dry/too-wet regions, the new chart tokens, and the selected city's end label. The ribbon design (`src/city_charts.py:9-24`) is kept. Rationale: words on the chart, not in a legend.
- [ ] Task 4.5. Served-vs-ensemble dumbbell chart (one row per city, sorted by gap, the selected city highlighted), and a reference-centre toggle between NOAA and ECMWF. Rationale: shows E13 and E36 at a glance.
- [ ] Task 4.6. Evidence ledger panel: each surviving claim, its attacks, its effect sizes and intervals, all pulled from the processed tables. Rationale: rigour becomes visible to professionals.
- [ ] Task 4.7. Scroll-driven reveals and count-ups via CSS scroll timelines. Numbers are rendered in the final state in the HTML, and animation is only an enhancement. Rationale: no-JS and print still show the true values.

### Phase 5: Professional tools in the main page
- [ ] Task 5.1. Dense section layout: once the globe docks to a mini view, the sections below use a wide 12-column grid with a tighter type scale, and each chart gets a "Method" drawer. Rationale: professionals need density, not cinema, without leaving the page.
- [ ] Task 5.2. League table: client-side sort over Python-rendered rows, rank-interval whiskers, duplicate-model annotations, and a "find your city" row pin. Rationale: the most-used professional view.
- [ ] Task 5.3. Two-city compare: split verdict card, two highlight colours on every chart, and a "compare" URL parameter. Rationale: "is my city better than X" is a natural question.
- [ ] Task 5.4. Downloads: per-chart CSV emitted by Python as hashed assets, and a "copy citation" action with a placeholder for a DOI (Task 32 in the strategy plan). Rationale: reuse and citation.

### Phase 6: Performance, accessibility, verification
- [ ] Task 6.1. Re-measure first load against `FIRST_LOAD_BUDGET_KB`. If over, lazy-load more sections as fragments via `fold(lazy=...)` (`src/report.py:1490-1525`) rather than raising the number, following the standing instruction at `src/report.py:96-110`.
- [ ] Task 6.2. Extend `src/check_site.py` with checks for:
  - stage fallback when WebGL is disabled (each renderer level still draws and still has parity)
  - the URL state round-trip
  - the docked-globe state keeps parity with the full stage
  - the slider and icon array showing values that match the payload
  - fonts failing to load without breaking the layout
  - reduced motion producing no scroll-linked motion
- [ ] Task 6.3. Accessibility pass:
  - contrast in all themes, including text over glass cards on the stage
  - keyboard paths through the globe, chips, slider and palette
  - screen-reader labels on the stage (`aria-hidden`), with the DOM markers as the accessible layer
  - `prefers-reduced-motion` and `prefers-reduced-transparency`
- [ ] Task 6.4. Performance pass on a mid-range laptop and a phone: stage at 50 fps or more, LCP under 2.5 s, no layout shift from fonts or count-ups. Mobile gets a static "poster" frame of the globe until it is tapped.
- [ ] Task 6.5. Decide the standalone `report.html` policy (static, print-styled, no stage), and verify that it still builds.

## Verification Criteria

- `src/check_site.py` passes all existing checks unchanged plus the new ones from Task 6.2. The determinism check (byte-identical rebuild) still passes.
- First load stays at or under 410 KB on the `/rain-check/` published build, with fonts and WebGL counted or excluded exactly as stated in the Assumptions.
- The globe renders at every fallback level (WebGL2, canvas, vector, no-JS). Every level draws exactly the verified cities.
- Every number on screen maps to a processed table. Every calibration verdict shows its event definition. Every data view shows an "as of" stamp.
- A first-time reader can state the city's verdict and the "cheap-action harm" finding from the hero plus the umbrella slider alone. Spot-check this with a fresh reader.
- A professional can reach n, the interval and the method of any headline number in one interaction, and can share a URL that restores the exact view.
- The stage holds 50 fps or more on the reference laptop. With reduced motion there is no camera animation.

## Potential Risks and Mitigations

1. **Budget breach (fonts, WebGL module, new components)**
   Mitigation: subset the fonts, load the stage after first paint with the canvas globe painting first, and move Methods and Reference into lazy fragments. Measure after each phase, not at the end.
2. **The WebGL port drifts from the canvas renderer (different mountains or colours)**
   Mitigation: share the constants file and material tokens. Add a check that renders both at a fixed rotation and compares mean luminance per region within a tolerance.
3. **Style over substance: glow and glass hurting legibility or implying precision**
   Mitigation: the existing rule that decoration cannot carry contrast or data (`src/web/app.css:225-228`). Glass cards get an opaque fallback under `prefers-reduced-transparency`. Blueprint stays plain.
4. **`check_site.py` breakage from DOM restructuring**
   Mitigation: freeze the list of ids and classes it asserts on before Phase 2, and run it after every task.
5. **Scrollytelling annoys professionals**
   Mitigation: the verdict is on the first screen, the palette jumps anywhere, deep links skip the story, and reduced motion removes the camera flights.
6. **Scope: this is a large redesign**
   Mitigation: build in priority order:
   1. tokens and type
   2. hero and umbrella slider
   3. WebGL globe with stage states
   4. professional tools
   5. the rest
   
   The branch is shippable after each step.

## Alternative Approaches

1. **Framework rewrite (Astro/SvelteKit + Three.js/globe.gl, Python exports JSON only):**
   - Pros: richest component ecosystem, and hot-reload design iteration.
   - Cons: it abandons the no-JS path, determinism and most of `check_site.py`, and breaks the budget. The existing relief look would have to be rebuilt anyway.
   - Worth it only if the report becomes an app.
2. **Keep the canvas globe, and redesign only type, tokens and layout:**
   - Pros: lowest risk, and about 40% of the visual gain.
   - Cons: no full-bleed stage and no camera flights.
   - A good fallback if the WebGL port stalls.
3. **Separate "Story" microsite (Slidev-like, reusing the RTR deck approach) linking to the current report as the Lab:**
   - Pros: the fastest route to a stunning piece, and RTR's components can be reused.
   - Cons: two products, drifting numbers, and two URLs to cite.
4. **deck.gl / MapLibre globe with raster terrain tiles:**
   - Pros: real 3D terrain zoom.
   - Cons: a network tile dependency, large payload, a licence audit through `src/provenance.py`, and loss of the theme-driven material rendering.
