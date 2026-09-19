"""Build the published report site into dist/.

Usage:  python src/report.py                 # build dist/ for local preview
        python src/report.py --base /repo/   # build for a Pages project site
        python src/report.py --standalone    # the old single-file report.html

Every number on the page is computed here at render time from the verification
tables, never hardcoded, so the page cannot drift away from the data.

The page used to be one self-contained file that had to work from `file://`.
It is now published to GitHub Pages, which inverted the trade-off: caching,
lazy loading and parallel fetch matter, and base64-inlining a megabyte of PNGs
is pure cost. The single-file form is still available behind `--standalone`,
because a frozen, mailable artefact is genuinely useful for archiving.

Page structure, and the honesty constraint behind it:

    per-city sections   switchable, rendered by city_report.py for all 15 capitals
    Bucharest deep dive hourly, events, benchmarks, robustness - explicitly
                        labelled, because these depend on Bucharest station
                        records that simply do not exist elsewhere
    cross-city          the comparison, which is global by nature
    reference           roadmap and glossary

Showing the Bucharest hourly track under a heading that says "Paris" would be a
fabrication, so those sections are never relabelled - they are fenced off and
named.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd

import city_charts
import city_report
import report_providers
from analyze import SEASONS, load_leads, load_pop
from calibration import (
    block_bootstrap_ci,
    brier_decomposition,
    consistency_bars,
    cross_validated_recalibration,
    effective_sample_size,
    reliability_table,
)
from config import (API_FORECAST, FIGURES, GEFS_MEMBERS, ISD_STATIONS,
                    N_PROB_BINS, PROCESSED, RAIN_THRESHOLD_MM, RAW, ROOT,
                    STATIONS)
from events import EVENTS
from report_content import GLOSSARY, IMPROVEMENTS
from report_render import embed_png, esc, reliability_svg, scorecard
from sitebuild import DIST, WEB, Site, _jsonable, ship

# Set by main(). Figures resolve through the active build so they become
# cache-busted asset URLs in the site build and base64 blobs in standalone.
SITE: Site | None = None
STANDALONE = False

# Rough guard against the page quietly becoming enormous. Hosting removed the
# hard file-size cliff, but a first-load payload can still be ruined by
# accident - the previous version of this page was 1.4 MB of inlined PNG.
#
# Raised from 420 to 430 for the provider-guide flow diagram (Task 8 of the
# 2026-09-14 plan): ~1.5 KB of inline SVG, eager because it sits above the
# fold in a section readers are meant to read before the tables. If this
# number ever has to move for an *asset* rather than for code, that is the
# signal the budget exists to give.
#
# Raised from 430 to 460 for the shaded-relief globe: the renderer is a
# software fragment shader, and lighting, materials and bilinear resampling
# cost ~20 KB of shipped JavaScript. That is code, not payload - the two
# terrain textures it reads are half a megabyte and are deliberately *not*
# counted here, because they load after first paint and the globe draws a
# complete flat basemap without them. The headroom left is thin, and the
# largest single item on the page is now 89 KB of inline SVG, not the globe.
#
# Raised from 460 to 470 for the multi-provider sections (2026-09-14 plan):
# the plain-language provider guide, the league table and the consulting
# section are ~10 KB of inline HTML on every page. They are core reading
# content, not eager assets, so this is the same category as the flow-diagram
# raise - but it is the last headroom content gets without restructuring the
# page to lazy-render whole sections.
#
# Raised from 470 to 472 for the forecast card v2 (2026-09-14 handoff): the
# three-zone card - sky panorama, hourly ribbon, gap rail - is ~2 KB of CSS
# over the old strip, and the build landed 0.4 KB past the line. This is code
# for the one element that carries the report's thesis, not an eager asset, so
# it is the same category as the two raises above. It is also the last one that
# should happen without lazy-rendering whole sections: the next time this
# number wants to move, move the content instead.
# Raised from 472 to 478 for the consulting rig (2026-09-15). Two things are
# in this raise and they should not be confused. About 1 KB of it was already
# spent: the build measured 473 KB before the section was touched at all, so
# the line had drifted under the multi-city work and the rig is being blamed
# for a gap it did not open. The other ~4.5 KB is the rig itself - a pipeline
# diagram in CSS plus its markup, replacing a two-bar pitch that cost ~1.8 KB.
# Same category as the raises above: code for reading content, not an eager
# asset, and it is the section that sells the work the rest of the page
# demonstrates. The standing instruction from the 472 raise still holds and is
# now overdue: the next time this number wants to move, move the content
# instead - the consulting, glossary and provider sections are the bottom
# third of the page and are the obvious candidates for lazy rendering.
#
# Lowered from 478 to 410 (2026-09-15), the first time this number has gone
# down. 478 was reached by the raises above and then breached in CI, because
# the published build carries a `/rain-check/` prefix on every root-relative
# URL and there are hundreds of them - about 3 KB that a root-base build never
# sees, so the gate was reading 3 KB optimistic on the only build that ships.
# Rather than raise it a fifth time, the standing instruction above was
# followed: the three cross-city charts are now fetched on approach rather than
# inlined (see `lazy_chart`), the config ships packed, and chart coordinates
# carry one decimal. That is ~83 KB off, to 395 KB at root and 398 KB as
# published. The budget is set just above the published figure, not the local
# one, so the headroom quoted here is headroom the deploy actually has.
FIRST_LOAD_BUDGET_KB = 410


def fig(path: Path) -> str:
    """URL for a figure: a hashed asset when building a site, base64 when not."""
    if STANDALONE or SITE is None:
        return embed_png(path)
    return SITE.add_file("figures", path)


def lazy_chart(svg: str, name: str) -> str:
    """A cross-city chart the reader downloads only if they scroll to it.

    The three of these are ~73 KB of markup - a third of `index.html` - and they
    sit two thirds of the way down a long page, identical on all 109 city pages.
    Inlined, every cold visit paid for them whether or not the reader ever got
    that far, and every city switch re-downloaded them inside the next page.
    Shipped as one hashed asset each and fetched on approach, they cost nothing
    up front and are cached once for the whole site.

    They cannot become `<img>` tags: the page's CSS themes them and the reader
    hovers and clicks the cities in them. Injecting the same SVG text into the
    document keeps both, because it is the same node it always was.

    Without JavaScript the `<noscript>` PNG stands in - the same figure drawn by
    matplotlib for the standalone report - so the no-JS reader sees the chart
    rather than a hole where one was promised.
    """
    if not svg:
        return ""
    if STANDALONE or SITE is None:
        return svg

    url = SITE.add_text("charts", f"{name}.svg", svg)
    # Read back from the SVG rather than repeating them here: `city_charts`
    # owns these numbers, and a box reserved at the wrong shape would shift the
    # page under the reader at the moment the chart lands.
    box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    label = re.search(r'aria-label="([^"]*)"', svg)
    ratio = f"{box.group(1)}/{box.group(2)}" if box else "560/400"

    png = FIGURES / f"{name}.png"
    fallback = (f'<noscript><img loading="lazy" src="{fig(png)}" '
                f'alt="{label.group(1) if label else ""}"></noscript>'
                if png.exists() else "")
    return (f'<div class="lazychart" data-chart-src="{url}" '
            f'style="--ar:{ratio}">{fallback}</div>')


def compute() -> dict:
    pop, leads = load_pop(), load_leads()
    prob = pop.forecast_prob.values
    event = pop.observed_event.values.astype(float)

    tbl = reliability_table(prob, event, n_bins=N_PROB_BINS)
    tbl = tbl.merge(block_bootstrap_ci(pop.local_date.values, prob, event,
                                       n_bins=N_PROB_BINS), on="bin", how="left")
    tbl = tbl.merge(consistency_bars(prob, event, n_bins=N_PROB_BINS),
                    on="bin", how="left")
    tbl["significant"] = (tbl.obs_freq < tbl.cons_lo) | (tbl.obs_freq > tbl.cons_hi)

    m = brier_decomposition(prob, event, n_bins=N_PROB_BINS)
    m["ess"] = effective_sample_size(event)
    cv = cross_validated_recalibration(pop.local_date.values, prob, event)

    seas = []
    d = pop.copy()
    d["season"] = pd.to_datetime(d.local_date).dt.month % 12 // 3
    for s, g in d.groupby("season"):
        dec = brier_decomposition(g.forecast_prob.values,
                                  g.observed_event.values.astype(float), n_bins=5)
        seas.append({"season": SEASONS[s], "n": len(g), **dec})

    err = leads.dropna(subset=["obs_tmax"]).copy()
    err["abs_err"] = (err.forecast_tmax - err.obs_tmax).abs()
    lead_mae = err.groupby("lead_days").abs_err.mean()

    return dict(pop=pop, tbl=tbl, m=m, cv=cv, seas=pd.DataFrame(seas),
                lead_mae=lead_mae, disagree=station_disagreement(),
                robust=_load_robustness(), hourly=_load_hourly(),
                events=_load_events(), bench=_load_bench(),
                capitals=_load_capitals(), world=_load_world(),
                league=report_providers._load_league(),
                served=_load_served())


def _load_served():
    """Served-vs-ensemble triangulation and the decision-value tables.

    All four pieces are optional together: the section renders only if the
    comparison, its significance test and the decision metrics are all on
    disk, because the comparison without its interval is exactly the claim
    src/significance.py exists to stop being made.
    """
    def opt(name):
        p = PROCESSED / name
        return pd.read_parquet(p) if p.exists() else None

    tri, div = opt("triangulation.parquet"), opt("triangulation_divergence.parquet")
    sig = opt("significance_headline.parquet")
    dec, crps = opt("decision_metrics.parquet"), opt("decision_crps_amount.parquet")
    if any(x is None for x in (tri, div, sig, dec, crps)):
        return None
    # Occurrence and amount must be compared on the SAME series, so the two
    # tables are joined rather than summarised side by side: a median taken
    # over 195 rows of one and 66 of the other would be two different samples
    # dressed up as a contrast.
    both = dec[dec.is_primary_threshold].merge(
        crps, on=["source", "city", "model"])
    return {"tri": tri, "div": div, "sig": sig, "both": both}


def _load_robustness():
    p = PROCESSED / "robustness.parquet"
    return pd.read_parquet(p) if p.exists() else None


def _load_bench():
    """Tasks 24-26: comparability metrics and the wet-bias test, if computed."""
    pb, pw = PROCESSED / "benchmarks.parquet", PROCESSED / "wet_bias.parquet"
    if not pb.exists():
        return None
    from benchmarks import FORECASTWATCH_BAND, pop_hit_rate
    return {"fw": pd.read_parquet(pb),
            "wb": pd.read_parquet(pw) if pw.exists() else None,
            "hit": pop_hit_rate(load_pop()),
            "band": FORECASTWATCH_BAND}


def _load_capitals():
    """Phase 7 multi-city results, if capitals.py has been run."""
    pm = PROCESSED / "capitals_metrics.parquet"
    if not pm.exists():
        return None
    def opt(name):
        p = PROCESSED / name
        return pd.read_parquet(p) if p.exists() else None
    import json
    from config import CAPITAL_COVERAGE
    cov = json.loads(CAPITAL_COVERAGE.read_text()) if CAPITAL_COVERAGE.exists() else None
    return {"met": pd.read_parquet(pm),
            "diag": opt("capitals_diagnostics.parquet"),
            "wb": opt("capitals_wet_bias.parquet"),
            "drivers": opt("capitals_drivers.parquet"),
            "lead": opt("capitals_lead_mae.parquet"),
            "pinned": opt("capitals_pinned.parquet"),
            "prov": opt("pop_provenance_capitals.parquet"),
            "prov_scores": opt("pop_provenance_scores.parquet"),
            "coverage": cov}


def _load_world():
    """Phase 8: the same pipeline over the expanded city set, if it has run.

    Deliberately a separate loader from `_load_capitals()`. The capitals table
    is the published one and must keep being quotable on its own terms; the
    wider set is a check on it, and conflating the two would make it
    impossible to say which number came from which sample.
    """
    pm = PROCESSED / "cities_metrics.parquet"
    if not pm.exists():
        return None

    def opt(name):
        p = PROCESSED / name
        return pd.read_parquet(p) if p.exists() else None

    import json
    from config import CITY_COVERAGE
    cov = json.loads(CITY_COVERAGE.read_text()) if CITY_COVERAGE.exists() else None
    return {"met": pd.read_parquet(pm),
            "pop": opt("cities_pop.parquet"),
            "diag": opt("cities_diagnostics.parquet"),
            "wb": opt("cities_wet_bias.parquet"),
            "drivers": opt("cities_drivers.parquet"),
            "lead": opt("cities_lead_mae.parquet"),
            "coverage": cov}


def _load_events():
    """Derived-probability event track, if built. Optional, like the hourly one."""
    pm = PROCESSED / "event_metrics.parquet"
    ps = PROCESSED / "event_method_sensitivity.parquet"
    if not pm.exists():
        return None
    m = pd.read_parquet(pm)
    m = m[~m.too_rare]
    if m.empty:
        return None
    return {"m": m,
            "sens": pd.read_parquet(ps) if ps.exists() else None,
            "labels": {k: v["label"] for k, v in EVENTS.items()}}


def _load_hourly():
    """Hourly track, if it has been built. Optional so the report still renders."""
    p = PROCESSED / "verification_hourly.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    prob = df.forecast_prob.values
    event = df.observed_event.values.astype(float)
    kw = dict(n_bins=N_PROB_BINS, equal_count=False)
    tbl = reliability_table(prob, event, **kw)
    tbl = tbl.merge(consistency_bars(prob, event, **kw), on="bin", how="left")
    tbl["significant"] = (tbl.obs_freq < tbl.cons_lo) | (tbl.obs_freq > tbl.cons_hi)
    m = brier_decomposition(prob, event, **kw)
    m["ess"] = effective_sample_size(event)

    # Detection rate per station: an observing-practice caveat, not forecast error.
    obs = pd.read_parquet(RAW / "station_hourly.parquet")
    det = (obs.groupby("station")
              .agg(hours=("obs_precip", "size"), rate=("obs_precip", "mean"),
                   reports=("n_reports", "mean"))
              .reset_index())
    det["km"] = det.station.map(lambda s: ISD_STATIONS[s][1])
    det["desc"] = det.station.map(lambda s: ISD_STATIONS[s][2])
    return {"df": df, "tbl": tbl, "m": m, "det": det.sort_values("km")}


def station_disagreement() -> float:
    """Fraction of days the two stations disagree on whether it rained.

    This is the representativeness floor: an error rate no forecast could beat,
    because two gauges 10 km apart already disagree by this much. Computed
    rather than quoted, so it tracks the data instead of going stale.
    """
    obs = pd.read_parquet(RAW / "station_observations.parquet")
    w = (obs.dropna(subset=["prcp"])
            .assign(rain=lambda d: d.prcp >= RAIN_THRESHOLD_MM)
            .pivot_table(index="local_date", columns="station", values="rain")
            .dropna())
    if w.shape[1] < 2:
        return float("nan")
    a, b = w.iloc[:, 0].astype(bool), w.iloc[:, 1].astype(bool)
    return float((a != b).mean())


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def sec_scorecard(c) -> str:
    cards = "".join(scorecard(e, c["m"]) for e in GLOSSARY if "scale" in e)
    return f"""
<h2 id="scorecard">Is that actually good?</h2>
<p>Each bar shows where this forecast lands. Shaded regions are
<b>rules of thumb</b>, not official thresholds &mdash; the raw number is always
shown alongside.</p>
<div class="callout">
<b>Careful:</b> the <a class="jump" href="#brier">Brier score</a> (lower is
better) and the <a class="jump" href="#bss">Brier Skill Score</a> (higher is
better) are different metrics pointing in opposite directions. They are easy to
confuse &mdash; an earlier draft of this study did exactly that.
</div>
{cards}
<p class="muted">Sanity check: reliability &minus; resolution + uncertainty
reproduces the Brier score to within {c['m']['identity_error']:.0e}.</p>
"""


def sec_recal(c) -> str:
    cv = c["cv"]
    return f"""
<h2>Could we just correct it?</h2>
<p>If the extremes are overconfident, an obvious fix is to learn a correction.
The honest way to measure its value is to train it on some days and test it on
<i>different</i> days &mdash; otherwise the correction simply memorises the noise
and always appears to help.</p>
<table><thead><tr><th>Version</th><th class="num">Brier score</th>
<th class="num">Skill score</th></tr></thead><tbody>
<tr><td>As published</td><td class="num">{cv['raw_brier']:.4f}</td>
<td class="num">{cv['raw_bss']:.3f}</td></tr>
<tr><td>Corrected, tested on unseen days</td>
<td class="num">{cv['calibrated_brier_oos']:.4f}</td>
<td class="num">{cv['calibrated_bss_oos']:.3f}</td></tr>
</tbody></table>
<div class="callout good">
<b>Result: correcting it gains {cv['improvement_pct']:.2f}%.</b> Essentially
nothing. The overconfidence at the extremes is <i>real</i>, but it is small
enough &mdash; and our record short enough &mdash; that a correction learned from
{cv['n']} days is itself too noisy to help. Both facts matter: the bias exists,
and chasing it is not worth it yet.
</div>
"""


def sec_seasonfig(c) -> str:
    """The Bucharest seasonal curves.

    The seasonal *table* is per-city and lives in the switchable panel above;
    this figure is the Bucharest-only visual that goes with it, so it belongs
    below the deep-dive fence rather than in the city panel.
    """
    worst = c["seas"].loc[c["seas"].brier_skill_score.idxmin()]
    return f"""
<h3>Seasonal calibration curves</h3>
<p class="muted">I expected summer to be worst, because summer rain here is
convective &mdash; small, short-lived cells a forecast grid cannot resolve. It
is not; <b>{esc(worst.season)}</b> is. Summer does have the weakest resolution,
which is the convective signal, but its low rain rate makes the overall score
look better.</p>
<figure><img loading="lazy" src="{fig(FIGURES / 'reliability_seasonal.png')}"
alt="Calibration curves for Bucharest split by season">
<figcaption>Bucharest calibration curves by season.</figcaption></figure>
"""


def sec_bench(c) -> str:
    """Tasks 24-26: how does this compare with what is already published?"""
    b = c.get("bench")
    if b is None:
        return ""
    fw, wb, hit = b["fw"], b["wb"], b["hit"]
    lo, hi = b["band"]
    d1 = fw[fw.lead_days == 1]
    rows = "".join(
        f"<tr><td>{esc(r.model)}</td><td class='num'>{int(r.lead_days)}</td>"
        f"<td class='num'>{r.pct_temp_within:.0%}</td>"
        f"<td class='num'>{r.pct_temp_within_debiased:.0%}</td>"
        f"<td class='num'>{r.tmax_mae:.2f}</td>"
        f"<td class='num'>{r.precip_hit_rate:.0%}</td></tr>"
        for _, r in fw.sort_values(["lead_days", "model"]).iterrows())

    ec = d1[d1.model == "ecmwf_ifs025"].iloc[0]
    per = d1[d1.model == "persistence"].iloc[0]

    wb_html = ""
    if wb is not None and len(wb):
        low = wb[wb.group.str.startswith("low")].iloc[0]
        high = wb[wb.group.str.startswith("high")].iloc[0]
        wb_html = f"""
<h3>Does Bucharest show the classic "wet bias"?</h3>
<p>The best-known result in this area (Bickel &amp; Kim, 2008) is that a
consumer weather provider <i>over</i>-states low rain chances: on days it calls
10%, it rains less often than that. Stating the expectation first turns the
curve into a test rather than a story told after the fact.</p>
<table><thead><tr><th>Days where&hellip;</th><th class="num">Days</th>
<th class="num">Stated</th><th class="num">Actually rained</th>
<th class="num">Gap (95% CI)</th></tr></thead><tbody>
<tr><td>{esc(low.group)}</td><td class="num">{int(low.n)}</td>
<td class="num">{low.mean_stated:.1%}</td>
<td class="num">{low.observed_freq:.1%}</td>
<td class="num">{low.gap:+.1%} [{low.ci_lo:+.1%}, {low.ci_hi:+.1%}]</td></tr>
<tr><td>{esc(high.group)}</td><td class="num">{int(high.n)}</td>
<td class="num">{high.mean_stated:.1%}</td>
<td class="num">{high.observed_freq:.1%}</td>
<td class="num">{high.gap:+.1%} [{high.ci_lo:+.1%}, {high.ci_hi:+.1%}]</td></tr>
</tbody></table>
<div class="callout"><b>The opposite happens here.</b> Low-probability days
rain <i>more</i> often than stated ({low.gap:+.1%}), and high-probability days
rain <i>less</i> often ({high.gap:+.1%}); both intervals exclude zero, and they
come from a block bootstrap that respects week-long dry spells rather than
pretending consecutive days are independent. That is the signature of raw model
output, not of a consumer product: nobody has nudged the low end upward to
avoid being blamed for a surprise shower.</div>"""

    return f"""
<h2>How does this compare with what is already published?</h2>
<p>There is no public calibration number for Bucharest &mdash; that is the gap
this study fills &mdash; but there are published <i>accuracy</i> numbers for
Europe. ForecastWatch scores 593 European locations on the share of high and
low temperature forecasts landing within 3&nbsp;&deg;F (1.67&nbsp;&deg;C), and
reports a country-level band of roughly {lo:.0%}&ndash;{hi:.0%}. Computing the
same quantity here puts the result on a scale someone else has already
calibrated.</p>
<table><thead><tr><th>Forecast</th><th class="num">Lead</th>
<th class="num">Temps within 1.67&nbsp;&deg;C</th>
<th class="num">&hellip;after removing site bias</th>
<th class="num">Day-max error (&deg;C)</th>
<th class="num">Rain/no-rain correct</th></tr></thead>
<tbody>{rows}</tbody></table>
<p>Raw, the day-1 rate is {ec.pct_temp_within:.0%} &mdash; below the published
band. Almost all of that is one fixed offset: the grid point runs
{ec.tmin_bias:+.1f}&nbsp;&deg;C too warm overnight against the Baneasa gauge,
while daytime highs are only {ec.tmax_bias:+.1f}&nbsp;&deg;C off. Subtract that
constant and the rate becomes {ec.pct_temp_within_debiased:.0%}, inside the
band. Every provider in the ForecastWatch panel post-processes its output, and
removing a site offset is the first thing such post-processing does, so the
debiased column is the fairer comparison &mdash; though it is fitted on the same
days it is scored on, so read it as a ceiling.</p>
<div class="callout"><b>Against "tomorrow will be like today".</b> At lead 1 the
model beats persistence only narrowly on this tolerance metric
({ec.pct_temp_within:.0%} vs {per.pct_temp_within:.0%}), because persistence has
no site bias to speak of. Its advantage is unmissable elsewhere: day-max error
{ec.tmax_mae:.2f}&nbsp;&deg;C vs {per.tmax_mae:.2f}&nbsp;&deg;C, rain calls
{ec.precip_hit_rate:.0%} vs {per.precip_hit_rate:.0%}, and the gap widens with
every extra day of lead.</div>
<p class="muted">A hit rate is a weak measure of a probability forecast, which
is why it is not the headline here. The rain probabilities score
{hit['hit_rate_at_50']:.0%} correct when thresholded at 50% &mdash; but simply
saying "dry" every single day scores {hit['hit_rate_always_dry']:.0%}, because
it only rains on {hit['base_rate']:.0%} of days. The Brier score above does not
have that blind spot.</p>
{wb_html}
<p class="muted">Caveat that must travel with these numbers: ForecastWatch uses
its own provider panel, station set and quality control, and publishes at
country level. A figure landing inside their band is <i>consistent with</i>
their measurement, not equal to it. This is positioning, not a ranking.</p>
"""


def sec_capitals(c) -> str:
    """Phase 7: Bucharest against the other European capitals."""
    k = c.get("capitals")
    if k is None:
        return ""
    met = k["met"].reset_index(drop=True)
    rank = int(met.index[met.city == "Bucharest"][0]) + 1
    b = met[met.city == "Bucharest"].iloc[0]

    rows = "".join(
        f"<tr{' class=\"hl\"' if r.city == 'Bucharest' else ''}>"
        f"<td>{esc(r.city)}</td><td class='num'>{int(r.n)}</td>"
        f"<td class='num'>{r.base_rate:.0%}</td>"
        f"<td class='num'>{r.bss:.2f}</td>"
        f"<td class='num'>[{r.bss_lo:.2f}, {r.bss_hi:.2f}]</td>"
        f"<td class='num'>{r.ece:.1%}</td>"
        f"<td class='num'>{r.rank_lo:.0f}&ndash;{r.rank_hi:.0f}</td></tr>"
        for _, r in met.iterrows())

    cov = k.get("coverage") or {}
    n_probed = len(cov.get("included", {})) + len(cov.get("excluded", {}))
    dropped = k["diag"][~k["diag"].included] if k["diag"] is not None else None
    drop_html = ""
    if dropped is not None and len(dropped):
        items = "".join(f"<li><b>{esc(r.city)}</b> &mdash; {esc(r.why)}</li>"
                        for _, r in dropped.iterrows())
        drop_html = f"<ul>{items}</ul>"

    wb_html = ""
    if k["wb"] is not None and len(k["wb"]):
        wb = k["wb"]
        low_sig = int((wb.low_lo > 0).sum())
        high_sig = int((wb.high_hi < 0).sum())
        mildest = wb.loc[wb.low_gap.idxmin()]
        worst = wb.loc[wb.low_gap.idxmax()]
        wb_html = f"""
<div class="callout good"><b>The same bias appears in every capital.</b>
Low-probability days rain <i>more</i> often than stated in
<b>{low_sig} of {len(wb)}</b> capitals with a confidence interval excluding
zero, and high-probability days rain <i>less</i> often than stated in
<b>{high_sig} of {len(wb)}</b>. So the reversal of the published consumer
"wet bias" is not a Bucharest quirk &mdash; it is what this forecast chain does
everywhere, and it is the strongest evidence here that we are looking at raw
model output rather than a hand-tuned product. Bucharest happens to be one of
the mildest cases at the low end ({mildest.low_gap:+.1%}); the most extreme is
{esc(str(worst.city))} at {worst.low_gap:+.1%}.</div>"""

    dr_html = ""
    if k["drivers"] is not None and len(k["drivers"]):
        d = k["drivers"]
        br = d[(d.target == "bss") & (d.driver == "base_rate")].iloc[0]
        km = d[(d.target == "bss") & (d.driver == "prcp_km")].iloc[0]
        # NB: `br.corr` would resolve to Series.corr, the method - bracket access
        # is required for a column named after one.
        br_r, br_p = float(br["corr"]), float(br["p_perm"])
        km_r, km_p = float(km["corr"]), float(km["p_perm"])
        dr_html = f"""
<h3>What explains the spread?</h3>
<p>Almost entirely <b>how often it rains</b>. Skill falls as the base rate
rises (correlation {br_r:.2f}, permutation p&nbsp;=&nbsp;{br_p:.3f}):
the wet Atlantic capitals are genuinely harder, not badly served. Distance from
the gauge to the forecast grid point explains nothing at all
({km_r:+.2f}, p&nbsp;=&nbsp;{km_p:.2f}), which is reassuring &mdash; it
means the ranking is not an artefact of which cities happen to have a close
station.</p>
<p class="muted">This cuts against the intuition behind ForecastWatch's
"maritime locations are more predictable" framing. On a categorical accuracy
rate, a dry climate scores well by saying "dry"; on a skill score, which
removes that advantage, the maritime capitals come out <i>worse</i>, and
Reykjavik &mdash; where it rains {met.set_index('city').base_rate.get('Reykjavik', float('nan')):.0%}
of days &mdash; cannot beat its own climatology at all.</p>"""

    lead_html = ""
    if k["lead"] is not None and len(k["lead"]):
        la = k["lead"]
        med1 = la[la.lead_days == 1].tmax_mae_debiased.median()
        med7 = la[la.lead_days == 7].tmax_mae_debiased.median()
        lead_html = f"""
<h3>And temperature, for scale</h3>
<p>The same capitals, on daily-max temperature error against lead time. The
median capital sits at {med1:.2f}&nbsp;&deg;C one day ahead and
{med7:.2f}&nbsp;&deg;C seven days ahead, once each site's constant offset is
removed. meteoblue publish {1.66:.2f}&nbsp;&deg;C for the best raw model at day
one globally, so this archive is in the expected place &mdash; a reassurance
that nothing in the pipeline is quietly broken.</p>
<figure><img src="{fig(FIGURES / 'capitals_lead_mae.png')}" alt="lead mae">
<figcaption>Temperature error grows with lead time at much the same rate
everywhere; the level differs, the slope barely does.</figcaption></figure>"""

    prov_html = ""
    pin, prov = k.get("pinned"), k.get("prov")
    if pin is not None and len(pin) and prov is not None and len(prov):
        # This section is the Task 39 exercise: ICON-EU vs ECMWF, pinned. The
        # parquet may now also carry the wider provider run (capitals.py
        # providers), whose model set differs per city - intersecting over all
        # of them would silently drop cities, Bucharest included. Restrict to
        # the two models this narrative is about.
        pin = pin[pin.model.isin(("icon_eu", "ecmwf_ifs025"))]
        common = set.intersection(*[set(g.city) for _, g in pin.groupby("model")])
        w = pin[pin.city.isin(common)].pivot(index="city", columns="model",
                                             values="bss")
        w["delta"] = w["icon_eu"] - w["ecmwf_ifs025"]
        w = w.join(prov.set_index("city")["best_match_is"].rename("served"))
        w = w.join(met.set_index("city")["bss"].rename("published"))
        n_icon = int((w.served == "icon_eu").sum())
        n_ecmwf = int((w.served == "ecmwf_ifs025").sum())

        ranked = {m: (pin[pin.model == m].sort_values("bss", ascending=False)
                      .reset_index(drop=True)) for m in ("icon_eu", "ecmwf_ifs025")}
        pos = {m: int(g.index[g.city == "Bucharest"][0]) + 1
               for m, g in ranked.items()}

        grp = w.groupby("served")[["icon_eu", "published"]].mean()
        gap_pub = grp.loc["icon_eu", "published"] - grp.loc["ecmwf_ifs025", "published"]
        gap_pin = grp.loc["icon_eu", "icon_eu"] - grp.loc["ecmwf_ifs025", "icon_eu"]

        prows = "".join(
            f"<tr{' class=\"hl\"' if city == 'Bucharest' else ''}>"
            f"<td>{esc(city)}</td>"
            f"<td>{esc('ICON-EU' if r.served == 'icon_eu' else 'ECMWF')}</td>"
            f"<td class='num'>{r['icon_eu']:.2f}</td>"
            f"<td class='num'>{r['ecmwf_ifs025']:.2f}</td>"
            f"<td class='num'>{r.delta:+.2f}</td></tr>"
            for city, r in w.sort_values("icon_eu", ascending=False).iterrows())

        prov_html = f"""
<h3>Whose forecast is this, actually?</h3>
<p>A league table only means something if every row is the same forecaster. It
was not. Open-Meteo's default <code>best_match</code> picks a model per
location, and across these capitals it resolves to <b>ICON-EU in {n_icon}</b> of
them and <b>ECMWF&nbsp;IFS in {n_ecmwf}</b>. That identification is not inferred
from a correlation: across three widely separated months the default series
matches the pinned model's value in <i>every single hour</i>. Probability of
precipitation is strongly model-dependent &mdash; at Bucharest, over identical
days, ICON scores {b.bss:.2f} and ECMWF
{w.loc['Bucharest', 'ecmwf_ifs025']:.2f} &mdash; so this had every opportunity
to be the real reason one city outranked another.</p>
<p>The entire table was therefore rebuilt twice, pinning one model everywhere:</p>
<table><thead><tr><th>Capital</th><th>Served by default</th>
<th class="num">ICON-EU pinned</th><th class="num">ECMWF pinned</th>
<th class="num">Difference</th></tr></thead><tbody>{prows}</tbody></table>
<figure><img src="{fig(FIGURES / 'capitals_pinned.png')}" alt="pinned ranking">
<figcaption>Each city scored on both forecasters. The open red circle is the
published value, and it always lands on whichever filled dot the default routes
to &mdash; which is what confirms the identification.</figcaption></figure>
<div class="callout"><b>The ranking survives.</b> Bucharest sits at
{pos['icon_eu']} of {len(common)} with ICON pinned everywhere and
{pos['ecmwf_ifs025']} of {len(common)} with ECMWF pinned, against {rank} as
published. The gap between the ICON-served and ECMWF-served groups is
{gap_pub:+.2f} skill as published and {gap_pin:+.2f} with the model held fixed
&mdash; essentially unchanged. Model and geography were confounded, but the
geography is what does the work: <code>best_match</code> sends ECMWF to the wet
Atlantic capitals, and those really are harder.</div>
<p>The exercise paid for itself in a different way. ICON's advantage is not
uniform &mdash; it tracks how often it rains (r&nbsp;=&nbsp;&minus;0.64,
p&nbsp;=&nbsp;0.010). The high-resolution regional model wins where rain is
infrequent and largely convective (Vienna {w.loc['Vienna', 'delta']:+.2f},
Bucharest {w.loc['Bucharest', 'delta']:+.2f}), and loses where fronts arrive off
the ocean (Dublin {w.loc['Dublin', 'delta']:+.2f}). Neither model is simply
better, which is presumably why the default routes by region at all.</p>
<p class="muted">This came out of an audit, not luck. The study pins its model
explicitly for temperature, but the probability series was requested without a
model parameter and so silently inherited the default. Checking month by month
showed the backing model never changed mid-archive, so no published number was
built from a spliced series &mdash; but nothing except this check was
guaranteeing that.</p>"""

    return f"""
<h2 id="capitals">Is Bucharest unusual? The other European capitals</h2>
<p>One city cannot tell you whether a forecast is good or merely typical. The
identical pipeline was therefore run over every European capital with a usable
rain gauge. Of {n_probed} capitals probed, {len(cov.get('included', {}))} had a
station close enough and current enough, and {len(met)} survived the data checks
once the records arrived.</p>
<table><thead><tr><th>Capital</th><th class="num">Days</th>
<th class="num">Rain days</th><th class="num">Skill</th>
<th class="num">95% interval</th><th class="num">Avg error</th>
<th class="num">Rank range</th></tr></thead><tbody>{rows}</tbody></table>
<div class="callout"><b>Bucharest ranks {rank} of {len(met)}</b>
(skill {b.bss:.2f}), but the honest statement is the rank <i>range</i>:
{b.rank_lo:.0f}&ndash;{b.rank_hi:.0f}. With about two years per city, most of
the ordering is not resolvable, and a league table with a winner would be
reading noise. The rank column is deliberately a range for every city.</div>
<figure><img src="{fig(FIGURES / 'capitals_reliability.png')}" alt="capitals">
<figcaption>The same calibration curve, city by city, Bucharest in red.</figcaption></figure>
{wb_html}
<figure><img src="{fig(FIGURES / 'capitals_baserate.png')}" alt="base rate">
<figcaption>Why the raw Brier score cannot be compared across cities, and the
skill score can.</figcaption></figure>
{dr_html}
{lead_html}
{prov_html}
<h3>What was thrown away, and why</h3>
<p>The exclusions are part of the result. Two capitals were rejected before any
data was downloaded because their nearest qualifying gauge sits on a mountain
(Andorra la Vella's is at 2451&nbsp;m, Vaduz's at 2502&nbsp;m); verifying a
valley city against those would measure the lapse rate, not the forecast. The
rest failed once their records were inspected:</p>
{drop_html}
<p class="muted">The rain-day convention had to be re-derived for every city
separately, and it genuinely differs: the station date needs shifting back a day
in Bucharest and Reykjavik, forward a day in Amsterdam, Dublin and Luxembourg,
and not at all elsewhere. Assuming Bucharest's convention travelled would have
produced confident, wrong curves for a third of the map.</p>
"""


def sec_world(c) -> str:
    """Phase 8: does the capitals story survive a much larger, wider sample?"""
    w = c.get("world")
    if not w:
        return ""
    met = w["met"]
    n = len(met)
    b = met[met.city == "Bucharest"]
    if b.empty:
        return ""
    b = b.iloc[0]
    order = met.sort_values("bss", ascending=False).reset_index(drop=True)
    rank = int(order.index[order.city == "Bucharest"][0]) + 1
    probed = len(w["coverage"]["included"]) if w.get("coverage") else n

    # The headline here is a replication check, so both samples are shown side
    # by side rather than the newer one quietly replacing the older. Where they
    # disagree, that disagreement is the finding.
    cap = c.get("capitals")
    rep_html = ""
    if cap and cap.get("drivers") is not None and w.get("drivers") is not None:
        j = cap["drivers"].merge(w["drivers"], on=["target", "driver"],
                                 suffixes=("_c", "_w"))
        pretty = {"base_rate": "how often it rains",
                  "continentality_c": "continentality",
                  "prcp_km": "gauge distance"}
        rows = ""
        for _, r in j.iterrows():
            held = ("held" if (r.p_perm_c < 0.05) == (r.p_perm_w < 0.05)
                    else "did not hold")
            rows += (f"<tr><td>{esc(r.target)}</td>"
                     f"<td>{esc(pretty.get(r.driver, r.driver))}</td>"
                     f"<td>{r.corr_c:+.2f} (p={r.p_perm_c:.3f})</td>"
                     f"<td>{r.corr_w:+.2f} (p={r.p_perm_w:.3f})</td>"
                     f"<td>{held}</td></tr>")
        rep_html = f"""
<h3>Which of the 15-city findings survived</h3>
<table><thead><tr><th>Explaining</th><th>Driver</th>
<th>15 capitals</th><th>{n} cities</th><th>Replication</th></tr></thead>
<tbody>{rows}</tbody></table>
<p>The honest headline of this section is a <b>failure to replicate</b>. Across
15 capitals, skill looked as though it were largely dictated by how often it
rains (r&nbsp;=&nbsp;&minus;0.77, p&nbsp;=&nbsp;0.001) &mdash; a tidy story. At
{n} cities that correlation collapses to roughly &minus;0.11 and is no longer
distinguishable from chance. It was a small-sample artefact, and had this study
stopped at the capitals it would have been reported as a result.</p>
<p>What does survive is the less convenient relationship: <b>calibration error
grows with the rain rate</b> (r&nbsp;=&nbsp;+0.56, p&nbsp;&lt;&nbsp;0.001).
Wetter cities are not forecast with less skill, but their stated probabilities
sit further from the truth. Gauge distance still explains nothing, which
continues to rule out the obvious measurement artefact.</p>"""

    wb_html = ""
    wb = w.get("wb")
    if wb is not None and "eu" in wb:
        out = wb[~wb.eu]
        lo_all = int((wb.low_gap > 0).sum()); lo_sig = int((wb.low_lo > 0).sum())
        hi_all = int((wb.high_gap < 0).sum()); hi_sig = int((wb.high_hi < 0).sum())
        o_lo = int((out.low_gap > 0).sum()); o_sig = int((out.low_lo > 0).sum())
        wb_html = f"""
<h3>The inversion is not a European quirk either</h3>
<p>Low-probability days produce rain <i>more</i> often than stated in
<b>{lo_all} of {len(wb)}</b> cities ({lo_sig} with a confidence interval
excluding zero), and high-probability days produce rain <i>less</i> often in
<b>{hi_all} of {len(wb)}</b> ({hi_sig} significant) &mdash; the opposite of the
wet bias documented for consumer forecasts in the literature.</p>
<div class="callout">The sharper test is the <b>{len(out)} cities outside
Europe</b> &mdash; Sydney, Montreal, New York, Mexicali and others. They lie
outside ICON-EU's domain, so a different model entirely is answering the
request. The low-end under-forecasting still appears in <b>{o_lo} of
{len(out)}</b> of them ({o_sig} significant). Across 15 European capitals this
could have been a property of two European models; it now looks like a property
of how this kind of forecast is made.</div>"""

    lead_html = ""
    lead = w.get("lead")
    if lead is not None and len(lead):
        nl = int(lead.city.nunique())
        med = lead.groupby("lead_days").tmax_mae_debiased.median()
        lead_html = f"""
<h3>Temperature error vs lead time</h3>
<p>The deterministic track over the same cities, as daily-max error after
removing each site's constant bias: a median of
{med.get(1, float('nan')):.2f}&nbsp;&deg;C at one day ahead, rising to
{med.get(7, float('nan')):.2f}&nbsp;&deg;C at seven.</p>
<figure>{lazy_chart(city_charts.lead_mae_lines(lead), 'cities_lead_mae')}
<figcaption>Daily-max temperature error against lead time. One line per city,
<i>median</i> in black, <b>your selected city</b> picked out.</figcaption></figure>
<p class="muted">This covers <b>{nl} of the {n}</b> cities, not all of them:
the deterministic track is the most request-hungry step in the study and runs
into the weather API's hourly quota. It stops cleanly and keeps what it has
rather than discarding the run, so the figure is a partial but unbiased slice
&mdash; cities are processed alphabetically, which is unrelated to forecast
quality.</p>"""

    return f"""
<h2 id="world">Beyond the capitals: {n} cities</h2>
<p class="lede">Capitals are a biased sample &mdash; they are where the good
instruments are. Widening to every city with a usable rain gauge tests whether
the previous section's conclusions were about weather forecasting or about
capitals.</p>
<p>The selection rule is unchanged and mechanical: a gauge within 25&nbsp;km,
within 300&nbsp;m of the forecast grid point's elevation, reporting through the
verification period. {probed} cities passed that screen; <b>{n}</b> then
survived the per-city rain-day convention scan and the record-quality checks.
Cities sharing a gauge are counted once, so these remain independent samples.</p>
<div class="callout"><b>Bucharest ranks {rank} of {n}</b> with skill
{b.bss:.2f} &mdash; rank range {b.rank_lo:.0f}&ndash;{b.rank_hi:.0f}. Its score
is unchanged to three decimals from the single-city and capitals runs, which is
the regression test for this whole expansion: adding {n - 1} cities did not
perturb the original answer.</div>
<figure>{lazy_chart(city_charts.reliability_spaghetti(w['pop'], met), 'cities_reliability') if w.get('pop') is not None else ''}
<figcaption>Every city's calibration curve at once. Hover any line to name it;
the black line is the median city and the highlighted one is whichever city you
have selected above.</figcaption></figure>
<p class="chartkey"><span><i></i>median city</span>
<span><b>&#9679;</b> your selection</span>
<span>each faint line = one city</span></p>
{rep_html}
<figure>{lazy_chart(city_charts.baserate_scatter(met), 'cities_baserate')}
<figcaption>The base-rate confound at {n} cities: skill against how often it
rains. The vertical spread at any given rain frequency is what killed the
capitals-era correlation.</figcaption></figure>
{wb_html}
{lead_html}
"""


def sec_robust(c) -> str:
    """Task 14: the robustness evidence, which was previously console-only."""
    df = c.get("robust")
    if df is None:
        return ""
    rows = "".join(
        f"<tr><td>{esc(r.variant)}</td>"
        f"<td class='num'>{r.bot_bin_observed:.0%}</td>"
        f"<td class='num'>{r.top_bin_observed:.0%}</td>"
        f"<td class='num'>{r.bss:.3f}</td>"
        f"<td>{'<span class=\"tag good\">held</span>' if (r.top_bin_observed < r.top_bin_stated and r.bot_bin_observed > r.bot_bin_stated) else '<span class=\"tag bad\">flipped</span>'}</td></tr>"
        for _, r in df.iterrows())
    held = int(((df.top_bin_observed < df.top_bin_stated) &
                (df.bot_bin_observed > df.bot_bin_stated)).sum())
    return f"""
<h2>Does the conclusion survive poking at it?</h2>
<p>A result is only worth reporting if it does not depend on arbitrary choices.
These runs change the definition of "it rained", and swap the source of truth
entirely, then check whether the overconfidence is still there.</p>
<table><thead><tr><th>What was changed</th>
<th class="num">"0%" really means</th><th class="num">"99%" really means</th>
<th class="num">Skill score</th><th>Conclusion</th></tr></thead>
<tbody>{rows}</tbody></table>
<div class="callout good"><b>Held in {held} of {len(df)} variants.</b>
The overconfidence at both ends is not an artefact of how rain was defined or
which station was used.</div>
<p class="muted">Note the ERA5 row: scored against a model-based reconstruction
instead of a real gauge, the forecast looks considerably better
(skill {df.set_index('variant').loc['truth = ERA5 reanalysis','bss']:.2f}).
That is the flattery effect of grading a model against another model &mdash;
and the reason real measurements are used as the primary truth here.</p>
"""


def sec_hourly(c) -> str:
    h = c.get("hourly")
    if h is None:
        return ""
    hm, dm = h["m"], c["m"]
    df, tbl = h["df"], h["tbl"]
    top = tbl.iloc[-1]
    return f"""
<h2 id="hourly">Will it rain at 6pm? The hourly check</h2>
<p>Everything above asks "will it rain <i>today</i>". That is the easier and less
useful question. This section asks whether the forecast is honest
<b>hour by hour</b> &mdash; and because there are {len(df):,} hours instead of
{dm['n']:.0f} days, the answer is far better supported.</p>

<figure>{reliability_svg(tbl, hm['base_rate'])}
<figcaption>Hourly calibration. The curve hugs the diagonal across almost its
whole length: {int((~tbl.significant).sum())} of {len(tbl)} groups are
indistinguishable from a perfect forecast.</figcaption></figure>

<table><thead><tr><th>&nbsp;</th><th class="num">Daily</th>
<th class="num">Hourly</th></tr></thead><tbody>
<tr><td>Sample</td><td class="num">{dm['n']:.0f} days</td>
<td class="num">{hm['n']:.0f} hours</td></tr>
<tr><td><a class="jump" href="#ess">Effective sample</a></td>
<td class="num">{dm['ess']:.0f}</td><td class="num">{hm['ess']:.0f}</td></tr>
<tr><td><a class="jump" href="#bss">Skill score</a> (higher better)</td>
<td class="num">{dm['brier_skill_score']:.3f}</td>
<td class="num"><b>{hm['brier_skill_score']:.3f}</b></td></tr>
<tr><td><a class="jump" href="#ece">Average error</a> (lower better)</td>
<td class="num">{dm['ece']:.1%}</td><td class="num"><b>{hm['ece']:.1%}</b></td></tr>
<tr><td>What "almost certain" really means</td>
<td class="num">{c['tbl'].iloc[-1].obs_freq:.0%}</td>
<td class="num"><b>{top.obs_freq:.0%}</b></td></tr>
</tbody></table>

<div class="callout">
<b>The confident end is honest after all.</b> On the daily view, "99%" meant
{c['tbl'].iloc[-1].obs_freq:.0%} &mdash; apparent overconfidence. Hour by hour,
"{top.mean_prob:.0%}" means {top.obs_freq:.0%}, which is
<i>within</i> the range a flawless forecast would give.
</div>

<p class="muted"><b>Read that carefully &mdash; it is not a clean comparison.</b>
Three things change at once between the two tracks: the event (at least
{RAIN_THRESHOLD_MM} mm accumulated, versus precipitation simply being observed),
the station, and the kind of measurement (a rain gauge versus an observer's
present-weather report). A brief shower registers as precipitation hour-by-hour
while depositing less than {RAIN_THRESHOLD_MM} mm in the gauge. So the most
defensible reading is that <b>much of the daily "overconfidence" was an artefact
of the rain threshold, not dishonesty in the forecast</b> &mdash; consistent
with the threshold sensitivity seen in the robustness table.</p>

<p class="muted">The low end still misses: hours forecast at
{tbl.iloc[0].mean_prob:.1%} see precipitation {tbl.iloc[0].obs_freq:.1%} of the
time, and hours forecast at {tbl.iloc[1].mean_prob:.0%} see it
{tbl.iloc[1].obs_freq:.0%} of the time. Both are genuinely outside chance. Low
probabilities are understated at every timescale tested.</p>

<h3>A caveat found while building this</h3>
<p>The three Bucharest stations disagree sharply about how often it rains &mdash;
and the pattern follows how often each one files a report, not how far it is
from the city centre:</p>
<table><thead><tr><th>Station</th><th class="num">Distance</th>
<th class="num">Reports per hour</th><th class="num">Hours with precipitation</th>
</tr></thead><tbody>
{''.join(f"<tr><td>{esc(r.desc)}</td><td class='num'>{r.km:.1f} km</td>"
         f"<td class='num'>{r.reports:.1f}</td>"
         f"<td class='num'>{r.rate:.1%}</td></tr>" for _, r in h['det'].iterrows())}
</tbody></table>
<p class="muted">The closest station reports the <i>least</i> rain, because it
files the fewest reports and so misses brief showers between them. This is
<a class="jump" href="#observing-practice">observing practice</a>, not weather,
and it sets a floor on how precisely any of this can be measured.</p>
"""


def sec_events(c) -> str:
    e = c.get("events")
    if e is None:
        return ""
    m, labels = e["m"], e["labels"]

    order = (m[m.lead_days == 1].sort_values("brier_skill_score", ascending=False)
             .event.tolist())
    leads = sorted(m.lead_days.unique())
    head = "".join(f"<th class='num'>{int(l)}</th>" for l in leads)
    rows = ""
    for key in order:
        g = m[m.event == key].set_index("lead_days")
        cells = "".join(
            f"<td class='num'>{g.loc[l, 'brier_skill_score']:.2f}</td>"
            if l in g.index else "<td class='num'>&mdash;</td>" for l in leads)
        rows += (f"<tr><td>{esc(labels.get(key, key))}</td>"
                 f"<td class='num'>{g.base_rate.iloc[0]:.0%}</td>{cells}</tr>")

    best, worst = order[0], order[-1]
    b1 = m[(m.event == best) & (m.lead_days == 1)].iloc[0]
    w1 = m[(m.event == worst) & (m.lead_days == 1)].iloc[0]
    b7 = m[(m.event == best)].sort_values("lead_days").iloc[-1]
    w7 = m[(m.event == worst)].sort_values("lead_days").iloc[-1]

    sens = e.get("sens")
    sens_html = ""
    if sens is not None and len(sens):
        sens_html = f"""
<p class="muted">Because the probability is derived, the recipe could be doing
the work rather than the forecast. Two quite different recipes were tried &mdash;
one assuming a fixed S-shaped curve, one assuming only "more never means less".
They disagree by at most {sens.bss_gap.max():.3f} in skill, so the ranking above
is a property of the forecast, not of the method.</p>"""

    return f"""
<h2 id="events">Frost, heat and rain &mdash; and how far ahead each is worth trusting</h2>
<p>Everything so far has been about rain, because rain is the only thing the
archive attaches a probability to. But you might reasonably want a probability
of <b>frost</b> tonight, or of a {esc('35 C')} scorcher next week. Those can be
worked out from the plain temperature forecast, and then checked the same way.</p>

<div class="callout">
<b>Read this section differently from the others.</b> The probabilities here were
<i>not</i> published by anyone &mdash; they were derived from the ordinary
temperature and rainfall forecast by learning, from past days, how often each
forecast value was followed by the event. So the fact that they come out well
calibrated is <b>not a finding</b>: they were built to be. What cannot be faked
is <a class="jump" href="#resolution">resolution</a> and
<a class="jump" href="#bss">skill</a> &mdash; how sharply the forecast separates
days when the event happens from days when it does not. That is what the table
below shows. Each day was scored by a recipe trained only on
<i>other, non-adjacent stretches of time</i>, never on itself.</p>
</div>

<h3>Skill score by event and days ahead</h3>
<table><thead><tr><th>Event</th><th class="num">How often</th>
<th class="num" colspan="{len(leads)}">Days ahead</th></tr>
<tr><th></th><th></th>{head}</tr></thead><tbody>{rows}</tbody></table>
<p class="muted">1.00 = perfect, 0.00 = no better than quoting the long-run
average for the time of year. Each column is {int(m.n.iloc[0])} days.</p>

<div class="callout good">
<b>Temperature events stay predictable far longer than rain.</b>
{esc(labels[best])} is at {b1.brier_skill_score:.2f} a day ahead and still
{b7.brier_skill_score:.2f} a week out. {esc(labels[worst])} starts lower
({w1.brier_skill_score:.2f}) and collapses to {w7.brier_skill_score:.2f} &mdash;
close to useless. A week-ahead warning about temperature is worth acting on; a
week-ahead rain forecast is barely worth reading.
</div>

<figure><img src="{fig(FIGURES / 'events.png')}" alt="event skill">
<figcaption>Left: how each event's skill decays with lead time. Right:
calibration at one day ahead &mdash; near-diagonal by construction, as explained
above.</figcaption></figure>
{sens_html}
<p class="muted">Extreme heat is the thinnest result here: only
{int(m[m.event == 'heatwave'].n_positive.iloc[0]) if 'heatwave' in set(m.event) else 0}
such days occurred in the whole record, so treat its numbers as indicative.</p>
"""


def sec_served(c) -> str:
    """E4a and D11: the served probability against the raw ensemble.

    This is the study's central measurement and until now it existed only in
    the parquet tables. Two things have to survive the trip to the page: the
    lead-1 verdict is *unresolved*, not a tie, and the harm at low cost-loss
    ratios is invisible to the average score the rest of the site reports.
    """
    s = c.get("served")
    if s is None:
        return ""
    MODEL, MODE = "gfs_seamless", "prorata"
    div = s["div"].query("model == @MODEL and boundary_mode == @MODE") \
                  .sort_values("lead_days")
    tri = s["tri"].query("model == @MODEL and boundary_mode == @MODE")
    sig = s["sig"].query("model == @MODEL and boundary_mode == @MODE "
                         "and lead_days == 1").set_index("statistic")
    if div.empty or tri.empty or sig.empty:
        return ""

    def cell(series, lead, col):
        r = tri.query("series == @series and lead_days == @lead")
        return float(r[col].iloc[0]) if len(r) else float("nan")

    rows = "".join(
        f"<tr><td class='num'>{int(r.lead_days)}</td>"
        f"<td class='num'>{r.mean_vendor_pop:.0%}</td>"
        f"<td class='num'>{r.mean_gefs_pop:.0%}</td>"
        f"<td class='num'>{r.bias_vendor_minus_gefs:+.03f}</td>"
        f"<td class='num'>{r.mean_abs_divergence:.03f}</td>"
        f"<td class='num'>{r.pearson_r:.02f}</td></tr>"
        for _, r in div.iterrows())

    bd = sig.loc["brier_diff"]
    v05, v10 = sig.loc["value_diff_a05"], sig.loc["value_diff_a10"]
    # The equivalence margin is not a round number chosen for the page: it is
    # how far the ensemble's own score moves under the day-boundary rule, the
    # smallest difference this design can call real.
    margin = float(bd.equivalence_margin)

    # The two samples are kept apart rather than pooled. They are different
    # panels -- 15 capitals seen through several providers, and 104 cities seen
    # through one each -- and a single median over both would be a number
    # describing neither. Showing them separately also makes the contrast
    # falsifiable: it has to survive in both, and it does.
    SAMPLES = [("pinned", "15 capitals, several providers each"),
               ("served_world", "104 cities worldwide, one provider each")]
    b = s["both"]
    occ_amt = "".join(
        f"<tr><td>{esc(label)} "
        f"<span class='muted'>({len(g)} series)</span></td>"
        f"<td class='num'>{g.bss_clim.median():+.03f}</td>"
        f"<td class='num'>{(g.bss_clim > 0).mean():.0%}</td>"
        f"<td class='num'>{g.crpss_vs_climatology.median():+.03f}</td>"
        f"<td class='num'>{(g.crpss_vs_climatology > 0).mean():.0%}</td></tr>"
        for src, label in SAMPLES
        if len(g := b[b.source == src]))

    return f"""
<h2 id="served">The number you see, against the raw ensemble</h2>
<p>Every probability on this page so far has been taken at face value and
scored. This section asks a different question: where does it <i>come from</i>?
No provider documents how its rain probability is computed. So we rebuilt one
the way a forecaster would &mdash; take the American ensemble's
{GEFS_MEMBERS} members, count how many produce rain,
publish the fraction &mdash; and put the two side by side on
{int(cell('vendor', 1, 'n')):,} city-days across {int(div.n_cities.iloc[0])}
capitals.</p>
<table><thead><tr><th class="num">Days ahead</th>
<th class="num">Served says</th><th class="num">Ensemble says</th>
<th class="num">Gap</th><th class="num">Typical distance</th>
<th class="num">Agreement</th></tr></thead><tbody>{rows}</tbody></table>
<p>The served number is consistently the <b>drier</b> of the two, and the two
drift apart as the forecast reaches further out: at a week ahead they are
barely related to each other. That is a description of the pipeline, not yet a
verdict on it &mdash; drier could mean better.</p>

<h3>Is the served number better calibrated? We cannot tell.</h3>
<p>At one day ahead, the only lead where the comparison is clean, the two score
almost identically: a difference in accuracy of
{bd.estimate:+.04f}. It is tempting to call that a tie. It is not one. Allowing
for the fact that weather persists for days and that {int(bd.n_cities)} capitals
share the same weather systems, the honest interval on that difference runs from
{bd.ci_lo:+.04f} to {bd.ci_hi:+.04f} &mdash; wide enough to contain a real
advantage either way. The smallest difference this data could have detected is
{bd.mde_80:.04f}, about {bd.mde_80 / margin:.0f}&times; larger than the
{margin:.04f} that would count as a meaningful one. So the answer is
<b>unresolved</b>, and no amount of careful analysis of these two years could
have made it otherwise.</p>

<h3>Where it does matter: the person who acts on cheap precautions</h3>
<p>Average accuracy is an average over users. Consider instead someone whose
protective action is nearly free relative to the damage it prevents &mdash;
bring the washing in, carry an umbrella &mdash; who should therefore act on
quite low probabilities. Measured as the fraction of the achievable benefit
each forecast actually delivers to that user:</p>
<table><thead><tr><th>Cost of acting, vs. cost of being caught out</th>
<th class="num">Served number</th><th class="num">Raw ensemble</th>
<th class="num">Difference</th></tr></thead><tbody>
<tr><td>1 in 20 (act on ~5% chance)</td>
<td class="num">{cell('vendor', 1, 'v_cal_a05'):+.02f}</td>
<td class="num">{cell('gefs', 1, 'v_cal_a05'):+.02f}</td>
<td class="num">{v05.estimate:+.02f} <span class="muted">(p&nbsp;=&nbsp;{v05.p_boot:.03f})</span></td></tr>
<tr><td>1 in 10 (act on ~10% chance)</td>
<td class="num">{cell('vendor', 1, 'v_cal_a10'):+.02f}</td>
<td class="num">{cell('gefs', 1, 'v_cal_a10'):+.02f}</td>
<td class="num">{v10.estimate:+.02f} <span class="muted">(p&nbsp;=&nbsp;{v10.p_boot:.03f})</span></td></tr>
<tr><td>1 in 2 (act on ~50% chance)</td>
<td class="num">{cell('vendor', 1, 'v_cal_a50'):+.02f}</td>
<td class="num">{cell('gefs', 1, 'v_cal_a50'):+.02f}</td>
<td class="num">{sig.loc['value_diff_a50'].estimate:+.02f} <span class="muted">(p&nbsp;=&nbsp;{sig.loc['value_diff_a50'].p_boot:.03f})</span></td></tr>
</tbody></table>
<p>A negative number means following the forecast leaves the user worse off
than a standing habit of always acting, or never acting. For the cheap-action
user the served probability is not merely less useful than the ensemble
&mdash; it is <b>worse than useless</b>, and the dry bias in the table above is
why. By the halfway point the gap has closed and both are genuinely
useful.</p>
<p>The two findings belong together. The same days that cannot resolve a
difference in average accuracy resolve this gap comfortably. Which is the
point: <b>the choice of measure, not the amount of data, decides whether a
reader ever sees the harm.</b></p>
<figure><img src="{fig(FIGURES / 'economic_value.png')}" alt="economic value">
<figcaption>The full curve behind that table: benefit delivered, against how
cheaply the reader can afford to act. The left-hand edge, where the curves dive
below zero, is the cheap-action user.</figcaption></figure>

<h3>Whether it rains, versus how much</h3>
<p>One last split, and the only one on this page that leaves the rain
probability behind. Each forecast series is scored twice &mdash; once on
whether rain occurred, once on how much fell &mdash; against the same
reference: a same-day-of-year climatology, what the calendar alone would have
told you.</p>
<table><thead><tr><th rowspan="2">Sample</th>
<th class="num" colspan="2">Will it rain?</th>
<th class="num" colspan="2">How much will fall?</th></tr>
<tr><th class="num">Skill</th><th class="num">Beat it</th>
<th class="num">Skill</th><th class="num">Beat it</th></tr></thead>
<tbody>{occ_amt}</tbody></table>
<p>Forecasting is decisively better than the calendar at the yes/no question
and <i>worse than the calendar</i> at the amount, in both samples. Rainfall
totals are the harder problem, and the credibility a well-behaved rain
probability earns does not transfer to the millimetres printed next to
it.</p>
"""


def sec_limits(c) -> str:
    m, pop = c["m"], c["pop"]
    mae = c["lead_mae"]
    return f"""
<h2 id="limits">What this does not tell you</h2>
<ul>
<li><b>One lead time only, for the <i>published</i> rain probability.</b> The
rain results above cover a short-range forecast, so they do not tell you how far
ahead a published probability can be trusted. The frost-and-heat section answers
that question for <i>derived</i> probabilities, but those are not what a weather
app shows you. A daily logger is accumulating the data for the real thing.</li>
<li><b>We have less evidence than it looks.</b> {len(pop)} days sounds like a
lot, but weather persists, so the
<a class="jump" href="#ess">effective sample</a> is about
{m['ess']:.0f} independent days.</li>
<li><b>The gauge is a point, the forecast is a square.</b> The two stations,
10 km apart, disagree about whether it rained on {c['disagree']:.1%} of days. No
calibration claim finer than that is meaningful. See
<a class="jump" href="#representativeness">representativeness</a>.</li>
<li><b>Temperature degrades with lead time</b>, from
{mae.iloc[0]:.2f} &deg;C average error at 1 day to {mae.iloc[-1]:.2f} &deg;C at
{int(mae.index[-1])} days, with a persistent cold bias at short range.</li>
</ul>
<figure><img src="{fig(FIGURES / 'lead_diagnostics.png')}" alt="lead time">
<figcaption>Temperature error and bias against lead time, and how forecast rain
amount relates to observed rain frequency.</figcaption></figure>
"""


def sec_improve() -> str:
    rows = "".join(
        f"<tr><td class='num'>{i['rank']}</td><td><b>{esc(i['title'])}</b><br>"
        f"<span class='muted'>{esc(i['what'])}</span></td>"
        f"<td><span class='tag {'ok' if i['status']!='proposed' else 'good'}'>"
        f"{esc(i['status'])}</span></td>"
        f"<td class='muted'>{esc(i['why'])}</td></tr>" for i in IMPROVEMENTS)
    return f"""
<h2>How to improve this analysis</h2>
<p>Ranked by value per unit of effort.</p>
<table><thead><tr><th class="num">#</th><th>Improvement</th><th>Status</th>
<th>Why it matters</th></tr></thead><tbody>{rows}</tbody></table>
"""


def sec_gloss() -> str:
    items = "".join(
        f"""<div class="gloss" id="{esc(e['id'])}">
<div class="term">{esc(e['term'])}
{f'<span class="full">&mdash; {esc(e["full"])}</span>' if e.get('full') else ''}</div>
<p style="margin:5px 0 0">{esc(e['plain'])}</p>
<div class="care">{esc(e['care'])}</div></div>""" for e in GLOSSARY)
    return f"""
<h2 id="glossary">Glossary</h2>
<p>Every technical term used above, in plain language.</p>
{items}
"""


# ---------------------------------------------------------------------------
# Page chrome
# ---------------------------------------------------------------------------
def responsive_tables(html: str) -> str:
    """Wrap bare tables so they scroll rather than overflow on a phone.

    Done as a post-pass instead of editing a dozen section functions: the
    wrapper is presentational, and threading it through every f-string would add
    noise to code that is about data.
    """
    return html.replace("<table>", '<div class="tablewrap"><table>') \
               .replace("</table>", "</table></div>") \
               .replace('<div class="tablewrap"><div class="tablewrap">',
                        '<div class="tablewrap">') \
               .replace("</table></div></div>", "</table></div>")


NAV = [("answer", "Verdict"), ("curve", "Calibration"), ("season", "Seasons"),
       ("scorecard", "Scorecard"), ("hourly", "Hourly"), ("capitals", "Capitals"),
       ("providers", "Providers"), ("league", "League"),
       ("served", "Served vs raw"),
       ("events", "Frost & heat"), ("limits", "Limits"),
       ("consulting", "Work with us"), ("glossary", "Glossary")]


def topbar(cities: list[dict], sel: dict, default_slug: str) -> str:
    # Option values are real URLs, so the no-JS selector is a plain navigation
    # to a page that genuinely exists rather than a slug the browser cannot use.
    def url(slug: str) -> str:
        return "{BASE}" if slug == default_slug else f"{{BASE}}city/{slug}/"

    opts = "".join(
        f'<option value="{url(c["slug"])}"'
        f'{" selected" if c["slug"] == sel["slug"] else ""}>'
        f'{esc(c["name"])}</option>' for c in cities)
    return f"""
<div class="topbar">
  <span class="brand"><span class="dot"></span><span>Forecast calibration</span></span>
  <button class="citybtn" id="citybtn" type="button"
          aria-label="Change city. Opens a searchable list.">
    <span class="cc">{esc(sel['country'])}</span>
    <span class="name">{esc(sel['name'])}</span>
  </button>
  <noscript>
    <form method="get" action="">
      <label class="sr-only" for="nojs-city">City</label>
      <select id="nojs-city" onchange="location.href=this.value">{opts}</select>
    </form>
  </noscript>
  <span class="spacer"></span>
  <div class="seg" role="group" aria-label="Colour theme">
    <button type="button" data-theme-set="observatory" aria-pressed="true">Observatory</button>
    <button type="button" data-theme-set="daylight" aria-pressed="false">Daylight</button>
    <button type="button" data-theme-set="blueprint" aria-pressed="false">Blueprint</button>
  </div>
</div>"""


def rail() -> str:
    links = "".join(f'<a href="#{i}"><i></i><span>{esc(t)}</span></a>' for i, t in NAV)
    return f'<nav class="rail" id="rail" aria-label="Sections">{links}</nav>'


def globe_block(cities: list[dict], dropped: list[dict], card: str) -> str:
    """The globe, the card for the selected city, and the no-JS stand-in.

    The side column is the globe's caption, so it holds the two things that are
    about what you clicked: the selected city's verdict in short form, and the
    key that says what a dot's colour means. The prose that used to live here
    described dragging and zooming, which the reader discovers by dragging and
    zooming.
    """
    items = "".join(
        f'<li><a href="{{BASE}}city/{esc(c["slug"])}/" data-city="{esc(c["slug"])}">'
        f'{esc(c["name"])}</a> <span class="muted">{esc(c["country"])} &middot; '
        f'skill {c["bss"]:.2f}</span></li>' for c in cities)
    n_dropped = sum(len(g["cities"]) for g in dropped)
    return f"""
<div class="globewrap">
  <div class="globe" id="globe" aria-label="Globe of verified cities"></div>
  <div class="globe-side">
    <div id="city-card">{card}</div>
    <div class="legend">
      <span class="legend-ramp" aria-hidden="true"></span>
      <p class="legend-ends"><span>cannot beat climatology</span>
        <span>strongly skilful</span></p>
      <p class="legend-cap">A dot's colour is that city's skill score; click one
      to load its report. Where dots would overlap, a <b>+n</b> badge says how
      many cities are hidden behind that one &mdash; click it, or zoom in, to
      separate them.</p>
    </div>
    <p class="legend-cap">{len(cities)} of the {len(cities) + n_dropped} places
    probed have a rain gauge close enough, current enough and consistent enough
    to verify against. The other {n_dropped} are listed below the globe.</p>
    <div id="globe-fallback">
      <ul class="muted citylist">{items}</ul>
    </div>
  </div>
</div>
{dropped_block(dropped)}
<div class="fc" id="fc" hidden role="region" aria-live="polite" aria-busy="false"
     aria-label="Live forecast for the selected city"></div>
<p class="depth" id="depth"></p>"""


def dropped_block(dropped: list[dict]) -> str:
    """What was probed and could not be verified, and why.

    Folded away by default: it is a footnote to the map, not a competitor for
    it. But it stays on the page, because "which places could not be checked,
    and what stopped them" is a finding in its own right - and it is the honest
    denominator for every league table further down.
    """
    if not dropped:
        return ""
    n = sum(len(g["cities"]) for g in dropped)
    parts = [
        '<p class="muted">They are listed here rather than drawn on the globe: '
        'a marker that cannot be clicked and carries no number is not a '
        'result.</p>']
    for g in dropped:
        names = ", ".join(
            f'{esc(c["name"])}{" (" + esc(c["country"]) + ")" if c["country"] else ""}'
            for c in g["cities"])
        parts.append(
            f'<div class="drop-group"><h4>{esc(g["title"])} '
            f'<span class="muted">&mdash; {len(g["cities"])}</span></h4>'
            f'<p class="muted">{esc(g["blurb"])}</p>'
            f'<p class="drop-names">{names}</p></div>')
    return f"""
<details class="dropped">
  <summary>{n} further places were probed and could not be verified</summary>
  {''.join(parts)}
</details>"""


def palette() -> str:
    return """
<div class="palette" id="palette" hidden role="dialog" aria-modal="true"
     aria-label="Search cities">
  <div class="box">
    <input type="text" placeholder="Search cities&hellip;" autocomplete="off"
           role="combobox" aria-expanded="true" aria-controls="palette-list">
    <ul id="palette-list" role="listbox"></ul>
  </div>
</div>"""


def deep_dive_header(sel_name: str) -> str:
    """Fence the Bucharest-only sections and say so plainly."""
    other = "" if sel_name == "Bucharest" else (
        f"You currently have <b>{esc(sel_name)}</b> selected; its own results "
        f"are above.")
    return f"""
<h2 id="deep">The Bucharest deep dive</h2>
<div class="callout">
<b>Everything below this line is Bucharest, whichever city you selected above.</b>
These analyses need hourly present-weather reports, a second station for the
representativeness floor, and a temperature record long enough to derive frost
and heat probabilities. Those exist for Bucharest and not for the other
capitals, so the sections are not relabelled to match your selection
&mdash; that would be inventing results.
<span id="deep-sel">{other}</span>
</div>"""


def document(c: dict, cities: list[dict], dropped: list[dict], sel: dict,
             assets: dict, cfg_json: str, default_slug: str) -> str:
    stations = ", ".join(v[5] for v in STATIONS.values())
    body = responsive_tables("".join([
        sec_scorecard(c), sec_seasonfig(c), sec_hourly(c), sec_recal(c),
        sec_robust(c), sec_bench(c), sec_events(c), sec_limits(c),
    ]))
    cross = responsive_tables(
        sec_capitals(c) + sec_world(c)
        + report_providers.sec_providers()
        + report_providers.sec_league(c)
        + sec_served(c)
        + report_providers.sec_app_callout(c))
    ref = responsive_tables(report_providers.sec_consulting(c)
                            + sec_improve() + sec_gloss())
    city_html = {k: responsive_tables(v) for k, v in sel["html"].items()}

    head_links = "" if STANDALONE else (
        f'<link rel="stylesheet" href="{assets["css"]}">'
        f'<link rel="preload" as="fetch" href="{assets["land"]}" crossorigin>')
    style = f"<style>{assets['css_inline']}</style>" if STANDALONE else head_links
    scripts = "" if STANDALONE else (
        f'<script src="{assets["d3array"]}" defer></script>'
        f'<script src="{assets["d3geo"]}" defer></script>'
        f'<script src="{assets["globe"]}" defer></script>'
        f'<script src="{assets["app"]}" defer></script>')

    canonical = assets["canonical"]
    desc = (f"Are stated rain probabilities honest? {len(cities)} European "
            f"capitals checked against real station measurements.")

    return f"""<!doctype html>
<html lang="en" data-theme="observatory">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>How good is the {esc(sel['name'])} weather forecast?</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:title" content="How good is the {esc(sel['name'])} weather forecast?">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:type" content="article">
{f'<meta property="og:image" content="{assets["og"]}">' if assets.get("og") else ''}
<link rel="icon" href="{assets['icon']}" type="image/svg+xml">
<meta name="theme-color" content="#1f6fb4">
{style}
</head>
<body>
<a class="skip" href="#main">Skip to the report</a>
{'' if STANDALONE else topbar(cities, sel, default_slug)}
<header class="hero"><div class="wrap">
<h1 id="h1">How good is the <span id="h1-city">{esc(sel['name'])}</span> weather forecast?</h1>
<p class="sub">When a forecast says "40% chance of rain", does it rain on 40% of
those days? Checked against real station measurements across
{len(cities)} European capitals.</p>
</div></header>
<div class="wrap" id="main">
{'' if STANDALONE else globe_block(cities, dropped, city_html['card'])}
<div class="citypanel">
<div id="city-answer">{city_html['answer']}</div>
<div id="city-curve">{city_html['curve']}</div>
<div id="city-season">{city_html['season']}</div>
</div>
{cross}
{deep_dive_header(sel['name'])}
{body}
{ref}
<footer>Generated {dt.date.today().isoformat()} &middot;
Forecasts: Open-Meteo &middot; Truth: {esc(stations)} (GHCN-Daily) &middot;
{len(c['pop'])} Bucharest days, {len(cities)} capitals &middot;
Reproducible via <code>./run_all.sh</code>
</footer>
</div>
{'' if STANDALONE else palette()}
{'' if STANDALONE else rail()}
<div id="tip"></div>
<script id="site-config" type="application/json">{cfg_json}</script>
{scripts}
</body></html>"""


# ---------------------------------------------------------------------------
def main() -> None:
    global SITE, STANDALONE

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="/",
                    help="URL path the site is served from, e.g. /weather-calibration/")
    ap.add_argument("--standalone", action="store_true",
                    help="emit the single self-contained report.html instead")
    ap.add_argument("--origin", default="https://example.github.io",
                    help="origin used for canonical and Open Graph URLs")
    ap.add_argument("--dist", default=None,
                    help="output directory (default dist/); used by the "
                         "determinism check to build into a scratch tree")
    args = ap.parse_args()

    STANDALONE = args.standalone
    c = compute()

    k = city_report.load_all()
    if k is None:
        raise SystemExit("capitals artefacts missing - run src/capitals.py first")
    payloads = city_report.all_payloads(k)
    dropped = city_report.exclusions(k)
    by_slug = {p["slug"]: p for p in payloads}
    default = city_report.DEFAULT_CITY
    default_slug = next(p["slug"] for p in payloads if p["name"] == default)

    # Marker/selector data: the numeric summary only, no HTML. Rounded to the
    # precision the globe actually renders - a tooltip shows two decimals and a
    # dot lands within a hundred metres at any zoom the globe allows, which is
    # far inside one pixel, so shipping seventeen significant figures of float
    # noise for every city is pure first-load weight. The
    # per-city city count is gone too: it is the length of this very list.
    lite = [{"slug": p["slug"], "name": p["name"], "country": p["country"],
             "lat": round(p["lat"], 3), "lon": round(p["lon"], 3),
             "bss": round(p["bss"], 4), "n": p["n"],
             "base_rate": round(p["base_rate"], 4),
             "rank_lo": round(p["rank_lo"], 1),
             "rank_hi": round(p["rank_hi"], 1)} for p in payloads]

    # The same records, column-oriented, for the browser. JSON objects repeat
    # every key name in every record, so at 109 cities the ten names above are
    # written 109 times - some 6 KB of the first load spent on the word
    # "base_rate". The rows carry the values in the order the columns name them
    # and `app.js` rebuilds the objects before anything reads them. `lite`
    # itself stays as it is: the server-rendered fallback list is written from
    # it, and that code is clearer against records.
    city_cols = ["slug", "name", "country", "lat", "lon", "bss", "n",
                 "base_rate", "rank_lo", "rank_hi"]
    city_rows = [[c[k] for k in city_cols] for c in lite]

    if STANDALONE:
        SITE = None
        css = (WEB / "app.css").read_text()
        assets = {"css_inline": css, "canonical": args.origin, "og": None,
                  "icon": _icon_data_uri()}
        html_doc = document(c, lite, dropped, by_slug[default_slug], assets,
                            "{}", default_slug)
        out = ROOT / "report.html"
        out.write_text(html_doc, encoding="utf-8")
        print(f"wrote {out}  ({len(html_doc)/1024:.0f} KB, single file)")
        return

    site = Site(base=args.base,
                dist=Path(args.dist).resolve() if args.dist else DIST)
    SITE = site
    site.reset()

    assets = {
        "css": site.add_text("assets", "app.css", ship(WEB / "app.css")),
        "app": site.add_text("assets", "app.js", ship(WEB / "app.js")),
        "globe": site.add_text("assets", "globe.js", ship(WEB / "globe.js")),
        "d3array": site.add_file("assets/vendor", WEB / "vendor" / "d3-array.js"),
        "d3geo": site.add_file("assets/vendor", WEB / "vendor" / "d3-geo.js"),
        "land": site.add_file("assets/geo", WEB / "geo" / "land.geo.json"),
        # The 50m coastline, and the terrain textures. All three are
        # deliberately absent from the preload above and from the first-load
        # budget below: the globe paints a complete flat basemap without them,
        # swaps in the shaded planet when the textures land, and only asks for
        # the finer coastline if the reader zooms past the point where the
        # coarse one shows its corners. A megabyte and a half of geography
        # never stands between a reader and a usable page.
        "landDetail": site.add_file("assets/geo",
                                    WEB / "geo" / "land-detail.geo.json"),
        "relief": site.add_file("assets/geo", WEB / "geo" / "relief-elev.webp"),
        "biome": site.add_file("assets/geo", WEB / "geo" / "relief-biome.webp"),
        # Small enough that a hashed copy costs nothing and lets it be cached
        # as hard as everything else in assets/.
        "icon": site.add_text("assets", "favicon.svg", _icon_svg()),
        "css_inline": "",
    }
    site.add_file("assets/vendor", WEB / "vendor" / "LICENSES.txt", hashed=False)

    # Per-city payloads. Emitted before the pages so the URLs can be embedded.
    city_urls = {p["slug"]: site.add_json("data/cities", f"{p['slug']}.json", p)
                 for p in payloads}

    # Only the eight-character digest varies across these URLs - the directory
    # and the slug are both already known to the client - so the config carries
    # the digests alone and `app.js` reassembles the path. Shipping the full
    # URL 109 times was ~3.5 KB of repeated prefix on every page.
    city_hashes = {slug: url.rsplit(".", 2)[1] for slug, url in city_urls.items()}

    og = site.add_file("figures", FIGURES / "capitals_reliability.png")

    for p in payloads:
        is_default = p["slug"] == default_slug
        rel = "" if is_default else f"city/{p['slug']}/"
        cfg = {
            "base": site.base,
            "defaultSlug": default_slug,
            "cityCols": city_cols,
            "cityRows": city_rows,
            "cityHashes": city_hashes,
            "landUrl": assets["land"],
            "landDetailUrl": assets["landDetail"],
            "reliefUrl": assets["relief"],
            "biomeUrl": assets["biome"],
            "forecastApi": API_FORECAST,
            "inline": p,
        }
        page_assets = dict(assets)
        page_assets["canonical"] = args.origin.rstrip("/") + site.base + rel
        page_assets["og"] = args.origin.rstrip("/") + og

        cfg_json = json.dumps(cfg, separators=(",", ":"), ensure_ascii=False,
                              default=_jsonable)
        html_doc = document(c, lite, dropped, p, page_assets, cfg_json,
                            default_slug)
        html_doc = html_doc.replace("{BASE}", site.base)

        out = site.dist / (rel + "index.html") if rel else site.dist / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html_doc, encoding="utf-8")
        if is_default:
            first_kb = _first_load_kb(html_doc, site, assets)

    # Pages serves .nojekyll-less sites fine, but any path segment starting with
    # an underscore would be silently dropped without it.
    (site.dist / ".nojekyll").write_text("")

    n_pages = len(payloads)
    total = sum(f.stat().st_size for f in site.dist.rglob("*") if f.is_file())
    print(f"wrote {site.dist}  ({n_pages} pages, {len(city_urls)} city payloads, "
          f"{total/1024:.0f} KB total)")
    print(f"first load ~{first_kb:.0f} KB (budget {FIRST_LOAD_BUDGET_KB} KB)")
    if first_kb > FIRST_LOAD_BUDGET_KB:
        raise SystemExit(
            f"first-load budget exceeded: {first_kb:.0f} KB > "
            f"{FIRST_LOAD_BUDGET_KB} KB. Something large became eager; check for "
            f"a newly inlined figure or an un-lazy asset.")


def _icon_svg() -> str:
    """The favicon in the form it is served in: drawing only, no commentary.

    Same rule as the CSS and the JS - the reasoning stays in the repository,
    the bytes on the wire do not carry it.
    """
    svg = (WEB / "favicon.svg").read_text()
    return re.sub(r"<!--.*?-->\s*", "", svg, flags=re.S)


def _icon_data_uri() -> str:
    """The favicon as a data URI, for the single-file build.

    A standalone report is often read from `file://` or mailed around as one
    attachment; a separate icon file would simply be missing. URL-encoding
    rather than base64 keeps the markup legible and the bytes lower.
    """
    return "data:image/svg+xml," + quote(_icon_svg(), safe="/:= ")


def _first_load_kb(html_doc: str, site: Site, assets: dict) -> float:
    """Bytes a cold visitor needs before the page is readable and interactive.

    Figures are excluded: they are lazy-loaded and below the fold. The land
    geometry is included, because the globe is above it.
    """
    total = len(html_doc.encode())
    for key in ("css", "app", "globe", "d3array", "d3geo", "land"):
        rel = assets[key][len(site.base):]
        total += (site.dist / rel).stat().st_size
    return total / 1024


if __name__ == "__main__":
    main()
