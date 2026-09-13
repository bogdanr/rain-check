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
