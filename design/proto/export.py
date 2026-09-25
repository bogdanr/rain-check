"""Export the real numbers the Atlas Noir prototype shows, and stage its assets.

Design-branch prototype only (plan 2026-09-24-site-redesign-atlas-noir, Task
0.2). The prototype is throwaway, but its numbers are not: every value it
displays is read here from the built site payloads or the processed tables,
never typed in, so a screenshot of it can be judged as the real page would be.

Reads:   dist/data/cities-index.*.json, dist/data/cities/<slug>.*.json
         data/processed/decision_value_curves.parquet
         data/processed/country_coverage.parquet
         data/raw/cities15000.txt            (capital coordinates only)
         data/raw/hygdata_v41.csv            (star catalogue, HYG v4.1, CC BY-SA 4.0)
Writes:  design/proto/data.json              (index, world numbers, tiers)
         design/proto/cities/<slug>.json     (one per city, loaded on demand)
         design/proto/assets/                (all git-ignored)

Usage:   .venv/bin/python design/proto/export.py
"""

from __future__ import annotations

import csv
import glob
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROTO = ROOT / "design" / "proto"
PROCESSED = ROOT / "data" / "processed"
FONTS = ROOT.parent.parent / "Work" / "Specure" / "RTR" / "slides" / "node_modules"
DEFAULT_CITY = "bucharest"


def _one(pattern: str) -> Path:
    hits = sorted(glob.glob(str(ROOT / pattern)))
    if len(hits) != 1:
        raise SystemExit(f"expected one match for {pattern}, found {len(hits)}"
                         " - build the site first (src/report.py)")
    return Path(hits[0])


def _r(v, k=4):
    return None if v is None or v != v else round(float(v), k)


def cities() -> list[list]:
    ix = json.loads(_one("dist/data/cities-index.*.json").read_text())
    col = {c: i for i, c in enumerate(ix["cols"])}
    return [[r[col["slug"]], r[col["name"]], r[col["country"]],
             r[col["lat"]], r[col["lon"]], r[col["bss"]]] for r in ix["rows"]]


def hero(slug: str) -> dict:
    p = json.loads(_one(f"dist/data/cities/{slug}.*.json").read_text())
    # The verdict sentence and the record span are the site's own words, taken
    # from the rendered answer so the prototype cannot restate them differently.
    ans = p["html"]["answer"]
    lead = re.sub(r"<[^>]+>", "", re.search(r'<p class="lead">(.*?)</p>', ans, re.S).group(1))
    span = re.search(r"(\d{4}-\d\d-\d\d) to\s+(\d{4}-\d\d-\d\d)", ans)
    return {
        "slug": slug, "name": p["name"], "country": p["country"],
        "lat": p["lat"], "lon": p["lon"],
        "bss": _r(p["bss"]), "bss_lo": _r(p["bss_lo"]), "bss_hi": _r(p["bss_hi"]),
        "rank": p["rank"], "rank_lo": _r(p["rank_lo"], 0), "rank_hi": _r(p["rank_hi"], 0),
        "n_cities": p["n_cities"], "n": p["n"], "base_rate": _r(p["base_rate"]),
        "station": p.get("station"), "station_km": p.get("prcp_km"),
        "first": span.group(1), "last": span.group(2),
        "lead": " ".join(lead.split()),
        "bins": [{"said": _r(b["mean"]), "rained": _r(b["obs"]), "n": b["n"],
                  "lo": _r(b["lo"]), "hi": _r(b["hi"]), "sig": b["sig"]}
                 for b in p["pop_map"]],
    }


def umbrella_all() -> dict:
    """World median curve plus one curve set per city, on one shared grid."""
    d = pd.read_parquet(PROCESSED / "decision_value_curves.parquet")
    w = d[d.source == "served_world"]
    med = w.groupby("alpha")[["v_calibrated"]].median().reset_index()
    grid = [_r(a, 2) for a in med.alpha]
    per = {}
    for name, c in w.groupby("city"):
        c = c.sort_values("alpha")
        if [_r(a, 2) for a in c.alpha] != grid:
            raise SystemExit(f"{name}: decision curve is not on the shared alpha grid")
        per[name] = {"follow": [_r(v, 3) for v in c.v_calibrated],
                     "best": [_r(v, 3) for v in c.v_envelope],
                     "trigger": [_r(v, 2) for v in c.envelope_threshold],
                     "n": int(c.n.iloc[0])}
    return {"world": {"alpha": grid, "follow": [_r(v, 3) for v in med.v_calibrated],
                      "n": int(w.city.nunique())},
            "cities": per}


def coverage() -> dict:
    c = pd.read_parquet(PROCESSED / "country_coverage.parquet")
    # One point per country: its capital from GeoNames (feature code PPLC).
    caps: dict[str, tuple[float, float]] = {}
    with open(ROOT / "data" / "raw" / "cities15000.txt", encoding="utf-8") as fh:
        for row in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            if row[7] == "PPLC":
                caps.setdefault(row[8], (float(row[4]), float(row[5])))
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
    # The population floor behind people_at_floor, read from its one definition.
    floor = int(re.search(r"^MIN_POPULATION = ([\d_]+)",
                          (ROOT / "src" / "probe_cities.py").read_text(), re.M)
                .group(1).replace("_", ""))
    return {
        "countries": out, "floor": floor,
        "n_countries": int(len(c)), "n_covered": int(inc.sum()),
        "people_missing": int(c[~inc].people_at_floor.sum()),
        "people_total": int(c.people_at_floor.sum()),
    }


def tiers() -> list[list]:
    """The site's skill tiers (upper bound, label, tone, plain phrase) from their one home."""
    sys.path.insert(0, str(ROOT / "src"))
    from city_report import _TIERS
    return [[None if hi == float("inf") else hi, label, tone, frag]
            for hi, label, frag, tone in _TIERS]


STAR_MAG_LIMIT = 7.5     # past the naked-eye limit, so the Milky Way shows by density


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


STAR_SATURATION = 2.8


def stars(as_of: str) -> int:
    """The real sky for the stage: every catalogue star to STAR_MAG_LIMIT.

    Right ascension is turned into the longitude the star stands over at
    00:00 UTC on the data date (Greenwich mean sidereal time), so the sky
    behind the globe is the sky that was really there. Packed as 8 bytes a
    star: int16 lon/pi, int16 lat/(pi/2), uint8 r, g, b, magnitude.
    """
    d = pd.read_csv(ROOT / "data" / "raw" / "hygdata_v41.csv", usecols=["id", "ra", "dec", "mag", "ci"])
    d = d[(d.id > 0) & (d.mag <= STAR_MAG_LIMIT)].sort_values("mag")
    jd = date.fromisoformat(as_of).toordinal() + 1721424.5       # 00:00 UTC
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360
    lon = (d.ra.to_numpy() * 15 - gmst + 180) % 360 - 180
    q = np.empty(len(d), dtype=[("lon", "<i2"), ("lat", "<i2"), ("rgb", "u1", 3), ("mag", "u1")])
    q["lon"] = np.round(lon / 180 * 32767)
    q["lat"] = np.round(d.dec.to_numpy() / 90 * 32767)
    q["rgb"] = np.round(_bv_rgb(d.ci.to_numpy()))
    q["mag"] = np.round((d.mag.to_numpy().clip(-1.5, 7.5) + 1.5) / 9.0 * 255)
    (PROTO / "assets" / "stars.bin").write_bytes(q.tobytes())
    return len(d)


def stage_assets() -> None:
    a = PROTO / "assets"
    a.mkdir(parents=True, exist_ok=True)
    for f in ("relief-elev.webp", "relief-biome.webp", "land.geo.json"):
        shutil.copy2(ROOT / "src" / "web" / "geo" / f, a / f)
    for rel in ("@fontsource-variable/inter/files/inter-latin-wght-normal.woff2",
                "@fontsource/jetbrains-mono/files/jetbrains-mono-latin-400-normal.woff2",
                "@fontsource/jetbrains-mono/files/jetbrains-mono-latin-700-normal.woff2"):
        src = FONTS / rel
        if src.exists():
            shutil.copy2(src, a / src.name)
        else:
            print(f"  font missing, prototype falls back to system fonts: {src.name}")


def main() -> None:
    thr = re.search(r"^RAIN_THRESHOLD_MM = ([\d.]+)",
                    (ROOT / "src" / "config.py").read_text(), re.M).group(1)
    idx, umb = cities(), umbrella_all()
    out = PROTO / "cities"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    lasts, no_curve = [], 0
    for slug, *_ in idx:
        h = hero(slug)
        u = umb["cities"].get(h["name"])
        no_curve += u is None
        (out / f"{slug}.json").write_text(
            json.dumps({"hero": h, "umbrella": u}, separators=(",", ":")))
        lasts.append(h["last"])
    data = {"cities": idx, "default": DEFAULT_CITY,
            "umbrella_world": umb["world"], "coverage": coverage(),
            "event": f"day total \u2265 {thr} mm at the gauge",
            "as_of": max(lasts), "tiers": tiers()}
    (PROTO / "data.json").write_text(json.dumps(data, separators=(",", ":")))
    stage_assets()
    n_stars = stars(data["as_of"])
    print(f"wrote {PROTO / 'data.json'} and {len(idx)} city files "
          f"({no_curve} without decision curves), "
          f"{len(data['coverage']['countries'])} countries, {n_stars} stars")


if __name__ == "__main__":
    main()
