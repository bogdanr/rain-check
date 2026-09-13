"""HTML component builders for the report: metric scorecards and the SVG curve."""

from __future__ import annotations

import base64
import html


def esc(s) -> str:
    return html.escape(str(s))


def _pos(v, lo, hi) -> float:
    """Map a value onto 0-100% of the bar, clamped."""
    return max(0.0, min(100.0, 100.0 * (v - lo) / (hi - lo)))


def _edge_shift(pos: float) -> str:
    """Keep labels inside the bar at the extremes instead of overflowing."""
    if pos < 8:
        return "translateX(0)"
    if pos > 92:
        return "translateX(-100%)"
    return "translateX(-50%)"


def scorecard(entry: dict, metrics: dict) -> str:
    """One metric row: value, qualitative bands, our marker, reference anchors."""
    sc = entry["scale"]
    lo, hi = sc["lo"], sc["hi"]
    val = metrics[sc["value_key"]]
    pct = sc.get("pct", False)
    shown = f"{val:.1%}" if pct else f"{val:.4f}".rstrip("0").rstrip(".")

    dir_txt = {"lower": "lower is better", "higher": "higher is better",
               None: "not a quality measure - context only"}[sc.get("better")]

    # Band labels are dropped for bands too narrow to hold text; the shading
    # still conveys the band, and overlapping labels are worse than none.
    parts = []
    for a, b, l, cls in sc.get("bands", []):
        pa, pb = _pos(a, lo, hi), _pos(b, lo, hi)
        parts.append(f'<div class="band {cls}" style="left:{pa}%;width:{pb-pa}%"></div>')
        if pb - pa >= 17:
            mid = (pa + pb) / 2
            parts.append(f'<div class="bandlab" style="left:{mid}%;'
                         f'transform:{_edge_shift(mid)}">{esc(l)}</div>')
    bands = "".join(parts)

    anchors = "".join(
        f'<div class="anchor" style="left:{_pos(v,lo,hi)}%">'
        f'<span style="transform:{_edge_shift(_pos(v,lo,hi))}">{esc(l)}</span></div>'
        for v, l in sc.get("anchors", []))

    marker = (f'<div class="marker" style="left:{_pos(val,lo,hi)}%">'
              f'<span>{esc(shown)}</span></div>')

    return f"""<div class="metric">
  <div class="top">
    <div><span class="name">{esc(entry['term'])}</span>
         <span class="dir">&nbsp;&middot;&nbsp;{esc(dir_txt)}</span></div>
    <div class="val">{esc(shown)}</div>
  </div>
  <div class="bar">{bands}{anchors}{marker}</div>
  <p class="muted" style="margin:0">{esc(entry['plain'])}</p>
</div>"""


def reliability_svg(tbl, base_rate: float, label: str = "Calibration curve") -> str:
    """Interactive calibration curve.

    Drawn from the reliability table rather than reusing the PNG, so each point
    can carry its sample count and consistency range on hover. Sample count is
    the main thing that qualifies a calibration point, and it cannot be shown
    for every bin in a static image without clutter.

    Colours come from CSS classes, never from hardcoded attributes: the page
    has three themes, and a chart with hex codes baked into it would stay in
    light-mode colours on a dark page. Every visual property that a theme is
    allowed to change lives in the stylesheet.
    """
    W = H = 430
    PAD_L, PAD_B, PAD_T, PAD_R = 58, 48, 18, 18
    px = lambda v: PAD_L + v * (W - PAD_L - PAD_R)
    py = lambda v: H - PAD_B - v * (H - PAD_B - PAD_T)

    p = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
         f'aria-label="{esc(label)}">']
    p.append(f'<rect class="chart-plot" x="{PAD_L}" y="{PAD_T}" '
             f'width="{W-PAD_L-PAD_R}" height="{H-PAD_B-PAD_T}"/>')

    for g in (0, .25, .5, .75, 1):
        p.append(f'<line class="chart-grid" x1="{px(g)}" y1="{py(0)}" '
                 f'x2="{px(g)}" y2="{py(1)}"/>')
        p.append(f'<line class="chart-grid" x1="{px(0)}" y1="{py(g)}" '
                 f'x2="{px(1)}" y2="{py(g)}"/>')
        p.append(f'<text class="chart-axis" x="{px(g)}" y="{py(0)+17}" '
                 f'font-size="11" text-anchor="middle">{g*100:.0f}%</text>')
        p.append(f'<text class="chart-axis" x="{PAD_L-9}" y="{py(g)+4}" '
                 f'font-size="11" text-anchor="end">{g*100:.0f}%</text>')

    # Consistency band: what a perfect forecast would produce at this sample size.
    if "cons_lo" in tbl.columns and tbl.cons_lo.notna().all():
        up = " ".join(f"{px(r.mean_prob)},{py(r.cons_hi)}" for _, r in tbl.iterrows())
        dn = " ".join(f"{px(r.mean_prob)},{py(r.cons_lo)}"
                      for _, r in tbl.iloc[::-1].iterrows())
        p.append(f'<polygon class="chart-band" points="{up} {dn}" opacity="0.16"/>')

    p.append(f'<line class="chart-ideal" x1="{px(0)}" y1="{py(0)}" x2="{px(1)}" '
             f'y2="{py(1)}" stroke-dasharray="5,4" stroke-width="1.3"/>')
    p.append(f'<line class="chart-base" x1="{px(0)}" y1="{py(base_rate)}" '
             f'x2="{px(1)}" y2="{py(base_rate)}" stroke-dasharray="2,3"/>')

    line = " ".join(f"{px(r.mean_prob)},{py(r.obs_freq)}" for _, r in tbl.iterrows())
    p.append(f'<polyline class="chart-line" points="{line}" fill="none" '
             f'stroke-width="2.4"/>')

    for _, r in tbl.iterrows():
        sig = bool(r.get("significant", False))
        verdict = ("outside the perfect-forecast range - real miscalibration"
                   if sig else "within the range a perfect forecast would give")
        tip = (f"Forecast said {r.mean_prob:.0%}&#10;"
               f"It rained {r.obs_freq:.0%} of the time&#10;"
               f"Based on {int(r.n)} days&#10;{verdict}")
        p.append(f'<g class="pt" data-tip="{esc(tip)}">'
                 f'<circle class="chart-dot{" sig" if sig else ""}" '
                 f'cx="{px(r.mean_prob)}" cy="{py(r.obs_freq)}" r="6.5" '
                 f'stroke-width="2"/></g>')

    p.append(f'<text class="chart-label" x="{px(0.5)}" y="{H-8}" font-size="12.5" '
             f'text-anchor="middle">What the forecast promised</text>')
    p.append(f'<text class="chart-label" x="15" y="{py(0.5)}" font-size="12.5" '
             f'text-anchor="middle" transform="rotate(-90 15 {py(0.5)})">'
             f'How often it actually rained</text>')
    p.append("</svg>")
    return "".join(p)


def embed_png(path) -> str:
    """Base64-inline an image so the page works offline from file://."""
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/png;base64,{b64}"
