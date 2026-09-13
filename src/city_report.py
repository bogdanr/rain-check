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
from config import CAPITAL_COVERAGE, N_PROB_BINS, PROCESSED, RAIN_THRESHOLD_MM
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
def load_all() -> dict | None:
    """Every capitals artefact, or None if the multi-city stage has not run."""
    pop_p = PROCESSED / "capitals_pop.parquet"
    met_p = PROCESSED / "capitals_metrics.parquet"
    if not (pop_p.exists() and met_p.exists()):
        return None

    def opt(name):
        p = PROCESSED / name
        return pd.read_parquet(p) if p.exists() else None

    cov = json.loads(CAPITAL_COVERAGE.read_text()) if CAPITAL_COVERAGE.exists() else {}
    return {
        "pop": pd.read_parquet(pop_p),
        "met": pd.read_parquet(met_p),
        "diag": opt("capitals_diagnostics.parquet"),
        "wb": opt("capitals_wet_bias.parquet"),
        "coverage": cov,
    }


# ---------------------------------------------------------------------------
# Per-city metrics
# ---------------------------------------------------------------------------
def compute_city(name: str, k: dict) -> dict:
    """Reliability table, decomposition, consistency bars and seasonal split.

    Identical binning and identical functions to the headline Bucharest run, so
    a number here and a number in the printed analysis cannot diverge.
    """
    g = k["pop"][k["pop"].city == name].sort_values("local_date")
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
    row = met[met.city == name].iloc[0]
    rank = int(met.index[met.city == name][0]) + 1
    cov = (k["coverage"].get("included") or {}).get(name, {})
    return {
        "name": name,
        "slug": slugify(name),
        "country": cov.get("country", ""),
        "lat": cov.get("lat"),
        "lon": cov.get("lon"),
        "timezone": cov.get("timezone", "UTC"),
        "station": cov.get("prcp_station_name", ""),
        "prcp_km": float(row.prcp_km) if pd.notna(row.prcp_km) else None,
        "offset_days": int(row.offset_days),
        "rank": rank,
        "rank_lo": float(row.rank_lo),
        "rank_hi": float(row.rank_hi),
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
def _verdict(tbl, m) -> tuple[str, str]:
    """A one-line characterisation of the city, derived rather than asserted.

    The original page hand-wrote Bucharest's verdict. With fifteen cities that
    does not scale, and a hand-written verdict per city would be an invitation
    to overstate. These thresholds are stated in the glossary as rules of thumb
    and the raw skill score is always shown beside them.
    """
    bss = m["brier_skill_score"]
    nsig = int(tbl.significant.sum())
    if bss < 0:
        quality = ("cannot beat its own climatology", "bad")
    elif bss < 0.2:
        quality = ("only marginally better than quoting the long-run average", "bad")
    elif bss < 0.35:
        quality = ("moderately skilful", "ok")
    elif bss < 0.5:
        quality = ("genuinely useful", "good")
    else:
        quality = ("strongly skilful", "good")

    if nsig == 0:
        honesty = "and its stated probabilities are honest throughout"
    elif nsig == 1:
        honesty = "and honest except at one point on the scale"
    else:
        honesty = f"though {nsig} points on its probability scale are off by more than chance explains"
    return f"{quality[0]}, {honesty}", quality[1]


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
Skill score {m['brier_skill_score']:.2f}, ranked {meta['rank']} of
{meta['n_cities']} &mdash; but see the rank <i>range</i>
({meta['rank_lo']:.0f}&ndash;{meta['rank_hi']:.0f}) before reading anything into
that position.</p>
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
        "answer": sec_city_answer(city, meta),
        "curve": sec_city_curve(city, meta),
        "season": sec_city_season(city, meta),
    }
    return meta


def all_payloads(k: dict) -> list[dict]:
    names = sorted(k["met"].city.unique())
    return [city_payload(n, k, deep=(n == DEFAULT_CITY)) for n in names]


def excluded_markers(k: dict) -> list[dict]:
    """Capitals that were probed and dropped, with the reason and a location.

    These are plotted on the globe too. The exclusions are part of the result -
    a city whose gauge sits on a mountain, or whose rain-day convention has no
    clean peak, says something about the data that an empty patch of map does
    not.
    """
    out = []
    cov = k.get("coverage") or {}
    included_names = set(k["met"].city)
    diag = k.get("diag")
    reasons = {}
    if diag is not None:
        for _, r in diag[~diag.included].iterrows():
            reasons[r.city] = r.why

    for name, v in (cov.get("excluded") or {}).items():
        if name in included_names:
            continue
        if v.get("lat") is None:
            continue
        out.append({"name": name, "slug": slugify(name), "lat": v["lat"],
                    "lon": v["lon"], "country": v.get("country", ""),
                    "why": v.get("why") or reasons.get(name, "no usable station")})
    # Cities that passed the coverage probe but failed once their records were
    # inspected - a different and more interesting kind of exclusion.
    for name, why in reasons.items():
        if name in included_names or any(o["name"] == name for o in out):
            continue
        v = (cov.get("included") or {}).get(name)
        if v:
            out.append({"name": name, "slug": slugify(name), "lat": v["lat"],
                        "lon": v["lon"], "country": v.get("country", ""),
                        "why": why})
    return sorted(out, key=lambda d: d["name"])
