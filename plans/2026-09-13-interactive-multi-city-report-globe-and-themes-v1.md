# Interactive Multi-City Calibration Report: Globe, Themes, and Live Forecast

## Objective

Transform `report.html` from a single-city, single-theme static document into an
interactive multi-city instrument: an orthographic globe where clicking a capital
switches the entire report to that city, three selectable visual themes with an
optional weather-reactive accent layer, and a current forecast strip for the
selected city — while preserving the two properties that make the artefact
valuable today: it opens offline from `file://`, and every number on it is
computed at render time from the verification tables.

## Current State Assessment

### What already exists (and must be reused, not rebuilt)

| Capability | Location | Status |
|---|---|---|
| Multi-city calibration engine | `src/capitals.py:1-480` | Built, correct, **unwired** |
| City registry with lat/lon/tz/country | `src/config.py:136-197` | Built |
| Frozen capital coverage verdict | `data/raw/capital_coverage.json` | Present |
| Report document shell | `src/report.py:626-654` | Bucharest-only |
| CSS as one flat string | `src/report_style.py:3-90` | Single hardcoded palette |
| Calibration curve as inline SVG | `src/report_render.py:69-130` | Reusable per city as-is |
| Base64 PNG embedding | `src/report_render.py:133-136` | Does not scale per city |
| Client-side JS | `src/report.py:32-43` | 12 lines, tooltip only |

### The three gaps, ranked by what blocks what

1. **`capitals.py` is not in the pipeline.** `run_all.sh:8-34` has 8 steps and
   none of them is `probe_capitals.py` or `capitals.py`. Every multi-city
   artefact is therefore stale-by-default and absent from a clean checkout.
   This blocks everything else and is the cheapest fix.
2. **`report.py` has no city dimension.** `compute()` at `src/report.py:46-77`
   loads exactly one `pop` table and every `sec_*` function closes over that
   single dict. Nothing is parameterised by city.
3. **The style layer has no theming seam.** `src/report_style.py:4-7` defines
   `:root` custom properties, which is the right primitive — but there is one
   set, and `src/report_render.py:82-129` hardcodes hex colours directly into
   SVG attributes, so the curve would not follow a theme switch.

### Constraints inherited from the completed plan

- `plans/2026-09-13-bucharest-calibration-explainer-and-web-report-v1.md:275-277`
  — must open from `file://` with **no external resource references**.
- Same file `:289-290` — re-running must produce an **identical file**.
- Same file `:123-124` — **no hardcoded result numbers**; all computed at render.

These are not negotiable defaults. Where this plan relaxes one (the live
forecast), it does so explicitly, behind a user gesture, with the offline path
as the default.

### Assumptions made

- Natural Earth 110m land geometry is public domain and may be vendored into the
  repo. If vendoring is rejected, the globe degrades to graticule + city dots,
  which is still usable.
- The capitals set is European (`src/probe_capitals.py:52-80`), so "globe"
  auto-orients to Europe on load; it is a globe for the aesthetic and for
  future non-European expansion, not because the data spans the planet.
- ~15-20 cities survive the coverage and offset rules, so per-city pre-rendered
  HTML is affordable but per-city base64 PNGs are not.
- Bucharest remains the default selected city and the narrative hero.

---

## Implementation Plan

### Phase 1 — Wire multi-city into the pipeline and establish a size budget

- [ ] Task 1. Add `probe_capitals.py` and `capitals.py` as explicit numbered
      steps in `run_all.sh:8-34`, before the report step, renumbering the echo
      banners. Rationale: the multi-city engine already exists and is already
      cached-on-warm-run, so the only thing standing between it and the report
      is pipeline membership. An artefact that must be remembered separately
      will go stale — the same reasoning that put `report.py` in the pipeline.
- [ ] Task 2. Make the capitals step tolerant of a cold cache in `run_all.sh`:
      if `capital_coverage.json` is missing the probe runs, and if the capitals
      stage fails the pipeline continues to a Bucharest-only report rather than
      aborting. Rationale: `load_capitals()` at `src/config.py:173-197` already
      degrades gracefully to the single city; the shell script should honour
      that contract instead of turning a network hiccup into a failed run.
- [ ] Task 3. Add a hard file-size assertion at the end of `src/report.py:656-658`
      with a named budget constant. Rationale: the page is a single self-contained
      file and every feature in this plan adds weight. Without a tripwire, the
      first person to embed a per-city PNG produces a 40 MB file and nobody
      notices until it fails to open.
- [ ] Task 4. Add a `--city` / `--cities` argument to `src/report.py` so a
      single-city report can still be produced. Rationale: keeps a fast
      iteration loop during development of the remaining phases, and preserves
      the existing artefact shape for anyone depending on it.

### Phase 2 — Restructure the renderer around a city dimension

- [ ] Task 5. Split `compute()` at `src/report.py:46-77` into
      `compute_global()` (robustness, benchmarks, events, hourly, glossary,
      roadmap — things that are Bucharest-deep or cross-city) and
      `compute_city(city)` (reliability table, decomposition, consistency bars,
      seasonal split, wet bias) reading from the `capitals_*.parquet` tables.
      Rationale: this is the single structural change the whole feature rests
      on. Doing it as an explicit split rather than threading a city parameter
      through every function makes it obvious which claims are per-city and
      which are not — a distinction the page must communicate honestly.
- [ ] Task 6. Classify every existing `sec_*` function as per-city or global and
      record the classification in code. Per-city: `sec_answer`, `sec_curve`,
      `sec_season`. Global/Bucharest-only: `sec_hourly`, `sec_events`,
      `sec_bench`, `sec_robust`, `sec_recal`, `sec_improve`, `sec_gloss`.
      Rationale: sections like the hourly track exist only for Bucharest
      (`src/report.py:112-135` reads Bucharest ISD stations). Silently showing
      them under a "Paris" heading would be a fabrication.
- [ ] Task 7. Render per-city sections into hidden panels keyed by city slug,
      with exactly one visible at a time. Rationale: pre-rendering in Python
      reuses `reliability_svg` and `scorecard` unchanged, which is what
      guarantees the page and the printed analysis cannot disagree. A
      JavaScript reimplementation of the calibration curve would create a second
      source of truth for the study's headline visual.
- [ ] Task 8. Add a visible "depth" badge to each city panel stating which
      analyses are available for it, with Bucharest marked as the deep case.
      Rationale: a reader who clicks Lisbon and sees fewer sections must
      understand that this is a data-availability fact, not a rendering bug.
- [ ] Task 9. Emit a compact per-city JSON payload (metrics, lat/lon, rank
      interval, base rate, sample size, wet-bias gaps) as an inline
      `<script type="application/json">` block. Rationale: the globe, the city
      picker and the comparison view all need the same small numeric summary;
      shipping it once as data is far smaller than three parallel HTML renderings.
- [ ] Task 10. Add a cross-city comparison section driven by that payload,
      leading with the bootstrap rank intervals from `src/capitals.py:297-308`.
      Rationale: `capitals.py:447-448` makes the key point in the console —
      with ~2 years per city the ordering is mostly not resolvable. A ranked
      list without its intervals would manufacture exactly the false "best
      capital" claim the analysis deliberately refuses to make.

### Phase 3 — The interactive globe

- [ ] Task 11. Add `src/globe_data.py` to fetch, simplify and quantise Natural
      Earth 110m land geometry into a compact coordinate blob cached under
      `data/raw/`. Rationale: the offline constraint forbids a CDN map library,
      so the geometry must be vendored. Quantising to roughly one decimal
      degree keeps it small at a resolution far finer than a globe of this size
      can display.
- [ ] Task 12. Implement orthographic projection, rotation and back-hemisphere
      clipping as a self-contained JS module embedded in the page. Rationale:
      orthographic projection is a short, well-defined piece of mathematics; it
      does not justify a dependency, and writing it directly is the only way to
      satisfy the no-external-resources rule.
- [ ] Task 13. Render the globe as SVG — ocean sphere, graticule, land paths,
      terminator-free flat shading — with a pointer-drag and keyboard rotation
      interface, auto-oriented to the centroid of the covered capitals on load.
      Rationale: SVG scales crisply, is inspectable, prints acceptably and
      matches the existing inline-SVG approach of the calibration curve.
      Keyboard rotation keeps it operable without a mouse.
- [ ] Task 14. Plot each covered capital as a marker whose size encodes sample
      size and whose colour encodes skill on a theme-aware, colour-blind-safe
      ramp, with a legend stating the encoding in words. Rationale: the marker
      must answer "how accurate is the forecast here" at a glance, but skill
      from a small sample is a weak claim — encoding sample size in the same
      mark keeps the caveat attached to the number instead of relegated to a
      footnote.
- [ ] Task 15. Make markers focusable and activatable by click, Enter and arrow
      keys, driving the panel switch, and show a hover/focus tooltip reusing the
      existing `#tip` element from `src/report.py:32-43`. Rationale: reusing the
      established tooltip keeps one tooltip implementation rather than two.
- [ ] Task 16. Render excluded capitals as distinct muted markers carrying their
      exclusion reason from `capitals_diagnostics.parquet` in the tooltip.
      Rationale: `src/capitals.py:198-203` records *why* each city was dropped.
      Surfacing that turns an apparent gap in coverage into a visible statement
      about data quality, which is one of the study's stronger themes.
- [ ] Task 17. Provide a non-globe fallback: if the geometry blob is absent,
      render graticule and markers only; if JavaScript is disabled, render a
      plain city list and leave all panels visible. Rationale: the page's core
      value is the analysis. No decorative feature may be allowed to hide it.

### Phase 4 — Navigation, information architecture and readability

- [ ] Task 18. Add a sticky top bar holding the city selector (name + country +
      skill chip), a theme control and a section jump menu, collapsing to a
      compact form on narrow screens. Rationale: with per-city switching the
      selected city becomes persistent state; state the user can change must
      remain visible, or they lose track of what they are reading.
- [ ] Task 19. Add a keyboard-navigable command palette for city search.
      Rationale: ~18 cities is past the point where a flat list is efficient,
      and a search affordance is the expected "high tech instrument" gesture.
- [ ] Task 20. Reflect selected city, theme and section in the URL hash and
      restore from it on load, persisting theme to `localStorage`. Rationale:
      makes any view of the report shareable and linkable, which is the whole
      point of a single portable file.
- [ ] Task 21. Add a progress/contents rail marking the current section.
      Rationale: the page is long and grows longer with a city dimension;
      orientation is currently provided only by scrolling.
- [ ] Task 22. Restyle tables and scorecards for density and scannability —
      tabular numerals throughout, subtle zebra striping, sticky table headers
      on long tables, consistent right-alignment of numerics. Rationale: the
      existing `.num` class at `src/report_style.py:35` already does the hard
      part; the remaining work is consistency and scan-line support.
- [ ] Task 23. Add a print stylesheet that forces the light theme, expands all
      city panels or the selected one only, and strips interactive chrome.
      Rationale: a scientific report will be printed or exported to PDF, and a
      dark theme with hidden panels prints as an unreadable, mostly-empty page.
- [ ] Task 24. Audit and fix responsive behaviour below 700px: the globe, the
      scorecard bars at `src/report_style.py:50-67` and the wide tables are the
      three known breakages. Rationale: the absolute-positioned band and anchor
      labels on the scorecard bar are the most fragile element on the page and
      will collide at narrow widths.

### Phase 5 — Three themes plus weather-reactive accenting

- [ ] Task 25. Restructure `src/report_style.py:3-90` into layered token sets:
      a semantic token contract (`--surface`, `--ink`, `--accent`, `--good`,
      `--warn`, `--bad`, `--grid`, `--line`) and three theme blocks selected by
      a `data-theme` attribute on the root element. Rationale: a theme system
      is only maintainable if components consume semantic names; the current
      names are already close, so this is a rename-and-partition rather than a
      rewrite.
- [ ] Task 26. Define the three themes with distinct purposes rather than three
      colour whims: **Observatory** (dark, low-glare, luminous data marks — the
      high-tech default), **Daylight** (the current light scientific look,
      refined), **Blueprint** (high-contrast, near-monochrome, print- and
      accessibility-oriented). Rationale: themes justified by use case stay
      internally consistent as the page grows; themes justified by taste drift.
- [ ] Task 27. Remove every hardcoded hex colour from `src/report_render.py:82-129`
      and the globe renderer, replacing them with `currentColor` or CSS custom
      property references. Rationale: this is the specific blocker that would
      otherwise leave the calibration curve stuck in light-mode colours while
      the rest of the page goes dark — the most visible possible theming bug.
- [ ] Task 28. Verify every theme against WCAG AA contrast for body text,
      muted text, tags and the good/warn/bad semantic trio, and record the
      measured ratios. Rationale: the study's credibility rests on the reader
      being able to read qualifying caveats set in muted small text; those are
      exactly the elements a dark theme degrades first.
- [ ] Task 29. Add a weather-reactive accent layer as a separate `data-sky`
      attribute (clear, cloud, rain, snow, storm, fog, night) that modulates
      **only** the hero backdrop, globe ocean tint and accent hue — never any
      text/background contrast pair. Rationale: this delivers the requested
      weather influence without letting a rainy city silently degrade
      legibility. Contrast is a correctness property; ambience is decoration,
      and decoration must not be able to break correctness.
- [ ] Task 30. Make the weather-reactive layer an explicit user toggle, default
      on, and gate all associated motion behind `prefers-reduced-motion`.
      Rationale: ambient tinting is polarising and animation is an accessibility
      hazard; both need an off switch that the page remembers.
- [ ] Task 31. Derive the `data-sky` value from the forecast payload via a
      single documented mapping function, with a neutral default when no
      forecast is available. Rationale: one mapping in one place keeps the
      visual claim ("this is what the sky looks like there now") honest and
      auditable rather than scattered across the stylesheet.

### Phase 6 — Current forecast for the selected city

- [ ] Task 32. Add `src/forecast_snapshot.py` fetching a short forecast for each
      covered city from the live endpoint at `src/config.py:203`, reusing the
      existing `fetch_json` caching layer, and writing a snapshot artefact with
      an explicit fetch timestamp. Rationale: reusing the established fetch and
      cache machinery means the new call obeys the same rate-limiting and
      offline-replay behaviour as every other network call in the project.
- [ ] Task 33. Embed the snapshot in the page and render a compact forecast
      strip for the selected city — condition, temperature range, and the
      stated rain probability. Rationale: this preserves the offline guarantee
      completely; the page ships with data rather than reaching for it.
- [ ] Task 34. **Tie the forecast strip back to the study's own finding**:
      annotate the displayed PoP with what that probability has historically
      meant *in that city*, taken from its reliability table. Rationale: this is
      the highest-value idea in the entire request. A generic forecast widget is
      a commodity; a forecast widget that says "it says 20%, and in this city
      20% has meant 31%" is the study delivering its result at the exact moment
      of use. It should be treated as the headline feature, not a decoration.
- [ ] Task 35. Stamp the strip with the snapshot age and add an opt-in "refresh
      live" control that fires a network request only on explicit user click,
      failing silently back to the snapshot. Rationale: this is the one
      deliberate relaxation of the no-network rule, so it must be user-initiated,
      clearly labelled, and non-fatal when it fails.
- [ ] Task 36. Exclude the forecast snapshot from the byte-identical
      reproducibility check, or add a flag that omits it, and document the carve-out
      in the plan record. Rationale: a live timestamp makes byte-identical
      regeneration impossible by construction. The verification criterion must be
      amended openly rather than quietly abandoned.

### Phase 7 — Verification

- [ ] Task 37. Add an automated check asserting the generated page contains no
      external resource references (`http://`, `https://`, `//` in `src`/`href`,
      or `@import`) outside plain anchor text. Rationale: the offline guarantee
      is currently maintained by discipline alone; this phase adds enough new
      markup that it needs a machine check.
- [ ] Task 38. Add a check that every city slug in the JSON payload has a
      corresponding panel and marker, and vice versa. Rationale: the globe and
      the panels are generated from the same tables but by different code paths;
      a mismatch would present as a dead marker, which is worse than a missing one.
- [ ] Task 39. Re-run the existing guarantees: Brier identity self-check, all
      glossary anchors resolving, no hardcoded result numbers. Rationale: the
      renderer restructuring in Phase 2 touches the code path that produces
      every number on the page.
- [ ] Task 40. Manually render and visually inspect each theme in both light and
      dark OS preferences, and at desktop and mobile widths. Rationale: the
      previous session's third documented error — a figure contradicting its own
      caption — was catchable only by rendering the page. Text extraction does
      not find visual defects.

---

## Verification Criteria

- `./run_all.sh` regenerates `report.html` end to end including the capitals
  stage, on both a cold and a warm cache.
- The page opens from `file://` with networking disabled, and the automated
  external-reference check returns no matches.
- Excluding the forecast snapshot block, two consecutive runs produce identical
  output.
- Every covered capital is clickable on the globe and switches every per-city
  section; every excluded capital shows its exclusion reason.
- Sections that exist only for Bucharest are never displayed under another
  city's name, and each city panel states which analyses it carries.
- All three themes pass WCAG AA for body text, muted text and the good/warn/bad
  semantic trio, with measured ratios recorded.
- The weather-reactive layer changes no text/background contrast pair, verified
  by comparing computed contrast ratios with the layer on and off.
- Disabling JavaScript still yields a readable report containing the full
  analysis for every city.
- The generated file stays under the declared size budget, enforced by assertion.
- The forecast strip shows, for the selected city, both the stated probability
  and its historically observed meaning drawn from that city's own reliability
  table.
- Printing produces a legible light-theme document with no interactive chrome.

## Potential Risks and Mitigations

1. **File size explosion.** Per-city content, coastline geometry and existing
   base64 PNGs compound.
   Mitigation: budget assertion in Task 3; per-city visuals restricted to SVG;
   PNGs confined to global sections; coastline quantised.
2. **The offline guarantee erodes by a thousand cuts.** Each new interactive
   feature creates a temptation to reach for a library or a font.
   Mitigation: the automated check in Task 37 makes the rule enforced rather
   than remembered.
3. **Visual polish outrunning evidential strength.** A dark, animated globe
   reads as far more authoritative than ~2 years of data per city supports.
   Mitigation: lead the cross-city section with bootstrap rank intervals
   (Task 10); encode sample size directly in the marker (Task 14); keep the
   existing effective-sample-size caveats prominent.
4. **Cross-city comparison inviting a "best capital" reading** — precisely the
   claim `src/capitals.py:447-448` refuses to make.
   Mitigation: never present a bare ordered list; rank intervals accompany every
   ranking, with an explicit statement that the ordering is mostly unresolvable.
5. **Theme switching breaking the SVG figures**, whose colours are currently
   baked into attributes at `src/report_render.py:82-129`.
   Mitigation: Task 27 is a prerequisite for Phase 5 acceptance, not a follow-up.
6. **Weather-reactive theming harming readability.**
   Mitigation: architecturally separate `data-sky` from `data-theme`; restrict
   it to non-text surfaces; toggle with persistence; contrast diff in verification.
7. **Determinism lost to the live forecast.**
   Mitigation: snapshot excluded from the identity check, with the carve-out
   documented (Task 36) rather than silently dropped.
8. **Renderer restructuring changing a published number.**
   Mitigation: capture the current Bucharest metrics before Phase 2 and diff
   them after; the Brier identity self-check remains in place.
9. **Capitals stage failing on a cold cache** and taking the whole pipeline with it.
   Mitigation: Task 2 degradation path, matching the contract `load_capitals()`
   already implements.

## Alternative Approaches

1. **Flat Europe map instead of a globe.** Simpler, no projection maths, better
   use of space for a European-only dataset, and easier to make accessible.
   Loses the requested high-tech feel and the headroom for non-European
   expansion. Strongest fallback if Phase 3 overruns.
2. **Vendor D3 + TopoJSON into the file.** Robust, well-tested projection and
   geometry handling. Costs a large embedded blob, contradicts the project's
   dependency-minimal character, and brings far more capability than a static
   orthographic globe needs.
3. **Multi-page output — one HTML file per city plus an index.** Smaller files
   and simpler code. Forfeits single-file portability and instant switching,
   which are the qualities that make the current artefact shareable.
4. **Client-side computation of per-city metrics from an embedded dataset.**
   Smaller output and arbitrary interactive slicing. Creates a second
   implementation of the statistics in JavaScript that could disagree with the
   Python — the exact failure mode `src/report.py:1-7` was written to prevent.
5. **Theme by OS preference only, without explicit switching.** Less UI surface.
   Fails the explicit request for three selectable themes and removes the
   print/accessibility theme, which has no OS-preference equivalent.
