"""Build the Atlas Noir site into dist/ - the published face of the study.

    python src/atlas.py                                   # local preview at /
    python src/atlas.py --base /rain-check/ \\
        --origin https://bogdan.nimblex.net               # the Pages build

Ported from the design prototype (design/proto/, plan
2026-09-24-site-redesign-atlas-noir). The numbers come from src/atlas_data.py,
which reads the same city payloads and processed tables the old report used;
this file only lays out the tree:

    index.html                  the default city (Bucharest)
    city/<slug>/index.html      one page per other city, same URLs as the old
                                report so existing links and index entries hold
    cities/index.html           a plain directory of every city page
    data/data.<hash>.json       the shared file; data/cities/<slug>.<hash>.json
    assets/...                  hashed CSS, JS, fonts, textures, sky
    sitemap.xml, 404.html, .nojekyll

Every page is complete without JavaScript for what a crawler or a link
preview reads: title, description, canonical URL, Open Graph, JSON-LD and the
verdict card's text are written in here from the city's own numbers. The
script then takes over and renders the same values live.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import atlas_data
import city_report
from config import API_FORECAST
from sitebuild import DIST, WEB, Site, ship

ATLAS = WEB / "atlas"
SITE_NAME = "rain check"
AUTHOR = {"name": "Bogdan R\u0103dulescu", "url": "https://bogdan.nimblex.net/",
          "id": "https://bogdan.nimblex.net/#person"}
REPO = "https://github.com/bogdanr/rain-check"

# The live sky's public sources. Each sends Access-Control-Allow-Origin: *, so
# the reader's browser fetches them directly: no job of ours republishes
# imagery, and none of it ever enters the audit (sky.js documents the reading).
EUMETSAT = "https://view.eumetsat.int/geoserver/mumi"
# Meteosat Third Generation, 0 degree: the Lightning Imager's accumulated flash
# area, a 5-minute product over the disk's +-70 degree box (lon and lat).
MTG = "https://view.eumetsat.int/geoserver/mtg_fd"
BOLT_BOX = [-70, -70, 70, 70]
GIBS = "https://gibs.earthdata.nasa.gov"
IMERG = "IMERG_Precipitation_Rate_30min"
WX = {
    # The layer's own capabilities: small, and its time dimension names the newest frame.
    "irCaps": f"{EUMETSAT}/worldcloudmap_ir108/ows?service=WMS&request=GetCapabilities&version=1.3.0",
    "irMap": (f"{EUMETSAT}/wms?service=WMS&version=1.3.0&request=GetMap&layers=mumi:worldcloudmap_ir108"
              "&styles=&crs=CRS:84&bbox=-180,-90,180,90&format=image/jpeg"),
    "rainDomain": f"{GIBS}/wmts/epsg4326/best/1.0.0/{IMERG}/default/2km/all/{{range}}.xml",
    "rainMap": (f"{GIBS}/wms/epsg4326/best/wms.cgi?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS={IMERG}"
                "&CRS=EPSG:4326&BBOX=-90,-180,90,180&FORMAT=image/png&TRANSPARENT=true"),
    # The layer's own capabilities (7 KB, one time dimension), then 5-minute frames.
    "boltCaps": f"{MTG}/li_afa/ows?service=WMS&request=GetCapabilities&version=1.3.0",
    "boltMap": (f"{MTG}/wms?service=WMS&version=1.3.0&request=GetMap&layers=li_afa&styles="
                f"&crs=CRS:84&bbox={','.join(map(str, BOLT_BOX))}&format=image/png&transparent=true"),
    "boltBox": BOLT_BOX,
    # The same satellite's 10-minute IR frame and H SAF h40b rain, same box:
    # laid over the world mosaic and IMERG inside the disk (sky.js).
    "mtgBox": BOLT_BOX,
    "mtgIrCaps": f"{MTG}/ir105_hrfi/ows?service=WMS&request=GetCapabilities&version=1.3.0",
    "mtgIrMap": (f"{MTG}/wms?service=WMS&version=1.3.0&request=GetMap&layers=ir105_hrfi&styles="
                 f"&crs=CRS:84&bbox={','.join(map(str, BOLT_BOX))}&format=image/png&transparent=true"),
    "mtgRainMap": (f"{MTG}/wms?service=WMS&version=1.3.0&request=GetMap&layers=h40b&styles="
                   f"&crs=CRS:84&bbox={','.join(map(str, BOLT_BOX))}&format=image/png&transparent=true"),
}

# Weather satellites on the globe. The roster and a baked set of elements come
# from data/processed/satellites.json (src/satellites.py, run by hand); the
# reader's browser asks CelesTrak for fresher elements (CORS-open), at most
# once per two hours, which is how often CelesTrak updates them.
SATELLITES = Path(__file__).resolve().parent.parent / "data" / "processed" / "satellites.json"
SATS_LIVE = ["https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=json",
             "https://celestrak.org/NORAD/elements/gp.php?CATNR=39574&FORMAT=json"]

# The prototype's grade words and plain phrases (atlas.js GRADE / SAY), so the
# pre-rendered card reads exactly as the script will render it.
GRADE = ["Fails", "Poor", "Fair", "Good", "Excellent"]
SAY = [
    "Worse than just quoting the usual chance of rain. Don\u2019t rely on it.",
    "Only slightly better than quoting the usual chance of rain.",
    "Clearly better than the usual chance of rain, but often off.",
    "Reliable enough to plan around on most days.",
    "Reliable. One of the strongest forecasts in this audit.",
]


def esc(s) -> str:
    return escape(str(s), quote=True)


def js_num(v) -> str:
    """A number the way JavaScript prints it: 21.0 -> "21", 1.1 -> "1.1"."""
    if v is None:
        return "\u2014"
    f = float(v)
    return str(int(f)) if f.is_integer() else repr(f)


def tier_index(tiers: list, v: float) -> int:
    for i, t in enumerate(tiers):
        if t[0] is None or v < t[0]:
            return i
    return len(tiers) - 1


def page_title(name: str) -> str:
    return f"How good is the {name} weather forecast? \u00b7 {SITE_NAME}"


# ---------------------------------------------------------------------------
# Pre-rendered text: the values atlas.js writes into [data-k] / [data-count]
# ---------------------------------------------------------------------------
def values(site: dict, city: dict) -> tuple[dict, dict, int]:
    h, C = city["hero"], site["coverage"]
    ti = tier_index(site["tiers"], h["bss"])
    minus = lambda s: s.replace("-", "\u2212", 1)
    k = {
        "name": h["name"], "country": h["country"], "n_cities": h["n_cities"], "lead": h["lead"],
        "ci": "not available" if h["bss_lo"] is None
              else minus("%.2f" % h["bss_lo"]) + " \u2013 " + minus("%.2f" % h["bss_hi"]),
        "tier": site["tiers"][ti][1], "grade": GRADE[ti], "say": SAY[ti],
        "rank": h["rank"], "rank_range": f"{js_num(h['rank_lo'])}\u2013{js_num(h['rank_hi'])}",
        "n": f"{h['n']:,} days", "base_pct": f"{h['base_rate'] * 100:.0f}%",
        "station": re.sub(r"\b\w", lambda m: m.group().upper(), (h["station"] or "").lower()) or "\u2014",
        "station_km": js_num(h["station_km"]),
        "span": f"{h['first']} \u2192 {h['last']}",
        "umb_n": f"{city['umbrella']['n']:,}" if city.get("umbrella") else "\u2014",
        "event": site["event"], "as_of": site["as_of"], "n_countries": C["n_countries"],
        "floor": f"{js_num(C['floor'] / 1000)}k", "umb_world": site["umbrella_world"]["n"],
    }
    counts = {"bss": f"{h['bss']:.2f}", "covered": f"{C['n_covered']}",
              "missing_bn": f"{C['people_missing'] / 1e9:.2f} bn"}
    return k, counts, ti


def prerender(html: str, k: dict, counts: dict, ti: int) -> str:
    def fill(m):
        key = m.group(3)
        return m.group(1) + esc(k[key]) + m.group(4) if key in k else m.group(0)
    html = re.sub(r'(<(\w+)\b[^>]*\bdata-k="(\w+)"[^>]*>)(</\2>)', fill, html)

    def count(m):
        key = m.group(3)
        return m.group(1) + esc(counts[key]) + m.group(4) if key in counts else m.group(0)
    html = re.sub(r'(<(\w+)\b[^>]*\bdata-count="(\w+)"[^>]*>)(</\2>)', count, html)
    return html.replace('class="card hero-card rise"', f'class="card hero-card rise t{ti}"')


# ---------------------------------------------------------------------------
# Search and link previews
# ---------------------------------------------------------------------------
def description(k: dict) -> str:
    return (f"{k['lead']} Graded {k['grade'].lower()} (skill {k['_bss']}), ranked {k['rank']} of "
            f"{k['n_cities']} cities, checked against {k['n']} of rain-gauge records.")


def head(k: dict, url: str, home: str, og: dict, as_of: str, first: str,
         is_home: bool) -> str:
    title, desc = page_title(k["name"]), description(k)
    person = {"@type": "Person", "@id": AUTHOR["id"], "name": AUTHOR["name"], "url": AUTHOR["url"]}
    graph = [
        {"@type": "WebSite", "@id": home + "#website", "url": home, "name": SITE_NAME,
         "description": "An audit of rain forecasts against rain gauges, city by city.",
         "inLanguage": "en", "publisher": {"@id": AUTHOR["id"]}},
        {"@type": "WebPage", "@id": url + "#webpage", "url": url, "name": title,
         "description": desc, "inLanguage": "en", "isPartOf": {"@id": home + "#website"},
         "about": {"@type": "Place", "name": k["name"]},
         "primaryImageOfPage": {"@type": "ImageObject", "url": og["url"],
                                "width": og["w"], "height": og["h"]},
         "author": {"@id": AUTHOR["id"]}, "dateModified": as_of},
        person,
    ]
    if is_home:
        graph.append({
            "@type": "Dataset", "@id": home + "#dataset", "url": home,
            "name": "Rain forecast verification for cities worldwide",
            "description": ("Daily chance-of-rain forecasts (Open-Meteo) scored against the nearest "
                            f"rain gauge in {k['n_cities']} cities: Brier skill score with block-bootstrap "
                            "intervals, reliability, rank ranges and decision value."),
            "creator": {"@id": AUTHOR["id"]}, "isAccessibleForFree": True,
            "sameAs": REPO, "temporalCoverage": f"{first}/{as_of}",
            "spatialCoverage": {"@type": "Place", "name": "Worldwide"},
            "variableMeasured": ["Brier skill score", "precipitation probability", "rain gauge daily total"],
        })
    else:
        graph.append({"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": home},
            {"@type": "ListItem", "position": 2, "name": "Cities", "item": home + "cities/"},
            {"@type": "ListItem", "position": 3, "name": k["name"], "item": url}]})
    ld = json.dumps({"@context": "https://schema.org", "@graph": graph},
                    ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<meta name="author" content="{esc(AUTHOR['name'])}">
<meta name="robots" content="index,follow,max-image-preview:large">
<link rel="canonical" href="{esc(url)}">
<link rel="author" href="{esc(AUTHOR['url'])}">
<link rel="sitemap" type="application/xml" href="{esc(home)}sitemap.xml">
<meta property="og:type" content="{'website' if is_home else 'article'}">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(url)}">
<meta property="og:locale" content="en_GB">
<meta property="og:image" content="{esc(og['url'])}">
<meta property="og:image:width" content="{og['w']}">
<meta property="og:image:height" content="{og['h']}">
<meta property="og:image:alt" content="{esc(og['alt'])}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{esc(og['url'])}">
<script type="application/ld+json">{ld}</script>"""


def directory(site: dict, a: dict, home: str, base: str) -> str:
    """Every city page as a plain link: the crawl path that needs no script."""
    rows = sorted(site["cities"], key=lambda r: r[1].lower())
    items = "\n".join(
        f'<li><a href="{base}{"" if r[0] == site["default"] else f"city/{r[0]}/"}">{esc(r[1])}</a>'
        f'<span class="mono">{esc(r[2])} \u00b7 {r[5]:.2f}</span></li>' for r in rows)
    n, url = len(rows), home + "cities/"
    title = f"All {n} cities \u00b7 how good is the weather forecast? \u00b7 {SITE_NAME}"
    desc = (f"Rain forecast accuracy for {n} cities, each scored against its nearest rain gauge. "
            "Pick a city to see its grade, its rank and whether following the forecast pays off.")
    return f"""<!doctype html>
<html lang="en" data-theme="noir">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(url)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(url)}">
<meta property="og:image" content="{esc(a['og_abs'])}">
<meta name="twitter:card" content="summary_large_image">
<meta name="color-scheme" content="dark">
<link rel="icon" href="{a['icon']}" type="image/svg+xml">
<link rel="stylesheet" href="{a['css']}">
</head>
<body>
<main class="dir">
<p class="kicker"><a href="{base}">{SITE_NAME}</a> \u00b7 forecast audit</p>
<h1>All {n} cities</h1>
<p class="lede">Each city\u2019s daily chance of rain, scored against its nearest rain gauge. The number
  is the skill score: 1 is perfect, 0 is no better than quoting the usual chance of rain.</p>
<ul>
{items}
</ul>
<p class="note mono">Data as of {esc(site['as_of'])} \u00b7 <a href="{REPO}">code and data</a> \u00b7 by
  <a href="{AUTHOR['url']}" rel="author">{esc(AUTHOR['name'])}</a></p>
</main>
</body>
</html>
"""


def not_found(a: dict, base: str) -> str:
    return f"""<!doctype html>
<html lang="en" data-theme="noir">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Page not found \u00b7 {SITE_NAME}</title>
<meta name="robots" content="noindex">
<link rel="icon" href="{a['icon']}" type="image/svg+xml">
<link rel="stylesheet" href="{a['css']}">
</head>
<body>
<main class="dir">
<h1>That page is not here.</h1>
<p class="lede">The report moved to a new design; every city still has its page.</p>
<p><a href="{base}">Open the report</a> \u00b7 <a href="{base}cities/">all cities</a></p>
</main>
</body>
</html>
"""


def sitemap(urls: list[str], lastmod: str) -> str:
    body = "\n".join(f"  <url><loc>{esc(u)}</loc><lastmod>{lastmod}</lastmod></url>" for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{body}\n</urlset>\n")


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="/", help="URL path the site is served from, e.g. /rain-check/")
    ap.add_argument("--origin", default="http://127.0.0.1:8000",
                    help="scheme and host for canonical, Open Graph and sitemap URLs")
    ap.add_argument("--dist", default=None, help="output directory (default dist/)")
    args = ap.parse_args()

    k = city_report.load_all()
    if k is None:
        raise SystemExit("capitals artefacts missing - run src/capitals.py first")
    payloads = city_report.all_payloads(k)
    default = next(p["slug"] for p in payloads if p["name"] == city_report.DEFAULT_CITY)
    built = atlas_data.build(payloads, default)
    data, cities = built["site"], built["cities"]

    site = Site(base=args.base, dist=Path(args.dist).resolve() if args.dist else DIST)
    site.reset()
    base, home = site.base, args.origin.rstrip("/") + site.base

    fonts = {f.name: site.add_file("assets/fonts", f) for f in sorted((ATLAS / "fonts").glob("*.woff2"))}
    site.add_file("assets/fonts", ATLAS / "fonts" / "LICENSE.txt", hashed=False)
    # Placeholders are url("font:<file>") - plain CSS strings, so CSS parsers and
    # editor tooling read atlas.css cleanly (a bare url({FONT:...}) made tree-sitter
    # report ~6000 cascading syntax errors for the whole file).
    css = re.sub(r'"font:([\w.-]+)"', lambda m: fonts[m.group(1)], ship(ATLAS / "atlas.css"))
    icon = re.sub(r"<!--.*?-->\s*", "", (WEB / "favicon.svg").read_text(), flags=re.S)
    og_file = ATLAS / "og.jpg"
    a = {
        "css": site.add_text("assets", "atlas.css", css),
        "sky": site.add_text("assets", "sky.js", ship(ATLAS / "sky.js")),
        "stage": site.add_text("assets", "stage.js", ship(ATLAS / "stage.js")),
        "bolts": site.add_text("assets", "bolts.js", ship(ATLAS / "bolts.js")),
        "sats": site.add_text("assets", "sats.js", ship(ATLAS / "sats.js")),
        # The satellite roster with the orbital elements it was built with
        # (src/satellites.py); the page refreshes them from CelesTrak.
        "satdata": site.add_json("assets", "satellites.json", json.loads(SATELLITES.read_text())),
        "app": site.add_text("assets", "atlas.js", ship(ATLAS / "atlas.js")),
        "icon": site.add_text("assets", "favicon.svg", icon),
        "portrait": site.add_file("assets", WEB / "portrait.webp"),
        "elev": site.add_file("assets/geo", WEB / "geo" / "relief-elev.webp"),
        "biome": site.add_file("assets/geo", WEB / "geo" / "relief-biome.webp"),
        "stars": site.add_bytes("assets", "stars.bin", built["stars"]),
        "moon": site.add_file("assets/geo", WEB / "geo" / "moon.webp"),
        "lights": site.add_file("assets/geo", WEB / "geo" / "night-lights.webp"),
        "og": site.add_file("assets", og_file),
    }
    a["og_abs"] = args.origin.rstrip("/") + a["og"]
    og = {"url": a["og_abs"], "w": 1200, "h": 630,
          "alt": "The Bucharest rain forecast verdict card beside a globe of the 216 audited cities"}

    # City files first: their digests travel in the shared file, which every
    # page preloads, so a switch needs no second lookup.
    data["hashes"] = {slug: site.add_json("data/cities", f"{slug}.json", c).rsplit(".", 2)[1]
                      for slug, c in cities.items()}
    data_url = site.add_json("data", "data.json", data)

    # stars.bin holds each star's ground longitude at 00:00 UTC on the data
    # date (atlas_data.stars); the page turns the sky on from that instant.
    stars_epoch = int(datetime.fromisoformat(data["as_of"]).replace(tzinfo=timezone.utc).timestamp() * 1000)
    template = (ATLAS / "page.html").read_text()
    first = min(c["hero"]["first"] for c in cities.values())
    urls = [home, home + "cities/"]
    for slug, city in cities.items():
        is_home = slug == default
        rel = "" if is_home else f"city/{slug}/"
        url = home + rel
        kv, counts, ti = values(data, city)
        kv["_bss"] = counts["bss"].replace("-", "\u2212")
        cfg = {"base": base, "data": data_url, "city": slug,
               "elev": a["elev"], "biome": a["biome"], "stars": a["stars"],
               "moon": a["moon"], "lights": a["lights"], "starsEpoch": stars_epoch,
               "forecast": API_FORECAST, "wx": WX, "sats": a["satdata"], "satsLive": SATS_LIVE}
        html = prerender(template, kv, counts, ti)
        for key, val in {
            "{HEAD}": head(kv, url, home, og, data["as_of"], first, is_home),
            "{ICON}": a["icon"], "{DATA}": data_url, "{CSS}": a["css"],
            "{SKY_JS}": a["sky"], "{STAGE_JS}": a["stage"], "{BOLTS_JS}": a["bolts"], "{SATS_JS}": a["sats"], "{APP_JS}": a["app"], "{PORTRAIT}": a["portrait"],
            "{CFG}": json.dumps(cfg, separators=(",", ":")).replace("</", "<\\/"),
            "{BASE}": base, "{N_CITIES}": str(len(cities)), "{AS_OF}": esc(data["as_of"]),
        }.items():
            html = html.replace(key, val)
        if re.search(r"\{[A-Z_]+\}", html):
            raise SystemExit(f"{slug}: unfilled placeholder {re.search(r'{[A-Z_]+}', html).group()}")
        out = site.dist / rel / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        if not is_home:
            urls.append(url)

    (site.dist / "cities").mkdir(exist_ok=True)
    (site.dist / "cities" / "index.html").write_text(directory(data, a, home, base), encoding="utf-8")
    (site.dist / "sitemap.xml").write_text(sitemap(urls, data["as_of"]), encoding="utf-8")
    (site.dist / "404.html").write_text(not_found(a, base), encoding="utf-8")
    (site.dist / ".nojekyll").write_text("")

    total = sum(f.stat().st_size for f in site.dist.rglob("*") if f.is_file())
    print(f"wrote {site.dist}  ({len(cities)} city pages, {total / 1024:.0f} KB total, "
          f"base {base}, origin {args.origin})")


if __name__ == "__main__":
    main()
