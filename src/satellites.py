"""Bake the weather-satellite roster for the Atlas globe. Run manually, not in CI.

    python src/satellites.py          # writes data/processed/satellites.json

Twelve real satellites, chosen to show the two orbit families every forecast
in this study is built on, not the whole fleet (about 70 weather objects are
tracked, 30-40 of them working):

    geostationary   six, one per stretch of the equator: they photograph a
                    whole disc every 10-15 min. Their infrared is what the
                    globe's cloud layer is made of, and what CHIRPS (one of
                    the audit's truth sources) reads rain from.
    polar           five sun-synchronous sounders in the three orbit planes
                    (early morning, mid-morning, afternoon) that the forecast
                    models assimilate most heavily, plus GPM, whose rain radar
                    the globe's rain layer (IMERG) is calibrated to.

The file holds the roster (who, what, why it matters to rain) and one set of
CelesTrak mean orbital elements per satellite (OMM JSON). The page draws from
these when it cannot fetch fresher ones from CelesTrak itself, so the site
still builds and renders offline; sats.js stops drawing a position once the
elements are too old to trust and keeps only the orbit.

Positions are never part of the audit.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed" / "satellites.json"

CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php"
# One group request covers every weather satellite but GPM, which CelesTrak
# files under science; it is asked for by catalogue number.
URLS = [f"{CELESTRAK}?GROUP=weather&FORMAT=json", f"{CELESTRAK}?CATNR=39574&FORMAT=json"]

# kind: geo | sso (sun-synchronous polar) | leo (inclined, not sun-synchronous)
# node: for sso, the plane's local time at the equator crossing, in words.
ROSTER = [
    # --- geostationary ring, west to east --------------------------------
    {"id": 51850, "name": "GOES-18", "role": "GOES-West", "op": "NOAA (USA)", "kind": "geo",
     "what": "Imager (ABI) and lightning mapper (GLM) over the eastern Pacific and the Americas",
     "rain": "Full-disc infrared every 10 min: the cloud tops forecasters watch storms grow in."},
    {"id": 60133, "name": "GOES-19", "role": "GOES-East", "op": "NOAA (USA)", "kind": "geo",
     "what": "Imager (ABI) and lightning mapper (GLM) over the Americas and the Atlantic",
     "rain": "GOES-East since 2025. With the rest of this ring, its infrared makes up the world cloud mosaic drawn as this globe's clouds."},
    {"id": 54743, "name": "Meteosat-12", "role": "MTG-I1", "op": "EUMETSAT (Europe)", "kind": "geo",
     "what": "Flexible Combined Imager and the Lightning Imager, over Europe and Africa",
     "rain": "Its Lightning Imager is the source of the lightning layer on this globe."},
    {"id": 58990, "name": "INSAT-3DS", "role": "Indian Ocean", "op": "ISRO / IMD (India)", "kind": "geo",
     "what": "Imager and sounder over India and the Indian Ocean",
     "rain": "Watches the monsoon and the Bay of Bengal cyclones."},
    {"id": 41882, "name": "Fengyun-4A", "role": "FY-4A", "op": "CMA (China)", "kind": "geo",
     "what": "Imager (AGRI), infrared sounder and lightning mapper over East Asia",
     "rain": "Part of the ring of imagers the world cloud mosaic is stitched from."},
    {"id": 41836, "name": "Himawari-9", "role": "JMA", "op": "JMA (Japan)", "kind": "geo",
     "what": "Advanced Himawari Imager over East Asia, Australia and the western Pacific",
     "rain": "Full disc every 10 min; JMA tracks typhoons with it."},
    # --- polar, sun-synchronous, by orbit plane --------------------------
    {"id": 49008, "name": "Fengyun-3E", "role": "Early morning", "op": "CMA (China)", "kind": "sso",
     "node": "early morning",
     "what": "Sounders and imager in the dawn-dusk plane, the first civil weather satellite there",
     "rain": "Fills the early-morning gap the models used to have between the other two planes."},
    {"id": 38771, "name": "Metop-B", "role": "Mid-morning", "op": "EUMETSAT (Europe)", "kind": "sso",
     "node": "mid-morning",
     "what": "Infrared sounder (IASI), microwave sounders and scatterometer",
     "rain": "Its soundings are among the observations that do the most for global forecast skill."},
    {"id": 43689, "name": "Metop-C", "role": "Mid-morning", "op": "EUMETSAT (Europe)", "kind": "sso",
     "node": "mid-morning",
     "what": "Infrared sounder (IASI), microwave sounders and scatterometer",
     "rain": "Shares Metop-B's plane, half an orbit behind it, for twice the coverage."},
    {"id": 43013, "name": "NOAA-20", "role": "Afternoon", "op": "NOAA / NASA (USA)", "kind": "sso",
     "node": "early afternoon",
     "what": "Microwave (ATMS) and infrared (CrIS) sounders, VIIRS imager",
     "rain": "One of the two afternoon sounders every global model assimilates."},
    {"id": 54234, "name": "NOAA-21", "role": "Afternoon", "op": "NOAA / NASA (USA)", "kind": "sso",
     "node": "early afternoon",
     "what": "Microwave (ATMS) and infrared (CrIS) sounders, VIIRS imager",
     "rain": "Flies NOAA-20's plane half an orbit apart, so each spot is seen twice as often."},
    # --- inclined: the rain radar ----------------------------------------
    {"id": 39574, "name": "GPM Core", "role": "Rain radar", "op": "NASA / JAXA", "kind": "leo",
     "what": "Dual-frequency precipitation radar (DPR) and microwave imager (GMI)",
     "rain": "Measures rain itself, from 65\u00b0S to 65\u00b0N. The rain layer on this globe (IMERG) is calibrated to it."},
]

# The OMM fields sats.js propagates from; the rest are dropped.
KEEP = ("OBJECT_NAME", "NORAD_CAT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
        "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY", "MEAN_MOTION_DOT")


def fetch(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "rain-check (satellites.py, manual refresh)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main() -> None:
    got: dict[int, dict] = {}
    for u in URLS:
        for o in fetch(u):
            got[int(o["NORAD_CAT_ID"])] = {k: o[k] for k in KEEP}
    missing = [s["name"] for s in ROSTER if s["id"] not in got]
    if missing:
        raise SystemExit(f"satellites: CelesTrak returned no elements for {', '.join(missing)} - "
                         "check the roster against CelesTrak / WMO OSCAR")
    out = {
        "read": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "source": "CelesTrak GP data (OMM JSON)",
        "urls": URLS,
        "sats": [dict(s, omm=got[s["id"]]) for s in ROSTER],
    }
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    oldest = min(got[s["id"]]["EPOCH"] for s in ROSTER)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(ROSTER)} satellites, oldest epoch {oldest}")


if __name__ == "__main__":
    main()
