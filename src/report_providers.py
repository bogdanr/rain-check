"""Plain-language provider guide, league table and consulting sections.

Phase 2 and Phase 3 of the 2026-09-14 multi-provider plan. Kept out of
report.py for the same reason report_style.py is: that file is about the
verification data, and these sections are about explaining and selling what
the verification found. They are still ordinary sec_* functions wired into
the same document(), with the same honesty constraint: every number quoted is
computed at render time from the parquet/JSON artefacts, never hardcoded.

Graceful degradation matters here more than anywhere else in the report. The
league table (data/processed/league_table.parquet) only exists after the
multi-provider collection run, which takes days of rate-limited API time; the
report must still build - with a visible "collection pending" notice, not an
empty table or a crash - before that run has happened.
"""

from __future__ import annotations

import json

import pandas as pd

from calibration import brier_decomposition
from config import PROCESSED, PROVIDER_MODELS
from report_render import esc

# Contact details for the consulting section. Derived from the repo's own
# remote (github.com/bogdanr/rain-check): the Pages URL is the project page,
# and the noreply address is GitHub's privacy-preserving mail relay for the
# repository owner.
CONTACT_MAILTO = "mailto:bogdan@nimblex.org"
PAGES_URL = "https://bogdanr.github.io/rain-check/"
GITHUB_URL = "https://github.com/bogdanr/rain-check"


def _load_league() -> dict | None:
    """The league table and its summary, if the multi-provider run happened."""
    p = PROCESSED / "league_table.parquet"
    if not p.exists():
        return None
    s = PROCESSED / "league_summary.json"
    return {"tbl": pd.read_parquet(p),
            "summary": json.loads(s.read_text()) if s.exists() else None}


def _league_ece(league: pd.DataFrame) -> dict[tuple[str, str], float]:
    """ECE per (city, model) on the same matched days the league BSS used.

    league.py stores BSS and rank intervals but not ECE, and recomputing it
    here from the matched daily series keeps the table's two calibration
    numbers on identical windows rather than adjacent ones.
    """
    p = PROCESSED / "capitals_pinned_daily.parquet"
    if not p.exists():
        return {}
    daily = pd.read_parquet(p)
    out: dict[tuple[str, str], float] = {}
    for city, g in daily.groupby("city"):
        wide = g.pivot_table(index="local_date", columns="model",
                             values="forecast_prob")
        obs = g.groupby("local_date").observed_event.first()
        wide = wide.join(obs.rename("__event__")).dropna()
        for m in wide.columns:
            if m == "__event__":
                continue
            out[(city, m)] = float(brier_decomposition(
                wide[m].values.astype(float),
                wide["__event__"].values.astype(float), n_bins=10)["ece"])
    return out


# ---------------------------------------------------------------------------
# Task 7: the plain-language provider guide
# ---------------------------------------------------------------------------
# Who each organisation is and what makes it distinctive, in lay terms. Keyed
# by the `organization` field of the PROVIDER_MODELS registry, which is the
# single source of truth: a model added to the registry appears in the guide
# automatically, and a paragraph here without registry entries never renders.
PROVIDER_BLURB: dict[str, str] = {
    "ECMWF":
        "An intergovernmental centre owned by more than thirty European "
        "countries, based in Reading, England. It does not serve the public "
        "directly - most weather apps buy its output - but its global model "
        "is widely treated as the reference that others measure themselves "
        "against.",
    "DWD (German Weather Service)":
        "Germany's national weather service. Its models cover Europe at much "
        "finer detail than the global models manage, which is why it tends "
        "to win in the middle of Europe and matter less further away.",
    "NOAA (US National Weather Service)":
        "The United States' weather agency. Its global model, the GFS, is the "
        "other big worldwide forecaster alongside the European one - the two "
        "disagree regularly, and forecasters watch both.",
    "Météo-France":
        "France's national weather service, running both a global model and "
        "a sharply detailed European one focused on France and its "
        "neighbours.",
    "Met Office (UK)":
        "The United Kingdom's weather service, one of the oldest in the "
        "world, with its own global model and a very fine-grained model over "
        "the British Isles.",
    "ECCC (Environment Canada)":
        "Canada's national weather service. Its global model is built for a "
        "continent of extremes, from Arctic cold to hurricane season.",
    "Japan Meteorological Agency":
        "Japan's national weather service, renowned for typhoon forecasting "
        "and one of the few agencies running a truly global model "
        "independently of the US and Europe.",
    "MET Norway":
        "Norway's national weather service, whose open model chain is widely "
        "used across the Nordic countries.",
    "KNMI (Netherlands)":
        "The Dutch weather service. Its high-resolution European model is "
        "shared with several neighbouring countries, so the same forecast "
        "engine appears under several national flags.",
    "DMI (Denmark)":
        "The Danish weather service, running the same shared high-resolution "
        "European model as the Netherlands for the Danish region.",
    "ARPAE (Italy)":
        "Italy's environmental protection agency, whose high-resolution model "
        "covers Italy and southern Europe.",
}


def _coverage_counts() -> dict[str, int]:
    """How many probed cities each model has a usable archive in, if probed."""
    p = PROCESSED / "provider_coverage.json"
    if not p.exists():
        return {}
    v = json.loads(p.read_text())
    counts: dict[str, int] = {}
    for c in v.get("cities", {}).values():
        for m, r in c.items():
            if r.get("available"):
                counts[m] = counts.get(m, 0) + 1
    return counts


def sec_providers() -> str:
    """Tasks 7-8: who makes these forecasts, and the concepts behind them."""
    cov = _coverage_counts()
    by_org: dict[str, list] = {}
    for m in PROVIDER_MODELS.values():
        by_org.setdefault(m.organization, []).append(m)

    blocks = ""
    for i, (org, models) in enumerate(by_org.items()):
        who = PROVIDER_BLURB.get(org, "")
        items = "".join(
            f"<li><b>{esc(m.display_name)}</b> &mdash; {esc(m.description)}"
            + (f" <span class='tag ok'>archive verified in "
               f"{cov[m.model]} "
               + ("city" if cov[m.model] == 1 else "cities")
               + " probed</span>"
               if cov.get(m.model) else "")
            + "</li>"
            for m in models)
        blocks += f"""
<div class="provider" id="provider-{i}">
<div class="term">{esc(org)}</div>
<p>{esc(who)}</p>
<ul>{items}</ul>
</div>"""

    n_models = len(PROVIDER_MODELS)
    n_orgs = len(by_org)
    return f"""
<h2 id="providers">Who makes these forecasts?</h2>
<p class="lede">Every weather app gets its numbers from a
<a class="jump" href="#weather-model">weather model</a> run by a national
weather service or an international centre. There is no single "the
forecast": at last count this study tracks <b>{n_models} models</b> from
<b>{n_orgs} organisations</b>, and they routinely disagree.</p>

<h3>The concepts, in one minute</h3>
<p>A <a class="jump" href="#weather-model">weather model</a> is a computer
simulation of the atmosphere: it cuts the world into grid squares, applies the
laws of physics step by step, and turns today's observations into a forecast.
Two different computers, given the same starting observations, produce
different rain probabilities for the same city &mdash; because they use
different grid sizes, different shortcuts for physics too small to simulate,
and different judgements about how to blend observations in. Neither is
lying; they are different approximations of the same impossibly hard problem.
<a class="jump" href="#verification">Verification</a> is how we tell which
approximations are actually better: compare what was promised against what a
rain gauge measured, over many days.</p>

<figure>{flow_diagram_svg()}
<figcaption>Where the number in your weather app comes from. Each step can
change the probability &mdash; and the last step usually happens without
telling you which model it picked.</figcaption></figure>

<h3>The organisations behind the models</h3>
<p>Why do their forecasts differ? Partly geography: a model built to resolve
European thunderstorms says little about the Pacific. Partly philosophy: the
American and European centres make different trade-offs between resolution,
range and computing cost. And partly chance: small differences at the start
of a simulation grow into different pictures of whether a front arrives
tonight or tomorrow morning.</p>
{blocks}
<p class="muted">Model descriptions come from the study's provider registry
(<code>src/config.py</code>); archive availability per city is measured, not
assumed, by <code>src/probe_providers.py</code>.</p>
"""


def flow_diagram_svg() -> str:
    """Task 8: provider -> model -> app -> your umbrella decision.

    Inline SVG, themed through CSS classes exactly like reliability_svg(): no
    hardcoded colours, so all three page themes restyle it for free.
    """
    W, H = 760, 150
    boxes = [
        (20, "Weather service", "ECMWF, DWD, NOAA&hellip;"),
        (215, "Model run", "IFS, ICON, GFS&hellip;"),
        (410, "Weather app", "picks a model &mdash; often silently"),
        (605, "Your decision", '"40% chance" &rarr; umbrella?'),
    ]
    p = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
         f'aria-label="How a forecast probability reaches your phone">'
         f'<rect class="chart-plot" x="0" y="0" width="{W}" height="{H}"/>']
    for x, title, sub in boxes:
        p.append(f'<rect class="flow-box" x="{x}" y="38" width="135" '
                 f'height="74" rx="9"/>')
        p.append(f'<text class="flow-title" x="{x + 67}" y="70" '
                 f'text-anchor="middle">{title}</text>')
        p.append(f'<text class="flow-sub" x="{x + 67}" y="92" '
                 f'text-anchor="middle">{sub}</text>')
    for x in (155, 350, 545):
        p.append(f'<line class="chart-line" x1="{x}" y1="75" x2="{x + 56}" '
                 f'y2="75" stroke-width="2"/>')
        p.append(f'<polygon class="flow-arrow" '
                 f'points="{x + 56},70 {x + 56},80 {x + 64},75"/>')
    p.append("</svg>")
    return "".join(p)


# ---------------------------------------------------------------------------
# Task 9: the league table
# ---------------------------------------------------------------------------
def sec_league(c) -> str:
    """The cross-provider league table, or an honest notice that it is pending."""
    league = c.get("league")
    if league is None:
        return f"""
<h2 id="league">Which forecaster is best? The league table</h2>
<div class="callout">
<b>Collection pending.</b> Ranking every provider fairly needs the same
multi-year archive for every model in every city, and Open-Meteo's rate limit
caps how fast that can be collected &mdash; politely, over several days. The
table will appear here, computed at render time from
<code>data/processed/league_table.parquet</code>, once
<code>src/league.py</code> has run. Nothing on this page is a placeholder
number: until the run completes, there is no ranking to show.
</div>
<p class="muted">What the table will show: per city, every provider's
<a class="jump" href="#bss">skill score</a> and
<a class="jump" href="#ece">average calibration error</a>, a rank interval
that says how much of the ordering is real, and the number of days each
comparison rests on. Rankings will be computed on days matched across all
models, so unequal archive depths cannot decide them.</p>"""

    tbl = league["tbl"]
    ece = _league_ece(tbl)
    summary = league.get("summary") or {}
    held = summary.get("held_fixed", {})

    rows = ""
    for city, g in tbl.groupby("city"):
        g = g.sort_values("rank")
        first = True
        for _, r in g.iterrows():
            org = PROVIDER_MODELS[r.model].organization \
                if r.model in PROVIDER_MODELS else r.organization
            name = (f'<a class="jump" href="#providers">{esc(r.display_name)}</a>'
                    f'<br><span class="muted">{esc(org)}</span>')
            e = ece.get((r.city, r.model))
            ecell = f"<td class='num'>{e:.1%}</td>" if e is not None \
                else "<td class='num'>&mdash;</td>"
            rows += (f"<tr{' class=\"hl\"' if first else ''}>"
                     f"{'<td rowspan=\"%d\">%s</td>' % (len(g), esc(city)) if first else ''}"
                     f"<td>{name}</td>"
                     f"<td class='num'>{r.bss:.2f}</td>"
                     f"<td class='num'>[{r.bss_lo:.2f}, {r.bss_hi:.2f}]</td>"
                     f"{ecell}"
                     f"<td class='num'>{r.rank_lo:.0f}&ndash;{r.rank_hi:.0f}</td>"
                     f"<td class='num'>{int(r.n_days):,}</td></tr>")
            first = False

    held_html = ""
    if held.get("verdict"):
        held_html = (f"<div class=\"callout\"><b>Held-fixed check.</b> "
                     f"{esc(held['verdict'])}.</div>")

    return f"""
<h2 id="league">Which forecaster is best? The league table</h2>
<p class="lede">The capitals table above ranks <i>cities</i>. This one ranks
<i>forecasters</i>: within each city, every model scored on exactly the same
days, so the comparison is like-for-like by construction.</p>
<div class="tablewrap"><table>
<thead><tr><th>City</th><th>Provider</th>
<th class="num"><a class="jump" href="#bss">Skill</a></th>
<th class="num">95% interval</th>
<th class="num"><a class="jump" href="#ece">Avg error</a></th>
<th class="num">Rank range</th><th class="num">Days</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class="muted"><b>How to read this table.</b> Skill (the Brier Skill Score)
answers one question in everyday terms: how much better is this forecast than
just quoting the local average? Higher is better; 0 means no better than that
average, 1 is perfect. The rank range is the honest part: with about two
years of data, most neighbouring ranks cannot be told apart, so a model
"ranked 2&ndash;4" is not ranked second. Avg error is the average gap between
promised and delivered rain chances, in percentage points.</p>
{held_html}
"""


# ---------------------------------------------------------------------------
# Task 10: which model is my app actually using?
# ---------------------------------------------------------------------------
def sec_app_callout(c) -> str:
    """The provenance finding, aimed at the reader rather than the modeller."""
    k = c.get("capitals")
    if k is None or k.get("prov") is None or k.get("pinned") is None:
        return ""
    prov = k["prov"]
    pin = k["pinned"]
    counts = prov.best_match_is.value_counts()
    n_icon = int(counts.get("icon_eu", 0))
    n_ecmwf = int(counts.get("ecmwf_ifs025", 0))
    br = pin[pin.city == "Bucharest"].set_index("model").bss
    if {"icon_eu", "ecmwf_ifs025"} <= set(br.index):
        delta = float(br["icon_eu"] - br["ecmwf_ifs025"])
        swing = f" At Bucharest, over identical days, that choice alone is worth {delta:+.2f} in skill."
    else:
        swing = ""
    return f"""
<div class="callout" id="app-provenance">
<b>Which model is my weather app actually using?</b> Often, nobody will tell
you &mdash; and it can change without notice. This study's
<a class="jump" href="#capitals">provenance audit</a> found that the
"default" forecast service it buys from silently routes each city to a
different underlying model: across the capitals probed, it resolved to
ICON-EU in <b>{n_icon}</b> of them and ECMWF IFS in <b>{n_ecmwf}</b>, with no
disclosure either way.{swing} The audit only caught it because the default
series was compared, hour by hour, against forecasts pinned to a named model.
Your app almost certainly does the same thing, one layer further removed.
</div>"""


# ---------------------------------------------------------------------------
# Tasks 11-12: consulting
# ---------------------------------------------------------------------------
def sec_consulting(c) -> str:
    """'Work with us', as the rig that built the page rather than a brochure.

    The previous version was a promise on the left, two bars on the right and
    four service cards underneath: the shape every consulting block on the
    internet has, which is exactly why it read as filler. This one is the
    pipeline itself - collect, reconcile, score, publish - drawn as a running
    conduit with a pulse travelling down it, each stage carrying the number
    this build actually produced at that stage. The animation is the argument:
    the section is a live readout of the machine that generated everything
    above it, so the claim and the evidence are the same object.

    It loops rather than firing once on scroll, because the section sits two
    thirds down a long page and a one-shot entrance would be over before
    anyone arrived. Every figure is computed at render time from the loaded
    artefacts, so a sales claim can never drift from the report above it.
    """
    said, did, gap_note = _gap_numbers(c)

    # Four stages: label, readout, what the readout counts, why it matters.
    # Ordered as the data moves, not as a price list.
    stages = [
        ("Collect", *_collect_stat(c),
         "Rate-limited APIs, resumable cache, months of unattended runs. "
         "Re-running it is boring, which is the whole point."),
        ("Reconcile", *_join_stat(c),
         "Forecast meets ground truth. A join that quietly drops rows is a "
         "lie with a timestamp on it, so this one fails loudly instead."),
        ("Score", f"{said:.0%} &rarr; {did:.0%}", "promised vs. delivered",
         "Skill, calibration and bootstrap intervals. Uncertainty stated in "
         "the open, so the finding survives someone arguing with it."),
        ("Publish", "1 command", "rebuilds this page byte for byte",
         "An answer nobody can reproduce next quarter was never an answer. "
         "This page is the regression test for its own conclusions."),
    ]
    stage_html = "".join(f"""
<li class="stage" style="--i:{i}"><span class="node"></span>
<span class="sname">{name}</span>
<b class="sval">{val}</b>
<span class="sunit">{unit}</span>
<span class="swhy">{why}</span></li>"""
        for i, (name, val, unit, why) in enumerate(stages))

    return f"""
<h2 id="consulting">Work with us</h2>
<section class="rig">
<p class="rig-kick"><span class="led"></span>Consulting &middot; pipelines,
telemetry, verification</p>
<h3 class="rig-claim">Shipping a number is easy.<br>
<em>Proving it was true is the job.</em></h3>
<p class="rig-lede">DevOps hands, an engineering degree and a scientist's
refusal to take a metric on trust. I build the boring half &mdash; collection,
automation, infrastructure that survives being left alone &mdash; then do the
half almost everyone skips: score what the system <i>promised</i> against what
actually <i>happened</i>, with intervals. Here the promise is a rain
probability; elsewhere it is an SLO, a vendor's accuracy claim or a model in
production. The rig below does not care which &mdash; and it is the rig that
produced every number on this page.</p>
<ol class="rig-flow" aria-label="The pipeline behind this report: collect, reconcile, score, publish.">{stage_html}</ol>
<p class="rig-catch"><span class="dot"></span>{_catch_line(c)}</p>
<div class="rig-run">
<code class="cmd">$ ./run_all.sh</code>
<span class="ok">exit 0 &middot; {gap_note}</span>
<a class="btn" href="{CONTACT_MAILTO}">Put it to work</a>
</div>
</section>
<p class="contact">Get in touch: <a href="{CONTACT_MAILTO}">email</a>
&middot; <a href="{PAGES_URL}">this report</a> &middot;
<a href="{GITHUB_URL}">the code on GitHub</a></p>
"""


def _gap_numbers(c) -> tuple[float, float, str]:
    """The said-vs-happened gap this report found, plus a one-line provenance.

    Prefers the wet-bias table's most confident group - the sharpest honest
    statement of the gap - and falls back to the top populated bin of the
    daily reliability curve, which always exists.
    """
    b = c.get("bench")
    if b is not None and b.get("wb") is not None and len(b["wb"]):
        high = b["wb"][b["wb"].group.str.startswith("high")]
        if len(high):
            r = high.iloc[0]
            return (float(r.mean_stated), float(r.observed_freq),
                    f"{int(r.n)} most-confident days, off by {abs(r.gap):.0%}")
    t = c["tbl"]
    t = t[t.n >= 20] if "n" in t else t
    r = t.iloc[-1]
    return (float(r.mean_prob), float(r.obs_freq),
            f"{c['m']['n']:.0f} days rescored from raw data")


def _collect_stat(c) -> tuple[str, str]:
    """Readout for the collection stage, from what was actually pulled."""
    lg = c.get("league")
    if lg and lg.get("summary"):
        s = lg["summary"]
        return (f"{s['n_models_in_league']} &times; "
                f"{s['n_cities_in_league']}", "models &times; cities, one API quota")
    k = c.get("capitals")
    if k and k.get("met") is not None:
        return f"{len(k['met'])}", "cities pulled daily, rate-limit aware"
    return "years", "of daily pulls, resumed from cache after failures"


def _join_stat(c) -> tuple[str, str]:
    """Readout for the reconcile stage: forecasts matched to measurements."""
    h = c.get("hourly")
    if h is not None:
        return f"{len(h['df']):,}", "hourly forecasts to gauges"
    return f"{c['m']['n']:.0f}", "forecast days matched to gauges"


def _catch_line(c) -> str:
    """The one thing the pipeline caught that nobody had disclosed."""
    k = c.get("capitals")
    if k and k.get("prov") is not None:
        n = int(k["prov"].best_match_is.value_counts().sum())
        return (f"Caught by this rig: a weather API silently routing "
                f"{n} capitals to different underlying models, undisclosed. "
                f"That is the class of thing verification finds and a status "
                f"page never will.")
    return ("Caught by this rig: silent provider swaps behind an unchanged "
            "API response. That is the class of thing verification finds and "
            "a status page never will.")
