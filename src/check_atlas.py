"""Smoke-test a built Atlas site in a real browser, at its published base path.

    python src/check_atlas.py --base /rain-check/        # after src/atlas.py

dist/ is served as <tmp>/<base>/ so every absolute asset URL resolves exactly
as it will on Pages. Checks: the home page and a city page render their own
city (pre-rendered and live), the league links every city, a switch moves the
address to the city's page, the old ?city= link still lands, the directory and
sitemap list every page, nothing 404s and no script errors.
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
        p = b.new_page(viewport={"width": 1440, "height": 900})
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
        check(not errs, "no script errors" + (f": {errs[:3]}" if errs else ""))
        check(not bad, "no failed requests" + (f": {bad[:3]}" if bad else ""))

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
