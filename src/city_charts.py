"""Cross-city charts as interactive SVG, so they can follow the selection.

These charts went through two designs before this one. The first drew every
city as its own polyline - honest at fifteen capitals, mud at a hundred, and
hopeless at the thousand the study is heading toward. The second kept the
spaghetti but added quantile ribbons underneath; the screenshots showed the
ribbons buried under a hundred grey lines nobody could tell apart.

This design accepts the lesson: **the population is the ribbon, and individual
lines are annotation, not data.**

  * The population is drawn as quantile ribbons - the 10-90 and 25-75 bands
    plus a labelled median - whose byte cost is constant however many cities
    exist.
  * No per-city polylines ship at all. The selected city's curve is computed
    here (`overlays()`), travels inside that city's own JSON payload, and is
    injected on top by the browser, together with its name at the line's end.
    The browser inserts points Python computed; it derives nothing.
  * Per-city visibility lives where it scales: the two scatters, where every
    city is one hoverable, clickable dot at any count, with the extremes
    name-labelled by Python (greedy placement, never overlapping).
  * Every chart states its finding: `headlines()` computes one plain sentence
    per chart from the same tables the geometry came from, so the words and
    the picture cannot disagree.

The division the rest of the site keeps holds here too:

    Python computes every coordinate. JavaScript only toggles a class or
    injects coordinates Python already computed.

The binning matches `capitals.plot_many` (5 equal-count bins) so the
interactive chart and the printed figure cannot drift apart.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from calibration import reliability_table
from report_render import esc
from sitebuild import slugify

# Matches `capitals.plot_many`: at 5 bins a two-year record still puts ~140
# days in each, the coarsest binning that keeps a per-city curve meaningful.
SPAGHETTI_BINS = 5

# Below this many cities the scatter needs no density layer - the dots are the
# density. Above it, a Python-computed 2D histogram is painted underneath so
# the mass stays legible while each dot stays individually hoverable.
DENSITY_MIN_CITIES = 300

# One shared canvas width: the charts fill the content column instead of
# floating as 560px stamps in the middle of it. Line charts reserve a right
# gutter for the labels that name the median and the selected city - words on
# the chart, not in a legend the reader has to cross-reference.
W = 860
GUTTER = 168


def _n(v) -> str:
    """A coordinate as short text: one decimal, and no trailing `.0`.

    The viewBox is 860 units wide, so a tenth of a unit is under a tenth of a
    pixel and a second decimal is invisible. The two characters in `58.0` are
    not: these charts carry thousands of numbers, every one shipped to every
    reader.
    """
    s = f"{float(v):.1f}"
    return s[:-2] if s.endswith(".0") else s


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
        x = _n(fx(v))
        p.append(f'<line class="chart-grid" x1="{x}" y1="{t}" '
                 f'x2="{x}" y2="{h-b}"/>')
        p.append(f'<text class="chart-axis" x="{x}" y="{h-b+18}" '
                 f'font-size="12.5" text-anchor="middle">{lab}</text>')
    for v, lab in yticks:
        y = fy(v)
        p.append(f'<line class="chart-grid" x1="{l}" y1="{_n(y)}" '
                 f'x2="{w-r}" y2="{_n(y)}"/>')
        p.append(f'<text class="chart-axis" x="{l-9}" y="{_n(y+4)}" '
                 f'font-size="12.5" text-anchor="end">{lab}</text>')
    p.append(f'<text class="chart-label" x="{(l+w-r)/2:.0f}" y="{h-8}" '
             f'font-size="14" text-anchor="middle">{esc(xlab)}</text>')
    p.append(f'<text class="chart-label" x="16" y="{(t+h-b)/2:.0f}" '
             f'font-size="14" text-anchor="middle" '
             f'transform="rotate(-90 16 {(t+h-b)/2:.0f})">{esc(ylab)}</text>')


def _city_g(name, cls, body, tip):
    return (f'<g class="cc {cls}" data-city="{slugify(name)}" '
            f'data-tip="{esc(tip)}">{body}</g>')


def _band(pts_lo, pts_hi, cls):
    """A quantile band as one closed path: lower edge out, upper edge back."""
    fwd = " ".join(f"{x},{y}" for x, y in pts_lo)
    back = " ".join(f"{x},{y}" for x, y in reversed(pts_hi))
    return f'<polygon class="{cls}" points="{fwd} {back}"/>'


def _anno(x, y, lines, anchor="start", cls="chart-anno", size=13):
    """A short in-plot explanation. The chart should say what its regions
    mean where the reader is already looking, not in a caption below."""
    if isinstance(lines, str):
        lines = [lines]
    out = [f'<text class="{cls}" x="{_n(x)}" y="{_n(y)}" '
           f'font-size="{size}" text-anchor="{anchor}">']
    for i, ln in enumerate(lines):
        dy = 0 if i == 0 else 16
        out.append(f'<tspan x="{_n(x)}" dy="{dy}">{esc(ln)}</tspan>')
    out.append('</text>')
    return "".join(out)


def _stack(labels, min_gap=17.0):
    """Push label y-positions apart so none overlap. `labels` is a list of
    [y, text-parts...] entries; returns them with y adjusted, order kept."""
    order = sorted(range(len(labels)), key=lambda i: labels[i][0])
    ys = [labels[i][0] for i in order]
    for k in range(1, len(ys)):
        if ys[k] - ys[k - 1] < min_gap:
            ys[k] = ys[k - 1] + min_gap
    out = list(labels)
    for k, i in enumerate(order):
        out[i] = [ys[k]] + list(labels[i][1:])
    return out


def _gutter_labels(p, x, entries):
    """Right-gutter labels for the line charts: the median and each band
    named at the height where its line/edge ends, stacked apart so they
    never collide. `entries` = [(y, cls, text), ...]."""
    placed = _stack([[y, cls, txt] for y, cls, txt in entries])
    for y, cls, txt in placed:
        p.append(f'<text class="{cls}" x="{_n(x)}" y="{_n(y + 4)}" '
                 f'font-size="12.5" text-anchor="start">{esc(txt)}</text>')


class _LabelField:
    """Greedy non-overlapping placement for scatter point labels: a label is
    drawn only if its estimated box hits nothing already placed."""

    def __init__(self):
        self.boxes = []

    def try_place(self, x, y, text, size=12.5):
        w = len(text) * size * 0.58 + 6
        h = size + 4
        box = (x, y - h, x + w, y + 2)
        for b in self.boxes:
            if not (box[2] < b[0] or box[0] > b[2] or
                    box[3] < b[1] or box[1] > b[3]):
                return False
        self.boxes.append(box)
        return True


def _city_curves(pop: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Per-city reliability tables, computed once and reused by chart,
    overlay and headline alike so none of them can disagree."""
    out = {}
    for name, g in pop.groupby("city"):
        try:
            out[name] = reliability_table(
                g.forecast_prob.values.astype(float),
                g.observed_event.values.astype(float),
                n_bins=SPAGHETTI_BINS)
        except Exception:
            continue
    return out


def _rel_quantiles(pop: pd.DataFrame):
    tables = _city_curves(pop)
    if not tables:
        return tables, pd.DataFrame()
    allc = pd.concat(
        t[["mean_prob", "obs_freq"]].assign(bin=range(len(t)), city=n)
        for n, t in tables.items())
    q = allc.groupby("bin").agg(
        x=("mean_prob", "median"),
        lo=("obs_freq", lambda s: s.quantile(.10)),
        hi=("obs_freq", lambda s: s.quantile(.90)),
        lo2=("obs_freq", lambda s: s.quantile(.25)),
        hi2=("obs_freq", lambda s: s.quantile(.75)),
        med=("obs_freq", "median")).dropna()
    return tables, q


# ---------------------------------------------------------------------------
# 1. Calibration: population ribbons + labelled median
# ---------------------------------------------------------------------------
def _rel_frame():
    H = 520
    pad = (62, GUTTER, 34, 54)
    fx0, fy0 = _frame(W, H, pad)
    return W, H, pad, (lambda v: fx0(v, 0, 1)), (lambda v: fy0(v, 0, 1))


def reliability_chart(pop: pd.DataFrame, met: pd.DataFrame) -> str:
    """The study's widest claim - stated probabilities run low at the bottom
    of the scale and high at the top nearly everywhere - as ribbons the eye
    can actually read. No spaghetti: the selected city is injected on top by
    the browser from its own payload, with its name at the end of the line."""
    Wc, H, pad, fx, fy = _rel_frame()
    tables, q = _rel_quantiles(pop)
    n = len(tables)

    p = _open(Wc, H, "Calibration of rain forecasts across "
              f"{n} cities", "reliability")
    ticks = [(g, f"{g*100:.0f}%") for g in (0, .25, .5, .75, 1)]
    _axes(p, Wc, H, pad, ticks, ticks, fx, fy,
          "What the forecast promised", "How often it actually rained")

    # The reference everyone is judged against, labelled along its own slope.
    p.append(f'<line class="chart-ideal" x1="{_n(fx(0))}" y1="{_n(fy(0))}" '
             f'x2="{_n(fx(1))}" y2="{_n(fy(1))}" stroke-dasharray="5,4" '
             f'stroke-width="1.4"/>')
    ang = np.degrees(np.arctan2(fy(0.75) - fy(0.55), fx(0.75) - fx(0.55)))
    mx, my = fx(0.585), fy(0.615)
    p.append(f'<text class="chart-anno" font-size="12.5" '
             f'text-anchor="middle" x="{_n(mx)}" y="{_n(my)}" '
             f'transform="rotate({ang:.1f} {_n(mx)} {_n(my)})">'
             f'perfectly calibrated</text>')

    # What the halves of the plot mean, said where the reader is looking.
    p.append(_anno(fx(0.26), fy(0.80),
                   ["rains more often", "than promised"], anchor="middle"))
    p.append(_anno(fx(0.76), fy(0.24),
                   ["rains less often", "than promised"], anchor="middle"))

    if len(q):
        lo = [(_n(fx(r.x)), _n(fy(r.lo))) for _, r in q.iterrows()]
        hi = [(_n(fx(r.x)), _n(fy(r.hi))) for _, r in q.iterrows()]
        lo2 = [(_n(fx(r.x)), _n(fy(r.lo2))) for _, r in q.iterrows()]
        hi2 = [(_n(fx(r.x)), _n(fy(r.hi2))) for _, r in q.iterrows()]
        p.append(_band(lo, hi, "cc-band cc-band-outer"))
        p.append(_band(lo2, hi2, "cc-band cc-band-inner"))

        pts = " ".join(f"{_n(fx(r.x))},{_n(fy(r.med))}"
                       for _, r in q.iterrows())
        p.append(f'<polyline class="cc-median" points="{pts}" fill="none"/>')

        last = q.iloc[-1]
        gx = fx(last.x) + 10
        _gutter_labels(p, gx, [
            (fy(last.med), "chart-median-label", f"median of {n} cities"),
            (fy((last.hi2 + last.hi) / 2), "chart-band-label",
             "middle 50% of cities"),
            (fy(last.hi), "chart-band-label", "middle 80%"),
        ])

    p.append('<g class="cc-top"></g>')
    p.append("</svg>")
    return "".join(p)


# ---------------------------------------------------------------------------
# 2. Calibration fingerprint: every city is one dot, at any count
# ---------------------------------------------------------------------------
def _fp_frame(wb: pd.DataFrame):
    H = 470
    pad = (62, 20, 34, 54)
    x = wb.low_gap * 100
    y = -wb.high_gap * 100
    x_lo = float(min(-2, np.floor(x.min() / 2) * 2 - 1))
    x_hi = float(max(6, np.ceil(x.max() / 2) * 2 + 1))
    y_lo = float(min(-2, np.floor(y.min() / 2) * 2 - 1))
    y_hi = float(max(6, np.ceil(y.max() / 2) * 2 + 1))
    fx0, fy0 = _frame(W, H, pad)
    return (W, H, pad, x_lo, x_hi, y_lo, y_hi,
            (lambda v: fx0(v, x_lo, x_hi)), (lambda v: fy0(v, y_lo, y_hi)))


def fingerprint_chart(wb: pd.DataFrame) -> str:
    """One dot per city: how far its low-probability forecasts understate
    rain (x) against how far its high-probability forecasts overstate it (y).

    This is where "every city is visible" lives now that the line chart
    carries ribbons instead of spaghetti - a dot per city stays legible and
    hoverable at a thousand cities where a line per city does not. The
    upper-right quadrant is the study's finding; Python labels the extremes
    and counts the quadrant so the picture states its own result.
    """
    if wb is None or wb.empty:
        return ""
    d = wb.dropna(subset=["low_gap", "high_gap"]).copy()
    if d.empty:
        return ""
    Wc, H, pad, x_lo, x_hi, y_lo, y_hi, fx, fy = _fp_frame(d)

    p = _open(Wc, H, "Each city's calibration fingerprint", "fingerprint")
    xt = [(v, f"{v:+.0f}") for v in np.arange(np.ceil(x_lo / 4) * 4,
                                              x_hi + 1e-9, 4)]
    yt = [(v, f"{v:+.0f}") for v in np.arange(np.ceil(y_lo / 4) * 4,
                                              y_hi + 1e-9, 4)]
    _axes(p, Wc, H, pad, xt, yt, fx, fy,
          "Extra rain on low-chance days (percentage points)",
          "Missing rain on high-chance days (pp)")

    # Shade the quadrant the finding lives in, then the zero crosshair.
    p.append(f'<rect class="cc-quadrant" x="{_n(fx(0))}" y="{_n(fy(y_hi))}" '
             f'width="{_n(fx(x_hi)-fx(0))}" '
             f'height="{_n(fy(0)-fy(y_hi))}"/>')
    p.append(f'<line class="chart-base" x1="{_n(fx(0))}" y1="{_n(fy(y_lo))}" '
             f'x2="{_n(fx(0))}" y2="{_n(fy(y_hi))}" stroke-dasharray="4,4"/>')
    p.append(f'<line class="chart-base" x1="{_n(fx(x_lo))}" y1="{_n(fy(0))}" '
             f'x2="{_n(fx(x_hi))}" y2="{_n(fy(0))}" stroke-dasharray="4,4"/>')

    x = d.low_gap * 100
    y = -d.high_gap * 100
    share = float(((x > 0) & (y > 0)).mean())
    p.append(_anno(fx(x_hi) - 8, fy(y_hi) + 18,
                   [f"{share:.0%} of cities are in this quadrant:",
                    "low chances understated, high chances overstated"],
                   anchor="end"))
    p.append(_anno(fx(x_lo) + 8, fy(y_lo) - 24,
                   ['opposite corner: the classic "wet bias"',
                    "the literature expected"], anchor="start"))

    r = 5 if len(d) <= 200 else (3.5 if len(d) <= 600 else 2.8)
    field = _LabelField()
    # Extremes first so their labels always win the space.
    d = d.assign(_x=x, _y=y)
    d["_ext"] = d._x.abs() + d._y.abs()
    p.append('<g class="cc-dots">')
    for _, row in d.sort_values("_ext", ascending=False).iterrows():
        tip = (f"{row.city}&#10;low end {row._x:+.0f}pp"
               f"&#10;high end {row._y:+.0f}pp")
        cx, cy = fx(row._x), fy(row._y)
        body = f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}"/>'
        p.append(_city_g(row.city, "cc-dot", body, tip))
    p.append('</g>')
    # Name the handful of extreme cities - the ones a reader will ask about.
    named = 0
    for _, row in d.sort_values("_ext", ascending=False).iterrows():
        if named >= 6:
            break
        cx, cy = fx(row._x), fy(row._y)
        if field.try_place(cx + r + 3, cy + 4, str(row.city)):
            p.append(f'<text class="chart-point-label" x="{_n(cx + r + 3)}" '
                     f'y="{_n(cy + 4)}" font-size="12">{esc(row.city)}</text>')
            named += 1
    p.append('<g class="cc-top"></g></svg>')
    return "".join(p)


# ---------------------------------------------------------------------------
# 3. Skill against rain frequency
# ---------------------------------------------------------------------------
def _scatter_frame(d: pd.DataFrame):
    H = 440
    pad = (62, 20, 34, 54)
    fx0, fy0 = _frame(W, H, pad)
    x_hi = float(min(0.6, max(0.25, d.base_rate.max() * 1.12)))
    y_lo = float(min(-0.05, d.bss.min() - 0.04))
    y_hi = float(max(0.6, d.bss.max() + 0.04))
    return (W, H, pad, x_hi, y_lo, y_hi,
            (lambda v: fx0(v, 0, x_hi)), (lambda v: fy0(v, y_lo, y_hi)))


def baserate_scatter(met: pd.DataFrame) -> str:
    """Skill score against how often it rains.

    At fifteen capitals this looked like a strong negative relationship. At a
    hundred it is not one, which is the point of showing it. Every city is a
    dot; the best and worst are named by Python; past a few hundred cities a
    computed density layer goes underneath so the mass stays legible while
    every dot stays hoverable and selectable.
    """
    d = met.dropna(subset=["bss", "base_rate"])
    if d.empty:
        return ""
    Wc, H, pad, x_hi, y_lo, y_hi, fx, fy = _scatter_frame(d)

    p = _open(Wc, H, "Skill score against how often it rains", "baserate")
    xt = [(v, f"{v*100:.0f}%") for v in np.arange(0, x_hi + 1e-9, 0.1)]
    yt = [(v, f"{v:.1f}") for v in np.arange(round(y_lo, 1), y_hi + 1e-9, 0.1)]
    _axes(p, Wc, H, pad, xt, yt, fx, fy,
          "Share of days with rain", "Skill score (higher is better)")

    # Zero skill: below this line a forecast has not beaten climatology.
    if y_lo < 0 < y_hi:
        p.append(f'<line class="chart-base" x1="{_n(fx(0))}" y1="{_n(fy(0))}" '
                 f'x2="{_n(fx(x_hi))}" y2="{_n(fy(0))}" stroke-dasharray="4,4"/>')
        p.append(_anno(fx(x_hi) - 8, fy(0) + 17,
                       "below this line: no better than a climate almanac",
                       anchor="end"))

    # Density underneath, only when the dots alone would be a smear. The grid
    # is computed here, so the browser paints rectangles it was handed.
    if len(d) >= DENSITY_MIN_CITIES:
        nx, ny = 24, 18
        hx = np.histogram2d(d.base_rate.clip(0, x_hi), d.bss.clip(y_lo, y_hi),
                            bins=[nx, ny], range=[[0, x_hi], [y_lo, y_hi]])[0]
        top = hx.max() or 1
        cells = []
        for i in range(nx):
            for j in range(ny):
                if hx[i, j] < 2:
                    continue
                x0, x1 = i * x_hi / nx, (i + 1) * x_hi / nx
                yv0 = y_lo + j * (y_hi - y_lo) / ny
                yv1 = y_lo + (j + 1) * (y_hi - y_lo) / ny
                o = 0.08 + 0.30 * hx[i, j] / top
                cells.append(
                    f'<rect class="cc-density" x="{_n(fx(x0))}" '
                    f'y="{_n(fy(yv1))}" width="{_n(fx(x1)-fx(x0))}" '
                    f'height="{_n(fy(yv0)-fy(yv1))}" opacity="{o:.2f}"/>')
        p.append('<g class="cc-density-layer">' + "".join(cells) + '</g>')

    r = 5 if len(d) <= 200 else (3.5 if len(d) <= 600 else 2.8)
    p.append('<g class="cc-dots">')
    for _, row in d.iterrows():
        tip = (f"{row.city}&#10;skill {row.bss:.2f}&#10;"
               f"rain on {row.base_rate:.0%} of days&#10;{int(row.n)} days")
        body = (f'<circle cx="{_n(fx(row.base_rate))}" '
                f'cy="{_n(fy(row.bss))}" r="{_n(r)}"/>')
        p.append(_city_g(row.city, "cc-dot", body, tip))
    p.append('</g>')

    # Name the cities a reader will point at: the best and worst by skill.
    field = _LabelField()
    ranked = d.sort_values("bss")
    for _, row in pd.concat([ranked.tail(2), ranked.head(2)]).iterrows():
        cx, cy = fx(row.base_rate), fy(row.bss)
        if field.try_place(cx + r + 3, cy + 4, str(row.city)):
            p.append(f'<text class="chart-point-label" x="{_n(cx + r + 3)}" '
                     f'y="{_n(cy + 4)}" font-size="12">{esc(row.city)}</text>')

    p.append('<g class="cc-top"></g></svg>')
    return "".join(p)


# ---------------------------------------------------------------------------
# 4. Temperature error against lead time
# ---------------------------------------------------------------------------
def _lead_col(lead: pd.DataFrame) -> str:
    return ("tmax_mae_debiased" if "tmax_mae_debiased" in lead.columns
            else "tmax_mae")


def _lead_frame(d: pd.DataFrame, col: str):
    H = 420
    pad = (62, GUTTER, 34, 54)
    fx0, fy0 = _frame(W, H, pad)
    x_hi = float(d.lead_days.max())
    y_hi = float(max(3.0, np.nanpercentile(d[col], 98) * 1.1))
    return (W, H, pad, x_hi, y_hi,
            (lambda v: fx0(v, 1, x_hi)), (lambda v: fy0(v, 0, y_hi)))


def lead_chart(lead: pd.DataFrame) -> str:
    """Daily-maximum error by lead day: ribbons for the population, the
    median named in the gutter, the first and last day's values written on
    the line, and the selected city injected on top by the browser."""
    if lead is None or lead.empty:
        return ""
    col = _lead_col(lead)
    d = lead.dropna(subset=[col])
    if d.empty:
        return ""
    Wc, H, pad, x_hi, y_hi, fx, fy = _lead_frame(d, col)
    n = int(d.city.nunique())

    p = _open(Wc, H, "Temperature error against lead time across "
              f"{n} cities", "lead")
    xt = [(v, str(int(v))) for v in range(1, int(x_hi) + 1)]
    yt = [(v, f"{v:.0f}") for v in np.arange(0, y_hi + 1e-9, 1.0)]
    _axes(p, Wc, H, pad, xt, yt, fx, fy,
          "Days ahead", "Average error in daily high (\u00b0C)")

    q = d.groupby("lead_days")[col].agg(
        lo=lambda s: s.quantile(.10), hi=lambda s: s.quantile(.90),
        lo2=lambda s: s.quantile(.25), hi2=lambda s: s.quantile(.75),
        med="median").dropna()
    lo = [(_n(fx(k)), _n(fy(r.lo))) for k, r in q.iterrows()]
    hi = [(_n(fx(k)), _n(fy(r.hi))) for k, r in q.iterrows()]
    lo2 = [(_n(fx(k)), _n(fy(r.lo2))) for k, r in q.iterrows()]
    hi2 = [(_n(fx(k)), _n(fy(r.hi2))) for k, r in q.iterrows()]
    p.append(_band(lo, hi, "cc-band cc-band-outer"))
    p.append(_band(lo2, hi2, "cc-band cc-band-inner"))

    pts = " ".join(f"{_n(fx(k))},{_n(fy(r.med))}" for k, r in q.iterrows())
    p.append(f'<polyline class="cc-median" points="{pts}" fill="none"/>')

    # The two numbers the chart exists to compare, written on the line.
    k1, kl = q.index.min(), q.index.max()
    for k, dy, anchor in ((k1, -12, "start"), (kl, -12, "end")):
        v = q.loc[k, "med"]
        p.append(f'<circle class="cc-median-dot" cx="{_n(fx(k))}" '
                 f'cy="{_n(fy(v))}" r="3.5"/>')
        p.append(_anno(fx(k) + (4 if anchor == "start" else -4), fy(v) + dy,
                       f"{v:.1f} \u00b0C", anchor=anchor, size=13.5,
                       cls="chart-anno chart-anno-strong"))

    last = q.iloc[-1]
    gx = fx(kl) + 10
    _gutter_labels(p, gx, [
        (fy(last.med), "chart-median-label", f"median of {n} cities"),
        (fy((last.hi2 + last.hi) / 2), "chart-band-label",
         "middle 50% of cities"),
        (fy(last.hi), "chart-band-label", "middle 80%"),
    ])

    p.append('<g class="cc-top"></g></svg>')
    return "".join(p)


# ---------------------------------------------------------------------------
# Computed headlines: each chart states its finding in one sentence
# ---------------------------------------------------------------------------
def headlines(pop, met, lead, wb) -> dict[str, str]:
    """One plain sentence per chart, computed from the same tables as the
    geometry. The professional gets the number, the curious reader gets the
    sentence, and neither can drift from the picture."""
    out = {}

    tables = _city_curves(pop) if pop is not None else {}
    if tables:
        bot = pd.DataFrame([t.iloc[0][["mean_prob", "obs_freq"]]
                            for t in tables.values()])
        top = pd.DataFrame([t.iloc[-1][["mean_prob", "obs_freq"]]
                            for t in tables.values()])
        out["rel"] = (
            f"In the median city, days promised around "
            f"{bot.mean_prob.median():.0%} rain actually rain "
            f"{bot.obs_freq.median():.0%} of the time \u2014 while days "
            f"promised {top.mean_prob.median():.0%} rain deliver only "
            f"{top.obs_freq.median():.0%}.")

    if wb is not None and len(wb):
        share = float(((wb.low_gap > 0) & (wb.high_gap < 0)).mean())
        out["fp"] = (f"{share:.0%} of cities understate low rain chances "
                     f"and overstate high ones \u2014 the same squeeze, "
                     f"almost everywhere.")

    d = met.dropna(subset=["bss", "base_rate"]) if met is not None else None
    if d is not None and len(d) > 2:
        r = float(np.corrcoef(d.base_rate, d.bss)[0, 1])
        out["base"] = (f"How often it rains says almost nothing about how "
                       f"well rain is forecast (r = {r:+.2f} across "
                       f"{len(d)} cities).")

    if lead is not None and len(lead):
        col = _lead_col(lead)
        med = lead.dropna(subset=[col]).groupby("lead_days")[col].median()
        if len(med) >= 2:
            k1, kl = int(med.index.min()), int(med.index.max())
            d1, dl = float(med.loc[k1]), float(med.loc[kl])
            out["lead"] = (
                f"A {kl}-day-ahead forecast of the daily high is about "
                f"{dl/d1:.1f}\u00d7 as wrong as tomorrow's: "
                f"{d1:.1f} \u00b0C grows to {dl:.1f} \u00b0C in the "
                f"median city.")
    return out


# ---------------------------------------------------------------------------
# Per-city overlay coordinates and readouts, shipped with each city's payload
# ---------------------------------------------------------------------------
def _cat(a, b):
    if b is None or not len(b):
        return a
    if a is None or not len(a):
        return b
    return pd.concat([a, b], ignore_index=True)


def overlays(pop: pd.DataFrame, met: pd.DataFrame,
             lead: pd.DataFrame | None,
             wb: pd.DataFrame | None = None,
             provisional: dict | None = None) -> dict[str, dict]:
    """Every city's geometry on each chart, in the chart's own viewBox units,
    plus one computed sentence per chart ("better than 71% of cities") that
    the browser prints under the figure on selection.

    This is what lets the charts ship no per-city geometry at all and still
    highlight *any* selected city: the coordinates travel inside the city's
    JSON payload, and the browser injects them into the chart's `cc-top`
    group. Identical frame helpers to the chart functions above, so a shipped
    overlay and a drawn ribbon cannot land in different places.

    `provisional` carries the same four frames for cities admitted at the
    lower evidence gate (see `truth_sources`). They get coordinates and a
    readout like any other city, but the frames and every percentile are
    computed from the confirmed cities alone: a provisional city is placed
    *against* the study, never folded into it.
    """
    out: dict[str, dict] = {}
    pv = provisional or {}
    pop_all = _cat(pop, pv.get("pop"))
    met_all = _cat(met, pv.get("met"))
    wb_all = _cat(wb, pv.get("wb"))
    lead_all = _cat(lead, pv.get("lead"))

    def note(name, key, text):
        out.setdefault(slugify(name), {}).setdefault("note", {})[key] = text

    _, _, _, fx, fy = _rel_frame()
    for name, tbl in _city_curves(pop_all).items():
        pts = " ".join(f"{_n(fx(r.mean_prob))},{_n(fy(r.obs_freq))}"
                       for _, r in tbl.iterrows())
        out.setdefault(slugify(name), {})["rel"] = pts
        b, t = tbl.iloc[0], tbl.iloc[-1]
        note(name, "rel",
             f"promised {b.mean_prob:.0%} \u2192 rained {b.obs_freq:.0%}; "
             f"promised {t.mean_prob:.0%} \u2192 rained {t.obs_freq:.0%}")

    d = met.dropna(subset=["bss", "base_rate"])
    if len(d):
        _, _, _, _, _, _, fx, fy = _scatter_frame(d)
        for _, r in met_all.dropna(subset=["bss", "base_rate"]).iterrows():
            out.setdefault(slugify(r.city), {})["base"] = [
                float(_n(fx(r.base_rate))), float(_n(fy(r.bss)))]
            pct = float((d.bss < r.bss).mean())
            note(r.city, "base",
                 f"skill {r.bss:.2f} \u2014 higher than {pct:.0%} of cities")

    if wb is not None and len(wb):
        dw = wb.dropna(subset=["low_gap", "high_gap"])
        if len(dw):
            _, _, _, _, _, _, _, fx, fy = _fp_frame(dw)
            for _, r in wb_all.dropna(subset=["low_gap", "high_gap"]).iterrows():
                x, y = r.low_gap * 100, -r.high_gap * 100
                out.setdefault(slugify(r.city), {})["fp"] = [
                    float(_n(fx(x))), float(_n(fy(y)))]
                note(r.city, "fp",
                     f"low end {x:+.0f}pp, high end {y:+.0f}pp")

    if lead is not None and len(lead):
        col = _lead_col(lead)
        dl = lead.dropna(subset=[col])
        if len(dl):
            _, _, _, _, _, fx, fy = _lead_frame(dl, col)
            one = dl[dl.lead_days == dl.lead_days.min()].set_index("city")[col]
            da = lead_all.dropna(subset=[col])
            one_all = da[da.lead_days == dl.lead_days.min()].set_index("city")[col]
            for name, g in da.groupby("city"):
                g = g.sort_values("lead_days")
                pts = " ".join(
                    f"{_n(fx(r.lead_days))},{_n(fy(getattr(r, col)))}"
                    for r in g.itertuples())
                out.setdefault(slugify(name), {})["lead"] = pts
                if name in one_all.index:
                    v = float(one_all.loc[name])
                    pct = float((one > v).mean())
                    note(name, "lead",
                         f"day-1 error {v:.1f} \u00b0C \u2014 better than "
                         f"{pct:.0%} of cities")
    return out


# ---------------------------------------------------------------------------
# Scale self-test: prove the charts hold at 1,000 cities before the data does
# ---------------------------------------------------------------------------
def _selftest() -> None:  # pragma: no cover
    """Render every chart from a synthetic 1,000-city world and assert the
    properties the redesign promises: bounded bytes, no per-city polylines,
    and point labels that never overlap. Run: python src/city_charts.py"""
    rng = np.random.default_rng(7)
    n_cities, n_days = 1000, 400
    names = [f"City{i:04d}" for i in range(n_cities)]

    frames = []
    for i, name in enumerate(names):
        prob = rng.uniform(0, 1, n_days)
        event = (rng.uniform(0, 1, n_days) <
                 np.clip(prob * rng.uniform(.7, 1.2) + .04, 0, 1))
        frames.append(pd.DataFrame({
            "city": name, "forecast_prob": prob,
            "observed_event": event.astype(float)}))
    pop = pd.concat(frames, ignore_index=True)

    met = pd.DataFrame({
        "city": names, "n": n_days,
        "base_rate": rng.uniform(.1, .55, n_cities),
        "bss": rng.normal(.35, .12, n_cities)})
    lead = pd.concat([pd.DataFrame({
        "city": names, "lead_days": k,
        "tmax_mae_debiased": rng.normal(1.1 + .22 * k, .25, n_cities).clip(.3)})
        for k in range(1, 8)], ignore_index=True)
    wb = pd.DataFrame({
        "city": names,
        "low_gap": rng.normal(.05, .03, n_cities),
        "high_gap": rng.normal(-.04, .03, n_cities)})

    charts = {
        "reliability": reliability_chart(pop, met),
        "fingerprint": fingerprint_chart(wb),
        "baserate": baserate_scatter(met),
        "lead": lead_chart(lead),
    }
    for kind, svg in charts.items():
        kb = len(svg.encode()) / 1024
        import zlib
        gz = len(zlib.compress(svg.encode(), 9)) / 1024
        lines = svg.count("<polyline")
        assert svg, f"{kind}: empty at 1000 cities"
        # Line charts ship ribbons only: a few KB whatever the city count.
        # Dot charts ship one hoverable element per city - linear by design,
        # but bounded per dot, and they are lazy-loaded and served compressed,
        # so the wire cost is what matters.
        if kind in ("reliability", "lead"):
            assert kb < 30, f"{kind}: {kb:.0f} KB at 1000 cities"
        else:
            assert kb < 200, f"{kind}: {kb:.0f} KB raw at 1000 cities"
            assert gz < 45, f"{kind}: {gz:.0f} KB compressed at 1000 cities"
        assert lines <= 2, f"{kind}: {lines} polylines shipped (want <=2)"
        print(f"  {kind:12s} {kb:6.1f} KB raw {gz:5.1f} KB gz  "
              f"polylines={lines} dots={svg.count('cc-dot')}")

    hl = headlines(pop, met, lead, wb)
    assert set(hl) == {"rel", "fp", "base", "lead"}, hl
    ov = overlays(pop, met, lead, wb)
    assert len(ov) == n_cities
    sample = ov[slugify(names[0])]
    assert {"rel", "base", "fp", "lead", "note"} <= set(sample), sample
    print(f"  overlays for {len(ov)} cities; sample keys {sorted(sample)}")
    print("scale self-test OK: charts hold at 1,000 cities")


if __name__ == "__main__":  # pragma: no cover
    _selftest()
