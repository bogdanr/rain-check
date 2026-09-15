"""Static-site build helpers: output tree, base path, content-hashed assets.

The report used to be one self-contained HTML file that had to work from
`file://`. It is now published to GitHub Pages, so the constraints inverted:
caching, lazy loading and parallel fetch matter, and base64-inlining every
figure is pure cost. This module owns the `dist/` tree and the URL discipline
that makes the result survive being served from a project subpath.

Two rules the rest of the build depends on:

  1. Nothing outside `dist/` is written by the build. `dist/` is disposable and
     git-ignored, so there is never any doubt about what is authored and what is
     derived.
  2. Every emitted URL goes through `Site.url()`. A GitHub Pages *project* site
     serves from `/<repo>/`, and hardcoded absolute paths work perfectly in
     local preview then 404 on deploy - a failure that only shows up at the
     moment it matters.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from config import ROOT

DIST = ROOT / "dist"
WEB = Path(__file__).resolve().parent / "web"

# Hash length for cache-busting filenames. Eight hex characters is ~4 billion
# values, which is ample for a few dozen assets and keeps URLs readable.
HASH_LEN = 8


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:HASH_LEN]


class Site:
    """Accumulates the output tree and hands back cache-safe relative URLs.

    `base` is the path the site is served from ("/" for a user site,
    "/weather-calibration/" for a project site). URLs are emitted relative to
    the document rather than absolute wherever possible, so that a build is
    portable between the two without rebuilding.
    """

    def __init__(self, base: str = "/", dist: Path = DIST) -> None:
        self.base = base if base.endswith("/") else base + "/"
        self.dist = dist
        self.assets: dict[str, str] = {}   # logical name -> emitted URL

    # -- output tree --------------------------------------------------------
    def reset(self) -> None:
        """Start from an empty tree so deleted inputs cannot linger as stale
        outputs - the classic way a static build serves a file nobody can
        explain."""
        if self.dist.exists():
            shutil.rmtree(self.dist)
        self.dist.mkdir(parents=True)

    def url(self, rel: str) -> str:
        """Site-root-relative URL for a path inside dist."""
        return self.base + rel.lstrip("/")

    # -- asset emission -----------------------------------------------------
    def add_bytes(self, rel_dir: str, name: str, data: bytes,
                  hashed: bool = True) -> str:
        """Write bytes into dist and return the URL to reference them by.

        Hashed names let the server promise immutability, which is what makes a
        long cache lifetime safe; without it a reader can get yesterday's CSS
        with today's markup.
        """
        stem, _, ext = name.rpartition(".")
        fname = f"{stem}.{_digest(data)}.{ext}" if hashed else name
        out = self.dist / rel_dir / fname
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return self.url(f"{rel_dir}/{fname}")

    def add_text(self, rel_dir: str, name: str, text: str,
                 hashed: bool = True) -> str:
        return self.add_bytes(rel_dir, name, text.encode("utf-8"), hashed=hashed)

    def add_json(self, rel_dir: str, name: str, payload, hashed: bool = True) -> str:
        # sort_keys + compact separators so the same data always serialises to
        # the same bytes; the determinism check depends on this.
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, default=_jsonable)
        return self.add_text(rel_dir, name, text, hashed=hashed)

    def add_file(self, rel_dir: str, path: Path, hashed: bool = True) -> str:
        """Copy an existing file (a figure, a vendored library) into dist."""
        return self.add_bytes(rel_dir, path.name, path.read_bytes(), hashed=hashed)

    def copy_tree(self, rel_dir: str, src: Path) -> None:
        """Verbatim copy, for directories whose internal references are fixed."""
        if src.exists():
            shutil.copytree(src, self.dist / rel_dir, dirs_exist_ok=True)


def _jsonable(v):
    """Coerce the numpy/pandas scalars that leak out of the analysis tables.

    json.dumps refuses numpy types outright. Converting here rather than at
    every call site means a new metric cannot quietly break serialisation.
    """
    import numpy as np
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if f != f else f          # NaN is not valid JSON
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    raise TypeError(f"not JSON-serialisable: {type(v)}")


def slugify(name: str) -> str:
    """City name -> URL slug. Stable, because it appears in shareable links."""
    out = []
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_":
            out.append("-")
    return "".join(out).strip("-")


# ---------------------------------------------------------------------------
# Shipping the stylesheet and the scripts
# ---------------------------------------------------------------------------
# The authored CSS and JS are heavily commented on purpose - the reasoning
# behind a contrast floor or a projection choice is worth more than the line it
# explains. A cold visitor does not need any of it, and it is not free: a third
# of app.css and a fifth of globe.js is prose, which the first-load budget in
# report.py was quietly paying for.
#
# So comments are stripped on the way into dist. This is deliberately *not* a
# minifier: no renaming, no whitespace crushing, no reordering. Nothing here can
# change what the code does, which is the property that makes it safe to run
# without a test suite for it. The shipped file stays readable in devtools, and
# the repository keeps every word.

def _strip_css(src: str) -> str:
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        out.append(src[i])
        i += 1
    return _tidy("".join(out))


# A `/` in JavaScript starts a comment, a regular expression or a division, and
# which one it is depends on what came before it. Anything that can end an
# expression (a name, a number, a closing bracket) means division; everything
# else means a regex may start. Getting this wrong is how naive strippers eat
# half a file, so it is spelled out rather than guessed at.
_JS_DIV_AFTER = set("_$)]}")


def _strip_js(src: str) -> str:
    out: list[str] = []
    i, n = 0, len(src)
    quote = ""          # "", or the delimiter of the string/template we are in
    in_regex = False
    in_class = False    # inside a [...] character class of a regex
    prev = ""           # last non-whitespace character emitted

    while i < n:
        ch = src[i]

        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue

        if in_regex:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if ch == "[":
                in_class = True
            elif ch == "]":
                in_class = False
            elif ch == "/" and not in_class:
                in_regex = False
            i += 1
            continue

        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue

        if ch in "'\"`":
            quote = ch
        elif ch == "/" and not (prev.isalnum() or prev in _JS_DIV_AFTER):
            in_regex = True

        out.append(ch)
        if not ch.isspace():
            prev = ch
        i += 1

    return _tidy("".join(out))


def _tidy(text: str) -> str:
    """Drop trailing spaces and the blank lines a removed comment leaves behind.

    Runs of blank lines collapse to one rather than to none: the shipped file is
    what a reader sees in devtools, and a 900-line wall with no paragraph breaks
    is a worse artefact than a slightly larger one.
    """
    lines, out, blank = text.split("\n"), [], False
    for line in lines:
        line = line.rstrip()
        if line:
            out.append(line)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip("\n") + "\n"


def ship(path: Path) -> str:
    """Read an authored web asset in the form it should be served in."""
    text = path.read_text()
    return _strip_css(text) if path.suffix == ".css" else _strip_js(text)
