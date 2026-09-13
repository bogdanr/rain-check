# Interactive Multi-City Calibration Report — Hosted Build, Globe, Themes, Live Forecast

**Supersedes:** `plans/2026-09-13-interactive-multi-city-report-globe-and-themes-v1.md`

**Why v2:** two inputs invalidated large parts of v1.
1. The offline `file://` constraint is withdrawn — the report will be hosted on
   GitHub Pages or Cloudflare Workers.
2. Phase 1 of v1 has already been implemented externally, and a cross-city
   narrative section now exists.

## Objective

Turn the report into a hosted, interactive multi-city instrument: an orthographic
globe that switches the whole report on click, three themes with an optional
weather-reactive accent layer, and a live forecast for the selected city
annotated with what that probability has historically meant there. Ship it as a
proper static site with a build step, while preserving the property that gives
the study its credibility: **every statistic is computed in Python at render
time, never in the browser.**

## Current State Assessment

### Already done since v1 (verified in the working tree)

- [x] Capitals wired into the pipeline — `run_all.sh:33-35` now runs
      `probe_capitals.py` then `capitals.py` as step 8/9. (v1 Task 1)
- [x] Multi-city artefacts loaded into the report — `_load_capitals()` at
      `src/report.py:98-114` reads `capitals_metrics`, `capitals_diagnostics`,
      `capitals_wet_bias`, `capitals_drivers`, `capitals_lead_mae` plus the
      coverage JSON.
- [x] Cross-city narrative section — `sec_capitals()` at `src/report.py:393-518`,
      already leading with rank intervals (`src/report.py:493-497`) and already
      reporting exclusions as part of the result (`src/report.py:506-517`).
      This substantially discharges v1 Task 10.
- [x] `tr.hl` row-highlight style added at `src/report_style.py:37`.

### What still blocks the interactive report

1. **No city dimension in the renderer.** `compute()` at `src/report.py:46-78`
   loads exactly one `pop` table; all 13 `sec_*` functions close over that
   single dict. `sec_capitals` compares cities but cannot *become* a city.
   The per-city daily pairs needed to fix this already exist in
   `capitals_pop.parquet`, written by `src/capitals.py:428`.
2. **Single-file delivery is now a liability, not a feature.** `embed_png()` at
   `src/report_render.py:133-136` base64-inlines every figure, and `main()` at
   `src/report.py:774-807` emits one monolithic string. On a host this is pure
   cost: no caching, no lazy loading, no parallel fetch.
3. **No theming seam in the SVG layer.** `src/report_render.py:82-129` writes
   hex colours directly into SVG attributes, so the calibration curve would not
   follow a theme switch. Unchanged from v1 and still the key blocker.
4. **No build, no deploy, no asset pipeline.** `report.py` writes one file to the
   repo root and stops.

### What hosting unlocks

| Now possible | Consequence for this plan |
|---|---|
| Real asset files | Drop base64; PNGs cached and lazy-loaded |
| Third-party libraries | Use `d3-geo` for the globe instead of hand-rolled projection |
| Code splitting / lazy fetch | Per-city JSON loaded on demand; fast first paint |
| Client-side network calls | Genuine live forecast, no baked snapshot |
| Self-hosted webfonts | Proper scientific typography with tabular numerals |
| Edge compute (Workers) | Optional cached forecast proxy |

### Assumptions made

- **GitHub Pages is the primary target**, Cloudflare Workers a documented
  alternative. Pages is free, requires no account beyond the repo, and matches
  "eventually host it". The build must not *preclude* Workers.
- Pages **project sites serve from a `/<repo>/` subpath**, so every asset
  reference must be relative or base-path aware. This is the single most common
  way a working local build breaks on deploy.
- Libraries are **vendored into the repo, not loaded from a CDN**. Pages serves
  them equally well, and it removes a third-party runtime dependency, a privacy
  leak, and an availability risk from an artefact whose whole point is
  reproducibility.
- Open-Meteo's public API is CORS-enabled and key-free, so the browser can call
  it directly; the Worker proxy is an optimisation, not a requirement.
- ~15-20 capitals survive the coverage rules, small enough that a per-city JSON
  file each is trivial.

### The invariant this plan is built around

> **JavaScript may display numbers computed by Python. It must never compute a
> statistic.**

The globe encodes BSS as colour — that is display. Recomputing a reliability
table in the browser would be a second source of truth that could silently
disagree with the study. This line is what `src/report.py:1-7` exists to protect,
and hosting does not change it.

---

## Implementation Plan

### Phase 1 — Build system and deployment target

- [ ] Task 1. Introduce a `dist/` build output with `index.html`, `assets/`
      (css, js, fonts, vendor), `figures/` and `data/`. Add `dist/` to
      `.gitignore` and make `src/report.py` write there instead of the repo
      root. Rationale: a hosted site needs a deployable directory whose contents
      are entirely generated; mixing build output into the source tree makes it
      impossible to tell what is authored and what is derived.
- [ ] Task 2. Make every asset reference base-path aware via a single
      configurable `BASE_URL` constant defaulting to `/`, injected once into the
      document as a `<base>` or used to prefix generated URLs. Rationale: a
      Pages project site serves from `/<repo>/`. Hardcoded absolute paths work
      perfectly in local preview and 404 on deploy — a failure that is invisible
      until the moment it matters.
- [ ] Task 3. Emit content-hashed filenames for CSS, JS and per-city JSON, and
      reference them from the generated HTML. Rationale: enables aggressive
      immutable caching without ever serving a stale mismatched pair of
      stylesheet and markup.
- [ ] Task 4. Replace `embed_png()` usage at `src/report_render.py:133-136` with
      a copy-to-`dist/figures/` helper returning a hashed relative URL, and add
      `loading="lazy"` plus explicit width/height to generated `<img>` tags.
      Rationale: base64 inlining defeats caching, blocks first paint and inflates
      the HTML several-fold. Intrinsic dimensions prevent layout shift as figures
      arrive.
- [ ] Task 5. Keep `embed_png` and a `--standalone` flag that produces the
      existing single self-contained `report.html`. Rationale: the single-file
      artefact is genuinely useful for archiving and emailing a frozen result,
      and the code to produce it already exists. Preserving it as an option costs
      almost nothing; deleting it discards a real capability.
- [ ] Task 6. Add a GitHub Actions workflow that runs the build and publishes
      `dist/` to Pages, with a manual dispatch trigger. Rationale: the report
      must redeploy when the data updates, and a deploy that depends on someone
      remembering a local command will drift — the same reasoning that put
      `report.py` into `run_all.sh`.
- [ ] Task 7. Decide and document the data-refresh cadence in the workflow:
      whether CI re-runs the full pipeline or only rebuilds the page from
      committed parquet artefacts. Rationale: the pipeline makes many external
      API calls and depends on `data/cache`; running it unconditionally in CI
      would be slow, rate-limited and non-deterministic. This choice determines
      whether processed artefacts must be committed, so it must be made
      explicitly and early rather than discovered.
- [ ] Task 8. Add a local preview command serving `dist/` over HTTP on the
      configured base path. Rationale: `file://` and HTTP differ on fetch, CORS,
      module loading and path resolution; previewing under the wrong one hides
      exactly the bugs this phase introduces.

### Phase 2 — Per-city data layer

- [ ] Task 9. Split `compute()` at `src/report.py:46-78` into `compute_global()`
      (robustness, benchmarks, events, hourly, cross-city, glossary, roadmap) and
      `compute_city(city)` reading per-city pairs from `capitals_pop.parquet`.
      Rationale: the structural change everything else rests on. An explicit
      split, rather than threading a city parameter through every function, makes
      it self-evident in code which claims are per-city and which are not.
- [ ] Task 10. Classify every `sec_*` function as per-city or global and encode
      the classification. Per-city: `sec_answer`, `sec_curve`, `sec_season`.
      Global or Bucharest-only: `sec_hourly`, `sec_events`, `sec_bench`,
      `sec_robust`, `sec_recal`, `sec_capitals`, `sec_improve`, `sec_gloss`.
      Rationale: the hourly track reads Bucharest ISD stations at
      `src/report.py:132-156`. Rendering it under a "Paris" heading would be a
      fabrication, not a layout bug.
- [ ] Task 11. Emit one JSON file per city into `dist/data/cities/`, containing
      the pre-rendered HTML fragments for its per-city sections plus its numeric
      summary, and a small `index.json` with the marker data for all cities.
      Rationale: pre-rendering fragments in Python reuses `reliability_svg` and
      `scorecard` unchanged, upholding the no-statistics-in-JS invariant, while
      per-file splitting keeps the initial payload to one city.
- [ ] Task 12. Inline the default city (Bucharest) into the initial HTML and
      lazy-fetch the rest on selection, with a prefetch on marker hover.
      Rationale: the page must be fully readable on first paint without waiting
      for a fetch; hover-prefetch makes subsequent switches feel instantaneous
      without loading twenty cities nobody asked for.
- [ ] Task 13. Add a visible depth badge per city stating which analyses it
      carries, with Bucharest marked as the deep case. Rationale: a reader who
      selects Lisbon and finds fewer sections must understand that this is a
      data-availability fact about their city, not a broken page.
- [ ] Task 14. Ensure every city view still renders without JavaScript, via
      `<noscript>` links to per-city URLs or a server-rendered full listing.
      Rationale: the analysis is the product. A search engine, a reader-mode
      client, or a failed script load must not reduce the report to an empty
      shell.

### Phase 3 — The globe

- [ ] Task 15. Vendor `d3-geo` (plus a minimal `d3-selection`/`d3-drag` or
      hand-written equivalents) and a world-atlas TopoJSON at 110m into
      `assets/vendor/`, pinned by version and recorded with licence attribution.
      Rationale: hosting removes the reason to hand-roll orthographic projection.
      `d3-geo` handles projection, clipping, graticule and geodesic paths
      correctly, including the antimeridian and back-hemisphere cases that a
      hand-rolled version gets subtly wrong.
- [ ] Task 16. Render an orthographic globe — ocean sphere, graticule, land,
      subtle limb shading — with pointer-drag and keyboard rotation, inertia
      gated behind `prefers-reduced-motion`, auto-oriented on load to the
      centroid of the covered capitals. Rationale: SVG or Canvas both work here;
      choose Canvas if marker count and redraw cost warrant it, SVG if
      per-marker DOM accessibility is preferred. Record the choice and why.
- [ ] Task 17. Plot each covered capital with size encoding sample size and
      colour encoding skill on a colour-blind-safe, theme-aware ramp, with a
      legend stating the encoding in words. Rationale: the marker must answer
      "how good is the forecast here" at a glance, but skill from a thin sample
      is a weak claim. Encoding both in one mark keeps the caveat attached to the
      number rather than exiled to a footnote.
- [ ] Task 18. Make markers focusable and activatable by click, Enter and arrow
      keys, driving the city switch, reusing the existing `#tip` tooltip from
      `src/report.py:32-43` for hover and focus. Rationale: one tooltip
      implementation, not two; and a globe that only responds to a mouse
      excludes keyboard and assistive-technology users from the primary
      navigation control.
- [ ] Task 19. Render excluded capitals as distinct muted markers carrying their
      exclusion reason in the tooltip, sourced from the same
      `capitals_diagnostics` data already narrated at `src/report.py:506-517`.
      Rationale: the exclusions are already framed as part of the result; showing
      them on the map turns an apparent hole in coverage into a visible statement
      about data quality.
- [ ] Task 20. Provide a degradation path: a styled city list if the globe script
      or geometry fails to load, and a `prefers-reduced-motion` static
      projection. Rationale: no decorative feature may be able to hide the
      analysis.

### Phase 4 — Navigation, information architecture, readability

- [ ] Task 21. Add a sticky top bar with the city selector (name, country, skill
      chip), theme control and section jump menu, collapsing on narrow screens.
      Rationale: selected city becomes persistent state, and state the user can
      change must stay visible or they lose track of what they are reading.
- [ ] Task 22. Add a keyboard-navigable command palette for city search.
      Rationale: ~18 cities is past the point where a flat list is efficient, and
      it is the expected gesture for an instrument-like interface.
- [ ] Task 23. Use real URL routes for cities (`/city/<slug>`) with History API
      navigation, falling back to hash routing, plus a Pages-compatible 404
      redirect shim. Rationale: hosting makes proper URLs available and they are
      far better for sharing, linking and search indexing than fragments. Pages
      has no server-side rewriting, so the 404 shim is what makes deep links work.
- [ ] Task 24. Persist theme and weather-layer preference to `localStorage`;
      keep the city in the URL, not storage. Rationale: a shared link must
      reproduce the sender's city; a theme is a personal preference and should
      not travel with a link.
- [ ] Task 25. Add a contents rail marking the current section. Rationale: the
      page is long and grows longer with a city dimension; orientation is
      currently supplied only by scrolling.
- [ ] Task 26. Self-host a typeface pairing with true tabular numerals and
      subset it to the glyphs used. Rationale: the page is dense with aligned
      figures; `font-variant-numeric: tabular-nums` at `src/report_style.py:35`
      is currently at the mercy of whatever the system font provides.
- [ ] Task 27. Restyle tables and scorecards for density and scanning — sticky
      headers on long tables, subtle zebra striping, consistent numeric
      alignment. Rationale: the `.num` class already does the hard part; the rest
      is consistency and scan-line support.
- [ ] Task 28. Add a print stylesheet forcing the light theme, expanding the
      selected city and stripping interactive chrome. Rationale: a scientific
      report gets printed or exported to PDF, and a dark theme with hidden panels
      prints as an unreadable, mostly blank page.
- [ ] Task 29. Fix responsive behaviour below 700px, prioritising the globe, the
      scorecard bars at `src/report_style.py:51-68` and the wide cross-city
      table at `src/report.py:489-492`. Rationale: the absolutely-positioned band
      and anchor labels on the scorecard bar are the most fragile elements on the
      page and will collide first.

### Phase 5 — Themes and weather-reactive accenting

- [ ] Task 30. Restructure `src/report_style.py:3-91` into a semantic token
      contract (`--surface`, `--ink`, `--muted`, `--accent`, `--good`, `--warn`,
      `--bad`, `--grid`, `--line`) plus three theme blocks selected by a
      `data-theme` attribute, and move the CSS out of the Python string into a
      real stylesheet asset. Rationale: theming is only maintainable when
      components consume semantic names; the existing names at
      `src/report_style.py:5-6` are already close, so this is partition and
      rename rather than rewrite. A real `.css` file also gets caching and
      editor tooling.
- [ ] Task 31. Define three themes by purpose, not taste: **Observatory** (dark,
      low-glare, luminous data marks — the high-tech default), **Daylight** (the
      current light scientific look, refined), **Blueprint** (high-contrast,
      near-monochrome, print- and accessibility-oriented). Rationale: themes
      justified by use case stay internally coherent as the page grows; themes
      justified by taste drift into inconsistency.
- [ ] Task 32. Remove every hardcoded hex colour from `src/report_render.py:82-129`
      and from the globe renderer, replacing them with `currentColor` or CSS
      custom properties. Rationale: this is the specific defect that would leave
      the calibration curve — the study's signature visual — stranded in
      light-mode colours on a dark page. Prerequisite for accepting Phase 5, not
      a follow-up.
- [ ] Task 33. Verify each theme against WCAG AA for body text, muted text, tags
      and the good/warn/bad trio, recording measured ratios. Rationale: the
      study's honesty depends on readers actually reading the qualifying caveats,
      which are set in muted small text — precisely what a dark theme degrades
      first.
- [ ] Task 34. Add the weather-reactive layer as a **separate** `data-sky`
      attribute (clear, cloud, rain, snow, storm, fog, night) modulating only the
      hero backdrop, globe ocean tint and accent hue — never any text/background
      contrast pair. Rationale: delivers the requested weather influence while
      making it structurally impossible for a rainy city to degrade legibility.
      Contrast is a correctness property; ambience is decoration, and decoration
      must not be able to break correctness.
- [ ] Task 35. Make the weather layer a user toggle, default on, with all motion
      gated behind `prefers-reduced-motion`. Rationale: ambient tinting is
      polarising and animation is an accessibility hazard; both need an off
      switch the page remembers.
- [ ] Task 36. Derive `data-sky` from the forecast payload through one documented
      mapping function, defaulting to neutral when no forecast is available.
      Rationale: a single mapping in a single place keeps the implicit claim
      ("this is what the sky is doing there") auditable instead of scattered
      through a stylesheet.

### Phase 6 — Live forecast, tied back to the study

- [ ] Task 37. Fetch a short forecast for the selected city client-side from the
      live endpoint at `src/config.py:203`, with a loading state, a timeout, and
      a silent fallback that hides the strip rather than showing an error.
      Rationale: hosting makes a genuine live forecast possible; a failed
      third-party call must never degrade the analysis, which is the actual
      product.
- [ ] Task 38. Render the forecast strip: condition, temperature range and stated
      rain probability, stamped with fetch time. Rationale: gives the globe
      selection an immediate, tangible payoff beyond statistics.
- [ ] Task 39. **Annotate the stated PoP with what that probability has
      historically meant in that city**, read from the city's own reliability
      table in its JSON payload. Rationale: the highest-value idea in the entire
      request. A generic forecast widget is a commodity; one that says "it says
      20%, and in this city 20% has meant 31%" is the study delivering its own
      finding at the exact moment of use. Treat as the headline feature, not
      decoration. Note the honest caveat: the historical mapping comes from a
      single short lead time, so the annotation must say so.
- [ ] Task 40. Optionally add a Cloudflare Worker route proxying and
      edge-caching the forecast call, behind a build flag, unused on Pages.
      Rationale: shields the upstream API from per-visitor traffic and gives one
      place to add caching. It is an optimisation, so it must not become a
      requirement for the Pages deployment to work.
- [ ] Task 41. Add a Content Security Policy allowing only self-hosted assets and
      the forecast origin, and remove inline `<script>`/`<style>` in favour of
      asset files. Rationale: a public site accepting third-party responses
      should declare exactly what it will execute and contact. This is also why
      Task 30 and Task 1 move CSS and JS out of Python strings.

### Phase 7 — Verification

- [ ] Task 42. Verify the deployed site under the real base path: every asset
      loads, deep links to `/city/<slug>` resolve, and the 404 shim works.
      Rationale: base-path and deep-link failures are invisible locally and are
      the most likely way this deployment breaks.
- [ ] Task 43. Add a check that every city in `index.json` has a JSON payload, a
      marker and a resolvable route, and vice versa. Rationale: markers and
      payloads come from the same tables by different code paths; a mismatch
      presents as a dead marker, which is worse than a missing one.
- [ ] Task 44. Re-run the inherited guarantees after the Phase 2 restructuring:
      Brier identity self-check returns exactly zero, all glossary anchors
      resolve, and no hardcoded result numbers appear in the source. Rationale:
      Phase 2 touches the code path producing every number on the page.
- [ ] Task 45. Assert determinism of the *data* artefacts — two runs from the
      same inputs produce identical per-city JSON, excluding build timestamps and
      content hashes. Rationale: the original byte-identical criterion is no
      longer meaningful for a hashed multi-file build, but the property it was
      protecting — that the page cannot drift from the data — still matters and
      needs a replacement check.
- [ ] Task 46. Set and measure a performance budget for first load (HTML, CSS,
      JS, vendor, first city payload) excluding lazy figures. Rationale: hosting
      removed the file-size cliff but introduced a latency cost; a budget keeps
      the globe from quietly becoming the reason the page is slow.
- [ ] Task 47. Manually render and inspect all three themes at desktop and mobile
      widths, in both OS colour preferences, with and without the weather layer.
      Rationale: the previous session's third documented error — a figure
      contradicting its own caption — was catchable only by looking at the
      rendered page. Text extraction does not find visual defects.
- [ ] Task 48. Add social and discovery metadata: title, description, canonical
      URL, Open Graph image generated from an existing figure. Rationale: the
      artefact is about to become public and link previews are how it will
      mostly be encountered.

---

## Verification Criteria

- `./run_all.sh` produces a complete `dist/` and the CI workflow deploys it.
- The deployed site works under a `/<repo>/` base path with no 404s, and deep
  links to individual cities resolve directly.
- Selecting any covered capital on the globe switches every per-city section;
  every excluded capital displays its exclusion reason.
- Bucharest-only sections never appear under another city's name, and each city
  states which analyses it carries.
- With JavaScript disabled the full analysis remains readable.
- All three themes pass WCAG AA for body text, muted text and the good/warn/bad
  trio, with recorded ratios; enabling the weather layer changes no measured
  contrast ratio.
- The forecast strip shows the stated probability **and** its historically
  observed meaning for that city, with the single-lead-time caveat visible.
- A forecast fetch failure leaves the report fully usable.
- Two builds from identical inputs produce identical per-city JSON payloads.
- First-load payload is within the declared performance budget.
- Brier identity self-check returns exactly zero; all glossary anchors resolve;
  no hardcoded result numbers in source.
- `--standalone` still produces a working single-file `report.html`.
- Printing yields a legible light-theme document without interactive chrome.

## Potential Risks and Mitigations

1. **Base-path breakage on Pages project sites.** Works locally, 404s deployed.
   Mitigation: single `BASE_URL` seam (Task 2), HTTP preview at the real base
   path (Task 8), post-deploy verification (Task 42).
2. **Statistics migrating into JavaScript.** The natural pull of a rich client is
   to recompute things in the browser, creating a second source of truth that
   can disagree with the study.
   Mitigation: the stated invariant, enforced by pre-rendering all
   statistic-bearing fragments in Python (Task 11).
3. **Visual polish outrunning evidential strength.** A dark animated globe reads
   as far more authoritative than ~2 years per city supports.
   Mitigation: sample size encoded directly in every marker (Task 17); rank
   intervals already lead the cross-city section at `src/report.py:493-497`;
   depth badges (Task 13).
4. **Live forecast failure degrading the page**, or upstream rate limiting once
   the site is public.
   Mitigation: silent fallback (Task 37), optional edge cache (Task 40).
5. **Theme switching breaking the SVG figures**, whose colours are baked into
   attributes at `src/report_render.py:82-129`.
   Mitigation: Task 32 is a Phase 5 acceptance gate, not a follow-up.
6. **Losing the single-file artefact**, which is genuinely useful for archiving.
   Mitigation: retained behind `--standalone` (Task 5).
7. **CI cost and non-determinism** if the full pipeline runs on every deploy.
   Mitigation: the cadence decision is its own explicit task (Task 7) rather
   than an emergent default.
8. **Renderer restructuring silently changing a published number.**
   Mitigation: capture current Bucharest metrics before Phase 2 and diff after;
   Brier identity check stays in place (Task 44).
9. **Privacy and supply-chain exposure** from third-party CDN assets on a public
   site. Mitigation: vendor everything (Task 15) and declare a CSP (Task 41).

## Alternative Approaches

1. **Keep the single self-contained file and host that.** Zero build system,
   trivially portable. Forfeits caching, lazy loading and code splitting, and
   the base64 figures make it slow on a first visit — the case against it is now
   performance rather than capability.
2. **Adopt a JS framework and a bundler (Vite + Svelte/React).** Better
   ergonomics for the interactive layer. Adds a toolchain and a node dependency
   to a Python project, and invites Risk 2 by making client-side computation the
   path of least resistance.
3. **Three.js / globe.gl textured 3D globe.** Higher visual impact. Much heavier,
   materially harder to make accessible and readable, and a textured earth
   communicates less about calibration than a clean cartographic projection does.
4. **Flat Europe map instead of a globe.** Simpler, better use of space for an
   all-European dataset, easiest to make accessible. Loses the requested
   high-tech feel and the headroom for non-European expansion. Strongest fallback
   if Phase 3 overruns.
5. **Static per-city pages with no client-side switching.** Simplest correct
   implementation, excellent for SEO. Loses the instant switching and the globe
   as a live control surface, which is the core of the request.
6. **Cloudflare Workers as primary instead of Pages.** Better caching control and
   native edge compute for the forecast proxy. Requires an account and more
   deployment machinery than the project currently needs; kept as the documented
   alternative.
