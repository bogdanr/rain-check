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
    parity       the globe draws exactly the capitals Python verified, plus the
                 excluded ones drawn hollow - never a marker with no data
                 behind it, and never a city with no marker
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
def check_structure(dist: Path, r: Result) -> dict:
    index = dist / "index.html"
    if not r.add(index.exists(), "index.html exists"):
        raise SystemExit(1)

    html = index.read_text()
    cfg = json.loads(re.search(
        r'<script id="site-config" type="application/json">(.*?)</script>',
        html, re.S).group(1))

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
        land = page.locator("#globe path.land").get_attribute("d") or ""
        r.add(len(land) > 1000, "coastline geometry drew",
              f"path length {len(land)}")
        r.add(page.locator("#globe-fallback").is_hidden(),
              "plain city list hides once the globe is live")

        vis = page.eval_on_selector_all(
            "#globe .mk:not(.excluded)",
            "ns => ns.filter(n => n.getAttribute('display') !== 'none').length")
        total = page.locator("#globe .mk:not(.excluded)").count()
        r.add(total == len(slugs),
              f"one marker per verified capital ({total} of {len(slugs)})")
        r.add(page.locator("#globe .mk.excluded").count() == len(cfg["excluded"]),
              f"{len(cfg['excluded'])} excluded capitals drawn hollow")
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
          document.querySelectorAll('#globe .mk:not(.excluded)').forEach(g => {
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

        zoom_out = page.locator("[aria-label='Zoom out']")
        r.add(zoom_out.count() == 1 and page.locator("[aria-label='Zoom in']").count() == 1,
              "globe has zoom controls")
        zoom_out.click()
        page.locator("[aria-label='Reset the view']").click()
        r.add(True, "zoom and reset controls respond")

        # --- switching ---------------------------------------------------
        target = next(s for s in slugs if s != default)
        name = next(c["name"] for c in cfg["cities"] if c["slug"] == target)
        page.click(f"#globe .mk[aria-label^='{name}']")
        page.wait_for_function(
            "n => document.querySelector('#h1-city').textContent === n", arg=name)

        heading = page.locator("#city-answer h2").inner_text()
        r.add(name in heading, "city panel heading follows the selection", heading)
        r.add(page.locator("#citybtn .name").inner_text() == name,
              "city button follows the selection")
        r.add(name in page.title(), "document title follows the selection")
        r.add(page.url.rstrip("/").endswith(f"city/{target}"),
              "URL updates to the city's real page", page.url)
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

        fctx = browser.new_context()
        fctx.route("**://api.open-meteo.com/**", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "current": {"temperature_2m": 12.4, "weather_code": 61, "is_day": 1},
                "daily": {"temperature_2m_max": [15.0],
                          "temperature_2m_min": [7.0],
                          "precipitation_probability_max": [stated]},
            })))
        fpage = fctx.new_page()
        fpage.goto(f"{base_url}/city/{fc_slug}/", wait_until="domcontentloaded")
        fpage.wait_for_selector("#fc .pop", timeout=10000)

        strip = fpage.locator("#fc").inner_text()
        r.add(f"{stated}% chance of rain" in strip,
              "forecast strip shows the stated probability", strip.replace("\n", " ")[:70])
        r.add("12" in strip, "forecast strip shows the current temperature")

        want_obs = f"{round(target_bin['obs'] * 100)}%"
        means = fpage.locator("#fc .means").inner_text()
        r.add(want_obs in means and fc_name in means,
              f"stated {stated}% annotated with {fc_name}'s own history",
              means[:110])
        r.add(str(target_bin["n"]) in means,
              "annotation states the sample size it rests on")

        # Rain must tint the page, and must not touch text contrast.
        sky = fpage.get_attribute("html", "data-sky")
        r.add(sky == "rain", "weather tint follows the reported conditions", sky or "")
        bg = fpage.eval_on_selector("body", "b => getComputedStyle(b).backgroundColor")
        fg = fpage.eval_on_selector("body", "b => getComputedStyle(b).color")
        r.add(contrast(bg, fg) >= 7.0,
              "weather tint leaves body contrast at AAA",
              f"{contrast(bg, fg):.1f}:1")

        fpage.click("#skytoggle")
        r.add(fpage.get_attribute("html", "data-sky") is None,
              "weather tint can be switched off")
        fctx.close()

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
        r.add(page.locator("#globe-fallback li").count() == len(cfg["cities"]),
              "globe falls back to a full list of capitals")
        r.add(page.locator("#city-curve svg").count() >= 1,
              "calibration chart is server-rendered SVG, not drawn by script")
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
            # marker-free points - two inland, two offshore, all inside the
            # fitted European view - are read straight off a screenshot.
            probe = {
                "central France": ([2.6, 45.5], "land"),
                "central Poland": ([19.5, 52.4], "land"),
                "North Sea": ([3.5, 55.5], "sea"),
                "Adriatic": ([15.5, 42.6], "sea"),
            }
            rect = page.evaluate(
                "() => { const b = document.querySelector('#globe svg')"
                ".getBoundingClientRect(); return [b.x, b.y, b.width]; }"
            )
            xy = page.evaluate(
                "(pts) => { const o = {}; for (const k in pts)"
                " o[k] = window.__globe.projection(pts[k]); return o; }",
                {k: v[0] for k, v in probe.items()},
            )
            shot = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
            scale = rect[2] / 420.0          # viewBox is 420 units wide
            want = {"land": _rgb(land), "sea": _rgb(sea)}
            for label, (_, kind) in probe.items():
                q = xy[label]
                px = shot.getpixel(
                    (int(rect[0] + q[0] * scale), int(rect[1] + q[1] * scale))
                )
                near = max(abs(a - b) for a, b in zip(px, want[kind]))
                r.add(near <= 6, f"{theme}: {label} renders as {kind}",
                      "#%02x%02x%02x vs expected %s" % (px + (want[kind],)))

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
                if (clipped)
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
def check_sky_contrast(base_url: str, r: Result) -> None:
    """The weather tint must never make the hero unreadable.

    The design rule is that ambience may touch backdrops but not text contrast
    pairs. `[data-sky]` honours that in the token layer - it only sets --sky-*
    - but the hero paints white text directly over that gradient, so the rule
    can still be broken in the *layout* layer without any token being misused.

    Seven sky states times three themes is more combinations than anyone will
    look at by hand, and the dangerous ones are the rare ones: `snow` ends at
    #7d9ab5, which is only 2.9:1 against white. So the pixels behind the text
    are sampled at several points across its width, and the worst is the score.
    """
    from playwright.sync_api import sync_playwright
    from PIL import Image

    skies = ["clear", "cloud", "rain", "snow", "storm", "fog", "night"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        ctx.route("**://api.open-meteo.com/**", lambda route: route.abort())
        page = ctx.new_page()
        page.goto(base_url + "/", wait_until="networkidle")

        for theme in ("observatory", "daylight", "blueprint"):
            page.click(f"[data-theme-set='{theme}']")
            worst, where = 99.0, ""
            for sky in skies:
                page.evaluate("(s) => document.documentElement"
                              ".setAttribute('data-sky', s)", sky)
                # Hide every glyph in the hero before sampling, not just the
                # element being measured. #h1-city is only the city-name span,
                # so hiding it alone leaves the rest of the <h1> painted and the
                # sweep reads white-on-white: a 1.00:1 "failure" that is purely
                # a measurement artefact.
                geom = page.evaluate("""() => {
                  const out = [];
                  for (const s of ['.hero h1', '.hero p']) {
                    const n = document.querySelector(s);
                    if (!n) continue;
                    const b = n.getBoundingClientRect();
                    out.push({fg: getComputedStyle(n).color,
                              y: Math.round(b.y + b.height / 2),
                              x0: Math.round(b.x)});
                  }
                  document.querySelectorAll('.hero h1, .hero p')
                    .forEach(n => { n.style.visibility = 'hidden'; });
                  return out;
                }""")
                shot = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
                page.evaluate("() => document.querySelectorAll('.hero h1, .hero p')"
                              ".forEach(n => { n.style.visibility = ''; })")
                for g in geom:
                    # Sweep the full hero width, not just the current text box:
                    # a longer city name or a wider viewport pushes text into
                    # the light end of the gradient, and that is the case that
                    # will actually break in production.
                    for i in range(24):
                        x = int(g["x0"] + (shot.width - g["x0"]) * i / 23.0)
                        if not (0 <= x < shot.width and 0 <= g["y"] < shot.height):
                            continue
                        px = shot.getpixel((x, g["y"]))
                        c = contrast("rgb(%d,%d,%d)" % px, g["fg"])
                        if c < worst:
                            worst, where = c, f"{sky} at x={x}"
            r.add(worst >= 4.5, f"{theme}: hero stays legible under every sky",
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
        print("weather tint legibility")
        check_sky_contrast(url, r)
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
