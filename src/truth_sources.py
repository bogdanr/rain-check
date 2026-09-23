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
  JMA    The Japan Meteorological Agency's own observatory records
         (`jma_truth`), for Japan only, where GHCN-Daily stops in 2025-08.
         A national daily gauge report, held to the full gate; where it
         reaches Japan it replaces GHCN-Daily there (one source per country).

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
JMA_PREFIX = "JMA:"

# The provisional gate. 400 is not a quality judgement on ISD; it is what the
# archive can physically supply. ISD ends 2025-08-24 and the forecast archive
# starts 2024-04-25, a 487-day ceiling, and reconstruction plus the day-
# convention shift cost a few percent more. 400 keeps the stations that
# reconstruct >80% of that ceiling. A city this short has confidence intervals
# roughly sqrt(500/400) = 1.12x wider than a full one - reported, not hidden.
MIN_PAIRS_PROVISIONAL = 400

SOURCES = ("GHCN", "GHCNh", "ISD", "JMA")


def source_of(station_id: str | None) -> str:
    s = str(station_id or "")
    if s.startswith(GHCNH_PREFIX):
        return "GHCNh"
    if s.startswith(ISD_PREFIX):
        return "ISD"
    if s.startswith(JMA_PREFIX):
        return "JMA"
    return "GHCN"


def is_external(station_id: str | None) -> bool:
    """True for any source other than GHCN-Daily - i.e. ids the GHCN bulk
    extract and the GHCN neighbour analyses must not look up."""
    return source_of(station_id) != "GHCN"


def is_provisional(station_id: str | None) -> bool:
    return source_of(station_id) == "ISD"


def known_day_offset(station_id: str | None) -> int | None:
    """The day shift a source's own timestamps imply, or None if unknown.

    GHCNh and ISD days are built here from timestamped reports on the local
    calendar day, and JMA files its daily totals on the 00-24 JST day, so
    all three are dated by construction: shift 0. GHCN-Daily carries no
    observation time, so its shift has to be found from the data. The lag
    scan still runs for known-day sources, but as a check: a record that
    lines up confidently at another shift contradicts its own timestamps
    and is excluded rather than moved.
    """
    return None if source_of(station_id) == "GHCN" else 0


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
    if src == "JMA":
        import jma_truth
        return jma_truth.load_prcp(station_id)
    return None


def load_temp(station_id: str) -> pd.DataFrame | None:
    src = source_of(station_id)
    if src == "GHCNh":
        import ghcnh_truth
        return ghcnh_truth.load_temp(station_id)
    if src == "ISD":
        import isd_truth
        return isd_truth.load_temp(station_id)
    if src == "JMA":
        import jma_truth
        return jma_truth.load_temp(station_id)
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
    assert source_of("JMA:47662") == "JMA"
    assert min_pairs("JMA:47662") == MIN_PAIRS and is_external("JMA:x")
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

    # 2026-09-23 plan, Tasks 2 and 6: the gate is on rain, and only a record
    # that can meet it claims a country for its source.
    import probe_cities
    pool = pd.DataFrame({
        "city": ["RainOnly", "Short", "ShortCap", "SmallTown"],
        "country": ["AA", "BB", "CC", "DD"],
        "population": [900_000, 900_000, 50_000, 50_000],
        "capital": [False, False, True, False],
        "prcp_station": ["GHCNH:a", "JA000047662", "ISD:c", "X"],
        "pair_days": [MIN_PAIRS, MIN_PAIRS - 1, MIN_PAIRS_PROVISIONAL,
                      MIN_PAIRS],
        "tmax_station": [None, "JA000047662", "ISD:c", "X"]})
    assert probe_cities.can_pass(pool).tolist() == [True, False, True, True]
    assert probe_cities.covered_countries(pool) == {"AA", "CC"}, \
        "a sub-gate record or a sub-floor non-capital must not claim a country"
    print("  ok  a rain-only city passes on rain; a record short of its gate "
          "never marks its country covered, and a capital counts below the "
          "floor")


if __name__ == "__main__":
    check()
