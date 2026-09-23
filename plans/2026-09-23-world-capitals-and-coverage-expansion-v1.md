# World capitals and coverage expansion (Asia, Africa, South America, Middle East)

## Objective

Add many more cities where most of the world's people live: world capitals first, then the largest cities of countries now missing (IN, ID, NG, EG, KE, ZA, ET, AR, PE and others). Also make Japan, Saudi Arabia, Pakistan, China and New Zealand properly verified; today they only look verified. The existing rules stay the same:
- one city per gauge;
- at least 30 km between cities;
- at most 8 cities per country;
- the 500-pair gate;
- provisional cities are kept out of pooled claims.

Expected outcome:
- A `city_coverage.json` where every included city can actually meet its pair gate.
- A capital in every country that has any usable truth source.
- A per-country record of why each still-missing country is missing.

## Diagnosis (why the gap exists)

The gauges often exist. The problem is mostly in how the selection logic decides which countries are already "covered".

1. **A country counts as "covered" if it has any GHCN candidate at all, even one that cannot pass the gate.**
   - When no gauge meets the 525-day preference, `pick_station` keeps "the fullest sparse record anyway" (`src/probe_capitals.py:218-225`).
   - `ghcnh_truth.candidates` then skips every country with such a row (`src/ghcnh_truth.py:264-269`). `probe_cities.with_external` does the same at merge time (`src/probe_cities.py:192-193`).
   - Result: Japan's 8 GHCN cities have 350–475 rain days (below 500), Buraydah has 10, Pakistan about 250 and Auckland 246. All of these countries are blocked from GHCNh, which would probably verify them fully.
   - India, Indonesia and Egypt very likely fall into the same trap. They have old or sparse GHCN stations. This is not yet confirmed and is checked in Task 1.
2. **Capitals are European only.** `CAPITALS` in `src/probe_capitals.py` lists 43 European capitals, and only they are exempt from the population floor and the budget. GeoNames already marks capitals (`fcode == "PPLC"`), but `load_geonames` drops that column (`src/probe_cities.py:118-119`).
3. **GHCNh probes too little per country.** It tries only the 4 largest cities and 2 stations per city (`src/ghcnh_truth.py:87-88`). If both nearby stations are METAR-only records with no SYNOP rain groups, the whole city is lost.
4. **Rain cities are rejected because of temperature.** The gate needs 500 days of both rain and daily max temperature (`src/ghcnh_truth.py:713-714`). The code's own comment says most cities short of the gate fail on the daily max, not on rain (`src/ghcnh_truth.py:720-722`). Rain probability, the headline measure, does not need a temperature record.
5. **Truly gauge-poor regions remain.** E30 found that ISD rain reporting reaches only 8% of stations in Africa and 3% in South America. E32 found the CHIRP satellite product disagrees with gauges on 0.259 of days, which is unusable as truth. For those countries, fixing the logic is not enough; a new source is needed (Task 8).

Note on the command trace: `./collect_members.py collect world` failed with exit 127 because the script lives in `src/` and must run through the venv. The working form is `(cd src && ../.venv/bin/python collect_members.py collect world)`, the same as `run_all.sh:55` and `run_all.sh:143`.

## Implementation Plan

- [x] Task 1. **Save a per-country loss record before changing any rule.** Extend `probe_cities.py` and `ghcnh_truth.py` so that for every country with a city of 200k+ people they save to disk:
  - which source was tried;
  - which step dropped it (no station within 25 km, elevation, not active, 2025 screen, rain pairs, temperature pairs);
  - the best number of pairs reached.

  Rationale: the print-only step counts (`src/ghcnh_truth.py:677-726`) cannot explain why IN or NG is missing. This record is the baseline that every later task is measured against, and it becomes a site/report table ("why your country is not here").

- [x] Task 2. **Count a country as "covered" only if a city there passes the gate.** In both `ghcnh_truth.candidates` and `probe_cities.with_external`, define a country as GHCN-covered only if some GHCN city there at or above the floor has a gauge whose record can reach `MIN_PAIRS`. In practice that means `prcp_days` of at least `MIN_STATION_DAYS`, or a pair count estimated from the bulk census. Countries with only sub-gate GHCN rows go on to GHCNh, then ISD. The "one source per country, GHCN first" priority stays.

  Rationale: this is the main cause of the Japan/SA/PK/CN/NZ gap and probably of the IN/ID/EG gap. It needs no new data source.

- [x] Task 3. **Drop included cities that cannot reach the gate.** After the merge in `probe_cities.py`, replace any city whose truth record cannot meet `truth_sources.min_pairs(...)` with a passing city from the next source, or leave it out.

  Rationale: stops the site listing cities (all of Japan, Buraydah) that never get a page. Published-city exemptions should cover population only, not the pair gate. If an already-published city goes away, record it explicitly.

- [x] Task 4. **World capitals.** Keep the `fcode` column in `load_geonames`. Define world capitals as GeoNames `PPLC` rows, with a small override list for countries whose seat of government differs from the listed capital, or where GeoNames tags several. Extend the capital exemption (population floor, forced retention in the budget) from European `CAPITALS` to all world capitals. Leave `probe_capitals.py` and `load_capitals()` unchanged so the European capitals study and its published numbers do not move.

  Rationale: capitals are the fairest "one city per country" anchor. The user explicitly asked for them, and they are usually where a country's main synoptic station is.

- [x] Task 5. **Probe capitals first in GHCNh and ISD.** Always include the capital in the per-country probe list (4 largest cities + capital). Raise `STATIONS_PER_CITY` from 2 to 3–4 within the 25 km radius.

  Rationale: an ICAO/WMO split record often leaves the SYNOP rain groups on the third-nearest ID. Capital airports are the most likely to carry them. The extra cost is a few hundred cached parquet downloads.

- [x] Task 6. **Separate the rain gate from the temperature gate.** Admit a city if its rain record meets the pair gate. Record whether temperature coverage is present or missing, and exclude temperature-less cities only from the temperature track.
  - Check every consumer of `tmax_station` first (`capitals.py`, `city_report.py`, `city_charts.py`, web pages) and make sure each handles a missing temperature series.
  - Pin the behaviour in the `truth_sources.check()` checks.

  Rationale: temperature is the documented main reason otherwise-good rain gauges fail (`src/ghcnh_truth.py:720-722`), and rain is the study's subject.

- [x] Task 7. **Budget and storage check before collecting.** Re-run the probe offline and count the new cities.
  - Estimate Open-Meteo calls (about 6 per city) and GEFS storage (about 0.5 GB per month per 47 cities, so roughly 12 GB per 47 cities over the 24-month window). Compare against `df -h`.
  - Raise `MAX_CITIES` above 300 only if needed. Keep `MAX_PER_COUNTRY = 8`, because more countries is what the study needs (E28/E29), not more cities per country.
  - Rationale: stages 11 and 14 are rate-limited, and storage-heavy per new city.

- [x] Task 8. **Add national open-data sources for countries that remain unreachable** (after Tasks 2–6, using the Task 1 record). Add them as new prefixed sources in `truth_sources.py`, following the GHCNh pattern: prefix, loader, gate, provisional flag. Validate each against co-located GHCN gauges, as `ghcnh_truth.crosscheck` does. Candidates, most accessible first:
  - Brazil: INMET automatic stations (open bulk yearly CSVs).
  - Argentina: SMN open data.
  - Japan: JMA historical daily data, only if GHCNh does not solve Japan.
  - Peru/Chile: SENAMHI / DMC open data.
  - Africa: TAHMO (research-access, not public). Check its licence before building anything.
  - India (IMD) and Indonesia (BMKG): data access needs registration. Investigate terms only, and record the finding either way.

  Every new source goes through the `provenance.py` licence audit (stage 27) before any value reaches a published page.

  Rationale: E30/E32 established that the NOAA archives and satellite products cannot cover Africa and South America, so only national networks can.

- [x] Task 9. **Rebuild and propagate.** Run in this order:
  - `ghcnh_truth.py`, `isd_truth.py`, `probe_cities.py`;
  - `capitals.py world`;
  - `collect_archive.py model-world gfs_seamless`;
  - `collect_members.py collect world` (from `src/`);
  - `ensemble_pop.py`;
  - stages 12–27.

  Update the plan documents: `plans/2026-09-22-session-state-v1.md` and the v5 strategy still say "Africa 1 / South America 0" and do not mention GHCNh. Add the new source mix and the per-country loss table.

  Rationale: stages 14–15 and the site are the first places the change becomes visible.

## Outcome (2026-09-23)

- Tasks 1-7 implemented in `probe_cities.py`, `ghcnh_truth.py`, `isd_truth.py`, `truth_sources.py`. Per-country record: `data/processed/country_coverage.parquet` (and a `countries` block in both candidate JSONs).
- Registry: 222 cities in 88 countries (was 221 in 74); 70 capitals; 14 rain-only. 51 sub-gate cities dropped (none had a page); 0 cities with a page lost. GHCNh: 83 cities in 50 countries (was 47). ISD: 1 (Gibraltar) - its public files end 2025-08.
- Stage 10: 166 cities with metrics (was 134). New countries include CN, PK, NZ, MM, TM, MV, BS and several island states. Japan's one registry city (Takamatsu, GHCN) reaches 495 pairs downstream and misses the gate by 5; the census counts 500+ days, so the two day counts disagree slightly at the edge. Japan will pass as the window grows.
- Capitals sit outside the per-country cap (so a new capital cannot push out a published city); The Hague is not an extra capital (it would only replace Rotterdam's page). Capital flag comes from the GeoNames row, not the name (two US "Washington"s).
- Budget: GEFS point extracts are ~1.4 MB per city, not the 12 GB/47 cities estimated; storage is not a constraint. MAX_CITIES unchanged.
- Still missing, with the step that stopped them (NOAA route exhausted): IN, ID, NG, ZA, EG, KE, ET, AR, PE, KR, TW all fail the GHCNh 2025 screen (best 5-58% of days reconstructable); PH 499, IQ 483, SN 477 rain pairs, just short of 500 (will pass as the window grows). SA, UA, KP: sparse GHCN plus a failed GHCNh screen. SD, UG: no active station in range.
- Next barrier, downstream: 58 registry cities are excluded at stage 10, 55 of them at the lag scan ("no clean single lag"), mostly tropical (Bogota, Kinshasa, Singapore, Niamey, Ouagadougou, Lome, the Caribbean). That QC, not station supply, now limits Africa and the tropics.

- Rebuild: `run_all.sh` completed all 27 stages (stage 27 needed a fix: `provenance.tracked_files` listed git-tracked site payloads that the content-hashed rebuild had deleted; it now audits files as they stand on disk). `capitals_metrics.parquet` is byte-identical; 0 sub-gate cities in the registry; site: 166 city pages.

Task 8 findings (not built):
- Brazil INMET: yearly open bulk CSVs exist for 2024-2026 (portal.inmet.gov.br/dadoshistoricos, 2026 to 31/08). No licence statement could be retrieved (dados.gov.br returned 403), and Brazil is already covered by GHCNh (Brasilia, Sao Paulo), so under the one-source rule it would add cities, not a country. Build only once the licence is confirmed and the one-source rule is revisited for national networks.
- Argentina SMN: the open hourly files (`datohorario*.txt`) carry temperature, humidity, pressure and wind but no precipitation. Not usable.
- TAHMO, IMD, BMKG: registration/research access; not attempted.

## Follow-up (2026-09-23): India via a zero rule, Japan via JMA

Cause found: GHCNh stations in IN, ID, EG, SA, PE, ZA... file their SYNOPs every day but leave the rain group blank on dry days (Mumbai 2025: 362 days reported, 157 with a rain value, 0.9% zeros). Japan: GHCN-Daily's Japanese feed stops 2025-08-31; Tokyo Haneda's GHCNh record has no rain group.

- [x] Zero rule (`ghcnh_truth.infer_zeros`): at stations that file almost no zeros, a scheduled FM-12 SYNOP with a blank rain group becomes a flagged 0 mm, unless present weather or a filed amount shows rain in its period. Off unless `data/raw/ghcnh_zero_validation.json` says passed (`zero_rule_on`).
- [x] Validation (`ghcnh_truth.py validate-zeros`), criteria fixed in `ZV_CRITERIA` before the run:
  - V1 withheld zeros at 226 zero-filing stations: 84% of days rebuilt, 99.98% wet/dry agreement, 0 false dry. **Pass.**
  - V2 CHIRP at 98 zero-omitting probe stations, 2,778 restored dry days: CHIRP wet on 39% of restored dry days vs 44% of filed dry days. **Pass.**
  - V3 independent co-located GHCN-Daily gauges: only 12 zero-omitting stations and 485 restored dry days, nearly all US Pacific territories (FM, AQ, GQ, RQ, VQ). Not evaluable (needs 1,000 days, 10 stations); where measured, restored dry days were gauge-wet 25% vs 9% for filed dry days. **Not passed.**
  - Verdict: **rule stays off**; no city is added through it. V3 cannot be run in the target countries with 2024-26 GHCN-Daily, because the lack of independent gauges there is the gap itself. Next step, if pursued: run V3 over an older overlap (GHCNh and GHCN-Daily both reach back decades, when Indian and Indonesian gauges were in GHCN-Daily), or against IMD's gridded gauge product. Same criteria.
  - Fixed on the way: prefetch loops that held every full GHCNh frame in memory (~21 GB peak, the process exited silently); now `prefetch` caches without reading (1.1 GB peak).
- [x] JMA source (`src/jma_truth.py`, prefix `JMA:`): etrn daily pages for the ~156 open observatories; `--` read as a real zero; `)` kept; `]`, `×`, `///`, `#` dropped. Licence: Public Data License 1.0 (CC BY 4.0 compatible), recorded in `provenance.py`. Cross-check against GHCN-Daily's copy of the same gauges (non-GSOD values, i.e. JMA's own feed): 1,917 days, 99.0% wet/dry agreement, 93% identical values. Against GSOD-derived values (source S, own day boundary) 92.7%, which is why Tokyo's S-only GHCN record looked like 77%. The stage withdraws its cities if agreement falls below 97%.
  - 12 Japanese cities reach 767 of 767 rain days; the probe takes 9 (8 + Tokyo as capital). JMA replaces GHCN-Daily in Japan only (national source first, then the usual GHCN > GHCNh > ISD order).
- [x] Rebuild with JMA: all 27 stages pass. Japan: Tokyo, Osaka, Nagoya, Sapporo, Fukuoka, Kyoto, Hiroshima, Sendai, Chiba, 761 pairs each, all at lag 0 (scan r 0.81-0.86; Sapporo 0.66, snow). Takamatsu (GHCN, 495 pairs) replaced. Cities with metrics 166 -> 175, none lost; `capitals_metrics.parquet` unchanged; NOTICE.md carries the JMA credit.

## Verification Criteria

- Every city in `data/raw/city_coverage.json` meets its source's pair gate. The count of sub-gate included cities is 0 (today it is at least 20: JP ×8, PK ×5, TJ/KG/UZ/TM, SA, NZ, CN, KP).
- Japan, Saudi Arabia, Pakistan and New Zealand each have at least one city passing the full 500-pair gate, or a recorded reason in the Task 1 table.
- A capital is included for every country that has any passing city. Countries without one are listed with the step that dropped them.
- Each of IN, ID, NG, EG, KE, ZA, ET, AR, PE, CN either has a passing city or has a named failure step and a named next source.
- Headline pooled numbers are recomputed only over `truth_sources.confirmed(...)` rows. The European capitals run (`capitals_metrics.parquet`) is byte-identical before and after.
- Stage 14 (`triangulation.py`) aborts only for real sample mismatches, and all selftests and checks pass.
- Every page showing a new source passes the provenance licence audit (stage 27).

## Potential Risks and Mitigations

1. **Changing the "covered" rule moves already-published cities or countries** (for example, Japan's GHCN cities replaced by GHCNh ones).
   Mitigation: list every added and removed city in the probe output, and state the change in the report. Pooled claims are recomputed and compared against the previous headline, with the difference reported.
2. **Mixing sources inside a country, or GHCNh reconstruction bias (light rain undercounted)** skews comparisons with GHCN countries.
   Mitigation: keep one source per country. Publish the existing GHCNh-vs-GHCN crosscheck statistics next to any pooled claim that includes GHCNh cities, and report a sensitivity run with GHCN-only cities.
3. **Rate limits and disk space during collection.**
   Mitigation: estimate the cost (Task 7) before collecting. All collectors are cache-resumable and warn rather than fail. Collect in batches.
4. **Capital definitions are messy** (Netherlands, Bolivia, South Africa with three capitals, Côte d'Ivoire, disputed territories).
   Mitigation: an explicit override list with one justification line per entry. Allow several capitals only where the country officially has several.
5. **National data licences** (share-alike, non-commercial, redistribution bans).
   Mitigation: run the licence check in Task 8 before writing any code. Where a licence forbids republishing, a source can still count toward a total but must not appear as raw values on the site.
6. **Dropping the temperature requirement breaks pages that assume a temperature series.**
   Mitigation: audit the consumers first (Task 6). Pages show "no temperature gauge" instead of failing.

## Alternative Approaches

1. **Only add world capitals (Task 4), leaving the coverage logic alone.** This is the fastest. But capitals in the "covered-but-failing" countries would still be sub-gate or blocked from GHCNh, so Japan and India stay missing. Not recommended alone.
2. **Accept ISD-style provisional cities in more places (lower gate) instead of fixing the logic.** This adds pages quickly, but those cities never count toward conclusions, and ISD's 2025-08 cut-off is permanent.
3. **Satellite truth (CHIRP/IMERG) for Africa and South America.** Rejected by E32 (disagreement of 0.259 against a 0.008 effect size). It could at most be a clearly labelled "indicative" layer, never a verification.
4. **Drop the per-country cap or the population floor.** This adds cities but not countries, and pulls the sample back toward the US and Germany (E29). Not recommended.

## Follow-up (2026-09-23): day-matching scan replaced

**Finding.** The station-vs-ERA5 lag scan (`capitals.scan_offset`) used Pearson correlation on raw mm with fixed thresholds (r >= 0.45, margin >= 0.04). On the 91 cities whose calendar day is fixed by timestamps (GHCNh, JMA; right answer 0), it chose a wrong shift at 23. It also rejected most tropical cities as "flat", and it had moved 8 published GHCNh cities by a day.

**Change.** The scan now uses Spearman rank correlation on the days common to all shifts, with a seeded 200-replicate block bootstrap. A shift is accepted if it wins >= 90% of resamples and rank r >= 0.30. For known-day sources (`truth_sources.known_day_offset`), 0 is kept unless another shift wins >= 90%, and in that case the city is excluded, never shifted. The 0.90 threshold was set on the known-day benchmark alone. `capitals.scan_benchmark` rewrites `cities_scan_benchmark.parquet` on every world run: 91 cities, 72 accepted, 2 wrong (Christchurch, Khulna). Both are excluded as "record contradicts its own dating".

**Result.**
- World cities with metrics: 175 -> 213. Gained 49, including Kinshasa, Abidjan, Douala, Lomé, Libreville, Bangui, Niamey, Ouagadougou, Bobo-Dioulasso, Cotonou, Parakou, N'Djamena, Maradi, Bogotá, Medellín, Cali, Barranquilla, Paramaribo, Kuala Lumpur, Ho Chi Minh City, Hamburg and Budapest.
- Lost 11, all GHCN gauges whose shift is no longer confirmed (<90%): Amsterdam, Córdoba, Iași, Mersin, Nador, Nassau, Nottingham, Preston, Saint Helier, Saipan, Zaragoza.
- 15 cities kept but re-dated. Forecast BSS improved at 14 of them (e.g. Dubai -0.09 -> 0.32, Luxembourg 0.26 -> 0.44). The scan never sees forecasts, so this is an independent check. Isfahan is the exception (0.31 -> 0.22).
- European capitals: Dublin and Luxembourg re-dated to 0, Budapest added, Amsterdam dropped. Bucharest BSS unchanged at 0.409; its rank moves 6 -> 7 of 16 because Luxembourg now ranks above it. Bucharest's scan agrees with its hand-set -1.

## Follow-up (2026-09-23): write-up and card

- Findings recorded as E37-E40 in `plans/2026-09-15-global-forecast-trust-journal-strategy-v5.md`; session state updated.
- The card beside the globe now shows the country's name instead of its ISO code (`src/country_names.py`, all 250 codes; used in `city_report.sec_city_card`). The compact city button and search palette keep codes.
