"""Per-city calibration: compute metrics and render the switchable sections.

The report used to be Bucharest and nothing else. `capitals.py` already runs the
identical pipeline over every European capital with a usable gauge, so the data
for a per-city view has existed for a while; what was missing was a renderer
with a city dimension.

The division of labour here is deliberate and load-bearing:

    Python computes every statistic. JavaScript only displays it.

So each city's sections are rendered to HTML *here* and shipped as fragments,
reusing `reliability_svg` and the same `calibration.py` functions the printed
analysis uses. Recomputing a reliability table in the browser would create a
second implementation of the study's own mathematics, free to disagree with the
first - which is exactly what `report.py` was written to prevent.

Not every city carries every analysis. The hourly track, the derived-event
track and the external benchmarks are Bucharest-only, because they depend on
Bucharest station records. Each city therefore declares its depth, so a reader
who selects Lisbon and sees fewer sections understands that this is a fact about
the data rather than a broken page.
"""

from __future__ import annotations

import json

import pandas as pd

from analyze import SEASONS
from calibration import (
    brier_decomposition,
    consistency_bars,
    effective_sample_size,
    reliability_table,
)
from config import (
    CAPITAL_COVERAGE,
    CITY_COVERAGE,
    N_PROB_BINS,
    PROCESSED,
    RAIN_THRESHOLD_MM,
)
from report_render import esc, reliability_svg
from sitebuild import slugify

# Below this many days a seasonal split produces four samples too thin to carry
# a decomposition, and the table would imply precision it does not have.
MIN_DAYS_FOR_SEASONS = 600
# Equal-count bins need enough days per bin to mean anything.
SEASON_BINS = 4

DEFAULT_CITY = "Bucharest"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _station_names() -> dict[str, str]:
    """GHCN id -> station name, from the fixed-width list already on disk.

    The capitals probe stored these; the wider city probe did not. Rather than
    re-run a probe that costs elevation-API calls to recover a display string,
    read them back from the station file the fetch already depends on. Returns
    empty if the file is absent, so a fresh clone degrades to a blank name
    rather than failing to build.
    """
    from config import RAW
    path = RAW / "ghcnd-stations.txt"
    if not path.exists():
        return {}
    out = {}
    with path.open() as fh:
        for line in fh:
            out[line[0:11].strip()] = line[41:71].strip()
    return out


def _fill_station_names(cov: dict) -> None:
    """Add missing `*_station_name` fields in place."""
    names = None
    for key in ("included", "excluded"):
        for v in (cov.get(key) or {}).values():
            for which in ("prcp", "tmax"):
                sid = v.get(f"{which}_station")
                if not sid or v.get(f"{which}_station_name"):
                    continue
                if names is None:
                    names = _station_names()
                if sid in names:
                    v[f"{which}_station_name"] = names[sid]


def load_all() -> dict | None:
    """Every per-city artefact, or None if the multi-city stage has not run.

    Prefers the expanded world set over the 15 capitals. The two are produced by
    the same module with the same code path (`capitals.py world`), so this is a
    widening rather than a switch: the capitals remain in the set, with
    identical numbers. Falling back keeps the site buildable from a capitals-only
    run, which is what a first-time clone will have.
    """
    for prefix, coverage in (("cities", CITY_COVERAGE),
                             ("capitals", CAPITAL_COVERAGE)):
        pop_p = PROCESSED / f"{prefix}_pop.parquet"
        met_p = PROCESSED / f"{prefix}_metrics.parquet"
        if pop_p.exists() and met_p.exists():
            break
    else:
        return None

    def opt(name):
        p = PROCESSED / name
        return pd.read_parquet(p) if p.exists() else None

    cov = json.loads(coverage.read_text()) if coverage.exists() else {}
    # The world probe records only the cities it selected. Bucharest and the
    # capitals were carried into that set, but their coverage entries live in
    # the capitals file, so merge rather than replace - otherwise the station
    # name and distance silently vanish from the capitals' own pages.
    if prefix == "cities" and CAPITAL_COVERAGE.exists():
        capcov = json.loads(CAPITAL_COVERAGE.read_text())
        for key in ("included", "excluded"):
            merged = dict(capcov.get(key) or {})
            for name, v in (cov.get(key) or {}).items():
                # Per-key, so the world entry wins where it has a value but
                # does not blank fields it never recorded.
                base = dict(merged.get(name) or {})
                base.update(v)
                merged[name] = base
            cov[key] = merged
    _fill_station_names(cov)

    return {
        "pop": pd.read_parquet(pop_p),
        "met": pd.read_parquet(met_p),
        "diag": opt(f"{prefix}_diagnostics.parquet"),
        "wb": opt(f"{prefix}_wet_bias.parquet"),
        "coverage": cov,
        "prefix": prefix,
        # Cities admitted at the lower evidence gate (`truth_sources`). They
        # get a page, labelled, and are placed against the confirmed cities -
        # never ranked among them, never inside a cross-city figure.
        "prov": {"pop": opt(f"{prefix}_provisional_pop.parquet"),
                 "met": opt(f"{prefix}_provisional_metrics.parquet"),
                 "wb": opt(f"{prefix}_provisional_wet_bias.parquet"),
                 "lead": opt(f"{prefix}_provisional_lead_mae.parquet")},
    }


def provisional_names(k: dict) -> set[str]:
    m = (k.get("prov") or {}).get("met")
    return set(m.city) if m is not None else set()


def _pop_for(name: str, k: dict) -> pd.DataFrame:
    pv = (k.get("prov") or {}).get("pop")
    if pv is not None and name in provisional_names(k):
        return pv
    return k["pop"]


# ---------------------------------------------------------------------------
# Per-city metrics
# ---------------------------------------------------------------------------
def compute_city(name: str, k: dict) -> dict:
    """Reliability table, decomposition, consistency bars and seasonal split.

    Identical binning and identical functions to the headline Bucharest run, so
    a number here and a number in the printed analysis cannot diverge.
    """
    pop = _pop_for(name, k)
    g = pop[pop.city == name].sort_values("local_date")
    prob = g.forecast_prob.values.astype(float)
    event = g.observed_event.values.astype(float)

    tbl = reliability_table(prob, event, n_bins=N_PROB_BINS)
    tbl = tbl.merge(consistency_bars(prob, event, n_bins=N_PROB_BINS),
                    on="bin", how="left")
    tbl["significant"] = (tbl.obs_freq < tbl.cons_lo) | (tbl.obs_freq > tbl.cons_hi)

    m = brier_decomposition(prob, event, n_bins=N_PROB_BINS)
    m["ess"] = effective_sample_size(event)

    seas = None
    if len(g) >= MIN_DAYS_FOR_SEASONS:
        rows = []
        d = g.copy()
        d["season"] = pd.to_datetime(d.local_date).dt.month % 12 // 3
        for s, sg in d.groupby("season"):
            try:
                dec = brier_decomposition(sg.forecast_prob.values,
                                          sg.observed_event.values.astype(float),
                                          n_bins=SEASON_BINS)
            except Exception:
                # A season whose probabilities collapse into too few distinct
                # bins cannot be decomposed. Dropping it is right; faking it
                # with a coarser binning would quietly change what is measured.
                continue
            rows.append({"season": SEASONS[s], "n": len(sg), **dec})
        if len(rows) == 4:
            seas = pd.DataFrame(rows)

    return {"g": g, "tbl": tbl, "m": m, "seas": seas}


def city_meta(name: str, k: dict) -> dict:
    """Location, station and ranking context for one city."""
    met = k["met"].reset_index(drop=True)
    cov = (k["coverage"].get("included") or {}).get(name, {})
    provisional = name in provisional_names(k)
    if provisional:
        row = k["prov"]["met"].set_index("city").loc[name]
        row = row.copy(); row["city"] = name
        # Where the score would fall among confirmed cities - a position,
        # stated as a percentile, not a rank in a league it is not part of.
        rank = None
        pct = float((met.bss < row.bss).mean())
    else:
        row = met[met.city == name].iloc[0]
        rank = int(met.index[met.city == name][0]) + 1
        pct = None
    return {
        "name": name,
        "slug": slugify(name),
        "provisional": provisional,
        "skill_pct": pct,
        "country": cov.get("country", ""),
        "lat": cov.get("lat"),
        "lon": cov.get("lon"),
        "timezone": cov.get("timezone", "UTC"),
        "station": cov.get("prcp_station_name", ""),
        "prcp_km": float(row.prcp_km) if pd.notna(row.prcp_km) else None,
        "offset_days": int(row.offset_days),
        "rank": rank,
        "rank_lo": float(row.rank_lo) if pd.notna(row.rank_lo) else None,
        "rank_hi": float(row.rank_hi) if pd.notna(row.rank_hi) else None,
        "n_cities": len(met),
        "bss": float(row.bss),
        "bss_lo": float(row.bss_lo),
        "bss_hi": float(row.bss_hi),
        "base_rate": float(row.base_rate),
        "brier": float(row.brier),
        "ece": float(row.ece),
        "reliability": float(row.reliability),
        "resolution": float(row.resolution),
        "n": int(row.n),
        "ess": float(row.ess),
    }


# ---------------------------------------------------------------------------
# Section rendering
# ---------------------------------------------------------------------------
# Skill tiers, in one place because two things now say them: the verdict
# sentence below the globe and the card beside it. A label written twice is a
# label that eventually disagrees with itself.
#
# Each row is (upper bound on the skill score, short label, sentence fragment,
# tone class). The thresholds are stated in the glossary as rules of thumb and
# the raw score is always shown beside the label.
_TIERS = [
    (0.0, "Cannot beat climatology", "cannot beat its own climatology", "bad"),
    (0.2, "Barely beats the average",
     "only marginally better than quoting the long-run average", "bad"),
    (0.35, "Moderately skilful", "moderately skilful", "ok"),
    (0.5, "Genuinely useful", "genuinely useful", "good"),
    (float("inf"), "Strongly skilful", "strongly skilful", "good"),
]


def _tier(bss: float) -> tuple[str, str, str]:
    """(short label, sentence fragment, tone class) for a skill score."""
    for cut, short, phrase, cls in _TIERS:
        if bss < cut:
            return short, phrase, cls
    return _TIERS[-1][1], _TIERS[-1][2], _TIERS[-1][3]


def _honesty(nsig: int) -> tuple[str, str]:
    """(clause for the verdict sentence, short form for the card)."""
    if nsig == 0:
        return ("and its stated probabilities are honest throughout",
                "probabilities honest throughout")
    if nsig == 1:
        return ("and honest except at one point on the scale",
                "honest except at one point")
    return (f"though {nsig} points on its probability scale are off by more "
            f"than chance explains",
            f"{nsig} points off by more than chance")


def _verdict(tbl, m) -> tuple[str, str]:
    """A one-line characterisation of the city, derived rather than asserted.

    The original page hand-wrote Bucharest's verdict. With fifteen cities that
    does not scale, and a hand-written verdict per city would be an invitation
    to overstate.
    """
    _, phrase, cls = _tier(m["brier_skill_score"])
    honesty, _ = _honesty(int(tbl.significant.sum()))
    return f"{phrase}, {honesty}", cls


def sec_city_card(city: dict, meta: dict) -> str:
    """The selected city, condensed to fit beside the globe.

    The column next to the globe used to hold instructions and a legend, both
    static: the one part of the page most obviously about the thing you just
    clicked said the same words whatever you clicked. This card is the answer
    in its shortest honest form - tier, score, rank range, record length - and
    it links down to the full verdict rather than restating it.
    """
    tbl, m = city["tbl"], city["m"]
    short, _, cls = _tier(m["brier_skill_score"])
    _, honesty = _honesty(int(tbl.significant.sum()))
    country = f' <span class="muted">{esc(meta["country"])}</span>' if meta["country"] else ""
    if meta.get("provisional"):
        rank_cell = (f'<div><dt>Position</dt><dd>above {meta["skill_pct"]:.0%}'
                     f' <span class="muted">of {meta["n_cities"]}</span></dd></div>')
        badge = (' <span class="tag prov" title="Fewer days of evidence than '
                 'the study requires; shown, but kept out of every cross-city '
                 'claim">provisional</span>')
    else:
        rank_cell = (f'<div><dt>Rank</dt><dd>{meta["rank_lo"]:.0f}&ndash;'
                     f'{meta["rank_hi"]:.0f}\n      <span class="muted">of '
                     f'{meta["n_cities"]}</span></dd></div>')
        badge = ""
    return f"""
<div class="gcard">
  <p class="gcard-head"><b>{esc(meta['name'])}</b>{country}{badge}</p>
  <p class="gcard-tier"><span class="tag {cls}">{short}</span>
    <span class="muted">{honesty}</span></p>
  <dl class="gcard-stats">
    <div><dt>Skill score</dt><dd>{m['brier_skill_score']:.2f}</dd></div>
    {rank_cell}
    <div><dt>Record</dt><dd>{meta['n']} <span class="muted">days</span></dd></div>
  </dl>
  <p class="gcard-more"><a href="#answer">The full verdict for
    {esc(meta['name'])} &darr;</a></p>
</div>"""


def sec_city_answer(city: dict, meta: dict) -> str:
    tbl, m, g = city["tbl"], city["m"], city["g"]
    rows = "".join(
        f"<tr><td class='num'>{r.mean_prob:.0%}</td>"
        f"<td class='num'><b>{r.obs_freq:.0%}</b></td>"
        f"<td class='num'>{int(r.n)}</td>"
        f"<td>{_tag(r)}</td></tr>" for _, r in tbl.iterrows())
    nsig = int(tbl.significant.sum())
    hi, lo = tbl.iloc[-1], tbl.iloc[0]
    line, cls = _verdict(tbl, m)
    name = esc(meta["name"])
    if meta.get("provisional"):
        standing = (
            f"Skill score {m['brier_skill_score']:.2f}, higher than "
            f"{meta['skill_pct']:.0%} of the {meta['n_cities']} confirmed "
            f"cities. It is not ranked among them.</p>\n"
            f'<p class="provnote"><span class="tag prov">provisional</span> '
            f"This city is verified by a NOAA ISD station whose public record "
            f"stops in August 2025, so it has {meta['n']} days of evidence "
            f"where the study requires 500. Its numbers are real but their "
            f"uncertainty is wider, and it is left out of every cross-city "
            f"figure, median and claim on this site.")
    else:
        standing = (
            f"Skill score {m['brier_skill_score']:.2f}, ranked {meta['rank']} of\n"
            f"{meta['n_cities']} &mdash; but see the rank <i>range</i>\n"
            f"({meta['rank_lo']:.0f}&ndash;{meta['rank_hi']:.0f}) before reading "
            f"anything into\nthat position.")

    return f"""
<h2 id="answer">The short answer for {name}</h2>
<div class="verdict">
<p class="lead"><b>The {name} rain forecast is {line}.</b></p>
<p>When it says rain is <i>near certain</i> ({hi.mean_prob:.0%}), believe about
<b>{hi.obs_freq:.0%}</b>. When it says rain is <i>very unlikely</i>
({lo.mean_prob:.0%}), there is still roughly a <b>{lo.obs_freq:.0%}</b> chance.</p>
<p class="muted">Based on {len(g)} days, {g.local_date.min()} to
{g.local_date.max()}. "Rain" means at least {RAIN_THRESHOLD_MM} mm measured at
{esc(meta['station']) or 'the station'}{f", {meta['prcp_km']:.1f} km from the forecast grid point" if meta['prcp_km'] is not None else ''}.
{standing}</p>
</div>

<h3>What the percentages actually mean here</h3>
<div class="tablewrap">
<table><thead><tr><th class="num">It said</th><th class="num">It rained</th>
<th class="num">Days</th><th>Verdict</th></tr></thead><tbody>{rows}</tbody></table>
</div>
<p class="muted">{nsig} of {len(tbl)} groups are further from honest than chance
alone can explain. The rest are indistinguishable from a perfect forecast.</p>
"""


def _tag(r) -> str:
    if not r.significant:
        return '<span class="tag good">honest</span>'
    return ('<span class="tag bad">overpromised</span>' if r.obs_freq < r.mean_prob
            else '<span class="tag bad">underpromised</span>')


def sec_city_curve(city: dict, meta: dict) -> str:
    return f"""
<h2 id="curve">The calibration curve</h2>
<p>Each dot is a group of days sharing a similar forecast. Horizontal position
is what was promised; vertical is what happened. <b>On the dashed diagonal =
honest.</b> Hover any dot for its sample size.</p>
<figure>{reliability_svg(city['tbl'], city['m']['base_rate'],
                         label=f"Calibration curve for {meta['name']}")}
<figcaption>Green band = where a <b>flawless</b> forecast would still land, given
how few days we have. Red dots fall outside it, so those are real errors rather
than noise. The faint horizontal line is climatology
({city['m']['base_rate']:.0%}).</figcaption></figure>
"""


def sec_city_season(city: dict, meta: dict) -> str:
    seas = city["seas"]
    if seas is None:
        return ("\n<h2 id=\"season\">Does the season matter?</h2>\n"
                "<p class=\"muted\">Not enough days in this city's record to split "
                "the year into four and still say anything defensible about each "
                "part. The split is shown only where it is supported.</p>\n")
    rows = "".join(
        f"<tr><td>{esc(r.season)}</td><td class='num'>{int(r.n)}</td>"
        f"<td class='num'>{r.base_rate:.0%}</td>"
        f"<td class='num'>{r.brier_skill_score:.3f}</td>"
        f"<td class='num'>{r.ece:.1%}</td></tr>"
        for _, r in seas.sort_values("brier_skill_score").iterrows())
    best = seas.loc[seas.brier_skill_score.idxmax()]
    worst = seas.loc[seas.brier_skill_score.idxmin()]
    return f"""
<h2 id="season">Does the season matter?</h2>
<p>In {esc(meta['name'])} the forecast is most skilful in
<b>{esc(best.season)}</b> ({best.brier_skill_score:.2f}) and least in
<b>{esc(worst.season)}</b> ({worst.brier_skill_score:.2f}).</p>
<div class="tablewrap">
<table><thead><tr><th>Season</th><th class="num">Days</th>
<th class="num">Rain days</th><th class="num">Skill score</th>
<th class="num">Avg error</th></tr></thead><tbody>{rows}</tbody></table>
</div>
<p class="muted">Four-way splits of a two-year record are thin, so read the
ordering as indicative. Coarser binning is used here than for the annual curve
for the same reason.</p>
"""


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------
def city_payload(name: str, k: dict, deep: bool) -> dict:
    """Everything the browser needs for one city: fragments plus numbers."""
    city = compute_city(name, k)
    meta = city_meta(name, k)
    tbl = city["tbl"]

    # The probability lookup that powers the forecast annotation. Bin edges and
    # observed frequency only - the browser matches a stated PoP to a bin, it
    # does not recompute one.
    meta["pop_map"] = [
        {"lo": float(r.lo), "hi": float(r.hi), "mean": float(r.mean_prob),
         "obs": float(r.obs_freq), "n": int(r.n), "sig": bool(r.significant)}
        for _, r in tbl.iterrows()]

    meta["depth"] = ["Rain-probability calibration", "Calibration curve"]
    if city["seas"] is not None:
        meta["depth"].append("Seasonal split")
    if deep:
        meta["depth"] += ["Hourly verification", "Frost and heat events",
                          "External benchmarks", "Robustness checks",
                          "Recalibration test"]

    meta["deep"] = deep
    meta["html"] = {
        "card": sec_city_card(city, meta),
        "answer": sec_city_answer(city, meta),
        "curve": sec_city_curve(city, meta),
        "season": sec_city_season(city, meta),
    }
    return meta


def all_payloads(k: dict) -> list[dict]:
    names = sorted(set(k["met"].city) | provisional_names(k))
    return [city_payload(n, k, deep=(n == DEFAULT_CITY)) for n in names]


def exclusions(k: dict) -> list[dict]:
    """Places that were probed and dropped, grouped by what stopped them.

    These used to be drawn on the globe as hollow dots. That was the wrong
    place for them: they were 44% of the markers, they competed with real
    cities for space when the globe declutters, and the reason was reachable
    only by hovering - so on a touch screen they were dots that did nothing and
    said nothing. The exclusions are still part of the result, and a grouped
    count says more than 66 scattered rings did: the dominant reason is not
    "no gauge" but "a gauge that files a third of its days".
    """
    groups = [
        ("sparse", "The gauge reports too few days",
         "A rain gauge can satisfy every coverage rule and still file only a "
         "fraction of its days. Below the minimum usable pairs there is not "
         "enough overlap with the forecast archive to score a calibration "
         "curve against."),
        ("lag", "The rain-day convention cannot be pinned down",
         "Station precipitation is a 24-hour total ending at an hour the "
         "observer chooses, which need not line up with the calendar day the "
         "forecast refers to. Where the lag scan has no clean peak the "
         "convention is unknown, and guessing it would not add noise - it "
         "would produce a confidently wrong answer."),
        ("nodata", "No usable record at all",
         "The station, the reanalysis or the forecast archive returned "
         "nothing overlapping the evaluation window."),
    ]

    def bucket(why: str) -> str:
        if "usable pairs" in why:
            return "sparse"
        if "single lag" in why:
            return "lag"
        return "nodata"

    cov = (k.get("coverage") or {}).get("included") or {}
    included_names = set(k["met"].city)
    diag = k.get("diag")
    rows: dict[str, list[dict]] = {g[0]: [] for g in groups}
    if diag is not None:
        for _, r in diag[~diag.included].iterrows():
            if r.city in included_names:
                continue
            v = cov.get(r.city) or {}
            rows[bucket(str(r.why))].append({
                "name": r.city,
                "country": v.get("country", ""),
                "detail": str(r.why),
            })

    out = []
    for key, title, blurb in groups:
        cities = sorted(rows[key], key=lambda d: d["name"])
        if cities:
            out.append({"key": key, "title": title, "blurb": blurb,
                        "cities": cities})
    return out
