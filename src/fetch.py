"""Cached HTTP fetch layer for the Open-Meteo APIs (Task 5).

Responses are cached on disk keyed by a hash of the fully-resolved request URL,
so re-running any analysis is free and reproducible from a cold cache with a
single command. The APIs are rate-limited and the study is re-run many times
during analysis, so caching is not an optimisation but a requirement.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from urllib.parse import urlencode

import requests

from config import CACHE

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "bucharest-forecast-calibration/0.1"})

# Politeness / rate-limit handling
_MIN_INTERVAL_S = 0.35
_MAX_RETRIES = 5
_last_call = 0.0


def _cache_key(url: str, params: dict[str, Any]) -> str:
    canonical = url + "?" + urlencode(sorted(params.items()))
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _throttle() -> None:
    global _last_call
    delta = time.monotonic() - _last_call
    if delta < _MIN_INTERVAL_S:
        time.sleep(_MIN_INTERVAL_S - delta)
    _last_call = time.monotonic()


def fetch_json(
    url: str,
    params: dict[str, Any],
    *,
    use_cache: bool = True,
    cache_errors: bool = True,
) -> dict[str, Any]:
    """GET `url` with `params`, returning parsed JSON, with on-disk caching.

    Client errors (4xx) that carry an Open-Meteo `reason` are cached too when
    `cache_errors` is set: a request for data outside an archive's coverage will
    fail identically every time, and re-issuing it wastes the rate-limit budget.
    """
    key = _cache_key(url, params)
    path = CACHE / f"{key}.json"

    if use_cache and path.exists():
        with path.open() as fh:
            return json.load(fh)

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        _throttle()
        try:
            resp = _SESSION.get(url, params=params, timeout=60)
        except requests.RequestException as exc:  # transient network failure
            last_exc = exc
            time.sleep(2**attempt)
            continue

        if resp.status_code == 200:
            payload = resp.json()
            path.write_text(json.dumps(payload))
            return payload

        # Rate limited or server-side hiccup: back off and retry.
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(60, 5 * 2**attempt))
            last_exc = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            continue

        # Deterministic client error - cache the refusal so we do not repeat it.
        try:
            payload = resp.json()
        except ValueError:
            payload = {"error": True, "reason": resp.text[:500]}
        payload.setdefault("error", True)
        payload["_http_status"] = resp.status_code
        if cache_errors:
            path.write_text(json.dumps(payload))
        return payload

    raise RuntimeError(f"failed after {_MAX_RETRIES} attempts: {last_exc}")


def is_error(payload: dict[str, Any]) -> bool:
    return bool(payload.get("error"))


def reason(payload: dict[str, Any]) -> str:
    return str(payload.get("reason", payload.get("_http_status", "unknown")))
