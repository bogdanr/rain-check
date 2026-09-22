# Session state — 2026-09-22

Resume point for the rain-check study. The authoritative document remains
`plans/2026-09-15-global-forecast-trust-journal-strategy-v5.md`; this file
records where the work stopped and what to pick up, so a new session does not
have to re-derive it.

## State

- Working tree **clean**, HEAD `8c44a47`.
- Pipeline `run_all.sh` at **27 stages**; `src/check_site.py` passes 128/128.
- Every **Tier A gap is closed**. Four of them closed in the negative, which is
  the shape of the paper now: G1 (verification is not currently possible
  outside Europe, North America and Asia), G3 (truth error sinks the Brier
  claim but not the decision claim), G4 (no city property predicts skill),
  G20 (no affordable sample resolves the headline Brier difference).

## The argument, as it currently stands

The surviving thesis is **D12**: the metric decides whether a reader sees the
harm. Its evidence is **E4a** — the served probability costs a cheap-action
user real value while its average score looks fine — and E4a has now survived
three separate attempts to make it an artefact:

| challenge | result |
|---|---|
| sample too small (E10) | widened to 105 cities, E4a sharpens (E35) |
| truth is one bucket (E22) | 13× its re-siting spread, never flips sign |
| wrong reference centre (E36) | holds against ECMWF as well as NOAA |

Two findings qualify every calibration statement and must travel with it:

- **The event definition outranks both the sample size and the reference
  centre.** Day-total versus any-step reverses the Brier verdict at p = 0.001
  on both sides (E35), and resolves against both centres where the day-total
  event resolves against neither (E36). No calibration verdict may be quoted
  without the event definition it was computed under.
- **Precision is unbuyable.** E28's design effect of 4.3 puts the margin the
  day-boundary rule already moves things by out of reach at ~68,300 cities.
  The paper cannot rest on a precision claim.

## Open work

### Yours (blocked on accounts)

- **Task 3b** — C1–C6 queries against Google Scholar Labs, Web of Science,
  ECMWF eLibrary, AMS journals; every citation verified by opening the paper.
  Closes the remaining half of G5. The grey-literature half is done
  (`plans/2026-09-21-grey-literature-assessment-v1.md`) and **narrowed C1**:
  ForecastWatch already does served-PoP Brier scoring commercially, so the
  claim is the open-and-reproducible version, not the first version.

### Unblocked, roughly in order of value

- **Task 5** — write the literature positioning around the corrected framing.
  Needs 3b first for completeness, but the grey-literature findings alone
  determine most of the shape.
- **Task 33** — preprint to timestamp priority. Defensible today: E4a, E13,
  E22, E24, E27–E36 are each standalone results with measured caveats.
- **Task 32** — Zenodo archive with DOI (G16).
- **Task 10** — re-specified: lift the *country* count, not the city count,
  and only as far as Tasks 11–12 allow. ISD integration for Asia is scoped
  engineering (E30) rather than an open question.
- **Task 7** — validated ML ids in `PROVIDER_MODELS` for the deterministic
  track (G13, D6).
- **Task 15** — evaluate `asos-parquet` as the low-latency truth source (G12).
  Deliberately deprioritised: E30 showed station networks fail in ways that
  look like findings, and three extra months is a poor trade before the
  argument is settled.

## Things a resuming session should not re-litigate

- **Temperature is a supporting track, not a subject** (`capitals_lead_mae`,
  `cities_lead_mae`). Wind does not exist. Decided on 2026-09-22 to keep it
  that way: temperature and wind are served as point values, so there is no
  served *probability* to audit and no cost-loss curve to run, and widening
  would dilute D12.
- **Two names, one forecast.** `metno_seamless` returns ECMWF's probability
  digit-for-digit while serving its own amounts; `ukmo_seamless` duplicates
  the UK 2 km model on both channels. `duplicates.py` collapses these before
  ranking. Anything treating them as independent opinions is wrong.
- **Scope is a flag, not a fork.** `triangulation.py` and `significance.py`
  take `--scope=world|capitals`; `ensemble_pop.py` takes `--cities=world`.
  The capitals-only run stays reproducible.

## Conventions worth preserving

These are the habits that produced most of the findings above, and several
stages exist only because a check caught something:

1. Every stage carries `check` self-tests that **abort the pipeline**, and
   each check must be able to fail — plant the effect, verify it is found;
   plant nothing, verify nothing is reported.
2. Resample the unit that carries the dependence — days for time, countries
   for space, gauges for truth — and *measure* the naive alternative's
   coverage rather than asserting it is wrong.
3. State resolution before direction. A sign difference between two
   unresolved estimates is not a reversal (caught in `reference_centre.py`
   before commit).
4. Report negative results as findings, with the bound that makes them mean
   something, rather than as silence.
