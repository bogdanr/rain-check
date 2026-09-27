"""Measure what the atlas page costs while nobody touches it.

Loads the built site in headless Chromium on this machine's real GPU (ANGLE
over EGL, not SwiftShader) and, for each scenario, records over a fixed
window after the page has settled:

  cpu     CPU % of Chromium's processes by type (renderer, gpu, browser),
          from /proc/<pid>/stat. 100 % = one core.
  gpu     render/compute engine busy % of Chromium's DRM clients, from
          /proc/<pid>/fdinfo (xe and i915 drivers).
  draws   globe frames per second, by kind, with their CPU and GPU time
          (EXT_disjoint_timer_query_webgl2 around Stage._draw).
  loops   JS time and calls per second of every requestAnimationFrame loop
          and timer, by the source file that scheduled it.
  chrome  Chrome's own ScriptDuration / RecalcStyle / Layout per second.

The page is not modified: everything is hooked from an init script.
Scenario names: blank, city, city-sky-off, city-no-bolts, city-wind,
city-wind-close, city-wind-debug, city-close, world, dim, dock.

  python src/perf_atlas.py                         # all scenarios, 10 s each
  python src/perf_atlas.py --only city,world --secs 20 --dpr 2
  python src/perf_atlas.py --json build/perf.json  # also keep the numbers
"""
import argparse
import functools
import http.server
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TCK = os.sysconf("SC_CLK_TCK")

NO_PULSE = ".mk .pulse { animation: none !important; } #pulse { display: none !important; }"
NO_BOLTS = {"rc-sky-bolts": "off"}

# (name, query string, localStorage, chapter to scroll to, extra CSS)
SCENARIOS = [
    ("blank", None, {}, None, ""),
    ("city", "", {}, None, ""),
    ("city-sky-off", "sky=off", {}, None, ""),
    ("city-no-bolts", "", NO_BOLTS, None, ""),
    ("city-no-bolts-no-pulse", "", NO_BOLTS, None, NO_PULSE),
    ("city-wind", "wind=on", {}, None, ""),
    ("city-wind-close", "wind=on", {}, "city-close", ""),
    ("city-wind-debug", "wind=debug", {}, None, ""),
    ("city-close", "", {}, "city-close", ""),
    ("world", "", {}, "world", ""),
    ("dim", "", {}, "dim", ""),
    ("dock", "", {}, "dock", ""),
    # Probes: the same views with one layer taken off the page.
    ("world-no-marks", "", {}, "world", "#marks { display: none !important; }"),
    ("world-no-sats", "", {}, "world", "#sats { display: none !important; }"),
    ("world-no-bolts", "", NO_BOLTS, "world", ""),
    ("dim-no-marks", "", {}, "dim", "#marks { display: none !important; }"),
    ("world-no-filter", "", {}, "world", ".mk circle { filter: none !important; }"),
    ("world-no-opacity", "", {}, "world", ".mk { opacity: 1 !important; }"),
    ("dim-no-filter", "", {}, "dim", ".mk circle { filter: none !important; }"),
    ("dim-no-opacity", "", {}, "dim", ".mk { opacity: 1 !important; }"),
    ("city-no-bolts-no-filter", "", NO_BOLTS, None, ".mk circle { filter: none !important; }"),
]

# Trace events worth adding up, by thread (names from Chrome's timeline).
TRACE_CATS = ["devtools.timeline", "disabled-by-default-devtools.timeline", "blink", "cc", "gpu", "viz"]

HOOK = r"""
(() => {
  const P = window.__perf = { raf: {}, timers: {}, draws: {}, gpu: {}, disjoint: 0 };
  const where = () => {
    const m = (new Error().stack || '').match(/\/assets\/([a-z]+)\.[0-9a-f]+\.js/);
    return m ? m[1] : 'other';
  };
  const bump = (o, k, dt) => { const e = o[k] || (o[k] = { n: 0, ms: 0 }); e.n++; e.ms += dt; };
  const rAF = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = function (cb) {
    const k = where();
    return rAF(function (t) {
      const t0 = performance.now();
      try { return cb(t); } finally { bump(P.raf, k, performance.now() - t0); }
    });
  };
  for (const name of ['setTimeout', 'setInterval']) {
    const orig = window[name].bind(window);
    window[name] = function (cb, ms, ...a) {
      if (typeof cb !== 'function') return orig(cb, ms, ...a);
      const k = where() + ':' + name + '(' + (ms | 0) + ')';
      return orig(function () {
        const t0 = performance.now();
        try { return cb.apply(this, arguments); } finally { bump(P.timers, k, performance.now() - t0); }
      }, ms, ...a);
    };
  }
  P.reset = () => { P.raf = {}; P.timers = {}; P.draws = {}; P.gpu = {}; P.disjoint = 0; };
  let AS;
  Object.defineProperty(window, 'AtlasStage', {
    configurable: true, get() { return AS; },
    set(S) { AS = S; patch(S.prototype); }
  });
  function patch(pr) {
    const draw = pr._draw, pend = [];
    pr._draw = function (moving, now, flashOnly) {
      window.__stage = this;
      const gl = this.gl;
      if (this._tq === undefined) this._tq = gl.getExtension('EXT_disjoint_timer_query_webgl2');
      const ext = this._tq, acc0 = this.acc;
      let q = null;
      if (ext) { q = gl.createQuery(); gl.beginQuery(ext.TIME_ELAPSED_EXT, q); }
      const t0 = performance.now();
      try { return draw.apply(this, arguments); } finally {
        const dt = performance.now() - t0;
        if (q) gl.endQuery(ext.TIME_ELAPSED_EXT);
        const kind = flashOnly ? 'flash' : this.acc > acc0 ? 'vol-refine'
          : moving && this._volW > 0 ? 'vol-move' : this._volW > 0 ? 'vol-cached' : 'globe';
        bump(P.draws, kind, dt);
        if (q) pend.push([q, kind]);
        const dis = ext && gl.getParameter(ext.GPU_DISJOINT_EXT);
        for (let i = pend.length - 1; i >= 0; i--) {
          const [qq, kk] = pend[i];
          if (!gl.getQueryParameter(qq, gl.QUERY_RESULT_AVAILABLE)) continue;
          if (dis) P.disjoint++; else bump(P.gpu, kk, gl.getQueryParameter(qq, gl.QUERY_RESULT) / 1e6);
          gl.deleteQuery(qq); pend.splice(i, 1);
        }
      }
    };
  }
})();
"""

STATE = """() => { const s = window.__stage || {}, c = s.cam || {};
  const w = document.querySelector('#wind');
  return { tier: s.tier, spin: s.spin, dim: c.dim, k: c.k, acc: s.acc, volW: s._volW,
           current: s.current, dpr: s.dpr && s.dpr(), canvas: s.c && [s.c.width, s.c.height],
           wind: w && !w.hidden ? { arrows: w.dataset.arrows, sea: w.dataset.sea, waves: s.c && s.c.dataset.waves, waveN: s.waveN, baseN: s.baseN } : null,
           anims: document.getAnimations().filter(x => x.playState === 'running').map(x => {
             const t = x.effect && x.effect.target;
             return (x.animationName || x.constructor.name) + ' on ' + (t ? t.tagName.toLowerCase() + '.' + String(t.getAttribute('class') || '') : '?'); }),
           marks: performance.getEntriesByType('mark').map(m => m.name).filter(n => n.startsWith('rc:')) }; }"""


def proc_tree(root):
    kids = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            ppid = int(open(f"/proc/{d}/stat").read().rsplit(")", 1)[1].split()[1])
        except OSError:
            continue
        kids.setdefault(ppid, []).append(int(d))
    out, todo = [], [root]
    while todo:
        p = todo.pop()
        out.append(p)
        todo += kids.get(p, [])
    return out


def ptype(pid):
    # Chromium rewrites its argv into one space-separated process title.
    try:
        m = re.search(r"--type=(\S+)", open(f"/proc/{pid}/cmdline", errors="replace").read())
    except OSError:
        return None
    if not m:
        return "browser"
    t = m.group(1)
    return "gpu" if t == "gpu-process" else t


def sample(root):
    """CPU jiffies by process type, and DRM engine cycles by client."""
    cpu, drm = {}, {}
    for pid in proc_tree(root):
        t = ptype(pid)
        try:
            f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
            cpu[t] = cpu.get(t, 0) + int(f[11]) + int(f[12])
            fds = os.listdir(f"/proc/{pid}/fdinfo")
        except OSError:
            continue
        for fd in fds:
            try:
                kv = dict(l.split(":", 1) for l in open(f"/proc/{pid}/fdinfo/{fd}").read().splitlines() if ":" in l)
            except OSError:
                continue
            if "drm-client-id" not in kv:
                continue
            drm[kv["drm-client-id"].strip()] = {k: int(v.split()[0]) for k, v in kv.items()
                                               if k.startswith(("drm-cycles-", "drm-total-cycles-", "drm-engine-"))}
    return time.monotonic(), cpu, drm


def diff(a, b):
    dt = b[0] - a[0]
    cpu = {k: 100 * (b[1].get(k, 0) - a[1].get(k, 0)) / TCK / dt for k in b[1]}
    gpu = {}
    for cid, e in b[2].items():
        e0 = a[2].get(cid)
        if not e0:
            continue
        for k, v in e.items():
            if k.startswith("drm-cycles-"):          # xe: busy cycles / GPU timestamp cycles
                eng = k[11:]
                tot = e.get("drm-total-cycles-" + eng, 0) - e0.get("drm-total-cycles-" + eng, 0)
                if tot > 0:
                    gpu[eng] = gpu.get(eng, 0) + 100 * (v - e0.get(k, 0)) / tot
            elif k.startswith("drm-engine-") and "capacity" not in k:   # i915: ns busy
                eng = k[11:]
                gpu[eng] = gpu.get(eng, 0) + 100 * (v - e0.get(k, 0)) / 1e9 / dt
    return dt, cpu, gpu


def serve(dist):
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(dist))
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{s.server_address[1]}/"


def per_s(o, secs):
    return {k: {"n": round(v["n"] / secs, 1), "ms": round(v["ms"] / secs, 2)} for k, v in sorted(o.items())}


def trace_sum(raw, secs):
    """Complete ('X') trace events summed by thread and name, in ms/s."""
    ev = json.loads(raw)
    ev = ev.get("traceEvents", ev) if isinstance(ev, dict) else ev
    names = {(e["pid"], e["tid"]): e["args"]["name"] for e in ev
             if e.get("ph") == "M" and e.get("name") == "thread_name"}
    tot = {}
    for e in ev:
        if e.get("ph") == "X" and e.get("dur"):
            k = names.get((e["pid"], e["tid"]), "?") + " " + e["name"]
            tot[k] = tot.get(k, 0) + e["dur"] / 1000
    top = sorted(tot.items(), key=lambda kv: -kv[1])[:18]
    return {k: round(v / secs, 1) for k, v in top}


def run(pw, base, name, query, store, chapter, css, a):
    mark = f"--rc-perf={uuid.uuid4().hex}"
    args = ["--use-gl=angle", "--use-angle=gl-egl", "--ignore-gpu-blocklist", "--enable-gpu", mark]
    b = pw.chromium.launch(args=args)
    root = next(int(d) for d in os.listdir("/proc") if d.isdigit() and
                mark in open(f"/proc/{d}/cmdline", errors="replace").read())
    ctx = b.new_context(viewport={"width": a.width, "height": a.height}, device_scale_factor=a.dpr,
                        reduced_motion="reduce" if a.reduce else "no-preference")
    ctx.add_init_script(HOOK)
    if store:
        ctx.add_init_script("try { Object.entries(" + json.dumps(store) +
                            ").forEach(([k, v]) => localStorage.setItem(k, v)); } catch (e) {}")
    if css:
        ctx.add_init_script("document.addEventListener('DOMContentLoaded', () => {"
                            " const s = document.createElement('style'); s.textContent = " + json.dumps(css) +
                            "; document.head.appendChild(s); });")
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    cdp = ctx.new_cdp_session(page)
    cdp.send("Performance.enable")
    if query is None:
        page.goto("about:blank")
    else:
        page.goto(base + ("?" + query if query else ""), wait_until="load")
        page.wait_for_function("window.__stage && window.__stage.hasTex", timeout=30000)
        try:
            page.wait_for_function("performance.getEntriesByName('rc:clouds').length > 0", timeout=25000)
        except Exception:
            errs.append("clouds did not arrive in 25 s")
        if chapter:
            page.evaluate(f"document.querySelector('[data-stage=\"{chapter}\"]').scrollIntoView({{block: 'center'}})")
    page.wait_for_timeout(a.settle * 1000)
    page.evaluate("window.__perf && window.__perf.reset()")
    m0 = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
    if a.trace and query is not None:
        b.start_tracing(page=page, categories=TRACE_CATS)
    s0 = sample(root)
    page.wait_for_timeout(a.secs * 1000)
    s1 = sample(root)
    raw = b.stop_tracing() if a.trace and query is not None else None
    m1 = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
    perf = page.evaluate("window.__perf ? {raf: __perf.raf, timers: __perf.timers, draws: __perf.draws, gpu: __perf.gpu, disjoint: __perf.disjoint} : null")
    state = page.evaluate(STATE) if query is not None else {}
    renderer = page.evaluate("""() => { const gl = document.createElement('canvas').getContext('webgl2');
        const e = gl && gl.getExtension('WEBGL_debug_renderer_info');
        return gl ? gl.getParameter(e ? e.UNMASKED_RENDERER_WEBGL : gl.RENDERER) : 'none'; }""")
    b.close()
    dt, cpu, gpu = diff(s0, s1)
    chrome = {k: round(1000 * (m1.get(k, 0) - m0.get(k, 0)) / dt, 1)
              for k in ("TaskDuration", "ScriptDuration", "RecalcStyleDuration", "LayoutDuration")}
    r = {"name": name, "secs": round(dt, 1), "cpu": {k: round(v, 1) for k, v in sorted(cpu.items())},
         "gpu": {k: round(v, 1) for k, v in sorted(gpu.items()) if v > 0.05}, "chrome_ms_per_s": chrome,
         "state": state, "renderer": renderer, "errors": errs}
    if raw:
        r["trace_ms_per_s"] = trace_sum(raw, dt)
    if perf:
        r["draws"] = per_s(perf["draws"], dt)
        r["gpu_ms_per_s"] = {k: round(v["ms"] / dt, 2) for k, v in sorted(perf["gpu"].items())}
        r["gpu_ms_per_draw"] = {k: round(v["ms"] / v["n"], 2) for k, v in sorted(perf["gpu"].items()) if v["n"]}
        r["raf"] = per_s(perf["raf"], dt)
        r["timers"] = per_s(perf["timers"], dt)
        r["disjoint"] = perf["disjoint"]
    return r


def show(r):
    cpu = r["cpu"]
    tot = sum(cpu.values())
    print(f"\n== {r['name']}  ({r['secs']} s)  tier={r['state'].get('tier')} spin={r['state'].get('spin')} "
          f"dim={r['state'].get('dim')} canvas={r['state'].get('canvas')}")
    print("   cpu %%   total %.1f | %s" % (tot, "  ".join(f"{k} {v}" for k, v in cpu.items() if v >= 0.1)))
    print("   gpu %%   %s" % ("  ".join(f"{k} {v}" for k, v in r["gpu"].items()) or "-"))
    c = r["chrome_ms_per_s"]
    print(f"   chrome  task {c['TaskDuration']} ms/s, script {c['ScriptDuration']}, style {c['RecalcStyleDuration']}, layout {c['LayoutDuration']}")
    if "draws" in r:
        print("   globe   " + ("  ".join(f"{k}: {v['n']}/s cpu {v['ms']} ms/s gpu {r['gpu_ms_per_s'].get(k, '?')} ms/s "
                                        f"({r['gpu_ms_per_draw'].get(k, '?')} ms each)" for k, v in r["draws"].items()) or "no frames"))
        print("   loops   " + ("  ".join(f"{k}: {v['n']}/s {v['ms']} ms/s" for k, v in r["raf"].items()) or "-"))
        busy = {k: v for k, v in r["timers"].items() if v["n"]}
        if busy:
            print("   timers  " + "  ".join(f"{k}: {v['n']}/s {v['ms']} ms/s" for k, v in busy.items()))
    if r["state"].get("wind"):
        print(f"   wind    {r['state']['wind']}")
    if r["state"].get("anims"):
        print(f"   css     running: {', '.join(r['state']['anims'])}")
    if r.get("trace_ms_per_s"):
        print("   trace   (ms/s, nested events overlap)")
        for k, v in r["trace_ms_per_s"].items():
            print(f"           {v:7.1f}  {k}")
    for e in r["errors"]:
        print(f"   !! {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    ap.add_argument("--only", help="comma-separated scenario names")
    ap.add_argument("--secs", type=float, default=10)
    ap.add_argument("--settle", type=float, default=6, help="seconds after load/scroll before measuring")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--dpr", type=float, default=1)
    ap.add_argument("--reduce", action="store_true", help="prefers-reduced-motion: reduce")
    ap.add_argument("--trace", action="store_true", help="also record a Chrome trace and add up its events")
    ap.add_argument("--json", help="write the results here")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    base = serve(a.dist)
    want = set(a.only.split(",")) if a.only else None
    out = []
    with sync_playwright() as pw:
        for name, query, store, chapter, css in SCENARIOS:
            if want and name not in want:
                continue
            r = run(pw, base, name, query, store, chapter, css, a)
            show(r)
            out.append(r)
    print(f"\nrenderer: {out[-1]['renderer'] if out else '?'}  viewport {a.width}x{a.height} @{a.dpr}x")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
