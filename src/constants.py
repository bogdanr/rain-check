"""Constants shared by the collectors (kept separate to keep modules small)."""

# PoP is null in the Historical Forecast archive before this date (bisected
# empirically on 2026-09-12; see data/raw/api_probe.json).
POP_ARCHIVE_START = "2024-04-25"

# Single Runs API host and the only model archived back to 2024 for Bucharest.
SINGLE_RUNS_MODEL = "ecmwf_ifs"
SINGLE_RUNS_START = "2024-04-01"
