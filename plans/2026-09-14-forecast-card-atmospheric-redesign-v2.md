# Forecast Card — Atmospheric Redesign (v2)

Supersedes `plans/2026-09-13-forecast-section-ui-redesign-v1.md`, which has been
implemented and verified (109/109 checks). That version fixed the *content*
problem — the city's observed frequency is now the dominant figure — but left a
three-box dashboard widget. This plan replaces the composition.

## Objective

Turn the forecast strip from three bordered boxes into a single atmospheric
panel: one edge-to-edge weather scene, typography with real scale, and a
comparison graphic that makes the gap between *stated* and *observed* the most
legible thing on the page.

## Diagnosis of the shipped v1 card

Ranked by contribution to "boring and full of dead space":

1. **The widest column holds the least content.** `grid-template-columns: 152px
   minmax(0,1fr) minmax(0,1.15fr)` (`src/web/app.css:427-429`) gives ~430px to
   four short lines. The dead space is structural, not a padding value.
2. **Two hairline dividers** (`src/web/app.css:443`, `:511`) cut the card into
   thirds. Three bordered cells is the visual grammar of a settings panel.
3. **The illustration is an icon in a box.** 104px centred in a 152px tile
   (`src/web/app.css:452`) with symmetrical air around it. Nothing is cropped,
   nothing bleeds, so nothing reads as a scene.
4. **The ring cannot do its job.** 40% and 46% are indistinguishable as arcs.
   It spends 96px of the card's most valuable column saying less than its own
   caption does.
5. **The weather is quarantined to 16% of the card.** The sky gradient stops at
   the first divider, so the card reads as grey furniture with a coloured chip
   attached rather than as a thing that knows what the weather is.
6. **No sense of time.** One request already carries the whole day; the card
   shows a single number and cannot answer "when?".

### A bug to fix on the way through

CSS `transform` animations **override SVG `transform` presentation
attributes**. `.s-cloud.front` (`src/web/app.css:585`) animates `transform`
while the element carries `transform="translate(-4 8)"`, so the authored
placement is discarded on the first frame. Any animated SVG shape must have its
placement on a wrapping `<g>` and its motion on a different element.

---

## Design direction

**One panel, four layers, no internal borders.**

```
┌────────────────────────────────────────────────────────────────────┐
│  ~~ sky gradient + parallax cloud layers + precipitation + sun ~~   │
│                                                                    │
│  BUCHAREST NOW                    THE FORECAST SAYS                │
│   13°                             40% chance of rain today         │
│   Mainly clear · H 23° L 13°                                       │
│   as of 08:00                     IN BUCHAREST, THAT HAS MEANT     │
│   ▁▂▃▅▆▇▆▅▃▂▁▁  next 12 hours     46%   ( in line with it )        │
│                                                                    │
│                        forecast 40%                                │
│   0% ├──────────────────────●══○──────────────────────────┤ 100%   │
│                              here it rained 46%                    │
│   In Bucharest, forecasts around this level have been followed by  │
│   rain 46% of the time (72 days).                                  │
└────────────────────────────────────────────────────────────────────┘
```

Four stacked layers inside one `position: relative` card:

1. `.fcsky` — the gradient, full bleed, using the existing `--sky-1`/`--sky-2`
   tokens with the hero's dark-base mix.
2. `.fcart` — an inline SVG **panorama**, `viewBox="0 0 360 120"` with
   `preserveAspectRatio="xMidYMid slice"`, so it crops and fills like a
   photograph. Soft, low-contrast, mostly in the upper band; exactly one crisp
   focal element per condition (sun, moon, bolt).
3. `.fcscrim` — a vertical dark scrim, `rgba(3,9,16,.30)` at the top to
   `rgba(3,9,16,.90)` at the bottom. This is the **contrast guarantee**: it is
   weather-independent, so the floor it provides cannot be bargained away by a
   pale sky.
4. Content — two columns over the scrim, plus a full-width rail beneath them.

**The gap rail replaces the ring.** One 0→100% rail spanning the card's full
width, with the stated probability marked above it and the observed frequency
marked below it, and the segment between them filled. The distance between the
two marks *is* the finding. On a 900px card, six points is ~54px of daylight —
unmissable, where the ring showed nothing. A badly calibrated city produces a
dramatic bar, which is the honest outcome.

**The signature motion:** the observed mark starts *on top of* the stated mark
and slides out to its true position as the fill grows behind it — "this is what
they said… this is what actually happened here."

**The hourly ribbon fills the dead space with information.** Twelve bars from
the same request (`&hourly=precipitation_probability&forecast_days=2`), sliced
from the current hour. No new round trip, no computation — a slice of an array
by timestamp.

### Why text on the weather layer is allowed here

`src/web/app.css:124-127` forbids the weather layer from touching *tokens* that
carry text contrast, and the v1 plan's constraint 2 permits a weather-reactive
surface that "guarantees its own contrast floor, as the hero already does". The
hero paints white text straight onto this gradient (`src/web/app.css:246-255`)
and is verified across 7 skies × 3 themes (`src/check_site.py:861-928`). This
card takes the same deal and submits to the same sweep — plus a scrim the hero
does not have. Blueprint opts out entirely.

---

## Implementation Plan

### Phase 1 — Composition

- [ ] Task 1. **Replace the three-column grid with a layered panel.** `.fc`
      becomes `position: relative; overflow: hidden`, holding `.fcsky`,
      `.fcart`, `.fcscrim` as absolutely positioned siblings and `.fcbody` +
      `.gap` + `.means` as flow content. Delete both internal borders
      (`src/web/app.css:443`, `:511`) and the `.fcscene` column entirely.
      Rationale: the dividers and the fixed 152px tile are the two structural
      causes of "dashboard widget"; no amount of restyling survives them.

- [ ] Task 2. **Rebalance the columns to `minmax(0,.85fr) minmax(0,1.15fr)`
      with a 32px gap**, conditions left, verdict right, and no fixed-width
      column anywhere. Rationale: removes the dead space directly — the widest
      column must hold the most content, which is the inverse of today.

- [ ] Task 3. **Give the type real scale**: temperature ~60px, observed
      frequency ~50px, kickers 11.5px uppercase tracked, supporting text
      13-13.5px. Card height lands ~260-280px. Rationale: the current 36px/40px
      figures floating in half-empty columns are what "boring" looks like at
      the typographic level.

- [ ] Task 4. **Define a card-local palette** on `.fc`: `--fc-ink: #fff`,
      `--fc-ink-2: rgba(255,255,255,.74)`, `--fc-ink-3: rgba(255,255,255,.5)`,
      `--fc-line: rgba(255,255,255,.22)`, plus `--fc-good`, `--fc-bad`,
      `--fc-accent` tuned for a dark scrim. Rationale: `--good`/`--bad` are
      tuned against `--panel`; Daylight's `--bad` (#b23a2c) on a dark scrim is
      unreadable. Scoping them to the card is the same move `--sky-ink` makes,
      and it keeps every other component's tokens untouched.

- [ ] Task 5. **Make Blueprint opt out wholesale**: hide `.fcsky` and
      `.fcscrim`, map the `--fc-*` tokens onto the theme tokens, render the
      panorama as ink outlines at low opacity on `--panel`. Rationale: the
      theme's entire premise is no large colour fills
      (`src/web/app.css:104-107`); a full-bleed gradient is the most direct
      possible contradiction of it.

### Phase 2 — The panorama

- [ ] Task 6. **Rebuild the seven scenes as wide panoramas** in a 360×120
      viewBox rendered with `slice`, replacing the 100×100 icons at
      `src/web/app.js:238-311`. Composition rules: soft masses at .10-.34
      opacity, one crisp focal element, elements cropped by the card edge, the
      focal body in the upper band where no text sits. Rationale: this is the
      single largest contributor to "stunning" — an icon centred in a tile
      cannot read as weather no matter how well it is drawn.

- [ ] Task 7. **Place every animated shape with a wrapping `<g>`.** Placement
      transform on the group, motion transform on a different element.
      Rationale: fixes the live bug where CSS keyframes discard the authored
      SVG `transform` attribute; without this, parallax placement silently does
      not happen.

- [ ] Task 8. **Give clouds genuine parallax**: three layers at different
      scales, opacities, drift amplitudes (`--amp`) and durations (84-148s).
      Precipitation falls across the *whole* card width (16-20 elements), not
      under one cloud. Rationale: depth is what separates atmosphere from
      illustration, and it costs only `transform`/`opacity`.

- [ ] Task 9. **Scatter deterministically**, via an index-based generator, not
      `Math.random()`. Rationale: the rain must not reshuffle on every city
      switch, and a screenshot comparison must be reproducible.

- [ ] Task 10. **Keep the WMO table the single source of truth**
      (`src/web/app.js:211-219`) — extend the existing rows, do not add a
      second lookup.

### Phase 3 — The gap rail

- [ ] Task 11. **Delete the ring** (`src/web/app.js:313-335`,
      `src/web/app.css:539-549`) and replace it with a full-width 0→100% rail:
      base rail, filled span between the two values, a tick-and-hollow-dot for
      stated, a filled dot for observed. Rationale: the ring is the card's
      largest element and its least informative one.

- [ ] Task 12. **Put the two labels on opposite sides of the rail** — stated
      above, observed below — positioned with
      `left: clamp(58px, N%, calc(100% - 58px))` and `translateX(-50%)`.
      Rationale: two labels six points apart collide on the same line, and
      clamping keeps the extreme values (0%, 100%) inside the card's padding.

- [ ] Task 13. **Animate the reveal**: the observed mark starts at the stated
      position and slides to its own while the span grows behind it.
      Rationale: this is the card's argument rendered as motion, and it is the
      one animation here that carries meaning rather than polish.

- [ ] Task 14. **Keep every position a pure lookup.** Offsets come from `pop`
      and `bin.obs` exactly as shipped by `src/city_report.py:357-360`;
      `Math.min`/`Math.max` to order the span ends is geometry, not statistics.

### Phase 4 — The next twelve hours

- [ ] Task 15. **Add `&hourly=precipitation_probability&forecast_days=2`** to
      the existing request (`src/web/app.js:~400`). Use `forecast_days`, not
      `forecast_hours`: an unrecognised parameter is an HTTP 400, and a 400
      hides the entire card. Rationale: one request, no new failure mode.

- [ ] Task 16. **Select the next twelve hours by timestamp comparison** against
      `current.time` and render as a bar ribbon in the left column. Display of
      vendor values only — no smoothing, no named peak, no aggregate.
      Rationale: answers "when?", which the card has never been able to, and it
      is what turns the left column's dead space into content.

- [ ] Task 17. **Wire the bars into the existing delegated tooltip** via
      `data-tip` (`src/web/app.js:87-102`), and label the group
      `role="img"` with an `aria-label`. Rationale: a per-element handler dies
      with the markup, which is replaced wholesale on every city switch.

- [ ] Task 18. **Degrade silently when `hourly` is absent** (fewer than six
      usable hours ⇒ render nothing). Rationale: the verification mock does not
      serve hourly data, and a half-empty ribbon is worse than none.

### Phase 5 — Verification

- [ ] Task 19. **Add a card contrast sweep** mirroring
      `check_sky_contrast` (`src/check_site.py:861-928`): 7 skies × 3 themes,
      hiding the card's text, screenshotting, and sampling the pixels behind
      `.temp`, `.obsnum`, `.poptitle` and `.means` across the card's full
      width. Floor: 4.5:1 for the large figures, 4.5:1 for the captions.
      Rationale: this is the price of putting text on the weather layer, and it
      is the same price the hero already pays.

- [ ] Task 20. **Assert the rail is driven by the city's own number**: the
      observed mark's computed `left` percentage must equal
      `round(bin.obs * 100)`. Rationale: a mark at a plausible-but-wrong offset
      is invisible to review and is exactly the "Bucharest's table for every
      city" failure in graphical form.

- [ ] Task 21. **Update the existing selectors** in the v1 checks:
      `.s-drop`/`.s-disc` become the new panorama classes
      (`src/check_site.py:508-510`), and the computed-colour card contrast
      assertions (`src/check_site.py:529-540`) are replaced by Task 19's pixel
      sweep, because the verdict column no longer has a background colour of
      its own to read.

- [ ] Task 22. **Assert the ribbon renders twelve bars** when the mock serves
      hourly data, and none when it does not.

- [ ] Task 23. **Re-run the full harness**: outage path still hides the card,
      reduced motion still stops every loop, no clipping at 390/700/860/1280px,
      byte-determinism, and `FIRST_LOAD_BUDGET_KB`. Current headroom is 20 KB
      (380/400) after the comment-stripping step in `src/sitebuild.py:135-251`.

---

## Verification Criteria

- No internal borders and no fixed-width column remain in `.fc`; the weather
  gradient spans the card's full width.
- Card text contrast ≥ 4.5:1 at every sampled point across 7 skies × 3 themes,
  measured from pixels, not computed colours.
- The observed mark's offset equals the city's observed frequency to the point.
- Twelve hourly bars render from the same single request; none render when the
  response omits `hourly`.
- Blueprint renders no gradient and no large colour fill.
- Under `prefers-reduced-motion`, nothing inside `#fc` is playing.
- With the API aborted, `#fc` is hidden and no error escapes.
- First load stays under 400 KB; rebuild stays byte-identical.

## Potential Risks and Mitigations

1. **A pale sky washes out the text.** `snow` ends at #7d9ab5 — 2.9:1 against
   white on its own.
   Mitigation: the scrim is weather-independent and sits between the sky and
   every glyph; Task 19 measures rather than assumes.

2. **The card grows too tall for its position** between the globe and the depth
   line.
   Mitigation: budget 260-280px and drop the ribbon's labels before its bars if
   it overruns; the ribbon is the one droppable element.

3. **Animating `left` and `width` on the rail costs layout.**
   Mitigation: one-shot, two elements, on a card that is not scrolling. If it
   shows up in a profile, switch the span to `transform: scaleX()` with a
   transform-origin at the stated mark.

4. **`hourly` pushes the response over a rate limit or adds latency.**
   Mitigation: same request, ~1 KB more; the existing 8-second abort and the
   hide-on-failure path are unchanged.

5. **Scope creep into computing statistics.** A ribbon invites "show the peak"
   and "show the recalibrated curve".
   Mitigation: Task 14 and Task 16 state the boundary; anything not present in
   `pop_map` requires a change in `src/city_report.py`, in Python.

## Alternative Approaches

1. **Keep the three-zone card, just widen the scene tile and delete the
   dividers.** Cheapest, and fixes perhaps a third of the problem — the ring
   and the dead middle column survive.
2. **Canvas particle sky.** Real volumetric rain and drifting fog. Highest
   ceiling; costs a second render loop beside the globe's, a palette-probing
   mechanism, and a much harder reduced-motion and battery story.
3. **Merge the card into the hero.** Most dramatic of all, and the sky is
   already there — but it puts the `<h1>` on a weather-driven surface and
   displaces the report's own headline with a third-party number.
