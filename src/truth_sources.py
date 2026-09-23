"""Which instrument verifies a city, and how much evidence it must bring.

Three truth sources feed the study, in strict priority, one per country:

  GHCN   GHCN-Daily gauge. The reference source; every published claim was
         built on it. Station ids are bare GHCN ids.
  GHCNh  NOAA's Global Historical Climatology Network - hourly, the successor
         to ISD. Daily totals are reconstructed from the SYNOP accumulation
         groups it carries (`ghcnh_truth`). Held to the same gate as GHCN.
  ISD    NOAA ISD, the archive GHCNh replaced. Its public files stop on
         2025-08-24, so no ISD station can reach the 500-pair gate. It is
         admitted at a lower gate as *provisional* evidence (`isd_truth`),
         only in countries neither source above reaches.

Provisional cities appear on the site, labelled, with their own numbers. They
are excluded from every pooled or headline claim: a statistic across cities
is computed over `is_provisional(...) == False` rows only, so adding them can
change what the site shows about a city but never what the study concludes.

The prefix on a station id is the whole contract. Every loader and every gate
dispatches on it, so a city cannot be measured by one source and gated by
another's rule.
"""

from __future__ import annotations

import pandas as pd

from probe_capitals import MIN_PAIRS

GHCNH_PREFIX = "GHCNH:"
ISD_PREFIX = "ISD:"

# The provisional gate. 400 is not a quality judgement on ISD; it is what the
# archive can physically supply. ISD ends 2025-08-24 and the forecast archive
# starts 2024-04-25, a 487-day ceiling, and reconstruction plus the day-
# convention shift cost a few percent more. 400 keeps the stations that
# reconstruct >80% of that ceiling. A city this short has confidence intervals
# roughly sqrt(500/400) = 1.12x wider than a full one - reported, not hidden.
MIN_PAIRS_PROVISIONAL = 400

SOURCES = ("GHCN", "GHCNh", "ISD")


def source_of(station_id: str | None) -> str:
    s = str(station_id or "")
    if s.startswith(GHCNH_PREFIX):
        return "GHCNh"
    if s.startswith(ISD_PREFIX):
        return "ISD"
    return "GHCN"


def is_external(station_id: str | None) -> bool:
    """True for any source other than GHCN-Daily - i.e. ids the GHCN bulk
    extract and the GHCN neighbour analyses must not look up."""
    return source_of(station_id) != "GHCN"


def is_provisional(station_id: str | None) -> bool:
    return source_of(station_id) == "ISD"


def min_pairs(station_id: str | None) -> int:
    """The pair gate for a city measured by this station."""
    return MIN_PAIRS_PROVISIONAL if is_provisional(station_id) else MIN_PAIRS


def load_prcp(station_id: str) -> pd.DataFrame | None:
    """Daily precipitation for a non-GHCN station, in `load_ghcn`'s shape;
    None for a GHCN id (the caller's own path handles those)."""
    src = source_of(station_id)
    if src == "GHCNh":
        import ghcnh_truth
        return ghcnh_truth.load_prcp(station_id)
    if src == "ISD":
        import isd_truth
        return isd_truth.load_prcp(station_id)
    return None


def load_temp(station_id: str) -> pd.DataFrame | None:
    src = source_of(station_id)
    if src == "GHCNh":
        import ghcnh_truth
        return ghcnh_truth.load_temp(station_id)
    if src == "ISD":
        import isd_truth
        return isd_truth.load_temp(station_id)
    return None


def confirmed(df: pd.DataFrame, station_col: str = "prcp_station") -> pd.DataFrame:
    """Rows that may enter a pooled claim: everything except provisional."""
    if df is None or df.empty or station_col not in df.columns:
        return df
    return df[~df[station_col].map(is_provisional)]


def check() -> None:
    assert source_of("USW00094846") == "GHCN"
    assert source_of("GHCNH:AEI0000OMDB") == "GHCNh"
    assert source_of("ISD:41194099999") == "ISD"
    assert source_of(None) == "GHCN"
    assert min_pairs("USW00094846") == MIN_PAIRS
    assert min_pairs("GHCNH:AEI0000OMDB") == MIN_PAIRS, \
        "GHCNh reaches the full window and must meet the full gate"
    assert min_pairs("ISD:41194099999") == MIN_PAIRS_PROVISIONAL
    assert is_external("ISD:x") and is_external("GHCNH:x")
    assert not is_external("RO000015420")
    df = pd.DataFrame({"prcp_station": ["A", "ISD:x", "GHCNH:y"]})
    assert confirmed(df).prcp_station.tolist() == ["A", "GHCNH:y"]
    print("  ok  one prefix decides source, loader and gate; provisional "
          "rows are the only ones a pooled claim drops")


if __name__ == "__main__":
    check()
