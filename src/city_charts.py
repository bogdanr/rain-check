"""Cross-city charts as interactive SVG, so they can follow the selection.

The multi-city figures were matplotlib PNGs with Bucharest picked out in red.
That was right when Bucharest was the only city with a page; it is wrong now
that all 97 have one, because the reader selects a city and the chart keeps
highlighting somebody else's.

A PNG cannot follow a selection, and rendering 97 variants of three figures is
291 images for a page that already watches its byte budget. So these three are
redrawn as SVG, with one `data-city` group per city. Selecting a city toggles a
class; nothing is recomputed.

That division is the same one the rest of the site keeps:

    Python computes every coordinate. JavaScript only toggles a class.

The binning here deliberately matches `capitals.plot_many` (5 equal-count bins
for the spaghetti, the same metrics table for the scatter) so the interactive
chart and the printed figure cannot drift apart. If the PNG changes, this must
change with it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from calibration import reliability_table
from report_render import esc
from sitebuild import slugify

# Matches `capitals.plot_many`: at 5 bins a two-year record still puts ~140 days
# in each, which is the coarsest binning that keeps a per-city curve meaningful.
SPAGHETTI_BINS = 5


def _frame(w, h, pad):
    l, r, t, b = pad
    return (lambda v, lo, hi: l + (v - lo) / (hi - lo) * (w - l - r),
            lambda v, lo, hi: h - b - (v - lo) / (hi - lo) * (h - b - t))


def _open(w, h, label, kind):
    return [f'<svg viewBox="0 0 {w} {h}" width="100%" class="citychart" '
            f'data-chart="{kind}" role="img" aria-label="{esc(label)}">']


def _axes(p, w, h, pad, xticks, yticks, fx, fy, xlab, ylab):
    l, r, t, b = pad
    p.append(f'<rect class="chart-plot" x="{l}" y="{t}" '
             f'width="{w-l-r}" height="{h-t-b}"/>')
    for v, lab in xticks:
        x = fx(v)
        p.append(f'<line class="chart-grid" x1="{x:.1f}" y1="{t}" '
                 f'x2="{x:.1f}" y2="{h-b}"/>')
        p.append(f'<text class="chart-axis" x="{x:.1f}" y="{h-b+16}" '
                 f'font-size="11" text-anchor="middle">{lab}</text>')
    for v, lab in yticks:
        y = fy(v)
        p.append(f'<line class="chart-grid" x1="{l}" y1="{y:.1f}" '
                 f'x2="{w-r}" y2="{y:.1f}"/>')
        p.append(f'<text class="chart-axis" x="{l-8}" y="{y+4:.1f}" '
                 f'font-size="11" text-anchor="end">{lab}</text>')
    p.append(f'<text class="chart-label" x="{(l+w-r)/2:.0f}" y="{h-6}" '
             f'font-size="12.5" text-anchor="middle">{esc(xlab)}</text>')
    p.append(f'<text class="chart-label" x="14" y="{(t+h-b)/2:.0f}" '
             f'font-size="12.5" text-anchor="middle" '
             f'transform="rotate(-90 14 {(t+h-b)/2:.0f})">{esc(ylab)}</text>')


def _city_g(name, cls, body, tip):
    return (f'<g class="cc {cls}" data-city="{slugify(name)}" '
            f'data-tip="{esc(tip)}">{body}</g>')


# ---------------------------------------------------------------------------
# 1. Reliability spaghetti
# ---------------------------------------------------------------------------
def reliability_spaghetti(pop: pd.DataFrame, met: pd.DataFrame) -> str:
    """Every city's calibration curve at once, with the median picked out.

    This is the figure that carries the study's widest claim - that stated rain
    probabilities run low at the bottom of the scale and high at the top nearly
    everywhere - so it is worth being able to find your own city in it.
    """
    W, H = 560, 470
    pad = (58, 16, 16, 46)
    fx0, fy0 = _frame(W, H, pad)
    fx = lambda v: fx0(v, 0, 1)
    fy = lambda v: fy0(v, 0, 1)

    p = _open(W, H, "Calibration curves for every verified city", "reliability")
    ticks = [(g, f"{g*100:.0f}%") for g in (0, .25, .5, .75, 1)]
    _axes(p, W, H, pad, ticks, ticks, fx, fy,
          "What the forecast promised", "How often it actually rained")

    p.append(f'<line class="chart-ideal" x1="{fx(0):.1f}" y1="{fy(0):.1f}" '
             f'x2="{fx(1):.1f}" y2="{fy(1):.1f}" stroke-dasharray="5,4" '
             f'stroke-width="1.3"/>')

    curves = []
    p.append('<g class="cc-lines">')
    for name, g in pop.groupby("city"):
        try:
            tbl = reliability_table(g.forecast_prob.values.astype(float),
                                    g.observed_event.values.astype(float),
                                    n_bins=SPAGHETTI_BINS)
        except Exception:
            continue
        pts = " ".join(f"{fx(r.mean_prob):.1f},{fy(r.obs_freq):.1f}"
                       for _, r in tbl.iterrows())
        curves.append(tbl[["mean_prob", "obs_freq"]].assign(
            bin=range(len(tbl)), city=name))
        row = met[met.city == name]
        bss = float(row.bss.iloc[0]) if len(row) else float("nan")
        tip = f"{name}&#10;skill {bss:.2f}&#10;{len(g)} days"
        p.append(_city_g(name, "cc-line",
                         f'<polyline points="{pts}" fill="none"/>', tip))
    p.append('</g>')

    if curves:
        allc = pd.concat(curves)
        med = allc.groupby("bin").agg(x=("mean_prob", "median"),
                                      y=("obs_freq", "median")).dropna()
        pts = " ".join(f"{fx(r.x):.1f},{fy(r.y):.1f}" for _, r in med.iterrows())
        p.append(f'<polyline class="cc-median" points="{pts}" fill="none"/>')

    # The selected city is re-drawn on top by the browser, but it also needs a
    # home in paint order so the highlight is never hidden behind the mass.
    p.append('<g class="cc-top"></g>')
    p.append("</svg>")
    return "".join(p)


# ---------------------------------------------------------------------------
# 2. Skill against rain frequency
# ---------------------------------------------------------------------------
def baserate_scatter(met: pd.DataFrame) -> str:
    """Skill score against how often it rains.

    At fifteen capitals this looked like a strong negative relationship. At 97
    it is not one, which is the point of showing it: the reader can see both the
    scatter and where their own city sits in it.
    """
    d = met.dropna(subset=["bss", "base_rate"])
    if d.empty:
        return ""
    W, H = 560, 400
    pad = (58, 16, 16, 46)
    fx0, fy0 = _frame(W, H, pad)

    x_hi = float(min(0.6, max(0.25, d.base_rate.max() * 1.12)))
    y_lo = float(min(-0.05, d.bss.min() - 0.04))
    y_hi = float(max(0.6, d.bss.max() + 0.04))
    fx = lambda v: fx0(v, 0, x_hi)
    fy = lambda v: fy0(v, y_lo, y_hi)

    p = _open(W, H, "Skill score against how often it rains", "baserate")
    xt = [(v, f"{v*100:.0f}%") for v in np.arange(0, x_hi + 1e-9, 0.1)]
    yt = [(v, f"{v:.1f}") for v in np.arange(round(y_lo, 1), y_hi + 1e-9, 0.1)]
    _axes(p, W, H, pad, xt, yt, fx, fy,
          "Share of days with rain", "Skill score (higher is better)")

    # Zero skill: below this line a forecast has not beaten climatology.
    if y_lo < 0 < y_hi:
        p.append(f'<line class="chart-base" x1="{fx(0):.1f}" y1="{fy(0):.1f}" '
                 f'x2="{fx(x_hi):.1f}" y2="{fy(0):.1f}" stroke-dasharray="4,4"/>')

    p.append('<g class="cc-dots">')
    for _, r in d.iterrows():
        tip = (f"{r.city}&#10;skill {r.bss:.2f}&#10;"
               f"rain on {r.base_rate:.0%} of days&#10;{int(r.n)} days")
        body = (f'<circle cx="{fx(r.base_rate):.1f}" cy="{fy(r.bss):.1f}" '
                f'r="5"/>')
        p.append(_city_g(r.city, "cc-dot", body, tip))
    p.append('</g><g class="cc-top"></g></svg>')
    return "".join(p)


# ---------------------------------------------------------------------------
# 3. Temperature error against lead time
# ---------------------------------------------------------------------------
def lead_mae_lines(lead: pd.DataFrame) -> str:
    """Daily-maximum error by lead day, one line per city."""
    if lead is None or lead.empty:
        return ""
    col = ("tmax_mae_debiased" if "tmax_mae_debiased" in lead.columns
           else "tmax_mae")
    d = lead.dropna(subset=[col])
    if d.empty:
        return ""

    W, H = 560, 380
    pad = (58, 16, 16, 46)
    fx0, fy0 = _frame(W, H, pad)
    x_hi = float(d.lead_days.max())
    y_hi = float(max(3.0, np.nanpercentile(d[col], 98) * 1.1))
    fx = lambda v: fx0(v, 1, x_hi)
    fy = lambda v: fy0(v, 0, y_hi)

    p = _open(W, H, "Temperature error against lead time, by city", "lead")
    xt = [(v, str(int(v))) for v in range(1, int(x_hi) + 1)]
    yt = [(v, f"{v:.0f}") for v in np.arange(0, y_hi + 1e-9, 1.0)]
    _axes(p, W, H, pad, xt, yt, fx, fy,
          "Days ahead", "Average error in daily high (\u00b0C)")

    p.append('<g class="cc-lines">')
    for name, g in d.groupby("city"):
        g = g.sort_values("lead_days")
        pts = " ".join(f"{fx(r.lead_days):.1f},{fy(getattr(r, col)):.1f}"
                       for r in g.itertuples())
        one = g[g.lead_days == 1]
        tip = (f"{name}&#10;day 1: {one[col].iloc[0]:.2f} \u00b0C"
               if len(one) else name)
        p.append(_city_g(name, "cc-line",
                         f'<polyline points="{pts}" fill="none"/>', tip))
    p.append('</g>')

    med = d.groupby("lead_days")[col].median()
    pts = " ".join(f"{fx(k):.1f},{fy(v):.1f}" for k, v in med.items())
    p.append(f'<polyline class="cc-median" points="{pts}" fill="none"/>')
    p.append('<g class="cc-top"></g></svg>')
    return "".join(p)
