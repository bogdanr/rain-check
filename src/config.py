"""Central configuration for the Bucharest forecast calibration study.

Every downstream join, aggregation and plot reads its parameters from here.
Task 1 / Task 2 / Task 3 / Task 4 of the plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
FIGURES = ROOT / "figures"

for _d in (CACHE, RAW, PROCESSED, FIGURES):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# Task 1: location and time
# --------------------------------------------------------------------------
# Bucharest city centre.
LATITUDE = 44.4268
LONGITUDE = 26.1025
TIMEZONE = "Europe/Bucharest"

# Verification is done on local calendar days; all internal timestamps are UTC
# and converted exactly once, at aggregation time.
LOCAL_TZ = "Europe/Bucharest"
UTC = "UTC"

# Lead times evaluated, in days. The Previous Runs API exposes exactly these.
LEAD_DAYS = [1, 2, 3, 4, 5, 6, 7]

# Evaluation window for the archive-based analysis. The Previous Runs archive
# starts January 2024 for most models.
ARCHIVE_START = "2024-01-01"
ARCHIVE_END = "2026-09-11"  # yesterday relative to study start

# --------------------------------------------------------------------------
# Task 2: pinned models
# --------------------------------------------------------------------------
# `best_match` silently changes which model backs the forecast over time, which
# would confound model drift with calibration error. We pin explicit models.
PRIMARY_MODEL = "ecmwf_ifs025"
COMPARATOR_MODEL = "icon_eu"
MODELS = [PRIMARY_MODEL, COMPARATOR_MODEL]

# --------------------------------------------------------------------------
# Task 3: event definition
# --------------------------------------------------------------------------
# "It rained today" = daily precipitation total at the station at or above the
# threshold, over the local calendar day.
RAIN_THRESHOLD_MM = 0.2           # primary
RAIN_THRESHOLD_VARIANTS_MM = [0.1, 0.2, 1.0]  # sensitivity runs (Task 21)

# --------------------------------------------------------------------------
# Task 4: ground truth hierarchy
# --------------------------------------------------------------------------
# Primary truth: station observations. Secondary: ERA5 reanalysis, used only as
# a cross-check, because verifying a model against a reanalysis produced by the
# same centre flatters the forecast.
# Stations resolved against the GHCN-Daily inventory on 2026-09-12. Otopeni is
# NOT in GHCN-Daily; the two usable Bucharest stations are Filaret and Baneasa.
# Filaret sits 1.1 km from the forecast grid point but reports precipitation
# only, so the temperature track has to run on Baneasa (10.1 km out). Keeping
# both also gives the station-to-station spread, which is the irreducible
# point-vs-grid representativeness floor for any calibration claim.
STATIONS = {
    # name: (ghcn_id, lat, lon, km_from_grid_point, variables, description)
    "filaret": ("ROE00100899", 44.4167, 26.1000, 1.1, ("PRCP",),
                "Bucuresti Filaret"),
    "baneasa": ("ROE00108889", 44.5167, 26.0831, 10.1, ("PRCP", "TMAX", "TMIN", "TAVG"),
                "Bucuresti Baneasa"),
}
PRIMARY_STATION = "filaret"        # precipitation / PoP track
TEMPERATURE_STATION = "baneasa"    # only station carrying TMAX/TMIN

# GHCN-Daily stores PRCP, TMAX, TMIN and TAVG in tenths (mm and degC).
GHCN_SCALE = 10.0

# --------------------------------------------------------------------------
# Hourly track: NOAA ISD stations
# --------------------------------------------------------------------------
# Resolved against the ISD station history on 2026-09-13. Note the USAF ids do
# NOT follow the WMO numbering one might guess: 154220 is Filaret, while 154200
# is Baneasa (not Otopeni).
#
# Hourly precipitation AMOUNTS are unavailable here - Filaret reports only 6/12
# hour accumulations, the airports mostly 24-hour or missing - so the hourly
# track uses present-weather occurrence instead. See observations_hourly.py.
#
# Baneasa is the primary: it is a METAR site reporting ~25k observations a year
# (so genuinely sub-hourly) and at 8.5 km is closer than Otopeni. Filaret is
# closest of all but is a synoptic site reporting far less frequently.
ISD_STATIONS = {
    # name: (isd_id, km_from_grid_point, description)
    "baneasa": ("15420099999", 8.5, "Baneasa Aurel Vlaicu"),
    "henri_coanda": ("15421099999", 16.1, "Henri Coanda / Otopeni"),
    "filaret": ("15422099999", 1.1, "Bucuresti Filaret"),
}
HOURLY_PRIMARY_STATION = "baneasa"

# ISD publication lags; measured end of data on 2026-09-13 was 2025-08-24.
ISD_END = "2025-08-24"

# Precipitation day convention. Established empirically on 2026-09-12: station
# precipitation correlates with ERA5 best when ERA5 is shifted +1 day (r=0.62
# for Filaret, 0.64 for Baneasa) rather than at zero shift (r=0.46). That
# diagnostic uses only station and reanalysis data - no forecast code - so the
# offset is station-side: these records follow the morning-observation
# convention, where the total reported on date D fell during date D-1.
# Temperature is NOT affected (tmax peaks at zero shift, r=0.986), so the
# correction is applied to precipitation only.
PRCP_DAY_OFFSET_DAYS = -1

# GHCN lags roughly three months, so the verification period ends earlier than
# the forecast archive does.
OBS_END = "2026-05-31"

# --------------------------------------------------------------------------
# City registry (Task 29)
# --------------------------------------------------------------------------
# The single-city constants above remain the canonical Bucharest definition -
# every existing module reads them, and the headline numbers must not move. The
# registry below generalises them so the same pipeline can run over other
# capitals, with Bucharest present as one entry rather than as a special case.


@dataclass(frozen=True)
class City:
    """Everything the pipeline needs to verify one location."""

    name: str
    latitude: float
    longitude: float
    timezone: str
    country: str
    prcp_station: str          # GHCN-Daily id carrying precipitation
    prcp_km: float             # station distance from the forecast grid point
    # Days to shift the GHCN date by so it lands on the day the rain fell.
    # Established per city by a station-vs-ERA5 lag scan; see capitals.py.
    prcp_day_offset_days: int | None = None
    tmax_station: str | None = None
    grid_elev_m: float | None = None
    prcp_elev_m: float | None = None


BUCHAREST = City(
    name="Bucharest",
    latitude=LATITUDE,
    longitude=LONGITUDE,
    timezone=LOCAL_TZ,
    country="RO",
    prcp_station=STATIONS[PRIMARY_STATION][0],
    prcp_km=STATIONS[PRIMARY_STATION][3],
    prcp_day_offset_days=PRCP_DAY_OFFSET_DAYS,
    tmax_station=STATIONS[TEMPERATURE_STATION][0],
)

CITIES: dict[str, City] = {"Bucharest": BUCHAREST}

# Where the committed coverage probes write their verdicts.
CAPITAL_COVERAGE = RAW / "capital_coverage.json"   # src/probe_capitals.py
CITY_COVERAGE = RAW / "city_coverage.json"         # src/probe_cities.py


def _cities_from(path) -> dict[str, City]:
    """Build the registry from a coverage JSON written by one of the probes."""
    import json

    payload = json.loads(path.read_text())
    out: dict[str, City] = {}
    for name, v in payload["included"].items():
        out[name] = City(
            name=name, latitude=v["lat"], longitude=v["lon"],
            timezone=v["timezone"], country=v["country"],
            prcp_station=v["prcp_station"], prcp_km=v["prcp_km"],
            tmax_station=v["tmax_station"], grid_elev_m=v["grid_elev_m"],
            prcp_elev_m=v["prcp_elev_m"],
        )
    return out


def load_capitals() -> dict[str, City]:
    """European capitals that passed the coverage probe, Bucharest included.

    Returns only Bucharest if the probe has not been run, so every downstream
    module degrades to the single-city study rather than failing.
    """
    if not CAPITAL_COVERAGE.exists():
        return dict(CITIES)

    out = _cities_from(CAPITAL_COVERAGE)
    # Bucharest keeps its hand-verified definition, including the offset that
    # the Track B result was built on.
    out["Bucharest"] = BUCHAREST
    return out


def load_cities() -> dict[str, City]:
    """The world city set (src/probe_cities.py), falling back to the capitals.

    This is a superset of `load_capitals()` by construction - the probe forces
    the already-analysed capitals to be retained - so results computed over it
    can be compared directly against the published capitals table.
    """
    if not CITY_COVERAGE.exists():
        return load_capitals()

    out = _cities_from(CITY_COVERAGE)
    for name, city in load_capitals().items():
        out.setdefault(name, city)
    out["Bucharest"] = BUCHAREST
    return out


# --------------------------------------------------------------------------
# Multi-provider registry (Phase 1, Task 1 of the 2026-09-14 plan)
# --------------------------------------------------------------------------
# One source of truth for both the collection pipeline and the report's
# plain-language provider guide. The model ids are Open-Meteo's; the archive
# depths are the nominal first dates the vendor documents and are MEASURED per
# city by src/probe_providers.py before any ranking is computed - a model is
# only compared where its PoP archive actually covers the city.

@dataclass(frozen=True)
class ProviderModel:
    """Everything the pipeline and the report need about one forecast model."""

    model: str                 # Open-Meteo model id (the `models` parameter)
    display_name: str          # how the report names it
    organization: str          # who produces it (the "provider" in lay terms)
    description: str           # one plain-language sentence, no jargon
    archive_depth: str         # nominal first archive date (probe confirms)
    domain: str                # "global" or "regional"


PROVIDER_MODELS: dict[str, ProviderModel] = {
    m.model: m for m in [
        ProviderModel(
            "ecmwf_ifs025", "ECMWF IFS 0.25°", "ECMWF",
            "The European intergovernmental weather centre's global model - "
            "widely regarded as the reference global forecast.",
            "2024-01-01", "global"),
        ProviderModel(
            "icon_eu", "ICON-EU", "DWD (German Weather Service)",
            "Germany's weather service's high-resolution model covering "
            "Europe; sharper detail than global models inside its area.",
            "2022-12-01", "regional"),
        ProviderModel(
            "icon_d2", "ICON-D2", "DWD (German Weather Service)",
            "Germany's very-high-resolution model for Germany and the "
            "immediate surroundings; short range only.",
            "2024-08-01", "regional"),
        ProviderModel(
            "gfs_seamless", "GFS", "NOAA (US National Weather Service)",
            "The United States' global model; the other big global "
            "forecaster alongside ECMWF.",
            "2024-01-01", "global"),
        ProviderModel(
            "meteofrance_arpege_world", "ARPEGE World", "Météo-France",
            "France's weather service global model.",
            "2024-01-01", "global"),
        ProviderModel(
            "meteofrance_arome_france", "AROME France", "Météo-France",
            "France's high-resolution model covering France and nearby "
            "countries.",
            "2024-01-01", "regional"),
        ProviderModel(
            "meteofrance_arome_france_hd", "AROME France HD",
            "Météo-France",
            "The higher-resolution variant of Météo-France's French model.",
            "2024-01-01", "regional"),
        ProviderModel(
            "ukmo_seamless", "UKMO Global", "Met Office (UK)",
            "The United Kingdom's weather service global model.",
            "2024-01-01", "global"),
        ProviderModel(
            "ukmo_uk_deterministic_2km", "UKMO UK 2 km", "Met Office (UK)",
            "The Met Office's very-high-resolution model over the British "
            "Isles.",
            "2024-01-01", "regional"),
        ProviderModel(
            "gem_seamless", "GEM", "ECCC (Environment Canada)",
            "Canada's weather service global model.",
            "2024-01-01", "global"),
        ProviderModel(
            "jma_seamless", "JMA", "Japan Meteorological Agency",
            "Japan's weather service global model.",
            "2024-01-01", "global"),
        ProviderModel(
            "metno_seamless", "MET Norway", "MET Norway",
            "Norway's weather service model chain.",
            "2024-01-01", "global"),
        ProviderModel(
            "knmi_harmonie_arome_europe", "HARMONIE (KNMI)", "KNMI (Netherlands)",
            "The Dutch weather service's high-resolution European model, "
            "shared with several neighbouring services.",
            "2024-01-01", "regional"),
        ProviderModel(
            "dmi_harmonie_arome_europe", "HARMONIE (DMI)", "DMI (Denmark)",
            "The Danish weather service's run of the same high-resolution "
            "European model.",
            "2024-01-01", "regional"),
        ProviderModel(
            "arpae_cosmo_5m", "COSMO 5 km", "ARPAE (Italy)",
            "Italy's environmental agency's high-resolution model over "
            "southern Europe.",
            "2024-01-01", "regional"),
    ]
}

# Where the per-city, per-model archive-availability probe writes its verdict.
PROVIDER_COVERAGE = PROCESSED / "provider_coverage.json"  # src/probe_providers.py

# --------------------------------------------------------------------------
# Ensemble member archive (Phase 1, Task 8 of the 2026-09-15 plan)
# --------------------------------------------------------------------------
# Open-Meteo keeps INDIVIDUAL ensemble members for three days only, so unlike
# every other track in this study the member archive cannot be reconstructed
# after the fact - it exists only if it was collected on the day. The forward
# collector (src/collect_ensemble.py) therefore writes into its own directory,
# one partition per collection date, and that directory grows daily.
ENSEMBLE_RAW = RAW / "ensemble"
ENSEMBLE_RAW.mkdir(parents=True, exist_ok=True)

# Which ensemble model ids actually respond, at which cities, with how many
# members - measured, not assumed, because the ensemble endpoint uses a model
# namespace of its own that does not match PROVIDER_MODELS.
ENSEMBLE_COVERAGE = PROCESSED / "ensemble_coverage.json"  # src/collect_ensemble.py

# Polite spacing between successive models in multi-model collection: each
# model's archive is a fresh burst of requests, and staying well under
# Open-Meteo's hourly quota matters more than finishing an hour sooner.
PROVIDER_SPACING_S = 5.0

# --------------------------------------------------------------------------
# External ensemble archive: NOAA GEFS via dynamical.org (Phase 2, Task 20a)
# --------------------------------------------------------------------------
# Route (ii) of the probability-triangulation design D3: a member-level
# ensemble, independent of the vendor, from which PoP can be computed the way
# a forecaster would, so the vendor's undisclosed derivation can be audited
# rather than caveated. Full assessment of the alternatives (TIGGE, raw GRIB2,
# ECMWF Open Data) in
# plans/2026-09-15-external-ensemble-archive-feasibility-v1.md.
#
# G16: the endpoint is NEVER hard-coded. `data.dynamical.org` URLs retire on
# 2026-09-30 and the store is versioned (v0.2.0 today), so the icechunk asset
# is resolved from the STAC catalogue at run time by src/ens_archive.py and
# cached with a timestamp. Only the catalogue entry point lives here.
GEFS_STAC_CATALOG = "https://stac.dynamical.org/catalog.json"
GEFS_STAC_COLLECTION_ID = "noaa-gefs-forecast-35-day"
GEFS_STAC_CACHE = RAW / "gefs_stac_asset.json"
# Re-resolve roughly monthly. Long enough that the daily collector does not
# depend on stac.dynamical.org being up; short enough that a re-versioned
# store is picked up automatically instead of at submission time.
GEFS_STAC_MAX_AGE_DAYS = 30

GEFS_VARIABLE = "precipitation_surface"   # per-member, kg m-2 s-1, avg rate

# Verified geometry (2026-09-15). Asserted on every open: the tile cost model
# below is derived from these numbers, so a silent rechunk or regrid must stop
# the run rather than quietly multiply the transfer.
GEFS_MEMBERS = 31           # gec00 control + gep01..gep30
GEFS_CHUNK_LEAD = 64        # one chunk carries leads 0..189 h
GEFS_CHUNK_LAT = 17
GEFS_CHUNK_LON = 16
GEFS_STEP_SECONDS = 10800   # 3-hourly steps; rate x dt = per-step accumulation

GEFS_RAW = RAW / "gefs"
GEFS_RAW.mkdir(parents=True, exist_ok=True)

# Headline verification window (design decision D1). Deliberately a parameter:
# the full record (2020-10-01 onward for GEFS, 2024-04-25 for vendor PoP) is a
# published sensitivity run, and the collector takes explicit dates.
GEFS_WINDOW_START = "2024-09-01"
GEFS_WINDOW_END = "2026-08-31"

# G17: ensemble steps are 3-hourly, so local midnight is unreachable for any
# time zone whose UTC offset is not a multiple of 3 h (UTC+1, +2, +4, +5:30,
# +5:45, +9:30 ...). Both handlings are implemented and both are runnable, so
# the sensitivity can be reported rather than asserted. See
# src/ensemble_pop.py for what each one does and why `prorata` is primary.
GEFS_BOUNDARY_MODES = ("prorata", "snap")
GEFS_BOUNDARY_PRIMARY = "prorata"

# Concurrent chunk reads. Eight was the figure the feasibility measurement
# used (0.226 MB and ~0.09 s per chunk); higher concurrency against an
# anonymous S3 prefix buys little and risks looking like abuse.
GEFS_THREADS = 8

# --------------------------------------------------------------------------
# The ensemble-source registry (Phase 5, Task 30)
# --------------------------------------------------------------------------
# GEFS above was the first member-level archive, and everything about it was
# written as module constants. Task 30 needs two more - ECMWF's physics
# ensemble and ECMWF's machine-learning ensemble - and the comparison is only
# worth anything if all three go through identical code. So the per-archive
# facts move into a record, and the GEFS constants become one entry in it
# rather than a special case; `src/ens_archive.py` and
# `src/collect_members.py` take a source and know nothing else.
#
# Why these two, and not AIFS against GEFS: the physics-versus-ML question is
# about the method, and a comparison across centres would confound method with
# centre, resolution, data assimilation and post-processing all at once. AIFS
# ENS and IFS ENS are the same centre, the same 0.25 deg grid, the same 51
# members and the same archive publisher, which leaves the forecast model as
# very nearly the only difference.
@dataclass(frozen=True)
class EnsembleSource:
    """One member-level ensemble archive on dynamical.org."""

    key: str
    collection_id: str
    label: str
    method: str                 # "physics" | "ml" - the Task 30 contrast
    members: int
    chunk_lead: int
    chunk_lat: int
    chunk_lon: int
    init_hours: tuple[int, ...]  # which UTC initialisations to collect
    window_start: str
    window_end: str
    # Native step ladders differ (GEFS 3-hourly, AIFS 6-hourly, IFS 3-hourly
    # then 6-hourly), so no single step length is stored: the ladder is read
    # from the archive and carried through the accumulation maths. `step_hint`
    # is only what the geometry check expects to see at the short end.
    step_hint_h: float
    # Measured megabytes per (tile, init) chunk read, used only to project a
    # run's transfer BEFORE it starts. Every run re-measures it from
    # /proc/net/dev and prints the comparison, so a stale number here shows up
    # as a discrepancy in the log rather than as a wrong bill.
    mb_per_chunk: float
    variable: str = "precipitation_surface"

    @property
    def raw(self):
        d = RAW / self.key
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def stac_cache(self):
        return RAW / f"{self.key}_stac_asset.json"

    @property
    def pop_table(self):
        return PROCESSED / f"{self.key}_pop.parquet"


ENSEMBLE_SOURCES = {
    "gefs": EnsembleSource(
        key="gefs", collection_id=GEFS_STAC_COLLECTION_ID,
        label="NOAA GEFS", method="physics", members=GEFS_MEMBERS,
        chunk_lead=GEFS_CHUNK_LEAD, chunk_lat=GEFS_CHUNK_LAT,
        chunk_lon=GEFS_CHUNK_LON, init_hours=(0,),
        window_start=GEFS_WINDOW_START, window_end=GEFS_WINDOW_END,
        step_hint_h=3.0, mb_per_chunk=0.226),
    # Matched window for both ECMWF systems: AIFS ENS begins 2025-07-02, which
    # is what bounds the sub-study (D6). IFS ENS goes back to 2024-04-01, but
    # collecting more of it would buy days the ML side cannot match and invite
    # exactly the pooling the plan forbids.
    "ifs_ens": EnsembleSource(
        key="ifs_ens",
        collection_id="ecmwf-ifs-ens-forecast-15-day-0-25-degree",
        label="ECMWF IFS ENS", method="physics", members=51,
        chunk_lead=85, chunk_lat=32, chunk_lon=32, init_hours=(0,),
        window_start="2025-07-02", window_end=GEFS_WINDOW_END,
        step_hint_h=3.0, mb_per_chunk=2.5),
    "aifs_ens": EnsembleSource(
        key="aifs_ens", collection_id="ecmwf-aifs-ens-forecast",
        label="ECMWF AIFS ENS", method="ml", members=51,
        chunk_lead=61, chunk_lat=32, chunk_lon=32, init_hours=(0,),
        window_start="2025-07-02", window_end=GEFS_WINDOW_END,
        step_hint_h=6.0, mb_per_chunk=2.5),
}

# The Task 30 pair. Named here rather than in the comparison module so that
# "which two series are being contrasted, and on what grounds" is a
# configuration fact the reader can check in one place.
PHYSICS_ML_PAIR = ("ifs_ens", "aifs_ens")

# AIFS ENS publishes 6-hourly steps and IFS ENS 3-hourly ones. Accumulating
# each on its own ladder would give the physics system a finer day boundary
# than the ML system and hand it an advantage that has nothing to do with
# forecast quality, so the comparison coarsens both to this ladder. The
# native-resolution run is kept as the sensitivity.
PHYSICS_ML_STEP_H = 6.0

# --------------------------------------------------------------------------
# API endpoints
# --------------------------------------------------------------------------
API_FORECAST = "https://api.open-meteo.com/v1/forecast"
API_PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
API_HISTORICAL_FORECAST = "https://historical-forecast-api.open-meteo.com/v1/forecast"
API_HISTORICAL_WEATHER = "https://archive-api.open-meteo.com/v1/archive"
API_ENSEMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"

# --------------------------------------------------------------------------
# Analysis settings
# --------------------------------------------------------------------------
N_PROB_BINS = 10          # equal-count bins for the reliability diagram
MIN_BIN_COUNT = 30        # bins below this are de-emphasised as indicative only
BOOTSTRAP_BLOCK_DAYS = 7  # block bootstrap block length (serial correlation)
BOOTSTRAP_N = 2000
RANDOM_SEED = 20260912

# --------------------------------------------------------------------------
# Baselines and the Brier reference (Tasks 18 and 24 of the 2026-09-15 plan)
# --------------------------------------------------------------------------
# Half-width of the day-of-year window used to estimate the smoothed seasonal
# climatology. Observations within the same number of calendar days of the day
# being scored are EXCLUDED (see baselines.doy_climatology), so a ~2-year
# record contributes roughly one other year's 31 days per estimate: enough to
# see a seasonal cycle, and free of the day's own synoptic neighbourhood, which
# would otherwise turn the reference into a two-sided persistence forecast.
# Sensitivity over 7/15/30 days is reported rather than assumed away.
CLIM_WINDOW_DAYS = 15

# Pseudo-observations pulling each day-of-year estimate toward the station's
# own annual rain frequency. With only one or two other years in the window the
# raw estimate carries several points of sampling noise, and noise in the
# REFERENCE inflates every skill score computed against it. Shrinkage is
# therefore conservative, not cosmetic: it makes the reference harder to beat.
CLIM_PSEUDO_COUNT = 10.0

# Trailing window for the frequency form of the persistence baseline: what
# fraction of the last 30 observed days were wet. Long enough to be a
# probability rather than a coin flip, short enough to still be "recent
# weather" rather than climatology.
PERSISTENCE_WINDOW_DAYS = 30
