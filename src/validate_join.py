"""Task 12: validate the verification join.

These are regression tests, not one-off checks. The shift scan is the decisive
one: if the local-day conversion were off by a day, correlation would peak at a
non-zero shift. Everything else in the study is meaningless if this fails.

Usage:  python src/validate_join.py
"""

from __future__ import annotations

import sys

import pandas as pd

from config import PROCESSED, RAW

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)


def shift_scan(df: pd.DataFrame, fcol: str, ocol: str, label: str) -> None:
    """Correlate forecast against observations shifted by -2..+2 days."""
    s = df.sort_values("local_date").reset_index(drop=True)
    best, corrs = None, {}
    for k in range(-2, 3):
        c = s[fcol].corr(s[ocol].shift(k))
        corrs[k] = c
        if best is None or c > corrs[best]:
            best = k
    detail = " ".join(f"{k:+d}:{v:.3f}" for k, v in corrs.items())
    check(f"{label}: correlation peaks at zero shift", best == 0, detail)


print("Track B - native PoP table")
pop = pd.read_parquet(PROCESSED / "verification_pop.parquet")
pop = pop[pop.usable]
check("PoP within [0,1]", bool(pop.forecast_prob.between(0, 1).all()),
      f"min={pop.forecast_prob.min():.2f} max={pop.forecast_prob.max():.2f}")
check("no duplicate dates", not pop.local_date.duplicated().any())
check("rain base rate is plausible for Bucharest (15-40%)",
      0.15 < pop.observed_event.mean() < 0.40,
      f"{pop.observed_event.mean():.3f}")
check("higher PoP goes with more observed rain",
      pop[pop.forecast_prob >= 0.6].observed_event.mean()
      > pop[pop.forecast_prob <= 0.1].observed_event.mean(),
      f"high={pop[pop.forecast_prob >= 0.6].observed_event.mean():.3f} "
      f"low={pop[pop.forecast_prob <= 0.1].observed_event.mean():.3f}")
shift_scan(pop, "forecast_precip_mm", "obs_precip_mm", "Track B precipitation")

print("\nTrack A - deterministic lead table")
lead = pd.read_parquet(PROCESSED / "verification_leads.parquet")
lead = lead[lead.usable]
l1 = lead[lead.lead_days == 1].sort_values("local_date")
shift_scan(l1, "forecast_tmax", "obs_tmax", "Track A lead-1 tmax")
shift_scan(l1, "forecast_precip_mm", "obs_precip_mm", "Track A lead-1 precipitation")

mae = (lead.groupby("lead_days")
       .apply(lambda g: (g.forecast_tmax - g.obs_tmax).abs().mean(), include_groups=False))
check("temperature MAE grows with lead time",
      mae.loc[7] > mae.loc[1], " ".join(f"d{k}:{v:.2f}" for k, v in mae.items()))

print("\nHand-checked extreme dates")
obs = pd.read_parquet(RAW / "station_observations.parquet")
era5 = pd.read_parquet(RAW / "era5_daily.parquet")
wettest = pop.loc[pop.obs_precip_mm.idxmax()]
e = era5[era5.local_date == wettest.local_date]
print(f"  wettest day in sample: {wettest.local_date} "
      f"obs={wettest.obs_precip_mm:.1f}mm PoP={wettest.forecast_prob:.0%} "
      f"forecast={wettest.forecast_precip_mm:.1f}mm "
      f"ERA5={float(e.precipitation_sum.iloc[0]):.1f}mm")
check("wettest observed day was forecast as wet (PoP >= 50%)",
      wettest.forecast_prob >= 0.5, f"PoP={wettest.forecast_prob:.0%}")
era5_mm = float(e.precipitation_sum.iloc[0])
# ERA5 need only register the event on the right DAY - agreement on the day is
# an alignment test. Agreement on the AMOUNT is not, and demanding it would be
# wrong: a 25 km reanalysis grid box cannot resolve a Bucharest convective
# cell. The gap below is precisely why station data is the primary truth.
check("ERA5 independently registers rain on the wettest day (>1mm)",
      era5_mm > 1.0, f"ERA5={era5_mm:.1f}mm vs station={wettest.obs_precip_mm:.1f}mm")
print(f"  note: ERA5 captured {era5_mm / wettest.obs_precip_mm:.0%} of the "
      f"station total - a 25km grid box smooths convective cells away, which "
      f"is why ERA5 is a cross-check and not the baseline")

driest = pop[pop.forecast_prob <= 0.05]
check("days forecast near-zero PoP rarely rained (<15%)",
      driest.observed_event.mean() < 0.15,
      f"n={len(driest)} rained={driest.observed_event.mean():.3f}")

print("\nStation-to-station representativeness floor")
piv = (obs.pivot_table(index="local_date", columns="station", values="prcp")
       .dropna())
piv = piv[piv.index >= pd.Timestamp("2024-01-01").date()]
agree = ((piv.filaret >= 0.2) == (piv.baneasa >= 0.2)).mean()
print(f"  two stations 10km apart agree on rain/no-rain on {agree:.1%} of days "
      f"(n={len(piv)})")
print(f"  -> any calibration claim finer than ~{(1 - agree):.1%} is below the "
      f"representativeness floor")

print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
sys.exit(1 if FAILURES else 0)
