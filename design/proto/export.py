"""Export the real numbers the Atlas Noir prototype shows, and stage its assets.

Design-branch prototype only (plan 2026-09-24-site-redesign-atlas-noir, Task
0.2). The prototype is throwaway, but its numbers are not: every value it
displays is read here from the built site payloads or the processed tables,
never typed in, so a screenshot of it can be judged as the real page would be.

Reads:   dist/data/cities-index.*.json, dist/data/cities/<slug>.*.json
         data/processed/decision_value_curves.parquet
         data/processed/country_coverage.parquet
         data/raw/cities15000.txt            (capital coordinates only)
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
from pathlib import Path

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
    """The site's skill tiers (upper bound, label, tone) from their one home."""
    sys.path.insert(0, str(ROOT / "src"))
    from city_report import _TIERS
    return [[None if hi == float("inf") else hi, label, tone]
            for hi, label, _frag, tone in _TIERS]


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
    print(f"wrote {PROTO / 'data.json'} and {len(idx)} city files "
          f"({no_curve} without decision curves), "
          f"{len(data['coverage']['countries'])} countries")


if __name__ == "__main__":
    main()
