# External Ensemble Archive Integration: Feasibility Report

**Closes Task 4** of `plans/2026-09-15-global-forecast-trust-journal-strategy-v2.md:137`. Decides route (ii) of design decision **D3** (`:56`) and therefore the Tier A ceiling on **G2** (`:96`).

**Date of investigation:** 2026-09-15. All access claims below were tested live against the actual endpoints; measured figures are marked **[measured]**.

---

## Executive summary

| | Verdict | Dev-days | Transfer | Stored | CC-BY redistribution | PoP(>0.2 mm, local day) |
|---|---|---|---|---|---|---|
| **NOAA GEFS via dynamical.org Zarr** | **Recommended — integrate first** | 3–5 | 79 GB | ~1 GB | **Yes** | Yes |
| **ECMWF IFS ENS via dynamical.org Zarr** | **Recommended — integrate second** | +1–2 | 361 GB | ~1 GB | **Yes** | Yes |
| NOAA GEFS raw GRIB2 (`noaa-gefs-pds`) | Viable, keep as validation path | 8–12 | 355 GB | ~1 GB | Yes | Yes |
| ECMWF Open Data raw GRIB2 (`ecmwf-forecasts`) | Viable but wasteful; throttled | 10–15 | ~1.9 TB | ~1 GB | Yes | Yes |
| **TIGGE** | **Do not integrate** | 15–25 + weeks of queue | 0.3–2 TB | — | **No — licence blocker** | Yes |
| ECMWF Open Data portal (`data.ecmwf.int`) | Forward collection only | 2 | — | — | Yes | Yes |
| MOGREPS-G on AWS | Not viable for the window | — | — | — | No (CC-BY-SA) | Yes |
| GEFS reforecast (`noaa-gefs-retrospective`) | Out of window | — | — | — | Yes | Yes |

**Headline:** the premise of the question has changed since the plan was written. Self-computed PoP for the full 2024-09-01 → 2026-08-31 window is **cheap, not expensive** — two independent ensembles (31-member GEFS, 51-member ECMWF IFS ENS) are retrievable at point locations for ~5 developer-days total, with no registration, no licence conflict, and no rate limits. TIGGE, the route the plan named, is the one route that is both expensive *and* licence-blocked. **Proceed with D3 route (ii). The Tier A ceiling is not capped by ensemble availability.**

---

## 1. TIGGE (ECMWF-hosted)

### Access method — changed in 2026

The `apps.ecmwf.int/datasets/data/tigge` Public Datasets Service **was decommissioned on 2026-05-27**. Access migrated to the **ECMWF Data Store (ECDS)**, opened 2026-04-21, using the **CDS-API** client rather than the old `ecmwfapi.ECMWFDataServer` WEB-API. Any code, tutorial or paper published before mid-2026 describing TIGGE retrieval is now wrong. Retrieval remains a MARS-language request:

```
class=ti, dataset=tigge, origin=ecmf, param=228228 (tp),
levtype=sfc, type=pf, number=1/to/50, grid=0.5/0.5, step=..., area=...
```

The CMA portal (`wisportal.cma.gov.cn/wis`) remains a synchronised second source. The NCAR mirror stopped at end-2015 and is irrelevant.

### Registration and licence — **this is the blocker**

Registration is free ("simple registration procedure") and gives access with a **mandatory 48-hour embargo** after forecast initial time. Real-time access requires separate approval. But the terms of use are fatal to the study's stated publication plan:

- Data are provided "freely for **Research and Education purposes**"; commercial use prohibited.
- **ECMWF's contribution to TIGGE is CC BY-NC 4.0** — explicitly Non-Commercial, *not* the CC-BY-4.0 that ECMWF applies to its Open Data.
- **Non-ECMWF centres** (NCEP, UKMO, JMA, DWD, ECCC, KMA, CMA, BoM, IMD, NCMRWF, Météo-France) carry an additional clause: *"Data must not be supplied as a whole or in part to any third party outside your organisation."*

A derived dataset released under CC-BY 4.0 containing TIGGE-derived PoP would breach both the NC restriction and the no-redistribution clause. Published *verification scores* are defensible as research results; a published *derived dataset* is not. Since the dataset descriptor to Scientific Data or ESSD is sequenced **first** (`:86`) and is the highest-probability acceptance in the whole plan (~60%), this alone disqualifies TIGGE as the integration target. See also **G15** (`:126`) — TIGGE would add a second, worse licence-segregation problem on top of the existing UK Met Office CC-BY-SA one.

### Centres, coverage, variables, resolution

Thirteen contributing centres; the archive's current phase is **2024–2028**, confirmed and underway, so the study window is fully covered. Centres actively contributing into 2026 (latest model-change dates recorded by ECMWF): ECMWF (2026-05-13, IFS 50r1), KMA (2026-06-04), Météo-France (2025-01-23), ECCC (2024-06-11), plus BoM, CMA, DWD, IMD, JMA, NCEP, NCMRWF, UKMO. CPTEC suspended contribution in 2020.

Ensemble sizes and archived resolutions: ECMWF 50+1 (O640), NCEP 30+1 (0.5°), JMA 50+1 (1.25°), UKMO 17+1 (~0.2°), DWD 40 (0.5°), ECCC 20+1 (0.25°), KMA 25+1 (0.5°), CMA 29+1 (0.5°), Météo-France 34+1, IMD 20+1, NCMRWF 11+1, BoM 16+1.

**Total precipitation (`tp`, paramId 228228, kg m⁻²)** is archived for all centres as a single-level accumulated field, **accumulated from forecast start**, on a **6-hourly** step grid. Runs at 00/12 UTC for most centres (00/06/12/18 for BoM, Météo-France, NCEP, UKMO).

### Volume and rate limits

Reported daily archive volume is ~2.7 TB across all centres (ECMWF alone 1100 GB/day). TIGGE has **no point-extraction or per-member spatial-subset service**: MARS serves whole fields, optionally area-cropped and regridded. Because the ~2000 cities are globally scattered, an area crop saves nothing — you retrieve global fields. For the ECMWF contribution alone, `tp` at 0.5° (720×361) × 51 members × 28 steps (to 168 h) ≈ 0.4–0.7 GB/day of GRIB, i.e. **300–500 GB for 730 days, per centre**. Multi-centre triangulation is a multi-terabyte retrieval.

Practical limits are worse than the volume suggests: the interactive portal caps at roughly **20,000 fields per request**; larger work must go through batch/MARS. MARS is tape-backed and **stored by date**, so a request for one parameter across 730 dates is the pathological access pattern — it mounts many tapes. ECMWF additionally documents ongoing **tape-damage data loss** in the TIGGE archive (including a bulk loss of control-forecast surface fields for 710 dates in 2006–2019), so completeness must be verified rather than assumed. Realistic wall-clock for a two-year, multi-centre `tp` extraction is **weeks of queueing**, not hours.

### Verdict

| Item | Assessment |
|---|---|
| Feasibility | Technically possible; **commercially/legally unsuitable** |
| Engineering effort | **15–25 developer-days**, plus weeks of retrieval latency |
| Storage | 0.3–2 TB transferred; ~1–2 GB derived |
| Licence for CC-BY derived release | **Blocked.** CC BY-NC 4.0 (ECMWF) + no-third-party-redistribution (all other centres) |
| Enables PoP(>0.2 mm, local day)? | Yes, at 6-hourly quantisation (coarser than the alternatives) |

**Do not integrate.** The one thing TIGGE uniquely offers — many *different* centres' ensembles — is exactly what its licence forbids you from publishing.

---

## 2. NOAA GEFS on AWS Open Data

### Buckets

- `s3://noaa-gefs-pds` (us-east-1) — **real-time/operational archive. Verified live: continuous daily coverage from `gefs.20170101` through `gefs.20260909`+, four cycles/day (00/06/12/18Z).** This is the bucket that matters.
- `s3://noaa-gefs-retrospective` — the GEFSv12 reforecast. **Covers 2000–2019 only, 5 members (11 weekly). Out of window; irrelevant to this study.** The registry name `noaa-gefs-reforecast` maps to this bucket.

### Structure, members, precipitation **[measured]**

For `gefs.YYYYMMDD/HH/atmos/` three products exist: `pgrb2ap5`, `pgrb2bp5` (0.5°) and `pgrb2sp25` (**0.25°**). Verified by HTTP range probe on `gefs.20240901/00/atmos/pgrb2sp25/`:

- **31 members**: `gec00` (control) + `gep01`…`gep30`. `gep31` returns 404.
- 0.25° product runs to **f240**; beyond that 0.5° (`pgrb2ap5`) to f384.
- Every GRIB2 file has a sibling **`.idx`** file enabling byte-range extraction of a single message.
- `APCP:surface` is present in every member file. Accumulation buckets reset every 6 h: `f003` = 0–3 h, `f021` = 18–21 h, `f024` = 18–24 h, `f027` = 24–27 h. **Full 3-hourly accumulation is reconstructable** by differencing.
- Measured APCP message size at 0.25°: **279,249 bytes**; a byte-range GET of exactly that message succeeded in 1.0 s and returned a valid `GRIB` magic header.

### License and cost

NOAA NODD terms: open to the public, use as desired, attribution requested, no implied endorsement. **Compatible with a CC-BY 4.0 derived release.** The bucket is in the AWS Open Data Sponsorship Programme: **anonymous egress from outside AWS is free to the user** (AWS sponsors the provider's egress). No requester-pays, no registration, no API key. No throttling was observed in any of the ~40 probe requests issued.

### Cost of extracting 2000 point time series for 2 years

Two routes.

**Route A — raw GRIB2 byte-ranges.** For days 1–7 you need APCP at 3-hourly steps f003…f168 = 56 messages per member per cycle. 31 × 56 = 1,736 messages/init-day × 0.28 MB = **486 MB per init-day → ~355 GB over 730 days**, and ~1.27 million HTTP range GETs. Requires `eccodes`/`cfgrib`, which the project does not currently have installed.

**Route B — dynamical.org analysis-ready Zarr (recommended).** See §4.1. **79 GB, no GRIB decoding.**

### Verdict

| Item | Assessment |
|---|---|
| Feasibility | **High.** Verified working, anonymous, unthrottled |
| Engineering effort | Route A **8–12 dev-days**; Route B **3–5 dev-days** |
| Transfer | Route A 355 GB; Route B 79 GB |
| Stored (derived) | ~0.5–1 GB as Parquet; 12 GB if member-level is kept |
| Licence for CC-BY derived release | **Clear** |
| Enables PoP(>0.2 mm, local day)? | **Yes**, at 3-hourly resolution out to 240 h |

---

## 3. ECMWF Open Data (data.ecmwf.int)

### What is free

Verified live at `https://data.ecmwf.int/forecasts/`. Free products at 0.25°: `ifs/0p25/{oper,enfo,wave}` and `aifs-single`, `aifs-ens`. The ensemble product `enfo/…-ef.grib2` carries **51 members** (control + 50 perturbed). The accompanying `.index` files were verified to contain exactly **50 `tp` entries per step file** plus the control — i.e. per-member total precipitation is present, at 0.25°, 3-hourly to 144 h and 6-hourly to 360 h.

### Retention — and the historical archive that *does* exist

The public portal is a **rolling ~4-day window**. Verified: on 2026-09-15 the portal listed only `20260912/` through `20260915/`. Concurrent-session limit is 750. **On its own the portal supports forward collection only** — it cannot serve the 2024–2026 window.

**However, the AWS replica does retain history.** The AWS Open Data registry entry `ecmwf-forecasts` (`s3://ecmwf-forecasts`, eu-central-1) is a replica that is *not* pruned to the rolling window: a live listing returned prefixes beginning at **`20230118/`** and running continuously to `20260915/`. `20240901/00z/ifs/0p25/enfo/` was confirmed present, with per-step `.grib2` + `.index` pairs. **So historical access to ECMWF Open Data ensemble precipitation for the entire study window does exist** — the plan's assumption that ECMWF ensembles are unavailable retrospectively is incorrect once the AWS replica is considered.

Two practical caveats, both measured:

1. **Object sizes are enormous.** `20240901000000-0h-enfo-ef.grib2` is **4.2 GB** (all parameters, all 51 members, one step). Byte-range extraction via `.index` is mandatory; naive downloads are not an option. Per-member `tp` messages are ~0.8 MB, so days 1–7 at 3-hourly steps costs 51 × 65 × 0.8 MB ≈ **2.65 GB per init-day → ~1.9 TB over 730 days**. Five times the GEFS cost for the same lead range, because ECMWF messages are 0.25° global at 51 members.
2. **The bucket is throttled.** Six consecutive anonymous GETs of small `.index` objects, across three different dates and spaced with backoff, **all returned HTTP 503 `SlowDown` — "Please reduce your request rate."** LIST operations succeeded throughout. This is a heavily contended bucket; a 1.9 TB / ~2.4 million-request campaign against it would need aggressive backoff, off-peak scheduling and would still be fragile.

### Licence

ECMWF Open Data is **CC BY 4.0** plus ECMWF Terms of Use (attribution as source; no warranty; remove attribution on request). **Not** the NC licence that governs TIGGE. **Compatible with a CC-BY 4.0 derived release.** Citation DOI: `10.21957/open-data`.

### Verdict

| Item | Assessment |
|---|---|
| Feasibility (portal, forward) | High, trivial — **do this anyway**, it complements D3 route (iii) |
| Feasibility (AWS replica, historical) | **Real but expensive and throttled** — superseded by §4.1 |
| Engineering effort | Forward: **2 dev-days**. Historical raw: **10–15 dev-days** |
| Transfer (historical raw) | ~1.9 TB |
| Licence for CC-BY derived release | **Clear (CC-BY-4.0)** |
| Enables PoP(>0.2 mm, local day)? | Yes, 3-hourly to 144 h |

---

## 4. Other viable free archives covering 2024–2026

### 4.1 dynamical.org analysis-ready Zarr/Icechunk archives — **the decisive finding**

`dynamical.org` is a not-for-profit research lab that republishes NWP archives as **time-optimised, chunked Zarr v3 (Icechunk)** stores on AWS Open Data / Source Cooperative storage. Everything below was **opened and read anonymously**, with no account, in a throwaway venv (`icechunk 2.2.0`, `zarr 3.3.0`, `xarray 2026.7.0`).

**`noaa-gefs-forecast-35-day`** — `s3://dynamical-noaa-gefs/noaa-gefs-forecast-35-day/v0.2.0.icechunk` **[measured]**

- Dimensions: `init_time` **2176** × `ensemble_member` **31** × `lead_time` **181** × `lat` 721 × `lon` 1440.
- `init_time` runs **2020-10-01 → 2026-09-15, exactly one per day with no gaps** (2176 values = 2176 days). Fully covers both the headline window and the full-record sensitivity run.
- Variable `precipitation_surface` (`prate`, kg m⁻² s⁻¹, "average rate since previous forecast step") is **per ensemble member**. 27 variables total.
- Chunk shape `[1, 31, 64, 17, 16]` — one chunk carries **all 31 members and leads 0–189 h** for a 4.25° × 4° tile. Days 1–7 for a city cost exactly **one chunk read**.
- **Licence: CC-BY-4.0.** DOI-archived.

**`ecmwf-ifs-ens-forecast-15-day-0-25-degree`** — `s3://dynamical-ecmwf-ifs-ens/…/v0.1.0.icechunk` **[measured]**

- Dimensions: `init_time` **897** × `lead_time` **85** × `ensemble_member` **51** × 721 × 1440.
- `init_time` **2024-04-01 → 2026-09-14, one per day, no gaps.** Starts *before* the archive's PoP start date of 2024-04-25 (`src/constants.py:3-5`) — so it covers the full record, not just the balanced window.
- `precipitation_surface` per member; 3-hourly 0–144 h, 6-hourly 144–360 h; 00Z runs only.
- Chunk `[1, 85, 51, 32, 32]` — one chunk carries **all 51 members and all 85 leads** for an 8° × 8° tile.
- Built from ECMWF Open Data via the AWS registry. **Licence: CC-BY-4.0 + ECMWF Terms of Use.** DOI `10.5281/zenodo.18777399`.

**`ecmwf-aifs-ens-forecast`** — 51 members, 0.25°, **2025-07-02 → present**, CC-BY-4.0. Directly serves **G13** and Task 30 (`:175`): a *machine-learning ensemble* whose self-computed PoP can be compared against the physics ensembles on a matched window. This is the strongest novelty hook in the plan and it is available at the same cost as the others.

Also present and relevant: `noaa-gefs-analysis` (0.25°, 3-hourly, 2000→present) and `nasa-imerg-analysis-{early,late}` (0.1°, 30-min, 1998→present) — the latter is a ready-made **satellite QPE cross-check for G3 / Task 12** (`:151`), and `asos-parquet` (~2500 global METAR stations, hourly GeoParquet) is a candidate low-latency observation source for **G12 / Task 15** (`:154`).

**Measured extraction cost.** Distinct chunk-tiles required for the top-2000 cities by population from `data/raw/cities15000.txt`:

| Cities | GEFS tiles (17×16) | IFS-ENS tiles (32×32) |
|---|---|---|
| 2,000 | **480** | **215** |
| 3,000 | 560 | 234 |
| 8,509 | 715 | 269 |

Cities cluster hard, so the tile count grows far more slowly than the city count — **scaling from 2,000 to the full 8,509-city qualifying pool of G1 costs only ~50% more transfer.**

Measured per-chunk transfer (from `/proc/net/dev`, 10 globally scattered cities × 5 init days, 8 threads): **0.226 MB per (tile, init) for GEFS** — 11.3 MB and 4.6 s for 50 chunk reads. For IFS ENS: **2.3 MB per (tile, init)**.

| | Tiles | Inits (730-day window) | Per chunk | **Total transfer** | Wall clock |
|---|---|---|---|---|---|
| GEFS, 2000 cities, days 1–7, 31 members | 480 | 730 | 0.226 MB | **79 GB** | ~3–9 h |
| GEFS, full record (859 inits) | 480 | 859 | 0.226 MB | 93 GB | ~4–10 h |
| GEFS, 8509 cities | 715 | 730 | 0.226 MB | 118 GB | ~5–13 h |
| IFS ENS, 2000 cities, all 15 days, 51 members | 215 | 730 | 2.3 MB | **361 GB** | ~4–8 h |

Sanity check on the values returned: Bucharest, 2024-09-01 00Z, 31 members × 64 leads, mean rate 6.67e-06 kg m⁻² s⁻¹ = **0.072 mm per 3 h** — physically sensible for early September.

Note also that a GEFS chunk delivers the **17×16 grid-cell neighbourhood** around each city at no extra cost — free raw material for the representativeness-error quantification demanded by **G3 / Task 13** (`:152`).

**Caveats to state in the paper.**

1. This is a **third-party republication**. For a paper whose most original contribution is a *provenance audit* (`:176`), depending on a re-publisher without checking it would be self-undermining. Mitigation: validate a sample of city-days against raw `noaa-gefs-pds` GRIB2 byte-ranges — proven working in §2 — and report the agreement. Budget 1 dev-day.
2. Values are stored with **rounded floating-point mantissas** for compression (Klöwer et al. 2021). Immaterial at a 0.2 mm threshold, but must be disclosed.
3. **`data.dynamical.org` URLs are retired on 2026-09-30.** Resolve the `icechunk-https` / `icechunk` asset from the STAC collection at run time; never hard-code.
4. Institutional-longevity risk (small non-profit). Mitigated by the raw-GRIB fallback path and by the study archiving its own extracted point data to Zenodo (Task 32, `:180`).

### 4.2 MOGREPS-G on AWS — not viable

`s3://met-office-global-ensemble-model-data` (eu-west-2). **30-day rolling archive only** — cannot serve 2024–2026. Licence is **CC-BY-SA**, which would propagate share-alike into any derived release and worsen the existing **G15** problem (`:126`). The older `mogreps-g` / `mogreps-uk` buckets are deprecated and under a Non-Commercial Government Licence. Forward collection is possible but buys a CC-BY-SA obligation for a third ensemble the study does not need.

### 4.3 Others assessed and rejected

- **Open-Meteo Ensemble API** — 3-day member retention; already settled in the plan (`:19`). Open-Meteo *does* publish its whole database at `s3://openmeteo` (CC-BY-4.0), which is worth a separate look for the vendor-side collection and rate-limit problem (**risk 5**, `:208`), but it is a vendor archive, not an independent ensemble, so it does not serve D3 route (ii).
- **GEFSv12 reforecast (2000–2019)** and **UFS GEFSv13 replay** — outside the window.
- **WeatherBench-2 / Google ARCO IFS ENS** — ends 2022.
- **ECMWF S2S database** — sub-seasonal cadence; wrong lead range.
- **DWD / Météo-France open data** — ~24–48 h retention, deterministic-dominated; forward-only at best.
- **Google WeatherNext 2** — 64-member ensemble, but access is via Earth Engine / BigQuery requiring a billed Google Cloud account, and the terms are not a clean CC-BY. Park it.

---

## 5. Answering the specific question: PoP(precip > 0.2 mm over a local calendar day)

**Yes, for GEFS and for ECMWF IFS ENS, at every one of the ~2000 points.** Method:

1. Read `precipitation_surface` (mean rate since previous step) for all members at the chunk's lead range.
2. Convert to per-step accumulation: `rate × Δt` (Δt = 10,800 s for 3-hourly steps).
3. Sum the steps whose valid-time interval falls inside the city's local calendar day, using the same per-station day convention machinery as **G11 / Task 6** (`src/config.py:113-121`).
4. PoP = fraction of members whose local-day total ≥ 0.2 mm. GEFS gives 31 members → PoP resolution 1/31 ≈ 0.032; ECMWF gives 51 → 1/51 ≈ 0.020.

**Four caveats that must be written into the methods section:**

- **Temporal quantisation.** Steps are 3-hourly. Local midnight for time zones whose UTC offset is not a multiple of 3 h (UTC+1, +2, +4, +5:30, +5:45, +9:30…) cannot be hit exactly. Either apportion the boundary step pro-rata (documented approximation) or snap to the nearest 3 h and report the sensitivity. The vendor PoP under comparison is *hourly*, so the divergence result (Task 21, `:163`) carries a resolution mismatch that must be separated from the genuine provenance signal — quantify it by re-deriving vendor PoP at 3-hourly quantisation as a control.
- **Beyond 144 h (ECMWF) / 240 h (GEFS)** steps become 6-hourly, coarsening alignment further.
- **Area vs point.** A 0.25° grid-box mean PoP is not a point PoP; grid-box precipitation is smoother and wetter-more-often at low thresholds. At a 0.2 mm threshold this bias is material and systematically favours the ensemble against the gauge. Report it; the free 17×16 neighbourhood from the GEFS chunk lets you estimate it directly.
- **Member count differs** (31 vs 51 vs vendor's undisclosed basis). Sharpness and reliability comparisons must account for the discretisation of achievable probabilities.

---

## 6. Recommendation

### Integrate **NOAA GEFS via `dynamical.org` `noaa-gefs-forecast-35-day`** first.

Rationale, in order of weight:

1. **It is the cheapest by a wide margin** — 79 GB, one chunk read per city-day, no GRIB decoding, no new heavyweight dependency beyond `icechunk` + `xarray`. **3–5 developer-days**, one overnight run.
2. **Its coverage is complete and gapless** across both the balanced window *and* the full record, verified index-by-index — which directly serves **D1** and **G6** (`:52`, `:106`).
3. **Its licence is unambiguous** — NOAA public-domain source, CC-BY-4.0 republication. Nothing stands between it and the Scientific Data / ESSD descriptor that is sequenced first.
4. **It has a raw-data validation path** that was proven working in this investigation (`noaa-gefs-pds` byte-ranges), so the provenance of the provenance audit is itself auditable.

**Then integrate `ecmwf-ifs-ens-forecast-15-day-0-25-degree`** (+1–2 dev-days — same API, same code path, different chunk geometry). This upgrades the divergence result from "vendor vs one ensemble" to "vendor vs two independent ensembles from the two dominant global centres", which is what makes Task 21 a headline rather than a footnote. Then, if appetite remains, `ecmwf-aifs-ens-forecast` for the physics-vs-ML calibration comparison (Task 30) at essentially zero marginal engineering cost.

**Do not integrate TIGGE.** It is 5× the effort, weeks of latency, has documented archive gaps, and its licence forbids exactly the derived-dataset release the publication strategy is built on. Strike it from `:56` and `:162` and replace with the two Zarr archives.

**Additionally, start forward collection from `data.ecmwf.int` now** (2 dev-days) — it is independent of everything above, and D3 route (iii) loses a day of members for every day of delay.

### Is the overall effort justified?

**Yes, decisively.** The plan's risk register (`:200`) budgeted for external archives proving "too costly to integrate", with the fallback of a vendor-PoP-only paper capped at Tier B. That contingency is no longer live. For **5–8 developer-days plus roughly one day of unattended transfer** the study obtains:

- two independent, member-level ensembles covering 100% of the verification window, plus a third (AIFS ENS) covering 14 months of it;
- the ability to publish the vendor-versus-ensemble PoP divergence (**G2**, Task 21) as a primary result under a clean CC-BY licence;
- free neighbourhood fields for representativeness error (**G3**, Task 13);
- a satellite QPE cross-check (IMERG) and a candidate low-latency observation source (ASOS Parquet) from the same catalogue, addressing **G3 / Task 12** and **G12 / Task 15**;
- removal of the single objection that the v2 plan rated as most damaging to the Nature Communications case.

Five to eight developer-days is less than the effort already budgeted for Task 27's multivariate model alone. Against an alternative that concedes the study's most original contribution and caps it at Tier B venues, this is the highest-return work item in the entire plan. **Proceed.**

---

## Appendix — verification log

Every claim marked live was tested on 2026-09-15 from outside AWS:

| Endpoint | Test | Result |
|---|---|---|
| `apps.ecmwf.int/datasets/data/tigge/...` | HTTP GET | 200, page reads "Migrated to ECDS on 27-05-2026" |
| `apps.ecmwf.int/datasets/licences/tigge/` | HTTP GET | 200; CC BY-NC 4.0 + no-third-party clause captured verbatim |
| `apps.ecmwf.int/datasets/licences/general/` | HTTP GET | 200; CC-BY-4.0 + ECMWF Terms |
| `noaa-gefs-pds` LIST | `?list-type=2` | 200; `gefs.20170101` → `gefs.20260909` continuous |
| `noaa-gefs-pds` member probe | range GET `f024`, gep01/15/30/31 | 206/206/206/**404** → 31 members confirmed |
| `noaa-gefs-pds` APCP extraction | range GET bytes 9706614–9985862 | 206, 279,249 B, valid `GRIB` header, 1.0 s |
| `noaa-gefs-pds` `.idx` parse | f003/f021/f024/f027/f120/f240 | 3-h and 6-h accumulation buckets confirmed |
| `data.ecmwf.int/forecasts/` | LIST | 4 days retained (20260912–20260915); 51-member `enfo`; 50 `tp` index entries |
| `ecmwf-forecasts` (AWS) LIST | `?list-type=2` | 200; `20230118/` → `20260915/` continuous |
| `ecmwf-forecasts` (AWS) GET | 6 attempts, 3 dates, with backoff | **503 `SlowDown` every time** |
| `dynamical-noaa-gefs` icechunk | anonymous open + read | 2176 daily inits, 31 members, 0.226 MB/chunk, values sane |
| `dynamical-ecmwf-ifs-ens` icechunk | anonymous open + read | 897 daily inits from 2024-04-01, 51 members, 2.3 MB/chunk |
| `stac.dynamical.org` | collection JSON × 3 | CC-BY-4.0 on all three ensemble datasets |
| `noaa-gefs-retrospective` registry | HTTP GET | 2000–2019, 5 members — out of window |
| AWS Open Data registry index | GitHub contents API | MOGREPS-G (30-day, CC-BY-SA), Météo-France, Open-Meteo identified |

No file in `src/` was modified. All test code was written to `/tmp` in a throwaway virtualenv.
