# Blog Post: Scoring Weather Forecasts Like a Superforecaster

Target: `../bogdanr.github.io/_posts/2026-09-XX-keeping-score-weather.md` + `assets/js/rain-check-score.js` + `assets/css/rain-check-score.css`

## Objective

A dense, ~2,000-word post in the author's voice explaining the scoring tools from *Superforecasting* Ch. "Keeping Score" (calibration curves, Brier score, Brier Skill Score) and how they were applied in the rain-check project to measure how good weather forecasts really are across 109 cities worldwide — with interactive/animated charts, honest null results, and clearly flagged novel contributions.

## Style Constraints (from existing posts)

- Front matter: exactly `layout`, `title`, `description` (one witty sentence), `category`, `tags`
- Intro: 2–4 short paragraphs, no heading, personal hook, first person, roadmap promise
- Sections: `###` headings, short/playful titles; `####` for numbered theory subsections
- Interactive charts: empty anchor `<div class="rc-widget" id="...">` inline at the relevant section; one JS + one CSS file loaded at post end; all stats precomputed (Python), JS only renders/animates
- Tables: markdown pipe tables, right-aligned numerics
- Ending: numbered bolded "Takeaways" + "What next?!" teaser + repo link
- Voice: casual, self-deprecating, honest about mistakes, inline Wikipedia/source links, occasional `:)`

## Implementation Plan

- [ ] 1. Create the post file with front matter: title (e.g. "Keeping Score: How Good Is Your Weather Forecast, Really?"), witty description (e.g. "Your weather app says 99%. It's lying. I have 109 cities of evidence."), category `Data`, tags `[forecasting, statistics, weather, python]`
- [ ] 2. Write intro (no heading): hook = reading *Superforecasting*, the "Keeping Score" chapter claims forecasters rarely know how good they are; realization that my weather app makes probability claims every day and nobody ever checks them; promise: theory → what I built → what I found → where else this applies
- [ ] 3. Section `### The theory, in three charts` — three `####` subsections, each with an interactive widget:
  - `#### 1. Calibration curves` — widget: animated reliability diagram where a slider drags the forecast probability and observed frequency dots snap into bins; shows "perfect" diagonal vs a biased curve; consistency band fades in to teach sampling noise
  - `#### 2. Brier score` — widget: animated mean-squared-error demo; small scatter of (forecast, outcome) points, squared-error bars grow/shrink as user hovers a forecast level; compare "always say 27%" (climatology) vs actual forecasts
  - `#### 3. Brier skill score` — widget: bar race / tug-of-war animation of skill vs baselines (climatology = 0, persistence, perfect = 1), with the Murphy decomposition (reliability − resolution + uncertainty) as animated stacked bars
- [ ] 4. Section `### What I actually did` — short: Open-Meteo forecast archive (15 models, 11 orgs) vs GHCN-Daily rain gauges + NOAA ISD hourly reports + ERA5 cross-check; 731 days in Bucharest deep-dive, 16 European capitals, then 109 cities worldwide; equal-count binning, block bootstrap CIs respecting week-long persistence; isotonic recalibration scored out-of-sample. Mention the per-city rain-day convention trap (would have corrupted a third of the map) as the "dumb bug" narrative beat
- [ ] 5. Section `### The results` — the headline numbers, dense, with widgets + one table:
  - Widget: reuse the draggable globe pattern (simplified) with skill-tier markers over 109 cities; or a hover-highlight multi-city calibration overlay
  - Bucharest scorecard table: skill 0.41, Brier 0.1171, base rate 27%; "99% really means ~82%, 0% still rains 5% of days"
  - Inverted wet bias: low PoPs under-forecast in 107/109 cities, high PoPs over-forecast in 103/109 — opposite of the consumer-provider bias in the literature
  - Hourly twist: "98% means 97%" hour-by-hour; daily overconfidence was a 0.2 mm threshold artefact
  - Honest nulls: recalibration gains 0.16% out-of-sample; the capitals-era r = −0.77 skill-vs-rain-rate correlation collapsed at 109 cities (what survives: calibration error grows with rain rate, r = +0.56); temperature events stay skilful a week out (0.66–0.84), rain decays to 0.12
  - Provenance audit: Open-Meteo's silent `best_match` model routing, worth up to +0.42 skill in Andorra
- [ ] 6. Section `### What's new here` — explicit bullet list of novel contributions: (a) first public per-city station-verified PoP calibration at 109-city scale (ForecastWatch only does country-level temperature tolerance), (b) consistency-band technique separating miscalibration from sampling noise, (c) provenance-audit methodology exposing silent model routing, (d) the wet-bias inversion at scale including non-European models, (e) reporting null results and rank ranges instead of league-table winners
- [ ] 7. Section `### Where else you can use this` — short bullets: your own forecasts (Superforecasting practice / prediction markets), LLM confidence scores, sports odds, medical screening probabilities, "any system that says X% — score it"
- [ ] 8. Section `### Replicate it yourself` — `sh` fenced block with the `run_all.sh` stages and a pip install line; link to the rain-check repo
- [ ] 9. Section `### Takeaways` — numbered bolded principles (aim for 5–6, e.g. "Calibration is checkable — '99%' is a testable claim", "Skill scores only mean something against a baseline", "Sample size lies: use consistency bands", "Null results are results"), then "What next?!" teaser (consumer-app logger: is the forecast on your phone honest?) + repo link
- [ ] 10. Create `assets/js/rain-check-score.js` hydrating all `rc-widget` divs (follow the `fono-llm-speed.js` convention); precompute all stats/curves into a small JSON payload (from `dist/data/cities/*.json` / report data) so JS only renders
- [ ] 11. Create `assets/css/rain-check-score.css` matching the blog's look; load both at post end

## Verification Criteria

- [ ] Post is ~2,000 words / ≤ ~250 lines of markdown, dense, no filler
- [ ] Every widget is placed at its concept's section, not grouped at the end
- [ ] All numbers match `report.html` (skill 0.41, Brier 0.1171, 107/109, 103/109, +0.42 Andorra, 0.16% recalibration gain, r = +0.56)
- [ ] Front matter matches the 5-field convention; description is witty one-liner
- [ ] Takeaways are numbered and bolded; "What next?!" teaser present; repo linked
- [ ] Widgets work with JS disabled (static fallback images or graceful empty state)

## Potential Risks and Mitigations

1. **Theory section bloats the post** — Mitigation: one interactive widget per concept replaces paragraphs; hard cap of ~3 sentences of prose per subsection
2. **Widget payload too large for a blog page** — Mitigation: downsample 109-city data to what each chart needs (binned curves, not raw points); globe optional/static fallback
3. **Numbers drift from the report** — Mitigation: generate the JSON payload from `dist/` data in the pipeline, don't hand-copy figures

## Alternative Approaches

1. **Static charts only** (older-post style): simpler, but loses the "animated charts demonstrate concepts" requirement — rejected
2. **Two posts** (theory post + results post): better SEO, but splits the narrative and doubles overhead — rejected given "not too long, dense" requirement
3. **Embed the full rain-check report via iframe**: maximal reuse, but heavy and off-brand for the blog's widget-per-section pattern — rejected; link to the report instead