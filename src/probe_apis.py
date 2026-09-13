"""Task 7 probe: determine which endpoints actually serve the data we need.

Run with the system python (stdlib only) so it works before deps are installed.
Writes a machine-readable summary to data/raw/api_probe.json.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LAT, LON = 44.4268, 26.1025
OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "api_probe.json"


def get(url: str, params: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(full, timeout=60) as r:
            return {"ok": True, "status": 200, "body": json.load(r), "url": full}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"raw": raw[:400]}
        return {"ok": False, "status": e.code, "body": body, "url": full}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "body": {"raw": str(e)}, "url": full}


def summarise(name: str, res: dict) -> dict:
    body = res["body"]
    hourly = body.get("hourly", {}) if isinstance(body, dict) else {}
    daily = body.get("daily", {}) if isinstance(body, dict) else {}
    keys = [k for k in hourly if k != "time"] + [f"daily:{k}" for k in daily if k != "time"]
    n = len(hourly.get("time", [])) or len(daily.get("time", []))
    non_null = {}
    for k, v in list(hourly.items()) + list(daily.items()):
        if k == "time" or not isinstance(v, list):
            continue
        non_null[k] = sum(1 for x in v if x is not None)
    out = {
        "name": name,
        "ok": res["ok"],
        "status": res["status"],
        "n_timesteps": n,
        "variables": keys,
        "non_null_counts": non_null,
        "reason": body.get("reason") if isinstance(body, dict) else None,
        "url": res["url"],
    }
    print(f"\n=== {name} ===")
    print(f"  status={out['status']} ok={out['ok']} n={n}")
    if out["reason"]:
        print(f"  reason: {out['reason']}")
    for k, c in non_null.items():
        print(f"  {k:52s} non-null {c}/{n}")
    return out


probes: list[dict] = []
base = {"latitude": LAT, "longitude": LON, "timezone": "UTC"}

# --- A. Previous Runs API: deterministic vars at fixed lead offsets ----------
probes.append(
    summarise(
        "A. previous-runs temperature_2m + precipitation, leads 1-7",
        get(
            "https://previous-runs-api.open-meteo.com/v1/forecast",
            {
                **base,
                "hourly": ",".join(
                    [f"temperature_2m_previous_day{d}" for d in range(1, 8)]
                    + [f"precipitation_previous_day{d}" for d in range(1, 8)]
                ),
                "start_date": "2025-06-01",
                "end_date": "2025-06-03",
                "models": "ecmwf_ifs025",
            },
        ),
    )
)

# --- B. Does Previous Runs expose precipitation_probability at all? ---------
probes.append(
    summarise(
        "B. previous-runs precipitation_probability_previous_day1 (expected to fail)",
        get(
            "https://previous-runs-api.open-meteo.com/v1/forecast",
            {
                **base,
                "hourly": "precipitation_probability_previous_day1",
                "start_date": "2025-06-01",
                "end_date": "2025-06-03",
            },
        ),
    )
)

# --- C. Historical Forecast API: PoP at short lead --------------------------
probes.append(
    summarise(
        "C. historical-forecast precipitation_probability (short lead)",
        get(
            "https://historical-forecast-api.open-meteo.com/v1/forecast",
            {
                **base,
                "hourly": "precipitation_probability,precipitation,temperature_2m",
                "start_date": "2025-06-01",
                "end_date": "2025-06-03",
            },
        ),
    )
)

# --- D. Single Runs API: full horizon of one run, incl. PoP -----------------
for run in ("2026-05-01T00:00", "2025-06-01T00:00", "2024-06-01T00:00"):
    probes.append(
        summarise(
            f"D. single-runs run={run} precipitation_probability",
            get(
                "https://api.open-meteo.com/v1/forecast",
                {
                    **base,
                    "hourly": "precipitation_probability,precipitation,temperature_2m",
                    "run": run,
                    "forecast_days": 7,
                },
            ),
        )
    )

# --- E. Ensemble API: members for derived probabilities ---------------------
probes.append(
    summarise(
        "E. ensemble precipitation members (past_days)",
        get(
            "https://ensemble-api.open-meteo.com/v1/ensemble",
            {
                **base,
                "hourly": "precipitation,temperature_2m",
                "models": "ecmwf_ifs025",
                "past_days": 3,
                "forecast_days": 1,
            },
        ),
    )
)

# --- F. ERA5 archive: secondary ground truth --------------------------------
probes.append(
    summarise(
        "F. archive ERA5 daily precipitation + temperature",
        get(
            "https://archive-api.open-meteo.com/v1/archive",
            {
                **base,
                "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min",
                "start_date": "2025-06-01",
                "end_date": "2025-06-05",
            },
        ),
    )
)

# --- G. Live forecast API: what the daily logger will capture ---------------
probes.append(
    summarise(
        "G. live forecast PoP + daily aggregates (logger source)",
        get(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": LAT,
                "longitude": LON,
                "timezone": "Europe/Bucharest",
                "daily": "precipitation_probability_max,precipitation_sum,temperature_2m_max,temperature_2m_min",
                "hourly": "precipitation_probability,precipitation",
                "forecast_days": 16,
            },
        ),
    )
)

OUT.write_text(json.dumps(probes, indent=2))
print(f"\nwrote {OUT}")
sys.exit(0)
