"""Every number the Atlas Noir site shows, read from the processed tables.

Ported from the design prototype's exporter (design/proto/export.py, plan
2026-09-24-site-redesign-atlas-noir Task 4.x). Nothing here is typed in: the
city verdicts come from the same payloads the old report was built from
(src/city_report.py), the curves and the evidence from data/processed/, and
every constant from its one definition in the source.

    build(payloads) -> {"site": ..., "cities": {slug: ...}, "stars": bytes}

`site` is the shared file (index, world numbers, tiers, evidence), each city
file is loaded on demand, and `stars` is the packed sky for the stage.
"""

from __future__ import annotations

import re
from datetime import date

import numpy as np
import pandas as pd

from city_report import _TIERS
from config import PROCESSED, RAIN_THRESHOLD_MM, ROOT
from metrics import contingency_rates, value_curve
from report_content import GLOSSARY

# Display-only renames. The source name stays the key for every data lookup
# (cities_metrics, decision curves); only what the reader sees changes.
DISPLAY_NAME = {"'s-Hertogenbosch": "Hertogenbosch"}

EVENT = f"day total \u2265 {RAIN_THRESHOLD_MM:g} mm at the gauge"

# Terms only the Atlas needs: the live sky on its globe (src/web/atlas/sky.js).
# None of this imagery enters the audit, so the report's glossary omits them.
SKY_GLOSSARY = [
    dict(
        id="ir-cloud", term="Infrared cloud image", full="EUMETSAT IR 10.8 \u00b5m",
        plain="A satellite picture of heat, not light, so it works at night. "
              "Cloud tops are colder than the ground below them and show up "
              "brighter. The globe's clouds are the latest such mosaic.",
        care="Low cloud and fog are nearly as warm as the ground, so they are "
             "under-shown. Cover is read from contrast in a styled image, not "
             "from calibrated temperatures.",
    ),
    dict(
        id="cloud-top", term="Cloud-top height (estimated)",
        plain="How tall the clouds on the globe stand. Colder tops are drawn "
              "higher, as they are in reality: thunderstorms reach the "
              "coldest, highest levels.",
        care="An estimate from brightness, not a measurement, and exaggerated "
             "about 20\u00d7 - true to scale, even a storm would be thinner "
             "than a pixel.",
    ),
    dict(
        id="sat-rain", term="Satellite rain estimate", full="NASA GPM IMERG",
        plain="Where it rained in the latest half hour, estimated by NASA "
              "from a constellation of satellites. It drives the rain shafts "
              "and the darker, wet ground on the globe.",
        care="An estimate, a few hours old, and least reliable beyond about "
             "60\u00b0 north or south and over snow. It is not the gauge "
             "rain the audit scores against.",
    ),
]


def show(name: str) -> str:
    return DISPLAY_NAME.get(name, name)


def _show_text(text: str) -> str:
    for src, dst in DISPLAY_NAME.items():
        text = text.replace(src, dst).replace(src.replace("'", "&#x27;"), dst)
    return text


def _r(v, k=4):
    return None if v is None or v != v else round(float(v), k)


def _const(path: str, name: str) -> float:
    """A constant read from its one definition in the source, never retyped."""
    m = re.search(rf"^{name} = ([\d._]+)", (ROOT / path).read_text(), re.M)
    return float(m.group(1).replace("_", ""))


# ---------------------------------------------------------------------------
# Cities
# ---------------------------------------------------------------------------
def index(payloads: list[dict]) -> list[list]:
    """[slug, source name, country, lat, lon, bss] per city, in payload order."""
    return [[p["slug"], p["name"], p["country"], round(p["lat"], 3),
             round(p["lon"], 3), round(p["bss"], 4)] for p in payloads]


def hero(p: dict) -> dict:
    # The verdict sentence and the record span are the report's own words,
    # taken from its rendered answer so the site cannot restate them differently.
    ans = p["html"]["answer"]
    lead = re.sub(r"<[^>]+>", "", re.search(r'<p class="lead">(.*?)</p>', ans, re.S).group(1))
    span = re.search(r"(\d{4}-\d\d-\d\d) to\s+(\d{4}-\d\d-\d\d)", ans)
    return {
        "slug": p["slug"], "name": show(p["name"]), "country": p["country"],
        "lat": p["lat"], "lon": p["lon"],
        "bss": _r(p["bss"]), "bss_lo": _r(p["bss_lo"]), "bss_hi": _r(p["bss_hi"]),
        "rank": p["rank"], "rank_lo": _r(p["rank_lo"], 0), "rank_hi": _r(p["rank_hi"], 0),
        "n_cities": p["n_cities"], "n": p["n"], "base_rate": _r(p["base_rate"]),
        "station": p.get("station"), "station_km": p.get("prcp_km"),
        "first": span.group(1), "last": span.group(2),
        "lead": _show_text(" ".join(lead.split())),
        "bins": [{"said": _r(b["mean"]), "rained": _r(b["obs"]), "n": b["n"],
                  "lo": _r(b["lo"]), "hi": _r(b["hi"]), "sig": b["sig"]}
                 for b in p["pop_map"]],
    }


# ---------------------------------------------------------------------------
# The umbrella chapter
# ---------------------------------------------------------------------------
def _served_series() -> dict:
    """The exact (probability, event) days each served_world value curve was
    computed from, rebuilt the way decision_metrics.build_table builds them."""
    d = pd.read_parquet(PROCESSED / "cities_pop.parquet")
    d["local_date"] = pd.to_datetime(d.local_date)
    d = d.sort_values(["city", "local_date"])
    out = {}
    for city, g in d.groupby("city"):
        g = g.dropna(subset=["forecast_prob", "obs_precip_mm"])
        g = g.drop_duplicates(subset="local_date").sort_values("local_date")
        out[city] = (g.forecast_prob.to_numpy(float),
                     (g.obs_precip_mm.to_numpy(float) >= RAIN_THRESHOLD_MM).astype(float))
    return out


def umbrella_all() -> dict:
    """World median curve plus one curve set per city, on one shared grid.

    Besides the value curves, each city carries the plain day counts behind
    them - how many days you would have protected yourself and how many rainy
    days you would have been caught out - for the textbook rule (act when the
    forecast's chance is at or above your cost ratio). The counts are
    recomputed from the daily series and checked against the stored curve, so
    the page's "caught out 25 times" is the same arithmetic as the saving it
    plots.
    """
    d = pd.read_parquet(PROCESSED / "decision_value_curves.parquet")
    w = d[d.source == "served_world"].copy()
    # The page's score: the share of your bother that following the forecast
    # saves against the best no-forecast habit, (habit - follow) / habit.
    # Per day, habit = min(a, s) and perfect = a*s (src/metrics.py _value), so
    # this is V * (habit - perfect) / habit - V rescaled with no "perfect" end.
    hab = np.minimum(w.alpha, w.base_rate)
    w["save"] = w.v_calibrated * (hab - w.alpha * w.base_rate) / hab
    med = w.groupby("alpha")[["save"]].median().reset_index()
    grid = [_r(a, 2) for a in med.alpha]
    series = _served_series()
    per = {}
    for name, c in w.groupby("city"):
        c = c.sort_values("alpha")
        if [_r(a, 2) for a in c.alpha] != grid:
            raise SystemExit(f"{name}: decision curve is not on the shared alpha grid")
        p, e = series[name]
        n = len(p)
        check = value_curve(p, e)
        if n != int(c.n.iloc[0]) or not np.allclose(check.v_calibrated, c.v_calibrated,
                                                    equal_nan=True):
            raise SystemExit(f"{name}: rebuilt daily series does not reproduce its value curve")
        h, f, m, _ = contingency_rates(p, e, c.alpha.to_numpy(float))
        acted = [int(round(v)) for v in (h + f) * n]
        missed = [int(round(v)) for v in m * n]
        # The saving must be the page's own sum: (habit - acted - missed/a) / habit.
        a_ = c.alpha.to_numpy()
        habit = np.where(a_ * n <= e.sum(), n, e.sum() / a_)
        sums = (habit - (np.array(acted) + np.array(missed) / a_)) / habit
        if not np.allclose(sums, c.save, atol=0.01, equal_nan=True):
            raise SystemExit(f"{name}: day counts do not reproduce the saving curve")
        per[name] = {"save": [_r(v, 3) for v in c.save], "n": n, "rainy": int(e.sum()),
                     "acted": acted, "missed": missed}
    return {"world": {"alpha": grid, "save": [_r(v, 3) for v in med.save],
                      "n": int(w.city.nunique())},
            "cities": per}


# Where the slider opens, and the range it covers: getting caught at most 20x
# worse than protecting yourself (the page trims the grid to this).
UMB_START = 0.25
UMB_MIN_ALPHA = 0.05


def umbrella_stops(alpha: list[float]) -> list[int]:
    """Grid points the slider stops on: one per whole "N times worse", the
    point whose 1/a is closest to N (atlas.js builds the same list)."""
    best: dict[int, tuple[int, float]] = {}
    for i, a in enumerate(alpha):
        if a < UMB_MIN_ALPHA - 1e-9:
            continue
        k = round(1 / a)
        dist = abs(1 / a - k)
        if k not in best or dist < best[k][1]:
            best[k] = (i, dist)
    return sorted(v[0] for v in best.values())


# ---------------------------------------------------------------------------
# Who can check: the coverage map
# ---------------------------------------------------------------------------
def coverage() -> dict:
    c = pd.read_parquet(PROCESSED / "country_coverage.parquet")
    caps = pd.read_csv(PROCESSED / "country_capitals.csv", keep_default_na=False)
    caps = {r.country: (float(r.lat), float(r.lon)) for r in caps.itertuples()}
    out = []
    for r in c.itertuples():
        if r.country not in caps:
            continue
        why = str(r.why_missing or "")
        reason = ("no gauge nearby" if "no gauge in range" in why and "no active" in why
                  else "gauge record too broken" if "reconstructs" in why
                  else "record too short" if why else "")
        out.append([r.country, *caps[r.country], int(r.people_at_floor),
                    int(r.included), reason])
    inc = c.included > 0
    return {
        "countries": out, "floor": int(_const("src/probe_cities.py", "MIN_POPULATION")),
        "n_countries": int(len(c)), "n_covered": int(inc.sum()),
        "people_missing": int(c[~inc].people_at_floor.sum()),
        "people_total": int(c.people_at_floor.sum()),
    }


def tiers() -> list[list]:
    """The skill tiers (upper bound, label, tone, plain phrase) from their one home."""
    return [[None if hi == float("inf") else hi, label, tone, frag]
            for hi, label, frag, tone in _TIERS]


# ---------------------------------------------------------------------------
# Evidence: claims and the challenges they faced, league, methods, glossary
# ---------------------------------------------------------------------------
def _sig(row) -> dict:
    return {"est": _r(row.estimate), "lo": _r(row.ci_lo), "hi": _r(row.ci_hi),
            "p": _r(row.p_boot, 3), "n": int(row.n), "cities": int(row.n_cities)}


def evidence(idx: list[list]) -> dict:
    """Every figure comes from a processed table or a source constant; the
    status of each challenge uses the same rule as the report section it comes
    from (src/report.py sec_robust, sec_world, sec_served)."""
    claims = []

    # 1. Bucharest's overconfidence at both ends (sec_robust's rule).
    rb = pd.read_parquet(PROCESSED / "robustness.parquet")
    ch = []
    for r in rb.itertuples():
        held = r.top_bin_observed < r.top_bin_stated and r.bot_bin_observed > r.bot_bin_stated
        ch.append({"what": r.variant, "status": "held" if held else "flipped",
                   "detail": f"\u201c{r.top_bin_stated:.0%}\u201d rained {r.top_bin_observed:.0%} \u00b7 "
                             f"\u201c{r.bot_bin_stated:.1%}\u201d rained {r.bot_bin_observed:.1%} \u00b7 skill {r.bss:.2f}"})
    # robustness.parquet has no city column: the study was run for the
    # case-study city only (src/config.py CITIES), so the claim says so.
    claims.append({"id": "overconfident", "scope": "Bucharest only", "group": "case",
                   "case_city": "Bucharest",
                   "claim": "The forecast is overconfident at both ends: its surest \u201cyes\u201d and surest \u201cno\u201d are both too sure.",
                   "source": "robustness", "challenges": ch})

    # 2 and 3. The served number against the raw ensemble (sec_served).
    sg = pd.read_parquet(PROCESSED / "significance_headline.parquet")
    sg = sg[sg.model == "gfs_seamless"]

    def row(stat, lead=1, mode="prorata"):
        return sg[(sg.statistic == stat) & (sg.lead_days == lead) & (sg.boundary_mode == mode)].iloc[0]

    def st_sign(r, want_neg):
        if r.ci_lo <= 0 <= r.ci_hi:
            return "unresolved"
        return "held" if (r.estimate < 0) == want_neg else "reversed"

    b1 = row("brier_diff")
    ch = [{"what": "baseline: day total \u2265 threshold, lead 1", "status": st_sign(b1, True), "sig": _sig(b1)},
          {"what": "boundary rule: snap instead of pro-rata",
           "status": st_sign(row("brier_diff", 1, "snap"), True), "sig": _sig(row("brier_diff", 1, "snap"))},
          {"what": "event: rain at any point in the day",
           "status": st_sign(row("brier_diff_anystep"), True), "sig": _sig(row("brier_diff_anystep"))}]
    tr = pd.read_parquet(PROCESSED / "truth_rescore.parquet")
    ch.append({"what": "truth: a different gauge nearby",
               "status": "held" if not tr.sign_flips.any() else "weakened",
               "detail": f"sign flips at {int(tr.sign_flips.sum())} of {len(tr)} alternative gauges "
                         f"in {tr.city.nunique()} cities"})
    claims.append({"id": "served-accuracy", "group": "all", "scope": f"{int(b1.n_cities)} cities",
                   "claim": "The chance of rain weather apps show is more accurate than the raw ensemble it could have been built from.",
                   "source": "significance_headline",
                   "stat": "Brier difference, served \u2212 ensemble (lower = served better)",
                   "challenges": ch})
    ch = [{"what": f"lead {d} day{'s' if d > 1 else ''}", "status": st_sign(row("value_diff_a05", d), True),
           "sig": _sig(row("value_diff_a05", d))} for d in sorted(sg.lead_days.unique())]
    ch.insert(1, {"what": "boundary rule: snap", "status": st_sign(row("value_diff_a05", 1, "snap"), True),
                  "sig": _sig(row("value_diff_a05", 1, "snap"))})
    ch.append({"what": "a costly action instead (1 in 2)", "status": "limit",
               "sig": _sig(row("value_diff_a50")),
               "detail": "the harm is confined to cheap actions: here the served number is ahead"})
    claims.append({"id": "cheap-harm", "group": "all", "scope": f"{int(b1.n_cities)} cities",
                   "claim": "For someone acting on cheap precautions (1 in 20), the chance of rain weather apps show is worse than the raw ensemble.",
                   "source": "significance_headline",
                   "stat": "Value difference, served \u2212 ensemble (negative = served worse)",
                   "challenges": ch})

    # 4. Low forecasts under-state rain, worldwide (sec_world wb_html).
    wb = pd.read_parquet(PROCESSED / "cities_wet_bias.parquet")
    out = wb[~wb.eu]
    claims.append({"id": "low-end", "group": "all", "scope": f"{len(wb)} cities",
                   "claim": "Low rain chances are too low: on \u201cunlikely\u201d days it rains more often than stated.",
                   "source": "cities_wet_bias", "challenges": [
                       {"what": "all cities", "status": "held",
                        "detail": f"{int((wb.low_gap > 0).sum())} of {len(wb)} \u00b7 {int((wb.low_lo > 0).sum())} with the interval above zero"},
                       {"what": "outside Europe (a different model answers)", "status": "held",
                        "detail": f"{int((out.low_gap > 0).sum())} of {len(out)} \u00b7 {int((out.low_lo > 0).sum())} significant"},
                       {"what": "the other end: high chances too high", "status": "held",
                        "detail": f"{int((wb.high_gap < 0).sum())} of {len(wb)} \u00b7 {int((wb.high_hi < 0).sum())} significant"}]})

    # 5. What drives skill: the capitals result, replicated (sec_world rep_html).
    cd = pd.read_parquet(PROCESSED / "capitals_drivers.parquet")
    wd = pd.read_parquet(PROCESSED / "cities_drivers.parquet")
    j = cd.merge(wd, on=["target", "driver"], suffixes=("_c", "_w"))
    pretty = {"base_rate": "how often it rains", "continentality_c": "continentality",
              "prcp_km": "gauge distance"}
    ch = []
    for r in j.itertuples():
        sc, sw = r.p_perm_c < 0.05, r.p_perm_w < 0.05
        ch.append({"what": f"{r.target} vs {pretty.get(r.driver, r.driver)}",
                   "status": "held" if sc == sw else ("new" if sw else "failed"),
                   "detail": f"{int(r.n_c)} capitals r {r.corr_c:+.2f} (p {r.p_perm_c:.3f}) \u2192 "
                             f"{int(r.n_w)} cities r {r.corr_w:+.2f} (p {r.p_perm_w:.3f})"})
    claims.append({"id": "drivers", "group": "all",
                   "scope": f"{int(j.n_c.iloc[0])} \u2192 {int(j.n_w.iloc[0])} cities",
                   "claim": "What the capitals suggested drives forecast skill, re-tested on every city.",
                   "source": "capitals_drivers, cities_drivers", "challenges": ch})

    # League table: every city, the columns a professional sorts by.
    m = pd.read_parquet(PROCESSED / "cities_metrics.parquet").set_index("city")
    league = []
    for slug, name, cc, *_ in idx:
        r = m.loc[name]
        league.append([slug, show(name), cc, _r(r.bss, 3), _r(r.bss_lo, 3), _r(r.bss_hi, 3),
                       _r(r.rank_lo, 0), _r(r.rank_hi, 0), int(r.n), _r(r.ess, 0),
                       _r(r.base_rate, 3), _r(r.reliability, 4), _r(r.resolution, 4),
                       _r(r.prcp_km, 1)])
    league.sort(key=lambda x: (-x[3], x[0]))
    methods = [
        ["Rain event", f"day total \u2265 {RAIN_THRESHOLD_MM:g} mm at the gauge; 0.1 and 1.0 mm as checks"],
        ["Gauge", f"within {_const('src/probe_capitals.py', 'MAX_STATION_KM'):g} km and "
                  f"{_const('src/probe_capitals.py', 'MAX_ELEV_DIFF_M'):g} m of the forecast point's elevation"],
        ["Cities", f"population \u2265 {int(_const('src/probe_cities.py', 'MIN_POPULATION')):,}; "
                   "cities sharing a gauge counted once"],
        ["Score", "Brier skill score against the city's own rain rate (climatology)"],
        ["Intervals", f"block bootstrap, {int(_const('src/config.py', 'BOOTSTRAP_BLOCK_DAYS'))}-day blocks, "
                      f"{int(_const('src/capitals.py', 'N_BOOT_CITY'))} replicates per city"],
        ["Rank range", f"{int(_const('src/league.py', 'N_BOOT_LEAGUE'))} joint resamples of every city's score"],
        ["Served vs ensemble", f"{int(_const('src/config.py', 'GEFS_MEMBERS'))} GEFS members, "
                               f"{int(b1.n):,} city-days, {int(b1.n_boot)} bootstrap draws in "
                               f"{int(b1.block_days)}-day blocks"],
        ["Reliability bins", f"{int(_const('src/config.py', 'N_PROB_BINS'))} equal-count bins"],
    ]
    return {"claims": claims,
            "league_cols": ["slug", "name", "cc", "bss", "bss_lo", "bss_hi", "rank_lo", "rank_hi",
                            "n", "ess", "base_rate", "reliability", "resolution", "gauge_km"],
            "league": league, "methods": methods,
            "glossary": [[e["id"], e["term"], e.get("full", ""), e["plain"], e["care"]]
                         for e in GLOSSARY + SKY_GLOSSARY]}


# ---------------------------------------------------------------------------
# The sky behind the stage
# ---------------------------------------------------------------------------
STAR_SATURATION = 2.8


def _bv_rgb(bv: np.ndarray) -> np.ndarray:
    """B-V colour index -> sRGB, via Ballesteros' temperature and a blackbody
    fit. The true colours are subtle pastels that read as white on a screen,
    so they are exaggerated on purpose: normalised to full brightness, then
    saturation raised 2.8x around their luminance. The order of colours (blue
    O/B stars to red M giants) stays the real one; only the strength is art."""
    bv = np.clip(np.nan_to_num(bv, nan=0.6), -0.4, 2.0)
    t = 4600 * (1 / (0.92 * bv + 1.7) + 1 / (0.92 * bv + 0.62)) / 100
    r = np.where(t <= 66, 255, 329.7 * (t - 60).clip(1e-6) ** -0.1332)
    g = np.where(t <= 66, 99.47 * np.log(t) - 161.1, 288.1 * (t - 60).clip(1e-6) ** -0.0755)
    b = np.where(t >= 66, 255, np.where(t <= 19, 0, 138.5 * np.log((t - 10).clip(1e-6)) - 305.0))
    rgb = np.stack([r, g, b], 1).clip(1, 255)
    rgb = rgb / rgb.max(1, keepdims=True) * 255
    lum = (rgb @ np.array([0.2126, 0.7152, 0.0722]))[:, None]
    return (lum + (rgb - lum) * STAR_SATURATION).clip(0, 255)


def stars(as_of: str) -> bytes:
    """The real sky: every catalogue star to magnitude 7.5 (src/atlas_prep.py).

    Right ascension is turned into the longitude the star stands over at
    00:00 UTC on the data date (Greenwich mean sidereal time), so the sky
    behind the globe is the sky that was really there. Packed as 8 bytes a
    star: int16 lon/pi, int16 lat/(pi/2), uint8 r, g, b, magnitude.
    """
    d = pd.read_parquet(PROCESSED / "stars.parquet")
    ra, dec = d.ra.to_numpy(float), d.dec.to_numpy(float)
    mag, ci = d.mag.to_numpy(float), d.ci.to_numpy(float)
    jd = date.fromisoformat(as_of).toordinal() + 1721424.5       # 00:00 UTC
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360
    lon = (ra * 15 - gmst + 180) % 360 - 180
    q = np.empty(len(d), dtype=[("lon", "<i2"), ("lat", "<i2"), ("rgb", "u1", 3), ("mag", "u1")])
    q["lon"] = np.round(lon / 180 * 32767)
    q["lat"] = np.round(dec / 90 * 32767)
    q["rgb"] = np.round(_bv_rgb(ci))
    q["mag"] = np.round((mag.clip(-1.5, 7.5) + 1.5) / 9.0 * 255)
    return q.tobytes()


# ---------------------------------------------------------------------------
def build(payloads: list[dict], default_slug: str) -> dict:
    idx, umb = index(payloads), umbrella_all()
    cities, lasts = {}, []
    for p in payloads:
        h = hero(p)
        cities[p["slug"]] = {"hero": h, "umbrella": umb["cities"].get(p["name"])}
        lasts.append(h["last"])
    as_of = max(lasts)
    site = {"cities": [[r[0], show(r[1])] + r[2:] for r in idx], "default": default_slug,
            "umbrella_world": umb["world"], "coverage": coverage(), "event": EVENT,
            "as_of": as_of, "tiers": tiers(), "evidence": evidence(idx)}
    return {"site": site, "cities": cities, "stars": stars(as_of)}
