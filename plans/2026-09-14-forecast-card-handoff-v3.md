# Forecast card v2 — handoff

Status as of 2026-09-14. Written so the next session can resume without
re-deriving anything. **The build is currently RED.** Two blockers, both in
"What's left" below. Everything else is done and was passing 122/123 checks on
the last complete run.

---

## 1. Read this first: the build does not currently pass

```
first load ~470 KB (budget 470 KB)
first-load budget exceeded: 470 KB > 470 KB.
```

`src/report.py` treats this as a build failure by design. The v2 CSS consumed
the last of the headroom. Nothing else is wrong with the card.

The last verification run also aborted partway with
`ReferenceError: maskFadeK is not defined` thrown from the globe. See §4 —
this is very likely a stale `dist/` artefact, **not** a source bug, and the
comment-stripping build step has been positively cleared of causing it.

---

## 2. What was built

The card was rebuilt from the v2 plan
(`plans/2026-09-14-forecast-card-atmospheric-redesign-v2.md`). Three zones,
one panel, no internal dividers:

| Zone | Where |
|---|---|
| Full-bleed animated sky panorama | `src/web/app.js` (scene builders), `src/web/app.css:566-634` |
| Weather-independent scrim | `src/web/app.css:596-611` |
| "Now" reading (temp, condition, as-of) | `src/web/app.css:636-660` |
| Hourly probability ribbon (12 h) | `src/web/app.css:699-730` |
| The gap rail — stated vs. observed | `src/web/app.css:732-791` |

### The three design fixes that mattered

1. **The gap rail carries the thesis and now reads like it.** It was a 3px
   hairline segment on a 3px hairline track — the one thing the whole study is
   about, and the least visible mark on the card. It now stands proud of the
   track with a glow (`src/web/app.css:754-781`).
2. **The hourly bars stopped looking like disabled UI controls.** Brightness
   now tracks the value alongside height, so wet hours catch the eye and a 10%
   hour is a dim stub rather than a short button (`src/web/app.css:699-723`).
   Two earlier attempts both read as a row of greyed-out chiclets.
3. **The emoji are gone**; scenes are drawn geometry, themed from the
   stylesheet, so Blueprint gets line art on white rather than a colour fill.

---

## 3. Bugs found and fixed along the way (do not undo these)

### 3a. The card contrast check was measuring the wrong pixels

It sampled each text element's **box**, not its glyphs. `.asof` is a 398px-wide
block holding ~70px of "as of 08:00", so the leftmost sample landed on the
hourly bar underneath and reported **2.70:1 for text sitting on a perfectly
dark backdrop**. Now uses `Range.getClientRects()` for the inked extent, with a
1px inset at each end to avoid antialiasing (`src/check_site.py:1057-1090`).

### 3b. The same check photographed the page mid-scroll

`src/web/app.css:211` sets `html { scroll-behavior: smooth }`. The check called
`scrollIntoView({block:'center'})` and waited 150ms, so the screenshot caught
the page in flight — the rail caption was measured at y=995, the viewport
floor, **below the card entirely**, against the white page background, giving
1.06:1. Fixed with `behavior: 'instant'` (`src/check_site.py:1055-1063`).

Both of these were latent: the check could report confident nonsense in either
direction, including passing when it should fail.

### 3c. The responsive failure was not the forecast card

`390px: no control sits off-screen` was failing on `a.jump` sort links in the
provider league table (`src/report_providers.py:301`), which live inside
`.tablewrap` — `overflow-x: auto` by design (`src/web/app.css:864`). Controls
in a genuinely scrollable ancestor are reachable, so they are now exempted, but
only when the ancestor actually scrolls (`src/check_site.py:909-924`). A box
with `overflow-x: auto` that fits its content scrolls nowhere and would still
be hiding the control.

### 3d. Contrast fixes in the card itself

- Both gradient stops are now mixed down against the near-black base, not just
  the second (`src/web/app.css:551-558`). The comment had always claimed this;
  the code only did half of it, leaving raw `--sky-1` (fog is `#8a9296`) under
  the smallest type.
- Scrim top stop `.30` → `.52` (`src/web/app.css:596-611`). Text sits on it
  from the first pixel row; the stop has to hold near-white type over a 34%
  white cloud, not over the sky alone.
- Blueprint `--c-ink-3` takes `--ink`, not `--muted` (`src/web/app.css:616-623`).
- Blueprint `.fcsky` opacity `.55` → `.38` (`src/web/app.css:628-633`). There
  is no scrim in that theme, so **this opacity is the contrast guarantee** —
  overlapping strokes stacked to `#737373` and put the ribbon labels at 4.43:1.

---

## 4. The `maskFadeK` question — evidence gathered, conclusion not confirmed

The error came from `globe.cae02dda.js`, referencing identifiers `maskFadeK`,
`rho2`, `MASK_FADE_LO2` that **appear nowhere in `src/web/globe.js`**. The
source at that site reads:

```js
var tg = (maskQk / vz - MASK_FADE_Q0) * MASK_FADE_K;
```

These are different *revisions*, not different comment densities.

**The comment-stripping step is cleared.** Running `sitebuild.ship()` on the
current `src/web/globe.js` produces output that is **byte-identical** to the
shipped file and contains `MASK_FADE_Q0`, with no `maskFadeK` anywhere:

```
stripped(current src) len: 54789
shipped len:               54789
identical: True
```

Note the stripper is properly state-aware (`src/sitebuild.py:162-226`) — it
tracks strings, template literals and regex literals, so it is not the naive
regex that would explain corruption.

**Loose end:** the content hash of that stripped output is `642f4158`, but the
file on disk is named `globe.cae02dda.js`. Under `_digest()`
(`src/sitebuild.py`, sha256[:8] of content) those should match. Either the
comparison read a file rewritten between steps, or the hash is computed over
something other than the shipped bytes. **This is the one thing left to
confirm**, and it is the most likely explanation for a stale/ghost artefact.

`dist/` currently holds **two** globe files — `globe.cae02dda.js` and an
unstripped `globe.e21bbbee.js` (111,994 bytes, identical to source). The latter
is a leftover restored by a `git checkout -- dist/` during a stash recovery.

---

## 5. What's left, in order

1. **Clear `dist/` completely and rebuild.** This is expected to resolve the
   globe error outright.
   ```
   rm -rf dist && cd src && ../.venv/bin/python report.py
   ```
   If `maskFadeK` survives a clean build, chase the hash mismatch in §4 —
   specifically whether `add_text`/`add_bytes` hashes the pre-strip or
   post-strip bytes.

2. **Resolve the 2 KB budget overage.** Three honest options, in preference
   order:
   - Trim ~2 KB of CSS rules (not comments — comments are already stripped on
     the way into `dist` and cost nothing on the wire).
   - Defer something genuinely non-critical out of first load.
   - Raise `FIRST_LOAD_BUDGET_KB` in `src/report.py` as a deliberate, commented
     decision. **Do not do this silently** — the budget exists to catch exactly
     this.

3. **Run the full suite, including determinism** (it was skipped on recent runs
   for speed):
   ```
   cd src && ../.venv/bin/python check_site.py
   ```
   Expect 123/123. The last complete run was 122/123, the single failure being
   `blueprint: forecast card legible under every sky — worst 4.43:1 (snow)`,
   which §3d fixed but which has **not been re-verified end to end**.

4. **Optional, deliberately deferred:** Phase 5 of the v2 plan. The card still
   cannot answer "*when* today?" beyond the 12-hour ribbon.

---

## 6. Invariants — breaking any of these is a regression

- **Python computes, JS displays** (`src/web/app.js:8-10`). The annotation is a
  `pop_map` lookup. The rail's geometry may be derived from `bin.obs`/`pop`; a
  probability may **not** be computed in JS.
- **An Open-Meteo outage must not break the page.** With the API aborted, `#fc`
  hides and clears the tint (asserted at `src/check_site.py:238-239`).
- **`.pop` and `.means` must survive as the same semantic nodes** — the
  "Bucharest's table for every city" regression test selects them.
- **Reduced motion:** the global rule collapses durations to `.001ms`, which
  for an *infinite* loop means a still-running animation at pathological speed,
  not a stopped one. Scene loops are stopped by name; the harness asserts zero
  running animations under `reduced_motion="reduce"`.
- **Byte-determinism** of the build.
- The card is the one place `--sky-*` is allowed near text, and only because it
  guarantees its own floor. That guarantee is now **measured** across 7 skies ×
  3 themes — keep it that way.

---

## 7. Process note

During diagnosis I ran a `git stash`-based A/B that briefly put the
uncommitted multi-provider work into a stash entry; a rebuilt `dist/` blocked
the `pop` and it took two steps to recover. Everything was restored intact
(stash dropped, `gaprail` present, budget back to 470) — but the comparison was
invalid anyway, because HEAD predates the league table. **Diagnose against the
live DOM rather than stashing the user's uncommitted work.**
