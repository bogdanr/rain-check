"""Phase 1, Task 6: threshold sensitivity of the provider league table.

The league table (src/league.py) defines "it rained" as 0.2 mm in a day. A
ranking that flips when the definition moves to 0.1 mm (trace) or 1.0 mm
(a proper shower) is an artefact of the threshold, not a property of the
forecasters - and the plan's risk list is explicit that no ranking claim may
depend on one threshold. So the league is recomputed at every threshold in
config.RAIN_THRESHOLD_VARIANTS_MM, on the same matched days as the primary
table, and the three rankings are published side by side.

Statistics stay in Python; the report only renders what lands in
data/processed/league_robustness.parquet.

Usage:  python src/league_robustness.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from calibration import brier_decomposition
from config import N_PROB_BINS, PROCESSED, RAIN_THRESHOLD_VARIANTS_MM

# Keep the same matched-day logic as the primary table: within a city, score
# every model on the days ALL models cover, at each threshold.
THRESHOLDS_MM = list(RAIN_THRESHOLD_VARIANTS_MM)


def _bss(p: np.ndarray, e: np.ndarray) -> float:
    return float(brier_decomposition(p, e, n_bins=N_PROB_BINS)
                 ["brier_skill_score"])


def robustness(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for city, g in daily.groupby("city"):
        wide = g.pivot_table(index="local_date", columns="model",
                             values="forecast_prob")
        obs = g.groupby("local_date").obs_precip_mm.first()
        wide = wide.join(obs.rename("__mm__")).dropna()
        models = [m for m in wide.columns if m != "__mm__"]
        if len(models) < 2:
            continue
        mm = wide["__mm__"].values
        p = {m: wide[m].values.astype(float) for m in models}

        for thr in THRESHOLDS_MM:
            e = (mm >= thr).astype(float)
            point = {m: _bss(p[m], e) for m in models}
            order = sorted(models, key=lambda m: -point[m])
            for rank_pos, m in enumerate(order, start=1):
                rows.append({"city": city, "model": m,
                             "threshold_mm": thr, "n_days": int(len(e)),
                             "bss": point[m], "rank": rank_pos,
                             "base_rate": float(e.mean())})
    out = pd.DataFrame(rows)
    out.to_parquet(PROCESSED / "league_robustness.parquet", index=False)
    return out


def rank_stability(out: pd.DataFrame) -> pd.DataFrame:
    """How often does each model hold the same rank across thresholds?"""
    piv = out.pivot_table(index=["city", "model"], columns="threshold_mm",
                          values="rank")
    piv = piv.dropna()
    piv["rank_range"] = piv.max(axis=1) - piv.min(axis=1)
    return piv.reset_index()


def main() -> None:
    path = PROCESSED / "capitals_pinned_daily.parquet"
    if not path.exists():
        raise SystemExit("missing capitals_pinned_daily.parquet - "
                         "run `capitals.py providers` first")
    daily = pd.read_parquet(path)
    print(f"=== Task 6: league table under thresholds {THRESHOLDS_MM} mm, "
          f"{daily.city.nunique()} cities ===")

    out = robustness(daily)
    for thr, g in out.groupby("threshold_mm"):
        med = g.groupby("model")["rank"].median().sort_values()
        print(f"\n  threshold {thr} mm - median rank per model:")
        print("    " + "  ".join(f"{m}={r:.0f}" for m, r in med.items()))

    st = rank_stability(out)
    flipped = st[st.rank_range > 0]
    print(f"\n  {len(flipped)}/{len(st)} city-model pairs change rank "
          f"across thresholds; "
          f"{int((st.rank_range == 0).sum())} hold their rank exactly.")
    if len(flipped):
        worst = st.nlargest(3, "rank_range")
        for _, r in worst.iterrows():
            print(f"    {r.city} / {r.model}: ranks "
                  f"{r[[t for t in THRESHOLDS_MM]].astype(int).tolist()}")

    print("\nwrote data/processed/league_robustness.parquet")


if __name__ == "__main__":
    main()
