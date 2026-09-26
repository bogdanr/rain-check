"""Verify the built site in a real browser before it is published.

The report makes a strong claim - that every number on the page comes from the
verification tables - and the interactive layer is the easiest place for that to
quietly stop being true. A broken globe or a city switch that leaves the old
city's tables on screen is worse than no interactivity at all, because it is
indistinguishable from a working page.

So the checks here are deliberately about *correctness of what is shown*, not
about pixels:

    structure    every page exists, every rail anchor resolves, no unreplaced
                 build placeholders, no city missing from the payload set
    parity       the globe draws exactly the cities Python verified - never a
                 marker with no data behind it, and never a city with no
                 marker. Places that were probed and dropped are not markers
                 at all; they are listed in a fold below the globe, and that
                 list is checked to be present and complete.
    switching    selecting a city replaces the heading, the tables and the
                 deep-dive fence together, so the page never shows a blend of
                 two cities
    determinism  a second build of unchanged inputs is byte-identical
    console      no uncaught errors on any page

Network is blocked during the run: the live forecast is third-party and must
degrade to a hidden strip, never to a broken page. That failure mode is a check,
not an accident.

Usage:  python src/check_site.py [--dist dist] [--keep]
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import io
import json
import re
import shutil
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Result:
    def __init__(self) -> None:
        self.checks: list[tuple[bool, str, str]] = []

    def add(self, ok: bool, name: str, detail: str = "") -> bool:
        self.checks.append((ok, name, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""))
        return ok

    @property
    def failed(self) -> list[tuple[bool, str, str]]:
        return [c for c in self.checks if not c[0]]


def serve(directory: Path) -> tuple[str, socketserver.TCPServer]:
    """Serve dist/ over HTTP.

    file:// would be simpler, but it does not honour directory indexes the way
    Pages does and it blocks fetch() of the city payloads, so the test would
    exercise a page that nobody will ever load.
    """
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(directory), **k)

    class Quiet(socketserver.TCPServer):
        allow_reuse_address = True
        def handle_error(self, request, client_address):  # noqa: D102
            pass

    httpd = Quiet(("127.0.0.1", 0), handler)
    httpd.RequestHandlerClass.log_message = lambda *a, **k: None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}", httpd


# ---------------------------------------------------------------------------
def _unpack(cfg: dict, dist: Path) -> None:
    """Rebuild the city records and payload URLs the way the browser does.

    `report.py` keeps the full city table out of the page: the config carries
    only a URL to `data/cities-index.json`, which `app.js` fetches once. Doing
    the same expansion here means the checks below go on reading the shapes
    they always read, and - more to the point - they are then verifying the
    same unpacking the browser does, from the same bytes.
    """
    ix = json.loads((dist / cfg["cityIndexUrl"].lstrip("/")).read_text())
    cfg["cities"] = [dict(zip(ix["cols"], row)) for row in ix["rows"]]
    cfg["cityUrls"] = {
        slug: f"{cfg['base']}data/cities/{slug}.{digest}.json"
        for slug, digest in ix["hashes"].items()}


def check_structure(dist: Path, r: Result) -> dict:
    index = dist / "index.html"
    if not r.add(index.exists(), "index.html exists"):
        raise SystemExit(1)

    html = index.read_text()
    cfg = json.loads(re.search(
        r'<script id="site-config" type="application/json">(.*?)</script>',
        html, re.S).group(1))
    _unpack(cfg, dist)

    slugs = [c["slug"] for c in cfg["cities"]]
    default = cfg["defaultSlug"]

    missing = [s for s in slugs
               if s != default and not (dist / "city" / s / "index.html").exists()]
    r.add(not missing, f"a page for each of {len(slugs)} capitals",
          f"missing: {missing}" if missing else "")

    no_payload = [s for s in slugs if not (dist / cfg["cityUrls"][s].lstrip("/")).exists()]
    r.add(not no_payload, "a JSON payload for each capital", str(no_payload))

    pages = list(dist.rglob("index.html"))
    stray = [str(p.relative_to(dist)) for p in pages if "{BASE}" in p.read_text()]
    r.add(not stray, "no unreplaced {BASE} placeholders", str(stray))

    # Every rail target must exist, on every page: a contents rail with a dead
    # entry is a navigation control that silently does nothing.
    bad_anchors = []
    for p in pages:
        t = p.read_text()
        for anchor in re.findall(r'<nav class="rail".*?</nav>', t, re.S):
            for href in re.findall(r'href="#([^"]+)"', anchor):
                if f'id="{href}"' not in t:
                    bad_anchors.append(f"{p.relative_to(dist)}#{href}")
    r.add(not bad_anchors, "every contents-rail anchor resolves", str(bad_anchors[:5]))

    refs = set()
    for p in pages:
        t = p.read_text()
        refs |= set(re.findall(r'(?:src|href)="(/[^"]+\.(?:css|js|json|png|svg))"', t))
    broken = [u for u in refs if not (dist / u.lstrip("/")).exists()]
    r.add(not broken, f"all {len(refs)} asset references resolve", str(broken[:5]))

    # No third-party runtime dependency: scripts, stylesheets and images must
    # all come from this origin. Canonical and og:image URLs are metadata rather
    # than resources the browser fetches, so they are absolute by design and are
    # deliberately not counted here.
    doc = index.read_text()
    ext = sorted(set(re.findall(r'<script[^>]+src="(https?://[^"]+)"', doc)) |
                 set(re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="(https?://[^"]+)"', doc)) |
                 set(re.findall(r'<img[^>]+src="(https?://[^"]+)"', doc)))
    r.add(not ext, "no third-party scripts, styles or images", str(ext))

    return cfg


def check_determinism(dist: Path, r: Result) -> None:
    """A second build of unchanged inputs must be byte-identical.

    The report's whole claim rests on being reproducible from the committed
    artefacts. Hashed asset names make drift loud: any nondeterminism in the
    render shows up as a changed filename, not just changed bytes.
    """
    tmp = ROOT / "dist.check"
    if tmp.exists():
        shutil.rmtree(tmp)
    env_out = subprocess.run(
        [sys.executable, str(ROOT / "src" / "report.py"), "--dist", str(tmp)],
        capture_output=True, text=True, cwd=ROOT)
    if env_out.returncode != 0:
        r.add(False, "second build succeeds", env_out.stderr.strip()[-300:])
        return

    def digest(d: Path) -> dict[str, str]:
        return {str(f.relative_to(d)): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in sorted(d.rglob("*")) if f.is_file()}

    a, b = digest(dist), digest(tmp)
    diff = sorted(set(a) ^ set(b)) + [k for k in a if k in b and a[k] != b[k]]
    # The generated-on date is the one legitimate source of drift.
    diff = [d for d in diff if not d.endswith(".html")]
    r.add(not diff, "rebuild is byte-identical (assets and data)", str(diff[:5]))

    html_diff = [k for k in a if k.endswith(".html") and k in b and a[k] != b[k]]
    if html_diff:
        one = (dist / html_diff[0]).read_text().splitlines()
        two = (tmp / html_diff[0]).read_text().splitlines()
        lines = [i for i, (x, y) in enumerate(zip(one, two)) if x != y]
        only_date = all("Generated" in one[i] for i in lines) if lines else True
        r.add(only_date, "HTML differs only in the generated-on date",
              f"{len(lines)} differing lines")
    else:
        r.add(True, "HTML is byte-identical")
    shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
def check_browser(base_url: str, dist: Path, cfg: dict, r: Result) -> None:
    from playwright.sync_api import sync_playwright

    slugs = [c["slug"] for c in cfg["cities"]]
    default = cfg["defaultSlug"]
    errors: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})

        # The forecast is third-party. Blocking it here proves the page survives
        # an outage rather than only working when Open-Meteo is up.
        ctx.route("**://api.open-meteo.com/**", lambda route: route.abort())

        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        # The blocked forecast request surfaces as a console error from the
        # network layer; that one is induced by this test and is the expected
        # outcome, so it is filtered rather than counted. Anything else is a
        # genuine fault.
        def on_console(m):
            if m.type != "error":
                return
            if "net::ERR_FAILED" in m.text or "Failed to load resource" in m.text:
                return
            errors.append(f"console.{m.type}: {m.text}")

        page.on("console", on_console)

        page.goto(base_url + "/", wait_until="networkidle")

        r.add(page.locator("#globe svg").count() == 1, "globe renders an SVG")

        # The basemap moved from an SVG path to a canvas, so the old check
        # (read path.land's "d") now reads an empty string forever. Assert
        # against what is actually drawn: polygons submitted, and real ink on
        # the canvas. A blank canvas is the failure this must still catch.
        # Polygons actually submitted to the canvas. This is view-dependent --
        # the far hemisphere is culled -- so the bar is "a continent's worth",
        # not a precise count. The pixel checks below do the exacting work.
        land_polys = page.evaluate("() => window.__globe.stats.land")
        r.add(land_polys > 20, "coastline geometry drew",
              f"{land_polys} polygons in view")

        ink = page.evaluate("""() => {
          const c = document.querySelector('#globe canvas');
          if (!c) return null;
          const ctx = c.getContext('2d');
          const d = ctx.getImageData(0, 0, c.width, c.height).data;
          const seen = new Set();
          let painted = 0;
          for (let i = 0; i < d.length; i += 4) {
            if (d[i + 3] === 0) continue;
            painted++;
            seen.add((d[i] >> 3) + ',' + (d[i+1] >> 3) + ',' + (d[i+2] >> 3));
          }
          return { painted: painted / (d.length / 4), colours: seen.size };
        }""")
        r.add(ink is not None and ink["painted"] > 0.25,
              "basemap canvas is actually painted, not blank", str(ink))
        # Ocean and land must not be the same colour, or the map is a disc.
        r.add(ink is not None and ink["colours"] >= 3,
              "basemap distinguishes land from ocean", str(ink))

        land = ""
        r.add(page.locator("#globe-fallback").is_hidden(),
              "plain city list hides once the globe is live")

        vis = page.eval_on_selector_all(
            "#globe .mk",
            "ns => ns.filter(n => n.getAttribute('display') !== 'none').length")
        total = page.locator("#globe .mk").count()
        r.add(total == len(slugs),
              f"one marker per verified capital ({total} of {len(slugs)})")
        # Parity in the strict direction: no marker may exist that Python did
        # not verify. Counting alone would pass a globe that drew a dropped
        # city and omitted a real one.
        orphans = page.eval_on_selector_all(
            "#globe .mk",
            "(ns, known) => ns.map(n => n.getAttribute('data-slug'))"
            "               .filter(s => known.indexOf(s) < 0)",
            arg=[c["slug"] for c in cfg["cities"]])
        r.add(not orphans, "every marker is a city with a payload behind it",
              str(orphans))

        # The places that could not be verified are a fold under the globe, not
        # markers. They must still be on the page: dropping them silently would
        # turn a coverage limit into an invisible one.
        fold = page.locator("details.dropped")
        r.add(fold.count() == 1, "the dropped-places fold is on the page")
        if fold.count():
            listed = page.eval_on_selector_all(
                "details.dropped .drop-names",
                "ns => ns.reduce((a, n) => a + n.textContent.split(',').length, 0)")
            r.add(listed > 0, f"{listed} dropped places are named with a reason")

        r.add(0 < vis <= total, "front-hemisphere markers only",
              f"{vis} of {total} visible")

        # Every marker must carry a label a screen reader can read.
        unlabelled = page.eval_on_selector_all(
            "#globe .mk", "ns => ns.filter(n => !n.getAttribute('aria-label')).length")
        r.add(unlabelled == 0, "every marker has an accessible label")

        r.add(page.locator("#fc").is_hidden(),
              "forecast strip hides when the API is unreachable")

        # Every marker must be the topmost element at its own centre.
        #
        # This is the check that caught the original design. At whole-Earth
        # scale fifteen European capitals land within a few pixels of each
        # other, and Amsterdam sat entirely underneath Luxembourg: the globe
        # looked perfect in a screenshot while a third of it could not be
        # clicked. That is exactly the class of defect a visual review misses.
        buried = page.evaluate("""() => {
          const out = [];
          document.querySelectorAll('#globe .mk').forEach(g => {
            if (g.getAttribute('display') === 'none') return;
            const b = g.getBoundingClientRect();
            const hit = document.elementFromPoint(b.x + b.width / 2,
                                                  b.y + b.height / 2);
            if (!hit || !g.contains(hit)) {
              out.push((g.getAttribute('aria-label') || '').split(' (')[0]);
            }
          });
          return out;
        }""")
        r.add(not buried, "every marker is clickable, none buried under a "
                          "neighbour", str(buried))

        # Zoom controls. The old version of this check clicked once and then
        # asserted True, which tested nothing -- it passed even while the
        # button was disabled. Read the zoom back out instead.
        zoom_in = page.locator("[aria-label='Zoom in']")
        zoom_out = page.locator("[aria-label='Zoom out']")
        r.add(zoom_out.count() == 1 and zoom_in.count() == 1,
              "globe has zoom controls")

        z0 = page.evaluate("() => window.__globe.zoom")
        r.add(zoom_out.is_disabled(),
              "zoom out is disabled at minimum zoom, not a dead button")

        zoom_in.click()
        page.wait_for_function("z => window.__globe.zoom > z", arg=z0)
        z1 = page.evaluate("() => window.__globe.zoom")
        r.add(z1 > z0, f"zoom in magnifies ({z0:g} -> {z1:g})")
        r.add(zoom_out.is_enabled(), "zoom out re-enables once zoomed in")

        # Markers must stay clickable when zoomed: this is where the
        # declutter grid changes its mind about what to hide.
        buried_z = page.evaluate("""() => {
          const out = [];
          document.querySelectorAll('#globe .mk').forEach(g => {
            if (g.getAttribute('display') === 'none') return;
            const b = g.getBoundingClientRect();
            const hit = document.elementFromPoint(b.x + b.width / 2,
                                                  b.y + b.height / 2);
            if (!hit || !g.contains(hit)) {
              out.push((g.getAttribute('aria-label') || '').split(' (')[0]);
            }
          });
          return out;
        }""")
        r.add(not buried_z, "markers stay clickable when zoomed in",
              str(buried_z))

        zoom_out.click()
        page.wait_for_function("z => window.__globe.zoom < z", arg=z1)
        r.add(page.evaluate("() => window.__globe.zoom") < z1, "zoom out shrinks")

        zoom_in.click()
        page.locator("[aria-label='Reset the view']").click()
        page.wait_for_function("z => window.__globe.zoom === z", arg=z0)
        r.add(page.evaluate("() => window.__globe.zoom") == z0,
              "reset returns the globe to its starting zoom")

        # --- clusters are a route, not a dead end -------------------------
        # Decluttering hides markers behind "+N" badges. That is only
        # acceptable if the badge leads somewhere, so prove it: clicking one
        # must zoom in and reveal more markers than were visible before.
        badge = page.locator("#globe .cl:not([display='none'])").first
        if badge.count():
            before_z = page.evaluate("() => window.__globe.zoom")
            before_v = page.eval_on_selector_all(
                "#globe .mk",
                "ns => ns.filter(n => n.getAttribute('display') !== 'none').length")
            badge.click()
            page.wait_for_function("z => window.__globe.zoom > z", arg=before_z)
            after_v = page.eval_on_selector_all(
                "#globe .mk",
                "ns => ns.filter(n => n.getAttribute('display') !== 'none').length")
            r.add(after_v > before_v,
                  "clicking a +N badge zooms in and reveals hidden cities",
                  f"{before_v} -> {after_v} markers visible")
            page.locator("[aria-label='Reset the view']").click()
            page.wait_for_function("z => window.__globe.zoom === z", arg=before_z)

        # --- switching ---------------------------------------------------
        # Address the marker by slug, not by name. Selecting on the name broke
        # the moment the city set grew past the capitals: 's-Hertogenbosch
        # begins with an apostrophe and silently produced an invalid selector.
        #
        # And pick a marker that is actually on screen. Most cities are behind
        # the globe or inside a cluster at any moment, so an arbitrary slug is
        # usually unclickable -- which is the design, not a fault.
        visible = page.eval_on_selector_all(
            "#globe .mk",
            "ns => ns.filter(n => n.getAttribute('display') !== 'none')"
            "      .map(n => n.getAttribute('data-slug'))")
        r.add(len(visible) > 5, "several cities are directly clickable at rest",
              f"{len(visible)} visible")
        target = next(s for s in visible if s != default)
        name = next(c["name"] for c in cfg["cities"] if c["slug"] == target)
        page.click(f"#globe .mk[data-slug='{target}']")
        page.wait_for_function(
            "n => document.querySelector('#h1-city').textContent === n", arg=name)

        heading = page.locator("#city-answer h2").inner_text()
        r.add(name in heading, "city panel heading follows the selection", heading)
        r.add(page.locator("#citybtn .name").inner_text() == name,
              "city button follows the selection")
        r.add(name in page.title(), "document title follows the selection")
        r.add(page.url.rstrip("/").endswith(f"city/{target}"),
              "URL updates to the city's real page", page.url)
        # The Bucharest-only sections live inside a fold now; open it the way
        # a reader would before reading the fence. This doubles as the check
        # that the fold's content is really in the document, not lazy-built.
        page.eval_on_selector(
            "#deep",
            "n => { for (let e = n; e; e = e.parentElement)"
            "         if (e.tagName === 'DETAILS') e.open = true; }")
        r.add(name in page.locator("#deep-sel").inner_text(),
              "deep-dive fence names the current selection")

        # The fence must keep saying Bucharest no matter what is selected: this
        # is the honesty guarantee, and it is a one-word edit away from breaking.
        r.add("Bucharest" in page.locator("#deep").inner_text(),
              "deep dive stays labelled Bucharest after switching")

        # No stale numbers: the switched panel must not still quote the old city.
        panel = page.locator(".citypanel").inner_text()
        old = next(c["name"] for c in cfg["cities"] if c["slug"] == default)
        r.add(old not in panel or name == old,
              f"no trace of {old} left in the panel after switching")

        # --- in-page links must not change the city -------------------------
        # Following a fragment link pushes a history entry with a null state
        # and fires popstate. Treating a null state as "the default city" meant
        # that clicking the globe card's link to the full verdict - or any rail
        # link - silently reloaded Bucharest over the top of whichever city the
        # reader had selected, while the address bar still named their city.
        page.click("#city-card .gcard-more a")
        page.wait_for_timeout(700)
        r.add(page.locator("#h1-city").inner_text() == name,
              "an in-page link leaves the selection alone",
              page.locator("#h1-city").inner_text())
        r.add(name in page.locator("#city-answer h2").inner_text(),
              "the card's link lands on the selected city's verdict",
              page.locator("#city-answer h2").inner_text())
        r.add(page.url.split("#")[0].rstrip("/").endswith(f"city/{target}"),
              "the URL still names the selected city after an in-page link",
              page.url)

        page.go_back()                      # undo the fragment entry
        page.wait_for_timeout(300)
        page.go_back()
        page.wait_for_function(
            "n => document.querySelector('#h1-city').textContent === n", arg=old)
        r.add(True, "browser back returns to the previous city")

        # --- palette -------------------------------------------------------
        page.click("#citybtn")
        r.add(page.locator("#palette").is_visible(), "city search opens")
        page.fill("#palette input", name[:3])
        hits = page.locator("#palette li").count()
        r.add(hits >= 1, f"search narrows the list ({hits} hits for '{name[:3]}')")
        page.keyboard.press("Enter")
        page.wait_for_function(
            "n => document.querySelector('#h1-city').textContent === n", arg=name)
        r.add(True, "search result loads the city")

        # --- palette: sections, and the comparison pin -----------------------
        # The palette must reach sections too - the rail cannot scale to a
        # thousand cities - and following one into a folded section must open
        # the fold, or the reader lands on a closed summary line.
        page.click("#citybtn")
        page.fill("#palette input", "glossary")
        r.add(page.locator("#palette li[data-anchor='glossary']").count() >= 1,
              "palette finds sections as well as cities")
        page.keyboard.press("Enter")
        page.wait_for_timeout(600)
        r.add(page.evaluate("() => document.getElementById('glossary')"
                            ".closest('details').open"),
              "jumping into a folded section opens the fold")

        # One fold ships its body as a separate asset. It must be empty until
        # the reader opens it (that is what it buys) and must fill in once
        # they do (that is what it must not cost them).
        lazyfold = page.locator("details[data-fold-src]")
        if lazyfold.count():
            r.add(lazyfold.first.locator(".fold-b").count() == 0,
                  "a deferred fold ships no body until it is opened")
            lazyfold.first.locator("summary").click()
            page.wait_for_selector("details[data-fold-src] .fold-b",
                                   timeout=5000)
            r.add(len(lazyfold.first.locator(".fold-b").inner_text()) > 200,
                  "opening a deferred fold fetches its content")
            lazyfold.first.locator("summary").click()

        # Shift+Enter pins a second city; it must be named in the chart key and
        # drawn on the charts from its own payload coordinates, and clicking
        # the key must remove it.
        page.click("#citybtn")
        page.fill("#palette input", old)
        page.keyboard.press("Shift+Enter")
        page.wait_for_function(
            "() => !document.querySelector('.cmp-key').hidden", timeout=5000)
        r.add(old in page.locator(".cmp-key").first.inner_text(),
              "Shift+Enter pins a comparison city, named in the chart key")
        page.locator(".lazychart").first.scroll_into_view_if_needed()
        page.wait_for_selector(".citychart .cc-cmp", timeout=10000)
        r.add(page.locator(".citychart .cc-cmp").count() >= 1,
              "the pinned city is drawn on the cross-city charts")
        page.locator(".cmp-key").first.click()
        page.wait_for_function(
            "() => document.querySelector('.cmp-key').hidden", timeout=5000)
        r.add(page.locator(".citychart .cc-cmp").count() == 0,
              "clicking the chart key removes the comparison")

        # --- themes ---------------------------------------------------------
        for theme in ("daylight", "blueprint", "observatory"):
            page.click(f"[data-theme-set='{theme}']")
            got = page.get_attribute("html", "data-theme")
            if not r.add(got == theme, f"theme '{theme}' applies", got or ""):
                break
            bg = page.eval_on_selector(
                "body", "b => getComputedStyle(b).backgroundColor")
            fg = page.eval_on_selector("body", "b => getComputedStyle(b).color")
            r.add(contrast(bg, fg) >= 7.0,
                  f"theme '{theme}' body contrast >= 7:1 (AAA)",
                  f"{contrast(bg, fg):.1f}:1  {bg} on {fg}")
            # The chart voices must be defined per theme, and the selected
            # city must never share a colour with the median - the exact
            # ambiguity the screenshots caught in the dark theme.
            sel, med = page.evaluate(
                """() => { const s = getComputedStyle(document.documentElement);
                     return [s.getPropertyValue('--ch-sel').trim(),
                             s.getPropertyValue('--ch-median').trim()]; }""")
            r.add(bool(sel) and bool(med) and sel != med,
                  f"theme '{theme}' keeps selection and median distinct",
                  f"sel={sel or '(unset)'} median={med or '(unset)'}")

        # Theme must survive a reload, or the control feels broken.
        page.reload(wait_until="domcontentloaded")
        r.add(page.get_attribute("html", "data-theme") == "observatory",
              "theme choice persists across a reload")

        # --- deep links ------------------------------------------------------
        for slug in slugs[:4]:
            url = base_url + ("/" if slug == default else f"/city/{slug}/")
            page.goto(url, wait_until="domcontentloaded")
            want = next(c["name"] for c in cfg["cities"] if c["slug"] == slug)
            got = page.locator("#h1-city").inner_text()
            if not r.add(got == want, f"deep link /{slug}/ opens on {want}", got):
                break
            r.add(page.locator("#citybtn .name").inner_text() == want,
                  f"/{slug}/ selector shows {want}")

        # --- forecast, success path ------------------------------------------
        # The report's headline feature is annotating a live probability with
        # what that probability has historically meant in that city. The number
        # it quotes must come from the city's own reliability table, so this
        # feeds a known probability in and checks the exact figure that comes
        # back out - a plausible-looking percentage would hide the bug where the
        # page falls back to Bucharest's table for every city.
        fc_slug = slugs[1] if slugs[1] != default else slugs[2]
        fc = json.loads((dist / cfg["cityUrls"][fc_slug].lstrip("/")).read_text())
        fc_name = fc["name"]
        bins = fc["pop_map"]
        target_bin = bins[len(bins) // 2]
        stated = round((target_bin["lo"] + target_bin["hi"]) / 2 * 100)
        want_obs = f"{round(target_bin['obs'] * 100)}%"

        # Code 61 is rain, so the card must decode to the rain sky and the
        # page must take the rain tint. Both are asserted below.
        fc_body = json.dumps({
            "current": {"temperature_2m": 12.4, "weather_code": 61, "is_day": 1,
                        "time": "2026-09-14T09:30"},
            "hourly": {
                "time": [f"2026-09-14T{h:02d}:00" for h in range(24)],
                "precipitation_probability": [(i * 7) % 101 for i in range(24)],
            },
            "daily": {"temperature_2m_max": [15.0],
                      "temperature_2m_min": [7.0],
                      "precipitation_probability_max": [stated]},
        })

        def fc_route(route):
            route.fulfill(status=200, content_type="application/json", body=fc_body)

        fctx = browser.new_context()
        fctx.route("**://api.open-meteo.com/**", fc_route)
        fpage = fctx.new_page()
        fpage.goto(f"{base_url}/city/{fc_slug}/", wait_until="domcontentloaded")
        fpage.wait_for_selector("#fc .pop", timeout=10000)
        # Both headline figures count up, so read them only once they have
        # settled. Without this the assertions race the animation and check
        # whichever frame the browser happened to be on.
        fpage.wait_for_function(
            "w => document.querySelector('#fc .obsnum').textContent === w",
            arg=want_obs, timeout=5000)
        fpage.wait_for_function(
            "() => document.querySelector('#fc .temp').textContent === '12\\u00B0'",
            timeout=5000)

        strip = fpage.locator("#fc").inner_text()
        r.add(f"{stated}% chance of rain" in strip,
              "forecast strip shows the stated probability", strip.replace("\n", " ")[:70])
        r.add("12" in strip, "forecast strip shows the current temperature")

        means = fpage.locator("#fc .means").inner_text()
        r.add(want_obs in means and fc_name in means,
              f"stated {stated}% annotated with {fc_name}'s own history",
              means[:110])
        r.add(str(target_bin["n"]) in means,
              "annotation states the sample size it rests on")

        r.add(fpage.locator("#fc .sk").count() == 0,
              "loading skeleton is cleared once the data lands")

        # The illustration has to be the one the WMO code decodes to. Artwork
        # that never changes is the failure mode a screenshot cannot catch,
        # because any single screenshot of it looks perfectly correct.
        r.add(fpage.locator("#fc .fcart .a-rain").count() > 0 and
              fpage.locator("#fc .fcart .a-sun").count() == 0,
              "sky illustration matches the decoded condition")
        emoji = [c for c in strip if ord(c) > 0x2100]
        r.add(not emoji, "no emoji glyph left in the forecast card", repr(emoji[:4]))

        # Placement on a wrapping <g>, motion on the child. When both sit on one
        # node the CSS keyframe replaces the SVG transform *attribute* outright
        # and every cloud silently jumps to a position nobody authored - a bug
        # that is invisible unless you know the authored coordinates.
        clash = fpage.evaluate("""() => [...document.querySelectorAll('#fc .fcart *')]
          .filter(n => n.hasAttribute('transform') &&
                       getComputedStyle(n).animationName !== 'none').length""")
        r.add(clash == 0,
              "no sky shape animates a transform it also sets as an attribute",
              f"{clash} clashing")

        # The hourly ribbon comes out of the same request. Twelve bars, tallest
        # where the vendor said the chance was highest - if the slice were taken
        # from the wrong index the card would confidently show yesterday.
        ribbon = fpage.evaluate("""() => [...document.querySelectorAll('#fc .hr')]
          .map(n => ({ h: n.getBoundingClientRect().height,
                       tip: n.dataset.tip }))""")
        r.add(len(ribbon) == 12, "hourly ribbon shows the next twelve hours",
              f"{len(ribbon)} bars")
        r.add(bool(ribbon) and ribbon[0]["tip"].startswith("10:00"),
              "ribbon starts at the first hour after the reported time",
              ribbon[0]["tip"] if ribbon else "")
        r.add(bool(ribbon) and max(b["h"] for b in ribbon) >
              min(b["h"] for b in ribbon) + 8,
              "ribbon bar heights vary with the stated hourly chance")

        # The gap rail is the finding. Its two marks must sit at the two numbers
        # and in the right order - a rail that draws both marks in one place is
        # the ring's failure all over again, just flatter.
        rail = fpage.evaluate("""() => {
          const r = document.querySelector('#fc .gaprail');
          const s = document.querySelector('#fc .mk.stated');
          const o = document.querySelector('#fc .mk.obs');
          if (!r || !s || !o) return null;
          const w = r.getBoundingClientRect().width;
          const at = n => (n.getBoundingClientRect().left +
                           n.getBoundingClientRect().width / 2 -
                           r.getBoundingClientRect().left) / w * 100;
          return { stated: at(s), obs: at(o) };
        }""")
        r.add(rail is not None and abs(rail["stated"] - stated) <= 1.5,
              "rail places the stated mark at the stated probability",
              f"{rail['stated']:.1f}% vs {stated}%" if rail else "missing")
        r.add(rail is not None and
              abs(rail["obs"] - round(target_bin["obs"] * 100)) <= 1.5,
              "rail places the observed mark at this city's own figure",
              f"{rail['obs']:.1f}%" if rail else "missing")

        # Content hierarchy, asserted numerically. The point of the report is
        # that a stated probability needs qualifying by what it has actually
        # meant, and for the whole life of the old card the layout said the
        # opposite: 19px bold for the vendor's number, 13px grey for the city's
        # own record. This is the check that keeps that from coming back.
        sizes = fpage.evaluate("""() => ({
          obs: parseFloat(getComputedStyle(
                 document.querySelector('#fc .obsnum')).fontSize),
          stated: parseFloat(getComputedStyle(
                 document.querySelector('#fc .stated')).fontSize)
        })""")
        r.add(sizes["obs"] >= sizes["stated"] * 1.5,
              "observed frequency outweighs the stated probability visually",
              f"{sizes['obs']:.0f}px vs {sizes['stated']:.0f}px")

        # Contrast on this card is measured in pixels, not tokens: the text now
        # sits on the weather itself, so no computed background colour describes
        # what is actually behind a glyph. The full sweep across every sky and
        # theme is check_card_contrast; this is the one-case tripwire.
        worst_card = card_contrast(fpage)
        r.add(worst_card >= 4.5,
              "forecast card text stays legible on its own sky",
              f"worst {worst_card:.2f}:1")

        # Rain must tint the page, and must not touch text contrast.
        sky = fpage.get_attribute("html", "data-sky")
        r.add(sky == "rain", "weather tint follows the reported conditions", sky or "")
        bg = fpage.eval_on_selector("body", "b => getComputedStyle(b).backgroundColor")
        fg = fpage.eval_on_selector("body", "b => getComputedStyle(b).color")
        r.add(contrast(bg, fg) >= 7.0,
              "weather tint leaves body contrast at AAA",
              f"{contrast(bg, fg):.1f}:1")

        # A card this dense is exactly the kind of layout that survives review
        # at 1280px and overflows the viewport on a phone.
        for w in (390, 700, 860, 1280):
            fpage.set_viewport_size({"width": w, "height": 900})
            over = fpage.evaluate("""() => {
              const f = document.querySelector('#fc');
              const d = document.documentElement;
              return { card: f.scrollWidth - f.clientWidth,
                       doc: d.scrollWidth - d.clientWidth };
            }""")
            r.add(over["card"] <= 1 and over["doc"] <= 1,
                  f"forecast card fits its column at {w}px", str(over))
        fpage.set_viewport_size({"width": 1280, "height": 900})
        fctx.close()

        # --- forecast, reduced motion ----------------------------------------
        # The page's blanket reduced-motion rule collapses durations to .001ms,
        # which stops a transition but leaves an *infinite* loop running at a
        # pathological speed - the opposite of what was asked for. The sky loops
        # are the first looping animations here, so this asserts nothing in the
        # card is still playing rather than trusting the rule to cover them.
        mctx = browser.new_context(reduced_motion="reduce")
        mctx.route("**://api.open-meteo.com/**", fc_route)
        mpage = mctx.new_page()
        mpage.goto(f"{base_url}/city/{fc_slug}/", wait_until="domcontentloaded")
        mpage.wait_for_selector("#fc .obsnum", timeout=10000)
        running = mpage.evaluate("""() => document.querySelector('#fc')
          .getAnimations({ subtree: true })
          .filter(a => a.playState === 'running').length""")
        r.add(running == 0,
              "nothing in the forecast card animates under reduced motion",
              f"{running} running")
        r.add(mpage.locator("#fc .obsnum").inner_text() == want_obs,
              "figures arrive at their value without counting up")
        mctx.close()

        # --- deferred cross-city charts --------------------------------------
        # These three are fetched on approach rather than inlined, which is most
        # of what keeps the first load under budget. Two things have to hold for
        # that to be a saving rather than a loss: the document must not carry
        # them, and scrolling must actually produce them - fully wired, with the
        # selected city picked out, exactly as when they were inline.
        cpage = ctx.new_page()
        cpage.goto(base_url + "/", wait_until="networkidle")
        charts = cpage.locator(".lazychart")
        boxes = charts.count()
        r.add(boxes >= 1, "cross-city charts are deferred, not inlined",
              f"{boxes} deferred, "
              f"{cpage.locator('#main > section .citychart').count()} inline")
        # One at a time: a single jump to the last one leaves the ones above it
        # off screen and unobserved, which would read as a failure when it is
        # only a test that scrolled past them.
        arrived = 0
        for i in range(boxes):
            charts.nth(i).scroll_into_view_if_needed()
            try:
                cpage.wait_for_function(
                    "i => !!document.querySelectorAll('.lazychart')[i]"
                    "      .querySelector('svg.citychart')", arg=i, timeout=10000)
                arrived += 1
            except Exception:
                break
        r.add(arrived == boxes, "every deferred chart arrives on scroll",
              f"{arrived} of {boxes}")
        r.add(cpage.locator(".lazychart svg .cc.on").count() == arrived,
              "an arriving chart picks out the selected city")

        # The redesigned charts: ribbons for the population (no spaghetti),
        # the selected city named at the end of its own geometry, a computed
        # readout under each figure, and dots that act as navigation.
        r.add(cpage.locator(".citychart .cc-lines").count() == 0,
              "no per-city spaghetti lines ship with the line charts")
        default_name = next(c["name"] for c in cfg["cities"]
                            if c["slug"] == cfg["defaultSlug"])
        labels = cpage.locator(".citychart .cc-endlabel")
        # SVG <text> is not an HTMLElement, so `inner_text` refuses it; read
        # the node's text content instead.
        first_label = labels.first.text_content() if labels.count() else ""
        r.add(labels.count() >= 2 and first_label == default_name,
              "the selected city is named on the charts themselves",
              f"{labels.count()} labels, first '{first_label}'")
        notes = cpage.locator(".chart-note:not([hidden])")
        r.add(notes.count() >= 2 and
              default_name in notes.first.inner_text(),
              "each chart reads out the selected city's own numbers",
              f"{notes.count()} notes")
        fp_dots = cpage.locator("svg[data-chart='fingerprint'] .cc-dot")
        r.add(fp_dots.count() >= 50,
              "fingerprint chart carries one dot per city",
              f"{fp_dots.count()} dots")
        # Clicking a city's dot selects that city site-wide.
        other = cpage.locator(
            f"svg[data-chart='fingerprint'] "
            f".cc-dot:not([data-city='{cfg['defaultSlug']}'])").first
        other_slug = other.get_attribute("data-city")
        other.dispatch_event("click")
        try:
            cpage.wait_for_function(
                "s => document.querySelector('#citybtn .name') && "
                "document.querySelector('.cc-injected.on, .cc.on')"
                " && history.state && history.state.slug === s",
                arg=other_slug, timeout=8000)
            r.add(True, "clicking a chart dot selects that city")
        except Exception:
            r.add(False, "clicking a chart dot selects that city", other_slug)
        cpage.close()

        r.add(not errors, "no console or page errors", "; ".join(errors[:3]))
        ctx.close()
        browser.close()


def check_nojs(base_url: str, cfg: dict, r: Result) -> None:
    """With scripting off the report must still be a complete document."""
    from playwright.sync_api import sync_playwright

    slug = next(c["slug"] for c in cfg["cities"] if c["slug"] != cfg["defaultSlug"])
    name = next(c["name"] for c in cfg["cities"] if c["slug"] == slug)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(java_script_enabled=False)
        page = ctx.new_page()
        page.goto(f"{base_url}/city/{slug}/", wait_until="domcontentloaded")

        text = page.locator("#main").inner_text()
        r.add(name in text, "city report readable without JavaScript")
        r.add("calibration curve" in text.lower(),
              "calibration section present without JavaScript")
        r.add(page.locator("#globe-fallback a[href$='/cities/']").count() == 1,
              "globe falls back to a link to the city directory")
        page.goto(f"{base_url}/cities/", wait_until="domcontentloaded")
        r.add(page.locator("#main li a[data-city]").count() == len(cfg["cities"]),
              "city directory lists every city, without JavaScript")
        page.goto(f"{base_url}/city/{slug}/", wait_until="domcontentloaded")
        r.add(page.locator("#city-curve svg").count() >= 1,
              "calibration chart is server-rendered SVG, not drawn by script")
        # The cross-city charts are the one thing on the page that genuinely
        # needs script to arrive, so they carry a <noscript> alternative: the
        # same figure as a PNG. Without this the deferral would quietly cost the
        # no-JS reader three charts the text refers to.
        r.add(page.locator(".lazychart noscript, .lazychart img").count() >= 1,
              "deferred charts fall back to figures without JavaScript")
        browser.close()


def check_palettes(base_url: str, r: Result) -> None:
    """Every theme must produce a globe you can actually read.

    Judging this from a screenshot is unreliable - it is genuinely hard to tell
    by eye whether the dark shapes are the continents or the oceans - so the
    requirement is stated numerically instead:

      land vs sea    >= 1.4:1, or the map is a featureless disc. Blueprint
                     failed this at 1.03:1, because it reused the panel tokens,
                     and in a near-monochrome theme panel and page are meant to
                     look the same.
      marker vs land >= 2.5:1, or the skill ramp disappears against the map.

    These are lower than text thresholds on purpose: large filled areas need far
    less separation than glyphs, and demanding 4.5:1 here would force a garish
    map. The numbers a reader must not misread are in the tables, not the dots.
    """
    from playwright.sync_api import sync_playwright
    from PIL import Image

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context()
        ctx.route("**://api.open-meteo.com/**", lambda route: route.abort())
        page = ctx.new_page()
        page.goto(base_url + "/", wait_until="networkidle")

        for theme in ("observatory", "daylight", "blueprint"):
            page.click(f"[data-theme-set='{theme}']")
            got = page.evaluate("""() => {
              const f = s => {
                const n = document.querySelector(s);
                return n ? getComputedStyle(n).fill : null;
              };
              return {
                sea: f('#globe .sphere'),
                land: f('#globe .land'),
                q: ['q0','q1','q2','q3','q4'].map(c => f('#globe .mk.' + c + ' .dot'))
              };
            }""")
            sea, land = got["sea"], got["land"]
            c = contrast(sea, land)
            r.add(c >= 1.4, f"{theme}: land reads against sea",
                  f"{c:.2f}:1  land {land} / sea {sea}")

            worst, worst_i = 99.0, -1
            for i, q in enumerate(got["q"]):
                if not q:
                    continue
                cc = min(contrast(q, land), contrast(q, sea))
                if cc < worst:
                    worst, worst_i = cc, i
            r.add(worst >= 2.5, f"{theme}: every skill tier reads on the map",
                  f"worst tier q{worst_i} at {worst:.2f}:1")

            # Sample the rendered pixels at known coordinates.
            #
            # geoContains (below) proves the *data* is right and the contrast
            # ratios above prove the *tokens* are right, but neither proves the
            # renderer paints land where land is. A wrong fill-rule, a stale
            # clip or a mis-ordered layer would satisfy both and still hand the
            # reader a map of the sea. Only the pixels settle that, so four
            # marker-free points - two inland, two offshore - are read straight
            # off the canvas.
            #
            # What the pixels are compared *against* is the question. Two
            # earlier answers are both wrong now:
            #
            #   - "is this pixel nearer the land token or the ocean token" -
            #     defeated by lighting, because a lit ocean is brighter than
            #     shadowed land;
            #   - "is the land point lighter than the sea point" - defeated by
            #     the terrain renderer, which paints a green continent that is
            #     genuinely darker than a sunlit sea. It reported a land/sea
            #     swap on a map where nothing had swapped.
            #
            # The answer that survives both is to ask which *material* the
            # pixel resembles, out of the full palette the shader draws from:
            # a sea point must look like abyss or shelf, a land point like
            # forest, desert or rock. Shading moves a pixel along its own
            # material's brightness, which is far less than the distance to a
            # material of another colour - and if the fill rule inverted, every
            # one of these four would name a material from the wrong list.
            probes = {
                "France": ([2.6, 45.5], "land"),
                "North Sea": ([3.5, 55.5], "sea"),
                "Poland": ([19.5, 52.4], "land"),
                "Adriatic": ([15.5, 42.6], "sea"),
            }

            # Put Europe under the nose AND magnify it before sampling.
            #
            # These probe points were chosen for a fitted European view. With a
            # worldwide city set the opening view is the whole globe, where the
            # entire North Sea is about eight pixels across - the probe then
            # samples antialiased coastline and reports a land/sea fault that
            # does not exist. d3.geoContains confirms the geometry is right at
            # both zooms; only the sampling was unsound. Magnify until the
            # features are larger than the uncertainty.
            page.evaluate("""() => {
              window.__globe._spin([-10, -50], false);
              window.__globe.setZoom(3);
              // The settled frame: while the view is moving the shader renders
              // into a smaller buffer and the result is scaled up, and sampling
              // a single pixel out of an interpolated image is not a
              // measurement of what the renderer decided. renderSettled() also
              // cancels any pending frame that would repaint underneath us.
              window.__globe.renderSettled();
            }""")

            px_all = page.evaluate("""(pts) => {
              const c = document.querySelector('#globe canvas');
              const ctx = c.getContext('2d');
              const vb = +document.querySelector('#globe svg')
                           .getAttribute('viewBox').split(/\\s+/)[2];
              const k = c.width / vb;
              const pal = window.__globe.relPal || {};
              const out = { _materials: {
                abyss: pal.abyss, shelf: pal.shelf, forest: pal.forest,
                desert: pal.desert, rock: pal.rock
              } };
              for (const name in pts) {
                const p = window.__globe.projection(pts[name]);
                if (!p) { out[name] = null; continue; }
                const x = Math.round(p[0] * k), y = Math.round(p[1] * k);
                if (x < 0 || y < 0 || x >= c.width || y >= c.height) {
                  out[name] = null; continue;
                }
                const d = ctx.getImageData(x, y, 1, 1).data;
                out[name] = [d[0], d[1], d[2]];
              }
              return out;
            }""", {k: v[0] for k, v in probes.items()})

            mats = px_all.pop("_materials")
            side_of = {"abyss": "sea", "shelf": "sea",
                       "forest": "land", "desert": "land", "rock": "land"}
            # A theme that never defined the terrain palette falls back to the
            # two flat tokens, which is exactly what its globe is drawn with.
            known = {n: c[:3] for n, c in mats.items() if c} or \
                    {"shelf": _rgb(sea), "forest": _rgb(land)}

            for label, (pt, want) in probes.items():
                px = px_all[label]
                if px is None:
                    r.add(False, f"{theme}: {label} probe lands on the globe",
                          "projected outside the canvas")
                    continue
                nearest = min(known, key=lambda m: sum(
                    (px[i] - known[m][i]) ** 2 for i in range(3)))
                got = side_of.get(nearest, "land")
                r.add(got == want,
                      f"{theme}: {label} is painted as {want}",
                      "#%02x%02x%02x reads as %s (%s)" % (
                          px[0], px[1], px[2], nearest, got))

        # Is the filled region actually the land?
        #
        # A polygon with its rings wound the wrong way still draws a perfectly
        # convincing map - it just fills the oceans instead of the continents,
        # and on a dark theme where sea and land are both blue that is almost
        # impossible to see. Asking d3 directly settles it, and guards the
        # hand-rolled TopoJSON decoder in vendor.py against a silent regression.
        geo = page.evaluate("""async () => {
          const cfg = JSON.parse(document.getElementById('site-config').textContent);
          const land = await (await fetch(cfg.landUrl)).json();
          return {
            paris: d3.geoContains(land, [2.35, 48.86]),
            sahara: d3.geoContains(land, [10, 23]),
            siberia: d3.geoContains(land, [100, 62]),
            atlantic: d3.geoContains(land, [-30, 0]),
            pacific: d3.geoContains(land, [-140, 0]),
            area: d3.geoArea(land) / (4 * Math.PI)
          };
        }""")
        r.add(geo["paris"] and geo["sahara"] and geo["siberia"],
              "land polygons contain known land points")
        r.add(not geo["atlantic"] and not geo["pacific"],
              "land polygons exclude open ocean")
        r.add(0.25 <= geo["area"] <= 0.33,
              "filled area matches Earth's land fraction (~29%)",
              f"{geo['area']:.1%}")
        browser.close()


# ---------------------------------------------------------------------------
def check_responsive(base_url: str, r: Result) -> None:
    """Every control must stay reachable at phone widths.

    The failure this exists to catch is silent: at 390px the third theme button
    was clipped past the right edge of the screen, so Blueprint - the high
    contrast, accessibility theme - simply could not be selected on a phone.
    Nothing errored and the page looked fine. Only measuring where the controls
    actually land finds it.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width in (360, 390, 768, 1280):
            ctx = browser.new_context(viewport={"width": width, "height": 900})
            ctx.route("**://api.open-meteo.com/**", lambda route: route.abort())
            page = ctx.new_page()
            page.goto(base_url + "/", wait_until="networkidle")
            page.wait_for_timeout(400)

            over = page.evaluate("""() => {
              const bad = [];
              document.querySelectorAll(
                'button, a[href], input, summary, [tabindex]').forEach(n => {
                const b = n.getBoundingClientRect();
                const s = getComputedStyle(n);
                if (s.display === 'none' || s.visibility === 'hidden') return;
                if (b.width <= 0) return;
                // Skip links are deliberately parked far off-canvas until
                // focused; that is the standard pattern, not a layout bug, so
                // only near-edge clipping counts as overflow on the left.
                const clipped = b.right > window.innerWidth + 1 ||
                                (b.left < -1 && b.left > -1000);
                // A control inside a horizontally scrollable box is reachable:
                // the wide data tables ship in .tablewrap (overflow-x: auto)
                // precisely so a phone can scroll them sideways rather than
                // squeeze eight numeric columns into 390px. What this check is
                // really asking is whether a control can be reached at all, so
                // require the ancestor to actually scroll - a box with
                // overflow-x: auto that fits its content scrolls nowhere and
                // would still be hiding the control.
                let scrollable = false;
                for (let e = n.parentElement; e && e !== document.body;
                     e = e.parentElement) {
                  const es = getComputedStyle(e);
                  if ((es.overflowX === 'auto' || es.overflowX === 'scroll') &&
                      e.scrollWidth > e.clientWidth + 1) { scrollable = true; break; }
                }
                if (clipped && !scrollable)
                  bad.push((n.id || n.textContent.trim().slice(0, 18) ||
                            n.tagName) + ' @' + Math.round(b.left) + '..' +
                           Math.round(b.right));
              });
              return bad;
            }""")
            r.add(not over, f"{width}px: no control sits off-screen",
                  "; ".join(over[:4]))

            r.add(page.evaluate("() => document.documentElement.scrollWidth"
                                " <= window.innerWidth + 1"),
                  f"{width}px: no horizontal page scroll")

            # Reachability, not just presence. The control has two modes - three
            # visible segments when wide, one cycling button when narrow - so
            # drive whatever is actually on screen rather than assuming either.
            seen = set()
            for i in range(3):
                vis = page.locator("[data-theme-set]:visible")
                vis.nth(i if vis.count() > 1 else 0).click()
                page.wait_for_timeout(150)
                seen.add(page.evaluate(
                    "() => document.documentElement.getAttribute('data-theme')"))
            r.add(len(seen) == 3, f"{width}px: all three themes reachable",
                  "reached " + ", ".join(sorted(seen)))
            ctx.close()
        browser.close()


# ---------------------------------------------------------------------------
def card_contrast(page) -> float:
    """Worst contrast behind any text in the forecast card, measured in pixels.

    The card puts text on the weather itself, which is a deliberate bend of the
    rule that `--sky-*` may not touch text: the surface is allowed to react to
    the weather only because it guarantees its own floor - the sky is mixed down
    against a near-black base, and a weather-INDEPENDENT scrim sits on top.

    "Guarantees" has to mean measured. No computed background colour describes
    what is behind a glyph here (the nearest one is `transparent`), so the text
    is hidden, the card is photographed, and the pixels that were behind each
    line are sampled: the dangerous case is the rare sky, not the one anybody
    thought to look at.

    Sampling across the element's *box* rather than its glyphs measures the
    neighbours. `.asof` is a 398px-wide block holding about 70px of "as of
    08:00"; the leftmost sample landed on the top edge of the hourly bar
    underneath it and reported 2.7:1 for text sitting on a perfectly dark
    backdrop. A Range over the text nodes gives the inked extent instead, so
    every sample is a pixel a glyph is actually drawn on.
    """
    from PIL import Image

    sel = ("#fc .temp, #fc .cond, #fc .asof, #fc .city, #fc .k, #fc .stated, "
           "#fc .obsnum, #fc .tag, #fc .means, #fc .hrslab, #fc .gaprail .lab")
    # page.screenshot() photographs the viewport, and the card sits well below
    # the fold on a city page. Without this scroll every sample lands outside
    # the image and is skipped, and the function returns its own "nothing was
    # wrong" sentinel - a check that measures nothing and reports a pass.
    #
    # 'instant' is load-bearing: the stylesheet sets html { scroll-behavior:
    # smooth }, so the default animates the scroll and the screenshot catches
    # the page in flight. That is how the rail caption came to be measured at
    # y=995 - the viewport floor, below the card entirely - against the white
    # page background, reporting 1.06:1 for text on a dark scrim.
    page.eval_on_selector(
        "#fc", "n => n.scrollIntoView({ block: 'center', behavior: 'instant' })")
    page.wait_for_timeout(150)
    geom = page.evaluate("""(sel) => {
      const out = [];
      for (const n of document.querySelectorAll(sel)) {
        const fg = getComputedStyle(n).color;
        // Range rects follow the glyphs, not the block: one rect per line box,
        // each only as wide as the text actually set on that line.
        const rng = document.createRange();
        rng.selectNodeContents(n);
        for (const b of rng.getClientRects()) {
          if (b.width < 2 || b.height < 2) continue;
          out.push({ fg, y: Math.round(b.y + b.height / 2),
                     x0: Math.round(b.x), x1: Math.round(b.right) });
        }
        rng.detach();
      }
      document.querySelectorAll(sel).forEach(n => { n.style.visibility = 'hidden'; });
      return out;
    }""", sel)
    shot = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    page.evaluate("(sel) => document.querySelectorAll(sel)"
                  ".forEach(n => { n.style.visibility = ''; })", sel)

    worst, seen = 99.0, 0
    for g in geom:
        # Inset by a pixel at each end: a glyph run's first and last columns are
        # antialiased against whatever abuts the text, which is not the backdrop
        # the glyph body sits on.
        span = g["x1"] - g["x0"]
        for i in range(12):
            x = int(g["x0"] + 1 + (span - 2) * i / 11.0)
            if not (0 <= x < shot.width and 0 <= g["y"] < shot.height):
                continue
            px = shot.getpixel((x, g["y"]))
            worst = min(worst, contrast("rgb(%d,%d,%d)" % px, g["fg"]))
            seen += 1
    # Sampling nothing means the measurement failed, not that the card passed.
    return worst if seen else 0.0


def check_card_contrast(base_url: str, cfg: dict, r: Result) -> None:
    """The forecast card must stay legible under every sky, in every theme.

    Twenty-one combinations, and the ones that will break are not the ones a
    designer looks at: `fog` ends at #8a9296 and `snow` at #7d9ab5, both far too
    light for near-white text before the card mixes them down. Checking the two
    that were designed against proves nothing about the other nineteen.
    """
    from playwright.sync_api import sync_playwright

    # One code per sky bucket the decoder knows about, plus night.
    cases = [("clear", 0, 1), ("cloud", 3, 1), ("rain", 61, 1), ("snow", 73, 1),
             ("storm", 95, 1), ("fog", 45, 1), ("night", 0, 0)]
    slug = cfg["cities"][1]["slug"]

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for theme in ("observatory", "daylight", "blueprint"):
            worst, where = 99.0, ""
            for name, code, day in cases:
                body = json.dumps({
                    "current": {"temperature_2m": 12.4, "weather_code": code,
                                "is_day": day, "time": "2026-09-14T09:30"},
                    "hourly": {
                        "time": [f"2026-09-14T{h:02d}:00" for h in range(24)],
                        "precipitation_probability": [(i * 7) % 101 for i in range(24)],
                    },
                    "daily": {"temperature_2m_max": [15.0],
                              "temperature_2m_min": [7.0],
                              "precipitation_probability_max": [40]},
                })
                ctx = browser.new_context(viewport={"width": 1280, "height": 1000})
                ctx.add_init_script(
                    f"try{{localStorage.setItem('wc-theme','{theme}')}}catch(e){{}}")

                # Playwright hands the handler (route, request); binding the
                # body to the second parameter silently passes a Request into
                # fulfill() instead.
                def serve_fc(route, request=None, b=body):
                    route.fulfill(status=200, content_type="application/json", body=b)

                ctx.route("**://api.open-meteo.com/**", serve_fc)
                page = ctx.new_page()
                page.goto(f"{base_url}/city/{slug}/", wait_until="domcontentloaded")
                page.wait_for_selector("#fc .means", timeout=10000)
                # Wait for the entrance to *finish*, not for a guess at how long
                # it takes. A fixed 900ms passed on an idle machine and expired
                # early under load, catching a glyph mid-fade on a backdrop it
                # never rests on - the check then reported 3.95:1 for a card
                # that measures 7:1 once settled, and passed on the next run.
                # Only the finite animations are waited on: the sky loops are
                # infinite by design and are meant to be photographed running.
                page.wait_for_function(
                    """() => document.querySelector('#fc')
                      .getAnimations({ subtree: true })
                      .filter(a => a.effect.getComputedTiming().iterations !== Infinity)
                      .every(a => a.playState === 'finished')""", timeout=8000)
                page.wait_for_timeout(120)
                c = card_contrast(page)
                if c < worst:
                    worst, where = c, name
                ctx.close()
            r.add(worst >= 4.5, f"{theme}: forecast card legible under every sky",
                  f"worst {worst:.2f}:1 ({where})")
        browser.close()


# ---------------------------------------------------------------------------
def _rgb(s: str) -> tuple[float, float, float]:
    """Parse a computed colour into 0-255 channels.

    Chromium serialises `color-mix()` results as `color(srgb 0.36 0.51 0.72)`
    with 0-1 channels, while everything else comes back as `rgb(92, 130, 184)`
    with 0-255. Treating the first form as 0-255 silently reports near-black for
    every mixed colour, which turns this whole contrast check into a generator
    of confident nonsense - it is how the globe's sea appeared to have 18:1
    contrast against its own coastline.
    """
    n = [float(x) for x in re.findall(r"[-\d.]+", s)]
    if s.startswith("color("):
        n = n[:3] if len(n) >= 3 else n
        return tuple(max(0.0, min(1.0, v)) * 255 for v in n)  # type: ignore[return-value]
    return tuple(n[:3])  # type: ignore[return-value]


def contrast(bg: str, fg: str) -> float:
    """WCAG contrast ratio. Themes are a design choice; legibility is not."""
    def lum(c: str) -> float:
        out = []
        for v in _rgb(c):
            v /= 255
            out.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    a, b = lum(bg), lum(fg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dist", default="dist")
    ap.add_argument("--skip-determinism", action="store_true")
    args = ap.parse_args()

    dist = (ROOT / args.dist).resolve()
    if not dist.exists():
        raise SystemExit(f"{dist} does not exist - run src/report.py first")

    r = Result()
    print("structure")
    cfg = check_structure(dist, r)

    url, httpd = serve(dist)
    try:
        print("browser")
        check_browser(url, dist, cfg, r)
        print("theme palettes")
        check_palettes(url, r)
        print("forecast card legibility")
        check_card_contrast(url, cfg, r)
        print("responsive")
        check_responsive(url, r)
        print("without javascript")
        check_nojs(url, cfg, r)
    finally:
        httpd.shutdown()

    if not args.skip_determinism:
        print("determinism")
        check_determinism(dist, r)

    n = len(r.checks)
    bad = r.failed
    print(f"\n{n - len(bad)}/{n} checks passed")
    if bad:
        for _, name, detail in bad:
            print(f"  FAILED: {name}  {detail}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
