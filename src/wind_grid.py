"""Fetch a coarse global 10 m wind grid for the globe's Wind layer.

    python src/wind_grid.py --out build/wind.json     # the Pages build, every 3 h
    python src/wind_grid.py --self-test               # encoding checks, no network

The Wind layer is context, never evidence: it is a model forecast (Open-Meteo's
best-match models), not an observation like the clouds, rain and lightning, and
nothing here enters the audit. The file goes straight into the built site; it
is never committed.

Grid: 10 degrees, cell centres, latitudes 75N..75S (16 rows) by longitudes
175W..175E (36 columns), 576 points. That resolves the trade winds, the
westerlies, monsoon flow and the broad direction near a city; it cannot show
hurricanes, fronts or sea breezes, and the page says so.

Why the build fetches it and not the reader: Open-Meteo counts every location
as a call against a per-IP limit (10,000 a day, 600 a minute). A 576-point
grid from each reader's browser would exhaust a shared office or mobile IP
within a few dozen page loads and take the city card's own request down with
it. Fetched here every 3 hours it is ~4,600 calls a day, from one IP.

File (JSON, ~20 KB, ~15 KB gzipped, loaded only when Wind is switched on):

    v        format version (1)
    fetched  ISO time of the fetch, UTC
    lat      [first, step, count]   row centres, north to south
    lon      [first, step, count]   column centres, west to east
    t0, dt   first frame's valid time (unix seconds, on the hour) and spacing
    n        number of hourly frames (FRAMES)
    scale    metres per second per unit of the packed values
    uv       base64 of int8 [frame][row][col][u, v]: u eastward, v northward,
             the direction the air moves (not the "from" direction)
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from config import API_FORECAST

LAT = (75.0, -10.0, 16)
LON = (-175.0, 10.0, 36)
FRAMES = 13            # now and 12 hours ahead
SCALE = 0.25           # m/s per unit: int8 covers +-31.75 m/s (114 km/h) per component
BATCH = 144            # locations per request (4 requests; URLs stay ~2 KB)
PAUSE_S = 20           # between requests: 576 calls stay well under 600 a minute
UA = "rain-check (wind_grid.py, Pages build; https://github.com/bogdanr/rain-check)"


def points() -> list[tuple[float, float]]:
    """Grid points, row-major: north to south, then west to east."""
    return [(LAT[0] + i * LAT[1], LON[0] + j * LON[1])
            for i in range(LAT[2]) for j in range(LON[2])]


def to_uv(speed: float, from_deg: float) -> tuple[float, float]:
    """Meteorological (speed, direction it blows *from*) -> (u east, v north)."""
    r = math.radians(from_deg)
    return -speed * math.sin(r), -speed * math.cos(r)


def pack(c: float) -> int:
    return max(-127, min(127, round(c / SCALE)))


def get(url: str, tries: int = 4) -> list[dict]:
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                j = json.load(r)
            return j if isinstance(j, list) else [j]
        except urllib.error.HTTPError as e:
            # 400 is our mistake: retrying cannot help. 429 / 5xx may pass.
            if e.code == 400 or k == tries - 1:
                raise SystemExit(f"wind grid: HTTP {e.code} {e.read()[:200]!r}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if k == tries - 1:
                raise SystemExit(f"wind grid: {e}")
        time.sleep(15 * (k + 1))
    raise AssertionError("unreachable")


def fetch() -> tuple[int, list[list[tuple[float, float]]]]:
    """(t0, frames[FRAMES][576] of (u, v)) from Open-Meteo."""
    pts = points()
    t0, cols = None, []
    for b in range(0, len(pts), BATCH):
        if b:
            time.sleep(PAUSE_S)
        chunk = pts[b:b + BATCH]
        q = urllib.parse.urlencode({
            "latitude": ",".join(f"{p[0]:g}" for p in chunk),
            "longitude": ",".join(f"{p[1]:g}" for p in chunk),
            "hourly": "wind_speed_10m,wind_direction_10m",
            "wind_speed_unit": "ms", "forecast_hours": FRAMES, "timeformat": "unixtime",
        }, safe=",")
        got = get(f"{API_FORECAST}?{q}")
        if len(got) != len(chunk):
            raise SystemExit(f"wind grid: asked for {len(chunk)} points, got {len(got)}")
        for loc in got:
            h = loc["hourly"]
            if t0 is None:
                t0 = h["time"][0]
            if h["time"][0] != t0 or len(h["time"]) < FRAMES:
                raise SystemExit("wind grid: locations disagree on the frame times")
            s, d = h["wind_speed_10m"], h["wind_direction_10m"]
            cols.append([to_uv(s[k], d[k]) if s[k] is not None and d[k] is not None else (0.0, 0.0)
                         for k in range(FRAMES)])
    frames = [[cols[p][k] for p in range(len(pts))] for k in range(FRAMES)]
    return t0, frames


def encode(t0: int, frames: list[list[tuple[float, float]]], fetched: str) -> dict:
    raw = bytearray()
    for f in frames:
        for u, v in f:
            raw += bytes([pack(u) & 0xFF, pack(v) & 0xFF])
    return {"v": 1, "fetched": fetched, "lat": list(LAT), "lon": list(LON),
            "t0": t0, "dt": 3600, "n": len(frames), "scale": SCALE,
            "model": "Open-Meteo best-match forecast, 10 m",
            "uv": base64.b64encode(bytes(raw)).decode("ascii")}


def decode(d: dict) -> list[list[tuple[float, float]]]:
    """The inverse, as wind.js reads it (kept here so the self-test pins it)."""
    b = base64.b64decode(d["uv"])
    n_pts = d["lat"][2] * d["lon"][2]
    s = lambda x: (x - 256 if x > 127 else x) * d["scale"]
    return [[(s(b[(k * n_pts + p) * 2]), s(b[(k * n_pts + p) * 2 + 1])) for p in range(n_pts)]
            for k in range(d["n"])]


def valid(d: dict) -> bool:
    """What atlas.py checks before shipping a file: shape and sane values."""
    try:
        n_pts = d["lat"][2] * d["lon"][2]
        return (d.get("v") == 1 and d["n"] >= 1 and d["scale"] > 0
                and len(base64.b64decode(d["uv"])) == d["n"] * n_pts * 2)
    except (KeyError, TypeError, ValueError):
        return False


def fixture(now_iso: str) -> dict:
    """A made-up but recognisable field for src/check_atlas.py, frames from
    the frozen clock's hour: westerlies poleward of 35 degrees, north-east /
    south-east trades inside 25, and one calm point (the first) so the calm
    ring is drawn too. Fetched two hours before the clock."""
    t = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    t0 = int(t.timestamp()) // 3600 * 3600
    frames = []
    for k in range(FRAMES):
        f = []
        for lat, lon in points():
            if abs(lat) >= 35:
                f.append(to_uv(12 + k * 0.2, 270))
            elif abs(lat) <= 25:
                f.append(to_uv(7, 45 if lat > 0 else 135))
            else:
                f.append(to_uv(3, 0 if lat > 0 else 180))
        f[0] = (0.0, 0.0)
        frames.append(f)
    fetched = datetime.fromtimestamp(t.timestamp() - 7200, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return encode(t0, frames, fetched)


def self_test() -> None:
    # A westerly (from 270) moves the air east; a northerly (from 0) moves it south.
    u, v = to_uv(10, 270)
    assert abs(u - 10) < 1e-9 and abs(v) < 1e-9, (u, v)
    u, v = to_uv(10, 0)
    assert abs(u) < 1e-9 and abs(v + 10) < 1e-9, (u, v)
    u, v = to_uv(10, 225)  # south-westerly: to the north-east
    assert u > 7 and v > 7
    pts = points()
    assert len(pts) == 576 and pts[0] == (75, -175) and pts[35] == (75, 175) and pts[-1] == (-75, 175)
    frames = [[to_uv(3 + (p % 7) + k, (p * 37) % 360) for p in range(576)] for k in range(3)]
    frames[0][0] = (80.0, -80.0)  # beyond int8 range: clipped, not wrapped
    d = encode(1_790_445_600, frames, "2026-09-26T00:00:00Z")
    assert valid(d)
    back = decode(d)
    assert back[0][0] == (127 * SCALE, -127 * SCALE), back[0][0]
    err = max(abs(a - b) for fa, fb in zip(frames, back) for (ua, va), (ub, vb) in zip(fa[1:], fb[1:])
              for a, b in ((ua, ub), (va, vb)))
    assert err <= SCALE / 2 + 1e-9, err
    size = len(json.dumps(d, separators=(",", ":")))
    print(f"wind_grid self-test ok (3 frames: {size} bytes; max error {err:.3f} m/s)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, help="where to write the JSON")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.out:
        ap.error("--out is required")
    t0, frames = fetch()
    d = encode(t0, frames, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    fastest = max(math.hypot(u, v) for f in frames for u, v in f)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(d, separators=(",", ":")))
    print(f"wrote {args.out}: {len(frames)} frames from "
          f"{datetime.fromtimestamp(t0, timezone.utc):%Y-%m-%d %H:%M} UTC, "
          f"{args.out.stat().st_size / 1024:.1f} KB, fastest {fastest * 3.6:.0f} km/h", file=sys.stderr)


if __name__ == "__main__":
    main()
