"""Smoke-test a built Atlas site in a real browser, at its published base path.

    python src/check_atlas.py --base /rain-check/        # after src/atlas.py

dist/ is served as <tmp>/<base>/ so every absolute asset URL resolves exactly
as it will on Pages. Checks: the home page and a city page render their own
city (pre-rendered and live), the league links every city, a switch moves the
address to the city's page, the old ?city= link still lands, the directory and
sitemap list every page, nothing 404s and no script errors.

The live parts never touch the network: Open-Meteo, EUMETSAT and NASA GIBS
are answered from src/fixtures/atlas/ and the clock is frozen with ?now=, so
the Sun, Moon, legend times and card are the same on every run. Also checked:
the volume shaders compile on SwiftShader, the sky and the card degrade
quietly when every live source is blocked, and a rapid city switch leaves
the card on the last city.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import re
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from sitebuild import DIST

FIX = Path(__file__).resolve().parent / "fixtures" / "atlas"
# The fixture set's frames: IR newest at 03:00 UTC (ir-caps.xml), IMERG
# newest at 23:30 UTC the day before (imerg-domain.xml).
NOW = "2026-09-26T05:00:00Z"
IR_T = "2026-09-26T03:00:00Z"


def live_fixtures(ctx, blocked: list[str]) -> None:
    """Answer every live source from the fixtures; refuse anything else."""
    def serve(route, name, ctype):
        route.fulfill(status=200, content_type=ctype, body=(FIX / name).read_bytes(),
                      headers={"Access-Control-Allow-Origin": "*"})

    def eumetsat(route):
        u = route.request.url
        if "GetCapabilities" in u:
            serve(route, "ir-caps.xml", "text/xml")
        else:
            serve(route, "ir-latest.jpg" if IR_T in u else "ir-prev.jpg", "image/jpeg")

    def gibs(route):
        if "/wmts/" in route.request.url:
            serve(route, "imerg-domain.xml", "text/xml")
        else:
            serve(route, "imerg.png", "image/png")

    def other(route):
        host = route.request.url.split("/")[2]
        if host.startswith("127.0.0.1"):
            route.continue_()
        else:
            blocked.append(route.request.url[:120])
            route.abort()

    ctx.route("**/*", other)       # registered first, so matched last
    ctx.route("**://api.open-meteo.com/**", lambda r: serve(r, "forecast.json", "application/json"))
    ctx.route("**://view.eumetsat.int/**", eumetsat)
    ctx.route("**://gibs.earthdata.nasa.gov/**", gibs)


def legend(p, has: str, ms: int = 30000) -> str:
    """The sky legend once it mentions `has` (or whatever it says at timeout)."""
    try:
        p.wait_for_function("(s) => document.querySelector('#sky-src').textContent.includes(s)",
                            arg=has, timeout=ms)
    except Exception:
        pass
    return p.inner_text("#sky-src")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="/")
    ap.add_argument("--dist", default=str(DIST))
    args = ap.parse_args()
    base = "/" + args.base.strip("/") + "/" if args.base.strip("/") else "/"
    dist = Path(args.dist).resolve()

    tmp = Path(tempfile.mkdtemp())
    mount = tmp / base.strip("/") if base != "/" else None
    root = tmp if mount else dist
    if mount:
        mount.parent.mkdir(parents=True, exist_ok=True)
        mount.symlink_to(dist)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    B = f"http://127.0.0.1:{srv.server_address[1]}{base}"

    fails: list[str] = []

    def check(ok: bool, what: str) -> None:
        print(("ok   " if ok else "FAIL ") + what)
        if not ok:
            fails.append(what)

    n_pages = len(list((dist / "city").glob("*/index.html"))) + 1
    sm = (dist / "sitemap.xml").read_text()
    check(sm.count("<url>") == n_pages + 1, f"sitemap lists {n_pages} city pages + directory")
    check(all(u.startswith("https://") for u in re.findall(r"<loc>(.*?)</loc>", sm)),
          "sitemap URLs are absolute https")

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                     "--enable-unsafe-swiftshader"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        outside: list[str] = []
        live_fixtures(ctx, outside)
        p = ctx.new_page()
        errs: list[str] = []
        bad: list[str] = []
        p.on("pageerror", lambda e: errs.append(str(e)))
        p.on("response", lambda r: r.status >= 400 and bad.append(f"{r.status} {r.url}"))

        p.goto(B, wait_until="networkidle")
        p.wait_for_timeout(1500)
        check("Bucharest" in p.inner_text("h1"), "home renders the default city")
        check(p.locator("#lg tbody a").count() == n_pages, "league links every city")
        canon = p.locator('link[rel="canonical"]').get_attribute("href")
        check(canon.startswith("https://") and canon.endswith(base), f"home canonical {canon}")
        link = p.locator("#lg tbody a").nth(3)
        href = link.get_attribute("href")
        link.click()
        p.wait_for_timeout(1200)
        check(p.url.endswith(href), f"switching city moves the address to {href}")

        p.goto(B + "city/tokyo/", wait_until="networkidle")
        p.wait_for_timeout(1200)
        check("Tokyo" in p.inner_text("h1") and "Tokyo" in p.title(), "city page renders its city")
        p.goto(B + "?city=paris", wait_until="networkidle")
        p.wait_for_timeout(1200)
        check(p.url.endswith("/city/paris/") and "Paris" in p.inner_text("h1"), "old ?city= link lands")
        p.goto(B + "cities/", wait_until="networkidle")
        check(p.locator("main li a").count() == n_pages, "directory links every city")

        # --- the live sky and card, from fixtures, at a frozen instant ----
        p.goto(B + f"?now={NOW}", wait_until="networkidle")
        s = legend(p, "Rain:")
        check("EUMETSAT infrared · 03:00 UTC (2 h ago)" in s, f"legend dates the cloud frame: {s!r}")
        check("NASA IMERG · 23:30 UTC" in s, "legend dates the rain frame")
        check("Sun and Moon at 05:00 UTC (fixed)" in s, "Sun and Moon follow the frozen clock")
        check("Flat clouds" in s, "software GL gets the flat cloud layer by default")
        p.wait_for_function("() => !document.querySelector('#now').classList.contains('pending')",
                            timeout=15000)
        card = p.locator("#now")
        check(card.is_visible() and "fail" not in (card.get_attribute("class") or ""),
              "forecast card fills from the (mocked) live forecast")
        check("%" in p.inner_text("#now-said"), "card states the chance of rain")
        check("Bucharest" in p.text_content("#now-h"), "card names the city")
        check("Stale" not in s, "fresh frames carry no stale warning")

        # Ten hours after the cloud frame the service has clearly stalled.
        p.goto(B + "?now=2026-09-26T13:00:00Z", wait_until="networkidle")
        s = legend(p, "Stale")
        check("Stale: the clouds and rain shown are over 9 h old" in s, "legend warns when frames are stale")

        # The volume, forced on: its shaders must compile on SwiftShader
        # (a failed compile drops to flat, which the legend would then say).
        p.goto(B + f"?now={NOW}&sky=mid", wait_until="networkidle")
        s = legend(p, "Rain:")
        p.wait_for_timeout(1500)
        s = p.inner_text("#sky-src")
        check("Rain:" in s and "Flat clouds" not in s, "volume clouds and rain compile on SwiftShader")
        check(not p.evaluate("document.body.classList.contains('nogl')"), "globe still drawn with the volume on")

        # Rapid switch: the card ends on the last city, not the slowest reply.
        links = p.locator("#lg tbody a")
        last = links.nth(5)
        want = last.inner_text().strip()
        links.nth(4).click()
        last.click()
        p.wait_for_timeout(2000)
        check(want in p.text_content("#now-h"), f"card follows a rapid switch to {want}")

        check(not errs, "no script errors" + (f": {errs[:3]}" if errs else ""))
        check(not bad, "no failed requests" + (f": {bad[:3]}" if bad else ""))
        check(not outside, "no request left the test server" + (f": {outside[:3]}" if outside else ""))

        # --- every live source down: the page is the audit, and says so ---
        dctx = b.new_context(viewport={"width": 1440, "height": 900})
        dctx.route("**/*", lambda r: r.continue_() if r.request.url.split("/")[2].startswith("127.0.0.1")
                   else r.abort())
        d = dctx.new_page()
        derrs: list[str] = []
        d.on("pageerror", lambda e: derrs.append(str(e)))
        d.goto(B + f"?now={NOW}", wait_until="networkidle")
        s = legend(d, "unavailable")
        check("Satellite images unavailable" in s, "legend says when the satellites are unreachable")
        d.wait_for_function("() => document.querySelector('#now').classList.contains('fail')",
                            timeout=15000)
        check("could not be loaded" in d.inner_text("#now-said"), "card says the live forecast is down")
        check("Bucharest" in d.inner_text("h1") and d.locator(".hero-card").is_visible(),
              "verdict unaffected by the outage")
        check(not derrs, "no script errors with live sources down" + (f": {derrs[:3]}" if derrs else ""))
        dctx.close()

        q = b.new_context(java_script_enabled=False).new_page()
        q.goto(B + "city/tokyo/")
        check(q.locator(".hero-card").is_visible() and "Tokyo" in q.inner_text(".hero-card"),
              "verdict card is readable without JavaScript")
        b.close()
    srv.shutdown()
    if fails:
        raise SystemExit(f"{len(fails)} check(s) failed")
    print("all checks passed")


if __name__ == "__main__":
    main()
