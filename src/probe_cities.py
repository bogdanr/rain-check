"""Task 40: generalise the city set from 43 European capitals to the world.

The capitals probe (probe_capitals.py) answered "which capitals can we verify?".
That framing turned out to be the wrong constraint. Capitals are 43 places
chosen for politics; the real limit is where a rain gauge still reports, and
that admits far more of the world:

    GeoNames cities >= 15k population          34,136
    ... with a usable PRCP gauge                8,509
    ... with a usable PRCP *and* TMAX gauge     8,150

So coverage was never the binding constraint - the city list was. What binds
instead is (a) fetch budget, roughly six API calls per city, and (b) two
selection rules that exist to stop the sample being garbage:

  1. **One city per gauge.** 8,150 cities share only ~3,750 distinct stations.
     Two cities verified against the same rain gauge are not two samples; they
     are one sample counted twice, and averaging them would silently
     over-weight wherever gauges are dense. Deduplicating by station is the
     single most important rule here.

  2. **A cap per country.** The qualifying pool is 39% United States and 13%
     Germany, purely because those countries report densely to GHCN. Ranking
     "world cities" off that pool would produce a league table of American
     suburbs. The cap trades raw n for the regime spread the study actually
     needs - the Phase 7/8 results are all driven by precipitation regime, not
     by how many cities were included.

Elevation is checked in two stages: a cheap pre-filter against the GeoNames
terrain model to shortlist, then the authoritative check against the *forecast
grid point* elevation for the shortlist only. The grid point is what the
forecast describes, so that is the comparison that matters - and it is what
rejected Andorra la Vella and Vaduz in the capitals probe.

Usage:  python src/probe_cities.py
"""

from __future__ import annotations

import io
import json
import math
import zipfile

import numpy as np
import pandas as pd
import requests

import truth_sources
from config import PROCESSED, RAW
from probe_capitals import (
    CAPITALS,
    MAX_ELEV_DIFF_M,
    MAX_STATION_KM,
    MIN_PAIRS,
    MIN_STATION_DAYS,
    WINDOW_END_YEAR,
    WINDOW_START_YEAR,
    ELEVATION_API,
    load_inventory,
    load_stations,
    pick_station,
    station_frame,
)

GEONAMES_URL = "https://download.geonames.org/export/dump/cities15000.zip"

# --------------------------------------------------------------------------
# Selection budget
# --------------------------------------------------------------------------
# Each city costs ~5 requests for the probability archive plus 1 for ERA5. At
# the throttle in fetch.py that is roughly two seconds per city, so a few
# hundred is comfortable and a few thousand is not.
#
# In practice this does not bind: the rules below leave ~140 cities, well under
# it. That is deliberate. More cities in the same countries buys no precision
# (E28: the design effect puts the resolving sample near 68,000 cities) and no
# explanatory power (G4: no city property predicts skill). What the study can
# use is more *countries* - more regimes, more national forecast pipelines -
# and that is limited by where GHCN still has a reporting gauge, not by this
# number. See Task 10 in plans/2026-09-22-session-state-v1.md.
MAX_CITIES = 300

# Ceiling per country. Set against the observation that the US alone supplies
# 3,328 qualifying cities: without a cap the sample is a map of GHCN reporting
# density rather than of world climate. Kept at 8 on purpose: the depth a
# larger cap would add is exactly the kind of n that E28 says cannot resolve
# anything, while it would tilt the sample back toward dense-GHCN countries.
MAX_PER_COUNTRY = 8

# Cities below this are unlikely to be anyone's daily forecast, and the pool is
# large enough that we can afford to prefer places people live. Raised from
# 100k to 200k: it costs 36 cities but only two countries (Iceland via
# Reykjavik, which the capital exemption below keeps anyway, and Morocco via
# Nador), and a 176k town was never the forecast most readers check.
#
# Capitals are exempt. They are in the sample for comparability with the
# published capitals run, not for their size, and the superset guarantee in
# `select` only holds if the floor cannot remove one first.
MIN_POPULATION = 200_000

# Two cities this close share weather as well as, usually, a gauge. Applied
# after station dedupe to catch neighbouring metros with separate stations.
MIN_CITY_SEPARATION_KM = 30.0

# --------------------------------------------------------------------------
# World capitals (2026-09-23 plan, Task 4)
# --------------------------------------------------------------------------
# The capital exemption used to cover the 43 European capitals only, so
# Nairobi, Lima or Jakarta had to compete on population with every other city
# in the pool and could lose their country's place to a larger city on a
# better gauge. A capital is the fairest one-city-per-country anchor, and is
# usually where a country's principal synoptic station sits.
#
# GeoNames marks one capital per country (feature code PPLC). That is taken as
# given, with two corrections:
#
#   * A capital of fewer than MIN_CAPITAL_POPULATION people is not exempt.
#     GeoNames tags territories as well as states, including Plymouth,
#     Montserrat, which has been abandoned since 1997 and lists 0 people.
#   * CAPITAL_EXTRA adds the seat of government where it is not the PPLC city,
#     and the further official capitals of countries that have several. Every
#     entry has its reason next to it. Disputed capitals are deliberately NOT
#     added (GeoNames lists none for Israel or Palestine): those countries'
#     cities still compete normally, and this study takes no position.
MIN_CAPITAL_POPULATION = 15_000

CAPITAL_EXTRA: dict[tuple[str, str], str] = {
    ("La Paz", "BO"): "seat of government; Sucre is the constitutional capital",
    ("Cotonou", "BJ"): "seat of government; Porto-Novo is the official capital",
    ("Abidjan", "CI"): "seat of most ministries; Yamoussoukro is official",
    # Not The Hague: the Netherlands is already anchored by Amsterdam, and The
    # Hague sits on Rotterdam's gauge, so it could only replace a live page.
    ("Cape Town", "ZA"): "legislative capital (South Africa has three)",
    ("Bloemfontein", "ZA"): "judicial capital (South Africa has three)",
}


def load_geonames() -> pd.DataFrame:
    """GeoNames cities >= 15k population, downloaded once and cached.

    `keep_default_na=False` matters: pandas otherwise reads Namibia's ISO code
    "NA" as a missing value, and every Namibian city - Windhoek included -
    silently had no country and could never be selected.
    """
    path = RAW / "cities15000.txt"
    if not path.exists():
        resp = requests.get(GEONAMES_URL, timeout=300)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            path.write_bytes(z.read("cities15000.txt"))
    cols = ["geonameid", "name", "asciiname", "alt", "lat", "lon", "fclass",
            "fcode", "country", "cc2", "a1", "a2", "a3", "a4", "population",
            "elevation", "dem", "timezone", "moddate"]
    df = pd.read_csv(path, sep="\t", names=cols, low_memory=False,
                     keep_default_na=False, na_values=[""])
    extra = set(CAPITAL_EXTRA)
    df["capital"] = (((df.fcode == "PPLC")
                      & (df.population >= MIN_CAPITAL_POPULATION))
                     | pd.Series([(a, c) in extra for a, c in
                                  zip(df.asciiname, df.country)],
                                 index=df.index))
    return df[["asciiname", "country", "lat", "lon", "population", "dem",
               "timezone", "capital"]].rename(columns={"asciiname": "city"})


def capital_keys() -> set[tuple[str, str]]:
    """(city, country) of every world capital, plus the European capitals the
    published capitals run was built on (spelled as that run spells them)."""
    geo = load_geonames()
    world = set(zip(geo.city[geo.capital], geo.country[geo.capital]))
    return world | {(name, v[3]) for name, v in CAPITALS.items()}


# --------------------------------------------------------------------------
# The pair gate, known before any forecast is fetched (Tasks 2 and 3)
# --------------------------------------------------------------------------
def can_pass(pool: pd.DataFrame) -> pd.Series:
    """True where the city's truth record can still meet its pair gate.

    A necessary condition, not a sufficient one: a pair needs an observed day,
    so a record with fewer in-window days than the gate cannot pass however
    the join goes. For GHCN rows the day count is the bulk census, which
    counts exactly the forecast-archive window the join uses; GHCNh and ISD
    rows carry the in-window count their own stages measured.

    `pick_station` keeps "the fullest sparse record anyway" when nothing in
    range reports often enough. Such a row used to count as its country being
    covered, which blocked every other source there: Japan's eight GHCN cities
    held 350-475 days, Buraydah 10, Lahore 247 - none could ever pass, and
    the GHCNh stage never looked at those countries.
    """
    days = pool["pair_days"] if "pair_days" in pool else pool["prcp_days"]
    need = pool.prcp_station.map(truth_sources.min_pairs)
    return days.fillna(0).astype(float) >= need


def covered_countries(pool: pd.DataFrame) -> set[str]:
    """Countries this pool can verify: a row that can meet its gate, in a
    city at the population floor or in a capital.

    One definition, used by every truth stage and by the merge, so a country
    cannot be "covered" for one stage and open for another. A capital counts
    because the source verifying the capital is the source for the country;
    a small city kept only as already published does not (see
    `with_external` on Nador).
    """
    if pool is None or pool.empty:
        return set()
    cap = pool["capital"] if "capital" in pool else False
    ok = can_pass(pool) & ((pool.population >= MIN_POPULATION) | cap)
    return set(pool.country[ok])


def candidate_pool() -> pd.DataFrame:
    """Every city with a gauge close enough, before any selection.

    The distance and elevation envelope is unchanged; what changed is that the
    gauge is chosen from inside it rather than assumed to be the nearest one.
    See `pick_station` - the inventory says only that a station reported in
    2024 and in 2026, which a gauge filing 37 days in between satisfies.
    """
    import ghcn_bulk

    cities = load_geonames()
    st, inv = load_stations(), load_inventory()
    cen = ghcn_bulk.census().set_index("station")
    covering = inv[(inv.year_first <= WINDOW_START_YEAR)
                   & (inv.year_last >= WINDOW_END_YEAR)]
    by_elem = {e: set(g.id) for e, g in covering.groupby("elem")}

    prcp = station_frame(st, by_elem.get("PRCP", set()), cen.prcp_days)
    tmax = station_frame(st, by_elem.get("TMAX", set()), cen.tmax_days)
    print(f"  stations reporting through {WINDOW_END_YEAR}: "
          f"PRCP {len(prcp)} ({int((prcp.days >= MIN_STATION_DAYS).sum())} of "
          f"them filing {MIN_STATION_DAYS}+ days), "
          f"TMAX {len(tmax)} ({int((tmax.days >= MIN_STATION_DAYS).sum())})")

    rows = []
    for r in cities.itertuples(index=False):
        # The pre-filter compares against GeoNames' terrain model; the shortlist
        # is re-checked against the forecast grid point in
        # confirm_grid_elevation(), which is the comparison that counts.
        p, _ = pick_station(prcp, r.lat, r.lon, r.dem)
        if p is None:
            continue
        # Rain decides (2026-09-23 plan, Task 6). A city without a usable
        # daily-maximum gauge keeps its rain verification and simply has no
        # temperature track; `capitals.track_a` already skips such a city.
        t, _ = pick_station(tmax, r.lat, r.lon, r.dem)
        if t is not None and int(t.days) < MIN_PAIRS:
            t = None
        tcols = ((t.id, float(t.km), float(t.elev_m), int(t.days))
                 if t is not None else (None, np.nan, np.nan, np.nan))
        rows.append((r.city, r.country, r.lat, r.lon, int(r.population),
                     r.dem, r.timezone, bool(r.capital),
                     p.id, float(p.km), float(p.elev_m), int(p.days),
                     *tcols))
    ghcn = pd.DataFrame(rows, columns=[
        "city", "country", "lat", "lon", "population", "dem", "timezone",
        "capital", "prcp_station", "prcp_km", "prcp_elev_m", "prcp_days",
        "tmax_station", "tmax_km", "tmax_elev_m", "tmax_days"])
    # The census counts days inside the forecast-archive window, which is
    # the window the join pairs over - so for GHCN it is the pair ceiling.
    ghcn["pair_days"] = ghcn.prcp_days
    return with_external(ghcn)


def with_external(ghcn: pd.DataFrame) -> pd.DataFrame:
    """Add GHCNh- and ISD-verified cities, if those stages have run.

    One truth source per country, in priority order GHCN > GHCNh > ISD (see
    truth_sources): a GHCNh city only where GHCN cannot verify a city at the
    floor (or the capital), an ISD city - provisional - only where neither
    can. "Can verify" means a record able to meet the pair gate
    (`covered_countries`); a sparse record kept as the best of a bad lot does
    not claim its country. The stages already chose their countries that way,
    but the pools move as gauges open and close, so the rule is enforced again
    here rather than trusted from files written earlier.

    The one sanctioned mix: a city kept below the floor only because it is
    already published does not claim its country for GHCN (a capital does:
    the source that verifies the capital is the source for the country).
    Otherwise one grandfathered small city - Nador, 176k, read off a gauge
    in Melilla - would lock Fes and Tangier out of Morocco. Every city
    carries its `truth` label, so a per-country comparison can see the mix.
    """
    import ghcnh_truth
    import isd_truth
    import jma_truth

    # A national source replaces GHCN-Daily in its own country (JMA for
    # Japan: GHCN-Daily's Japanese records stop in 2025-08). It is applied
    # only if it verifies a city there; otherwise GHCN rows stand.
    national = {}
    jr = jma_truth.pool_rows()
    if len(jr):
        jr = jr.assign(truth="JMA", provisional=False)
        for cc in covered_countries(jr):
            national[cc] = jr[jr.country == cc]
    ghcn = ghcn[~ghcn.country.isin(national)]

    out = ghcn.assign(truth="GHCN", provisional=False)
    if "inferred_share" not in out:
        out["inferred_share"] = 0.0
    for cc, rows in national.items():
        for c in out.columns:
            if c not in rows:
                rows = rows.assign(**{c: np.nan})
        out = pd.concat([out, rows[out.columns]], ignore_index=True)
    caps = None
    for label, rows in (("GHCNh", ghcnh_truth.pool_rows()),
                        ("ISD", isd_truth.pool_rows())):
        if rows.empty:
            continue
        covered = covered_countries(out)
        rows = rows[~rows.country.isin(covered)].assign(
            truth=label, provisional=label == "ISD")
        if "capital" not in rows:
            if caps is None:
                caps = capital_keys()
            rows["capital"] = [(c, k) in caps
                               for c, k in zip(rows.city, rows.country)]
        for c in out.columns:
            if c not in rows:
                rows[c] = np.nan
        out = pd.concat([out, rows[out.columns]], ignore_index=True)
    return out


def published_keys() -> set[tuple[str, str]]:
    """(city, country) for every city in the last written coverage file.

    That file is what the site and the analyses were built from, so it is the
    record of what has been published. Because this probe rewrites it with the
    exempt cities included, the exemption carries forward run to run rather
    than lapsing after one.
    """
    path = RAW / "city_coverage.json"
    if not path.exists():
        return set()
    inc = json.loads(path.read_text()).get("included", {})
    # Registry keys may carry a disambiguating " (CC)" (see unique_names);
    # the selection matches on the GeoNames name, so strip it back off.
    out = set()
    for name, v in inc.items():
        cc = v["country"]
        suffix = f" ({cc})"
        out.add((name[:-len(suffix)] if name.endswith(suffix) else name, cc))
    return out


# Every rule below that has to choose between two cities chooses in this order:
# a capital, then a city already published, then the larger. Population alone
# let a bigger neighbour on the same gauge (or within 30 km) take a capital's
# place, and let a newly admitted city silently push a published one out.
PRIORITY = ["capital", "published", "population"]


def _by_priority(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values(PRIORITY, ascending=False, kind="stable")


def select(pool: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Apply the selection rules, recording what each one removed."""
    log = []
    pool = pool.copy()
    published = published_keys()
    key = list(zip(pool.city, pool.country))
    # The flag comes from the GeoNames ROW (feature code), not the name:
    # (name, country) is not unique - the US has several Washingtons, and
    # name matching made two of them capitals. The published European
    # capitals, spelled as that run spells them, are added by name, but only
    # to the most populous row of that name in its country.
    row_cap = (pool["capital"].fillna(False).astype(bool) if "capital" in pool
               else pd.Series(False, index=pool.index))
    euro = {(name, v[3]) for name, v in CAPITALS.items()}
    top = (pool.sort_values("population", ascending=False)
               .drop_duplicates(["city", "country"]).index)
    euro_cap = pd.Series([k in euro for k in key], index=pool.index) \
        & pool.index.isin(top)
    pool["capital"] = row_cap | euro_cap
    pool["published"] = [k in published for k in key]

    # Rule 0: the pair gate, before anything else (Task 3). A city whose
    # record cannot reach its gate is dropped here rather than downstream,
    # so it neither takes a country's slot nor gets listed without a page.
    # The published exemption covers the population floor only, never this.
    n0 = len(pool)
    ok = can_pass(pool)
    lost = pool[~ok & pool.published]
    keep = pool[ok].copy()
    log.append({"rule": "record can meet its pair gate",
                "removed": n0 - len(keep), "remaining": len(keep),
                "published_removed": sorted(
                    f"{r.city} ({r.country})" for r in lost.itertuples())})

    # Cities already published are exempt from the floor. It was raised after
    # they went out; applying it retroactively would delete 35 live pages and
    # silently change the sample E35 was measured on. New cities meet the new
    # floor; old ones keep their place. Capitals - now every country's, not
    # only Europe's - are exempt too.
    n1 = len(keep)
    keep = keep[(keep.population >= MIN_POPULATION) | keep.capital
                | keep.published]
    log.append({"rule": f"population >= {MIN_POPULATION:,} "
                        "(capitals and published cities exempt)",
                "removed": n1 - len(keep), "remaining": len(keep)})

    # Rule 1: one city per rain gauge.
    n1 = len(keep)
    keep = _by_priority(keep).drop_duplicates(subset="prcp_station",
                                              keep="first")
    log.append({"rule": "one city per PRCP station",
                "removed": n1 - len(keep), "remaining": len(keep)})

    # Rule 2: drop near-neighbours that survived rule 1 on a separate gauge.
    n2 = len(keep)
    keep = _by_priority(keep).reset_index(drop=True)
    chosen_lat: list[float] = []
    chosen_lon: list[float] = []
    mask = []
    for r in keep.itertuples(index=False):
        if chosen_lat:
            dy = (np.asarray(chosen_lat) - r.lat) * 110.574
            dx = ((np.asarray(chosen_lon) - r.lon) * 111.320
                  * math.cos(math.radians(r.lat)))
            far = float(np.hypot(dx, dy).min()) >= MIN_CITY_SEPARATION_KM
        else:
            far = True
        mask.append(far)
        if far:
            chosen_lat.append(r.lat); chosen_lon.append(r.lon)
    keep = keep[mask]
    log.append({"rule": f"cities >= {MIN_CITY_SEPARATION_KM:.0f} km apart",
                "removed": n2 - len(keep), "remaining": len(keep)})

    # Rule 3: cap per country. Capitals sit outside the cap: adding a
    # capital must not push out a published city (Washington would otherwise
    # have taken Philadelphia's page, Canberra the Sunshine Coast's), and a
    # capital is one per country (three at most, South Africa), so the cap
    # still does its job of keeping dense-GHCN countries from dominating.
    n3 = len(keep)
    keep = pd.concat([
        keep[keep.capital],
        _by_priority(keep[~keep.capital]).groupby(
            "country", group_keys=False).head(MAX_PER_COUNTRY)])
    log.append({"rule": f"at most {MAX_PER_COUNTRY} per country "
                        "(plus capitals)",
                "removed": n3 - len(keep), "remaining": len(keep)})

    # Always retain capitals, so every country with a verifiable capital has
    # it, and the run stays a superset of the published capitals run. The
    # rest of the budget goes to published cities, then the largest.
    n4 = len(keep)
    forced = keep.capital
    rest = _by_priority(keep[~forced]).head(
        max(0, MAX_CITIES - int(forced.sum())))
    keep = (pd.concat([keep[forced], rest])
              .sort_values("population", ascending=False)
              .reset_index(drop=True))
    log.append({"rule": f"budget of {MAX_CITIES} (capitals retained)",
                "removed": n4 - len(keep), "remaining": len(keep)})
    return keep, log


def confirm_grid_elevation(sel: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Authoritative elevation check: station vs the forecast grid point.

    The pre-filter used GeoNames' terrain model, which is not what the forecast
    describes. This re-checks the shortlist against the grid point Open-Meteo
    actually serves, in batches, and drops anything that fails.
    """
    from fetch import fetch_json, is_error

    elevs: list[float] = []
    for i in range(0, len(sel), 100):
        chunk = sel.iloc[i:i + 100]
        payload = fetch_json(ELEVATION_API, {
            "latitude": ",".join(f"{v:.4f}" for v in chunk.lat),
            "longitude": ",".join(f"{v:.4f}" for v in chunk.lon),
        })
        if is_error(payload):
            elevs.extend([np.nan] * len(chunk))
            continue
        elevs.extend(payload["elevation"])

    sel = sel.copy()
    sel["grid_elev_m"] = elevs
    gap = (sel.prcp_elev_m - sel.grid_elev_m).abs()
    bad = gap > MAX_ELEV_DIFF_M
    dropped = {
        r.city: {"reason": "station elevation differs from the forecast grid "
                           f"point by more than {MAX_ELEV_DIFF_M:.0f} m",
                 "station_elev_m": float(r.prcp_elev_m),
                 "grid_elev_m": float(r.grid_elev_m),
                 "nearest_km": round(float(r.prcp_km), 1)}
        for r in sel[bad].itertuples(index=False)}
    return sel[~bad].copy(), dropped


def _num(v, digits: int | None = None):
    """float/int for JSON, None for a missing value (a rain-only city has no
    temperature gauge, and says so rather than carrying NaN)."""
    if v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v):
        return None
    return round(float(v), digits) if digits is not None else float(v)


def unique_names(sel: pd.DataFrame) -> list[str]:
    """Registry keys, one per city. The pipeline keys everything by name, and
    a world set repeats names across countries (Hyderabad, Santiago,
    Valencia). The first holder keeps the bare name - selection order puts
    published cities first, so no live page is renamed - and later ones get
    their country code: "Hyderabad (PK)"."""
    published = {c for c, _ in published_keys()}
    order = sorted(range(len(sel)), key=lambda i: (
        sel.city.iloc[i] not in published, -int(sel.population.iloc[i])))
    names: dict[int, str] = {}
    taken: set[str] = set()
    for i in order:
        n = sel.city.iloc[i]
        if n in taken:
            n = f"{n} ({sel.country.iloc[i]})"
        taken.add(n)
        names[i] = n
    return [names[i] for i in range(len(sel))]


def country_record(pool: pd.DataFrame, sel: pd.DataFrame) -> pd.DataFrame:
    """Task 1: for every country with a city at the floor, which source was
    tried, how far it got, and - where nothing was included - the step that
    stopped it. Written to data/processed so the site can say why a country
    is absent instead of leaving the reader to guess."""
    import ghcnh_truth
    import isd_truth
    import jma_truth

    geo = load_geonames()
    big = geo[(geo.population >= MIN_POPULATION) | geo.capital]
    ext = {"GHCNh": ghcnh_truth.country_log(), "ISD": isd_truth.country_log(),
           "JMA": jma_truth.country_log()}
    g = pool[pool.truth == "GHCN"]
    g = g[(g.population >= MIN_POPULATION) | g.capital.fillna(False)]
    rows = []
    for cc, cities in big.groupby("country"):
        caps = cities[cities.capital].city.tolist()
        gg = g[g.country == cc]
        inc = sel[sel.country == cc]
        rec = {
            "country": cc,
            "cities_at_floor": int((cities.population >= MIN_POPULATION).sum()),
            "people_at_floor": int(cities.population[
                cities.population >= MIN_POPULATION].sum()),
            "capital": ", ".join(caps),
            "ghcn_in_range": int(len(gg)),
            "ghcn_can_pass": int(can_pass(gg).sum()) if len(gg) else 0,
            "ghcn_best_days": int(gg.pair_days.max()) if len(gg) else 0,
            "source": inc.truth.iloc[0] if len(inc) else "",
            "included": int(len(inc)),
            "capital_included": bool(inc.capital.any()) if len(inc) else False,
        }
        for label, log in ext.items():
            e = log.get(cc, {})
            rec[f"{label.lower()}_step"] = e.get("step", "not probed")
            rec[f"{label.lower()}_best_pairs"] = int(e.get("best_pairs", 0))
        if len(inc):
            why = ""
        elif rec["ghcn_can_pass"]:
            why = ("GHCN gauges can verify it, but every city was removed by "
                   "a later selection rule (gauge shared, 30 km, elevation)")
        else:
            ghcn = ("no gauge in range" if not len(gg)
                    else f"best gauge {rec['ghcn_best_days']} days")
            steps = [f"GHCN: {ghcn}"]
            for label in ext:
                steps.append(f"{label}: {rec[label.lower() + '_step']}")
            why = "; ".join(steps)
        rec["why_missing"] = why
        rows.append(rec)
    return pd.DataFrame(rows).sort_values("people_at_floor", ascending=False)


def main() -> None:
    print("=== Task 40: how many cities can this study actually cover? ===\n")
    pool = candidate_pool()
    print(f"\n  cities with a usable gauge (all sources): {len(pool):,}")
    print(f"  distinct PRCP stations behind them      : "
          f"{pool.prcp_station.nunique():,}")
    print(f"  top countries in the raw pool           : "
          + ", ".join(f"{k} {v}" for k, v in
                      pool.country.value_counts().head(5).items()))

    before = published_keys()
    sel, log = select(pool)
    print("\n  Selection:\n")
    for step in log:
        print(f"    {step['rule']:42s} -{step['removed']:6,d}  "
              f"-> {step['remaining']:,}")
        if step.get("published_removed"):
            print(f"      published cities that could never pass, now dropped "
                  f"({len(step['published_removed'])}): "
                  + ", ".join(step["published_removed"]))

    sel, dropped = confirm_grid_elevation(sel)
    print(f"\n  grid-elevation check dropped {len(dropped)}: "
          + (", ".join(sorted(dropped)) if dropped else "none"))

    sel = sel.reset_index(drop=True)
    names = unique_names(sel)
    included = {}
    for name, r in zip(names, sel.itertuples(index=False)):
        included[name] = {
            "lat": float(r.lat), "lon": float(r.lon), "timezone": r.timezone,
            "country": r.country, "population": int(r.population),
            "grid_elev_m": _num(r.grid_elev_m),
            "prcp_station": r.prcp_station,
            "prcp_km": _num(r.prcp_km, 1),
            "prcp_elev_m": _num(r.prcp_elev_m),
            "prcp_days": int(r.prcp_days),
            "pair_days": int(r.pair_days),
            # None for a rain-only city (Task 6): its rain is verified at the
            # full gate, it simply has no temperature track.
            "tmax_station": r.tmax_station if isinstance(r.tmax_station, str)
            and r.tmax_station else None,
            "tmax_km": _num(r.tmax_km, 1),
            "tmax_days": None if _num(r.tmax_days) is None
            else int(r.tmax_days),
            "is_capital": bool(r.capital),
            "truth": r.truth,
            # Share of the in-window rain days built from zeros restored by
            # GHCNh's zero rule (ghcnh_truth.infer_zeros); 0 elsewhere.
            "inferred_share": _num(r.inferred_share, 3) or 0.0,
            # Provisional cities (ISD, 400-pair gate) are shown on the site
            # with their own numbers but never enter a pooled claim.
            "provisional": bool(r.provisional),
        }

    countries = country_record(pool, sel)
    countries.to_parquet(PROCESSED / "country_coverage.parquet", index=False)

    out = RAW / "city_coverage.json"
    out.write_text(json.dumps({
        "probed": pd.Timestamp.now("UTC").strftime("%Y-%m-%d"),
        "rules": {
            "max_station_km": MAX_STATION_KM,
            "max_elev_diff_m": MAX_ELEV_DIFF_M,
            "min_pairs": MIN_PAIRS,
            "min_station_days": MIN_STATION_DAYS,
            "min_population": MIN_POPULATION,
            "max_per_country": MAX_PER_COUNTRY,
            "min_city_separation_km": MIN_CITY_SEPARATION_KM,
            "max_cities": MAX_CITIES,
            "window_years": [WINDOW_START_YEAR, WINDOW_END_YEAR],
        },
        "pool_size": int(len(pool)),
        "included": included,
        "excluded": dropped,
    }, indent=2, sort_keys=True))

    now = set(zip(sel.city, sel.country))
    gone = sorted(f"{n} ({c})" for n, c in before - now)
    new = sorted(f"{n} ({c})" for n, c in now - before)
    print(f"\nwrote {out}  ({len(included)} cities)")
    print(f"  added since the last probe ({len(new)}): " + ", ".join(new))
    print(f"  removed since the last probe ({len(gone)}): " + ", ".join(gone))
    print(f"\n  countries represented : {sel.country.nunique()}")
    print(f"  capitals retained     : {int(sel.capital.sum())}")
    print(f"  rain-only (no TMAX)   : "
          f"{sum(v['tmax_station'] is None for v in included.values())}")
    print(f"  population range      : {sel.population.min():,} .. "
          f"{sel.population.max():,}")
    print(f"  latitude range        : {sel.lat.min():.1f} .. {sel.lat.max():.1f}")
    print("\n  By country:")
    vc = sel.country.value_counts()
    print("    " + ", ".join(f"{k}:{v}" for k, v in vc.items()))
    missing = countries[countries.included == 0]
    print(f"\n  wrote {PROCESSED / 'country_coverage.parquet'}: "
          f"{len(countries) - len(missing)} of {len(countries)} countries "
          f"with a city at the floor are covered. Largest still missing:")
    for r in missing.head(25).itertuples():
        print(f"    {r.country}  {r.people_at_floor / 1e6:6.1f} M  {r.why_missing}")


if __name__ == "__main__":
    main()
