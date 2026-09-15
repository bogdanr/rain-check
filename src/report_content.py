"""Explanatory content for the HTML report: glossary and improvement roadmap.

Kept as structured data rather than prose embedded in markup so that the page
can render a consistent visual scale for every metric automatically, and so the
wording stays reviewable in one place.

`scale` fields describe how to draw the "is this good?" bar:
  lo, hi    : axis range
  better    : "lower" or "higher"
  bands     : (from, to, label, css_class) shaded qualitative regions
  anchors   : (value, label) reference points drawn as ticks
  value_key : key into the computed metrics dict, injected at render time

The qualitative bands are CONVENTIONS, not laws. The page must label them as
rules of thumb and always show the raw number alongside.
"""

from __future__ import annotations

GOOD, OK, BAD = "good", "ok", "bad"

GLOSSARY = [
    dict(
        id="pop", term="PoP", full="Probability of Precipitation",
        plain="The number behind \"30% chance of rain\". It means: across many "
              "days that look like this one, it rains on about 30% of them.",
        care="It does NOT mean rain over 30% of the city, or for 30% of the "
             "day. That misreading is extremely common.",
    ),
    dict(
        id="calibration", term="Calibration", full="also: reliability",
        plain="Whether those percentages are honest. Take every day the "
              "forecast said 30%, and check how often it actually rained. If "
              "it is close to 30%, the forecast is calibrated.",
        care="A calibration curve just does this for every percentage at once. "
             "Points on the diagonal = honest. Below = overpromising rain.",
    ),
    dict(
        id="base-rate", term="Base rate", full="climatology",
        plain="How often it simply rains here, ignoring any forecast.",
        care="This is the no-skill baseline. You could say this number every "
             "single day and be perfectly calibrated while being useless - "
             "which is why calibration alone is not enough.",
        scale=dict(lo=0, hi=1, better=None, value_key="base_rate", pct=True,
                   anchors=[(0.5, "coin flip")], bands=[]),
    ),
    dict(
        id="brier", term="Brier score", full="BS",
        plain="Average squared miss between the promised probability and what "
              "happened (1 = it rained, 0 = it did not). Lower is better.",
        care="Brier scores are NOT comparable between places. A desert "
             "forecaster who always says 2% scores a brilliant Brier score "
             "while telling you nothing. That is why the skill score exists.",
        scale=dict(lo=0, hi=0.3, better="lower", value_key="brier",
                   anchors=[(0.0, "perfect"), (0.25, "always saying 50%")],
                   bands=[(0, 0.10, "excellent", GOOD),
                          (0.10, 0.18, "good", OK),
                          (0.18, 0.3, "no better than climatology", BAD)]),
    ),
    dict(
        id="bss", term="Brier Skill Score", full="BSS",
        plain="How much better than just quoting the long-run average. "
              "Higher is better; 1.0 is perfect.",
        care="This is the number that actually answers \"is the forecast any "
             "good?\". Operational day-ahead rain forecasts typically land "
             "between 0.3 and 0.5.",
        scale=dict(lo=-0.2, hi=1.0, better="higher", value_key="brier_skill_score",
                   anchors=[(0.0, "no better than climatology"), (1.0, "perfect")],
                   bands=[(-0.2, 0.0, "worse than saying nothing", BAD),
                          (0.0, 0.2, "marginal", BAD),
                          (0.2, 0.5, "typical operational skill", OK),
                          (0.5, 1.0, "excellent", GOOD)]),
    ),
    dict(
        id="reliability", term="Reliability term", full="the honesty penalty",
        plain="The part of the Brier score caused purely by percentages being "
              "dishonest. Zero is perfect.",
        care="Compare it to the uncertainty term to judge size. Here it is a "
             "few percent of it, which is small.",
        scale=dict(lo=0, hi=0.05, better="lower", value_key="reliability",
                   anchors=[(0.0, "perfect")],
                   bands=[(0, 0.01, "well calibrated", GOOD),
                          (0.01, 0.025, "mild bias", OK),
                          (0.025, 0.05, "clearly biased", BAD)]),
    ),
    dict(
        id="resolution", term="Resolution term", full="the discrimination reward",
        plain="How much the forecast actually separates rainy days from dry "
              "ones. Higher is better, capped by the uncertainty term.",
        care="This is what proves the forecast is not just hedging toward the "
             "base rate every day. Calibration without resolution is useless.",
        scale=dict(lo=0, hi=0.2, better="higher", value_key="resolution",
                   anchors=[(0.0, "always same forecast")],
                   bands=[(0, 0.04, "weak", BAD), (0.04, 0.09, "useful", OK),
                          (0.09, 0.2, "strong", GOOD)]),
    ),
    dict(
        id="uncertainty", term="Uncertainty term", full="difficulty of the problem",
        plain="How hard forecasting rain is here at all, fixed by the local "
              "climate. Not a measure of forecast quality.",
        care="It peaks at 0.25 for a 50/50 event. A place where it always or "
             "never rains is trivially easy and scores near zero.",
        scale=dict(lo=0, hi=0.25, better=None, value_key="uncertainty",
                   anchors=[(0.25, "hardest possible (50/50)")], bands=[]),
    ),
    dict(
        id="ece", term="ECE", full="Expected Calibration Error",
        plain="The average gap between what was promised and what happened, in "
              "percentage points. The most directly readable calibration number.",
        care="Under 5 points is excellent, 5-10 is good, above 15 is poor.",
        scale=dict(lo=0, hi=0.2, better="lower", value_key="ece", pct=True,
                   anchors=[(0.0, "perfect")],
                   bands=[(0, 0.05, "excellent", GOOD), (0.05, 0.10, "good", OK),
                          (0.10, 0.2, "poor", BAD)]),
    ),
    dict(
        id="sharpness", term="Sharpness", full=None,
        plain="Willingness to commit to confident numbers like 5% or 95%, "
              "instead of mumbling \"40%\" every day.",
        care="Not accuracy - it is the companion to calibration. A useful "
             "forecast needs both: honest AND decisive.",
    ),
    dict(
        id="consistency", term="Consistency range", full="perfect-forecast range",
        plain="How far off the diagonal a point would land even if the forecast "
              "were flawless, purely because we only have a limited number of days.",
        care="This is what separates real miscalibration from noise. A point "
             "inside this range is NOT evidence of a problem, however far from "
             "the diagonal it looks.",
    ),
    dict(
        id="ess", term="Effective sample size", full=None,
        plain="How many genuinely independent days we have, which is fewer than "
              "the calendar days we collected.",
        care="Weather persists: if it rained today it probably rained "
             "yesterday. Consecutive days are not independent evidence, so all "
             "error bars must use this smaller number.",
    ),
    dict(
        id="block-bootstrap", term="Block bootstrap", full=None,
        plain="The method used for the error bars. It resamples the data in "
              "multi-day chunks rather than single days.",
        care="Resampling single days would assume they are independent and "
             "produce error bars that are far too narrow.",
    ),
    dict(
        id="recalibration", term="Isotonic recalibration", full=None,
        plain="Learning a correction that maps the stated percentage onto the "
              "one actually observed.",
        care="It must be tested on days it was not trained on. Fitted and "
             "scored on the same days, it always looks like it helps.",
    ),
    dict(
        id="lead-time", term="Lead time", full=None,
        plain="How many days ahead the forecast was made. A 3-day lead means "
              "predicted three days before the day in question.",
        care="Accuracy degrades with lead time; that degradation is usually "
             "more useful to know than any single accuracy number.",
    ),
    dict(
        id="mae", term="MAE / bias", full="Mean Absolute Error",
        plain="MAE is the average size of the temperature error, ignoring "
              "direction. Bias is the average signed error.",
        care="They answer different questions. A forecast can have zero bias "
             "and still be wildly wrong daily, if errors cancel out.",
    ),
    dict(
        id="representativeness", term="Representativeness", full=None,
        plain="The forecast covers a grid square of several kilometres; the "
              "rain gauge is a single point.",
        care="A summer shower can soak the gauge and miss the rest of the "
             "square, or vice versa. This looks like forecast error but is not.",
    ),
    dict(
        id="era5", term="ERA5 / reanalysis", full=None,
        plain="A best-estimate reconstruction of past weather, blending a model "
              "with observations.",
        care="Used only as a cross-check here. Scoring a forecast against a "
             "reanalysis from the same centre flatters the forecast, so real "
             "station measurements are the primary truth.",
    ),
    dict(
        id="present-weather", term="Present weather", full="WMO code table 4677",
        plain="A code an observer files for what the sky is doing right now: "
              "clear, mist, fog, drizzle, rain, snow, thunderstorm.",
        care="Used as the hourly truth, because Bucharest stations do not report "
             "hourly rain amounts at all. It records whether precipitation is "
             "falling, not how much - which actually matches what a probability "
             "of precipitation claims.",
    ),
    dict(
        id="weather-model", term="Weather model", full="also: NWP model",
        plain="A computer simulation of the atmosphere. It divides the world "
              "into grid squares, applies the laws of physics step by step, "
              "and turns today's observations into tomorrow's weather.",
        care="Different weather services run different models - different "
             "grid sizes, different physics approximations, different ways of "
             "using the same starting observations. Two models can therefore "
             "give two different rain probabilities for the same city, and "
             "both can be honest.",
    ),
    dict(
        id="verification", term="Verification", full=None,
        plain="Checking forecasts against what actually happened, measured "
              "independently - here, rain gauges and weather stations.",
        care="A forecast can be detailed, confident and confidently wrong. "
             "Verification is the only way to tell skill from confidence.",
    ),
    dict(
        id="observing-practice", term="Observing practice", full=None,
        plain="How often, and by what method, a station files its reports.",
        care="A station reporting three times an hour catches more brief showers "
             "than one reporting once, so it records more rain for identical "
             "weather. That is a property of the observer, not the climate, and "
             "it limits how precisely two stations can be compared.",
    ),
]

# Ranked by value per unit of effort, with the reasoning exposed.
IMPROVEMENTS = [
    dict(rank=1, title="Hourly instead of daily verification",
         status="DONE",
         effort="medium", payoff="high",
         what="Verify hourly PoP against hourly present-weather reports, rather "
              "than collapsing everything to \"did it rain at all today\".",
         why="Implemented. Raised the sample from 731 days to ~10,700 hours, and "
             "showed the confident end of the forecast is honest after all - the "
             "daily overconfidence was largely an artefact of the rain threshold. "
             "Hourly precipitation AMOUNTS do not exist for Bucharest, so "
             "occurrence was used instead."),
    dict(rank=2, title="Per-lead-time reliability curves",
         status="partly done",
         effort="low", payoff="high",
         what="One calibration curve per lead day 1-7.",
         why="Now answered for DERIVED probabilities (see the frost and heat "
             "section): skill by lead is measured for four events out to seven "
             "days. Still open for the PUBLISHED probability of rain, which "
             "needs the logger - gated on elapsed time, not effort."),
    dict(rank=3, title="More event types",
         status="DONE", effort="low", payoff="medium",
         what="Frost tonight, hot day, extreme heat, alongside rain.",
         why="Implemented by deriving probabilities from the deterministic "
             "forecast with a time-blocked monotone fit. Showed temperature "
             "events stay skilful a week out while rain decays to near-useless. "
             "Wind and snow remain unavailable: neither is in the collected "
             "forecast variables nor the daily station record."),
    dict(rank=4, title="Log a consumer weather app alongside the raw model",
         status="proposed", effort="medium", payoff="high",
         what="Record what a public app or the national service publishes.",
         why="Answers the question most people actually mean: is the forecast "
             "on my phone honest? Consumer products sometimes carry a "
             "deliberate wet bias that a raw model will never show."),
    dict(rank=5, title="Neighbourhood verification with more stations",
         status="proposed", effort="medium", payoff="medium",
         what="Score a forecast as correct if rain fell anywhere nearby.",
         why="Separates \"the forecast was wrong\" from \"the rain missed the "
             "gauge\" - currently an acknowledged but only crudely quantified "
             "confound."),
    dict(rank=6, title="Extend the temperature record to 2021",
         status="proposed", effort="low", payoff="medium",
         what="Pull GFS 2m temperature, archived back to March 2021.",
         why="Roughly triples the temperature sample at no analytical cost, "
             "enabling year-to-year comparison."),
    dict(rank=7, title="Formal statistical test of calibration",
         status="proposed", effort="low", payoff="low",
         what="A significance test against the null of perfect calibration.",
         why="Consistency bars already answer this visually and per-bin; a "
             "single global test adds rigour but little new insight."),
]
