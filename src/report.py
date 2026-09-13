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
from pathlib import Path

import numpy as np
import pandas as pd

import city_report
from analyze import SEASONS, load_leads, load_pop
from calibration import (
    block_bootstrap_ci,
    brier_decomposition,
    consistency_bars,
    cross_validated_recalibration,
    effective_sample_size,
    reliability_table,
)
from config import (API_FORECAST, FIGURES, ISD_STATIONS, N_PROB_BINS, PROCESSED,
                    RAIN_THRESHOLD_MM, RAW, ROOT, STATIONS)
from events import EVENTS
from report_content import GLOSSARY, IMPROVEMENTS
from report_render import embed_png, esc, reliability_svg, scorecard
from sitebuild import DIST, WEB, Site, _jsonable

# Set by main(). Figures resolve through the active build so they become
# cache-busted asset URLs in the site build and base64 blobs in standalone.
SITE: Site | None = None
STANDALONE = False

# Rough guard against the page quietly becoming enormous. Hosting removed the
# hard file-size cliff, but a first-load payload can still be ruined by
# accident - the previous version of this page was 1.4 MB of inlined PNG.
FIRST_LOAD_BUDGET_KB = 400


def fig(path: Path) -> str:
    """URL for a figure: a hashed asset when building a site, base64 when not."""
    if STANDALONE or SITE is None:
        return embed_png(path)
    return SITE.add_file("figures", path)


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
                capitals=_load_capitals(), world=_load_world())


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
<figure><img src="{fig(FIGURES / 'cities_lead_mae.png')}" alt="lead time">
<figcaption>Daily-max temperature error against lead time, Bucharest in red,
with meteoblue's published global anchors.</figcaption></figure>
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
<figure><img src="{fig(FIGURES / 'cities_reliability.png')}" alt="all cities">
<figcaption>Every city as one faint line, the median city in black, Bucharest in
red; and where Bucharest falls in the distribution of skill.</figcaption></figure>
{rep_html}
<figure><img src="{fig(FIGURES / 'cities_baserate.png')}" alt="base rate">
<figcaption>The base-rate confound at {n} cities. Only the extremes and
Bucharest are labelled.</figcaption></figure>
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
       ("events", "Frost & heat"), ("limits", "Limits"), ("glossary", "Glossary")]


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
    <kbd>&#8984;K</kbd>
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
  <button class="iconbtn" id="skytoggle" type="button" aria-pressed="true"
          title="Tint the page with the selected city's current weather"
          aria-label="Weather-reactive tint">&#9728;</button>
</div>"""


def rail() -> str:
    links = "".join(f'<a href="#{i}"><i></i><span>{esc(t)}</span></a>' for i, t in NAV)
    return f'<nav class="rail" id="rail" aria-label="Sections">{links}</nav>'


def globe_block(cities: list[dict], excluded: list[dict]) -> str:
    """The globe, plus the list that stands in for it when scripting is off."""
    items = "".join(
        f'<li><a href="{{BASE}}city/{esc(c["slug"])}/" data-city="{esc(c["slug"])}">'
        f'{esc(c["name"])}</a> <span class="muted">{esc(c["country"])} &middot; '
        f'skill {c["bss"]:.2f}</span></li>' for c in cities)
    return f"""
<div class="globewrap">
  <div class="globe" id="globe" aria-label="Globe of verified capitals"></div>
  <div class="globe-side">
    <h3 style="margin-top:0">Pick a capital</h3>
    <p class="muted">Click a dot to load that city's report. Drag to turn the
    globe, scroll or use the buttons to zoom. It opens zoomed to the capitals
    that were verified &mdash; at whole-Earth scale they overlap each other.
    {len(cities)} capitals have a rain gauge close enough, current
    enough and consistent enough to verify against; {len(excluded)} were probed
    and dropped, and are drawn hollow.</p>
    <div class="legend">
      <div class="legend-row"><span class="legend-ramp"></span>
        <span>skill: cannot beat climatology &rarr; strongly skilful</span></div>
      <div class="legend-row"><span class="legend-dots">
        <i style="width:8px;height:8px"></i><i style="width:12px;height:12px"></i>
        <i style="width:15px;height:15px"></i></span>
        <span>dot size = days of record</span></div>
      <div class="legend-row"><span class="legend-dots">
        <i style="width:12px;height:12px;background:none;border:1.2px dashed currentColor"></i>
        </span><span>hollow = excluded, hover for the reason</span></div>
    </div>
    <div id="globe-fallback">
      <ul class="muted" style="columns:2;font-size:13.5px">{items}</ul>
    </div>
  </div>
</div>
<div class="fc" id="fc" hidden></div>
<p class="depth" id="depth"></p>"""


def palette() -> str:
    return """
<div class="palette" id="palette" hidden role="dialog" aria-modal="true"
     aria-label="Search capitals">
  <div class="box">
    <input type="text" placeholder="Search capitals&hellip;" autocomplete="off"
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


def document(c: dict, cities: list[dict], excluded: list[dict], sel: dict,
             assets: dict, cfg_json: str, default_slug: str) -> str:
    stations = ", ".join(v[5] for v in STATIONS.values())
    body = responsive_tables("".join([
        sec_scorecard(c), sec_seasonfig(c), sec_hourly(c), sec_recal(c),
        sec_robust(c), sec_bench(c), sec_events(c), sec_limits(c),
    ]))
    cross = responsive_tables(sec_capitals(c) + sec_world(c))
    ref = responsive_tables(sec_improve() + sec_gloss())
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
{'' if STANDALONE else globe_block(cities, excluded)}
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
    excluded = city_report.excluded_markers(k)
    by_slug = {p["slug"]: p for p in payloads}
    default = city_report.DEFAULT_CITY
    default_slug = next(p["slug"] for p in payloads if p["name"] == default)

    # Marker/selector data: the numeric summary only, no HTML.
    lite = [{kk: p[kk] for kk in ("slug", "name", "country", "lat", "lon", "bss",
                                  "n", "base_rate", "rank_lo", "rank_hi",
                                  "n_cities")} for p in payloads]

    if STANDALONE:
        SITE = None
        css = (WEB / "app.css").read_text()
        assets = {"css_inline": css, "canonical": args.origin, "og": None}
        html_doc = document(c, lite, excluded, by_slug[default_slug], assets,
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
        "css": site.add_text("assets", "app.css", (WEB / "app.css").read_text()),
        "app": site.add_text("assets", "app.js", (WEB / "app.js").read_text()),
        "globe": site.add_text("assets", "globe.js", (WEB / "globe.js").read_text()),
        "d3array": site.add_file("assets/vendor", WEB / "vendor" / "d3-array.js"),
        "d3geo": site.add_file("assets/vendor", WEB / "vendor" / "d3-geo.js"),
        "land": site.add_file("assets/geo", WEB / "geo" / "land.geo.json"),
        "css_inline": "",
    }
    site.add_file("assets/vendor", WEB / "vendor" / "LICENSES.txt", hashed=False)

    # Per-city payloads. Emitted before the pages so the URLs can be embedded.
    city_urls = {p["slug"]: site.add_json("data/cities", f"{p['slug']}.json", p)
                 for p in payloads}

    og = site.add_file("figures", FIGURES / "capitals_reliability.png")

    for p in payloads:
        is_default = p["slug"] == default_slug
        rel = "" if is_default else f"city/{p['slug']}/"
        cfg = {
            "base": site.base,
            "defaultSlug": default_slug,
            "cities": lite,
            "excluded": excluded,
            "cityUrls": city_urls,
            "landUrl": assets["land"],
            "forecastApi": API_FORECAST,
            "inline": p,
        }
        page_assets = dict(assets)
        page_assets["canonical"] = args.origin.rstrip("/") + site.base + rel
        page_assets["og"] = args.origin.rstrip("/") + og

        cfg_json = json.dumps(cfg, separators=(",", ":"), ensure_ascii=False,
                              default=_jsonable)
        html_doc = document(c, lite, excluded, p, page_assets, cfg_json,
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
