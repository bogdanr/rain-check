"""Screenshot a static directory at fixed viewports, for design review.

Design-branch tooling only (plan 2026-09-24-site-redesign-atlas-noir, Phase 0).
Used for both the baseline of the current site and the prototype, so every
before/after pair is taken with the same browser, viewports and wait rules.

Usage:
    python design/shoot.py baseline            # dist/ -> design/baseline/
    python design/shoot.py proto               # design/proto/ -> design/proto/shots/
"""

from __future__ import annotations

import http.server
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}
THEMES = ["observatory", "daylight", "blueprint"]


def serve(directory: Path) -> tuple[str, socketserver.TCPServer]:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):  # noqa: D102
            pass

    handler = lambda *a, **k: Handler(*a, directory=str(directory), **k)  # noqa: E731

    class Quiet(socketserver.TCPServer):
        allow_reuse_address = True

        def handle_error(self, request, client_address):  # noqa: D102
            pass

    httpd = Quiet(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/", httpd


def first_load_kb(dist: Path) -> float:
    """Same accounting as report._first_load_kb: the HTML plus every eager
    stylesheet and script it references (figures and textures excluded)."""
    import re
    html = (dist / "index.html").read_text(encoding="utf-8")
    total = len(html.encode())
    refs = re.findall(r'<(?:link rel="stylesheet"|script) [^>]*?(?:href|src)="/([^"]+)"', html)
    refs += re.findall(r'<link rel="preload" as="fetch" href="/([^"]+)"', html)
    for rel in refs:
        p = dist / rel
        if p.is_file():
            total += p.stat().st_size
    return total / 1024


def shoot_baseline() -> None:
    dist, out = ROOT / "dist", ROOT / "design" / "baseline"
    out.mkdir(parents=True, exist_ok=True)
    print(f"first load ~{first_load_kb(dist):.0f} KB")
    url, httpd = serve(dist)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for vp, (w, h) in VIEWPORTS.items():
            for theme in THEMES:
                ctx = browser.new_context(viewport={"width": w, "height": h})
                ctx.add_init_script(
                    f"try{{localStorage.setItem('wc-theme','{theme}')}}catch(e){{}}")
                page = ctx.new_page()
                page.goto(url, wait_until="networkidle")
                page.wait_for_timeout(2500)          # globe relief paints late
                page.screenshot(path=str(out / f"{vp}-{theme}-top.png"))
                page.screenshot(path=str(out / f"{vp}-{theme}-full.png"),
                                full_page=True)
                ctx.close()
                print(f"  {vp} {theme}")
        browser.close()
    httpd.shutdown()


def shoot_proto() -> None:
    proto = ROOT / "design" / "proto"
    out = proto / "shots"
    out.mkdir(parents=True, exist_ok=True)
    url, httpd = serve(proto)
    variants = [""]          # black ground + cyan accent chosen 2026-09-25
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                           "--enable-unsafe-swiftshader"])
        for vp, (w, h) in VIEWPORTS.items():
            for q in variants:
                tag = q.lstrip("?").replace("=", "-") or "noir"
                page = browser.new_page(viewport={"width": w, "height": h})
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url + "index.html" + q, wait_until="networkidle")
                page.wait_for_timeout(2500)
                page.screenshot(path=str(out / f"{vp}-{tag}-1-hero.png"))
                for i, sid in enumerate(["promise", "umbrella", "desert"], start=2):
                    page.evaluate(
                        f"document.getElementById('{sid}').scrollIntoView({{block:'start',behavior:'instant'}})")
                    page.wait_for_timeout(1600)       # camera tween is 900 ms
                    page.screenshot(path=str(out / f"{vp}-{tag}-{i}-{sid}.png"))
                if vp == "desktop" and not q:
                    page.screenshot(path=str(out / "desktop-noir-full.png"), full_page=True)
                print(f"  {vp} {tag}" + (f"  ERRORS: {errors}" if errors else ""))
                page.close()
        browser.close()
    httpd.shutdown()


if __name__ == "__main__":
    {"baseline": shoot_baseline, "proto": shoot_proto}[sys.argv[1]]()
