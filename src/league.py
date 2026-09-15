"""Phase 1, Task 5: the cross-provider calibration league table.

The published capitals table (capitals_metrics.parquet) ranks cities, but each
city's number comes from whichever model Open-Meteo's `best_match` happens to
route it to - pop_provenance.py showed that is ICON-EU in some capitals and
ECMWF in others, and that the choice alone moves BSS by ~0.12. This module
answers the question the published table cannot: for a fixed city, which
*forecaster* is best?

Two things keep the answer honest:

  * Rank intervals. Task 34's lesson generalises: with ~2 years of daily data,
    model-to-model BSS differences are mostly not resolvable, so every rank
    carries a block-bootstrap interval computed on days MATCHED across models
    (plan risk 2: unequal archive depths must not decide rankings).
  * A held-fixed sanity check. Generalising pop_provenance.py's verdict logic:
    where the probe identified which model backs best_match in a city, the
    published BSS should equal that model's pinned BSS, and the published
    city ranking should track the like-for-like one. If it does, the published
    ordering was geography, not routing; if it does not, the routing was part
    of the story and the league table is the correction.

Inputs (all on disk, no network): data/processed/capitals_pinned.parquet and
capitals_pinned_daily.parquet from `capitals.py providers`, the published
capitals_metrics.parquet, and pop_provenance_capitals.parquet.

Outputs: data/processed/league_table.parquet and league_summary.json (the
report's renderer prefers JSON for the narrative verdicts).

Usage:  python src/league.py
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from calibration import brier_decomposition
from config import (
    BOOTSTRAP_BLOCK_DAYS,
    N_PROB_BINS,
    PROCESSED,
    PROVIDER_MODELS,
    RANDOM_SEED,
)
from capitals import _block_indices

N_BOOT_LEAGUE = 600   # replicates per city for the rank intervals


def _bss(p: np.ndarray, e: np.ndarray) -> float:
    return float(brier_decomposition(p, e, n_bins=N_PROB_BINS)
                 ["brier_skill_score"])


def load_daily() -> pd.DataFrame:
    path = PROCESSED / "capitals_pinned_daily.parquet"
    if not path.exists():
        raise SystemExit(
            "missing data/processed/capitals_pinned_daily.parquet - "
            "run `capitals.py providers` first")
    return pd.read_parquet(path)


def league_table(daily: pd.DataFrame) -> pd.DataFrame:
    """Per city, rank the models by BSS on matched days, with rank intervals.

    All models in a city are scored on the SAME days (the intersection of the
    days every model covers), and the block bootstrap resamples those days
    jointly, so a replicate is a fair re-run of the whole comparison: rank
    intervals reflect sampling noise, not differing windows.
    """
    rng = np.random.default_rng(RANDOM_SEED)
    rows = []
    for city, g in daily.groupby("city"):
        wide = g.pivot_table(index="local_date", columns="model",
                             values="forecast_prob")
        obs = g.groupby("local_date").observed_event.first()
        wide = wide.join(obs.rename("__event__")).dropna()
        models = [m for m in wide.columns if m != "__event__"]
        if len(models) < 2:
            continue
        p = {m: wide[m].values.astype(float) for m in models}
        e = wide["__event__"].values.astype(float)

        point = {m: _bss(p[m], e) for m in models}
        # Block bootstrap: resample days, recompute every model's BSS on the
        # same replicate, re-rank. Same construction as Task 34's city ranks.
        boots = {m: [] for m in models}
        for _ in range(N_BOOT_LEAGUE):
            idx = _block_indices(len(e), rng)
            for m in models:
                try:
                    boots[m].append(_bss(p[m][idx], e[idx]))
                except Exception:
                    boots[m].append(np.nan)
        boot = pd.DataFrame({m: boots[m] for m in models}).dropna()
        ranks = (-boot[models].values).argsort(axis=0).argsort(axis=0) + 1

        order = sorted(models, key=lambda m: -point[m])
        for rank_pos, m in enumerate(order, start=1):
            j = models.index(m)
            rows.append({
                "city": city, "model": m,
                "display_name": PROVIDER_MODELS[m].display_name
                if m in PROVIDER_MODELS else m,
                "organization": PROVIDER_MODELS[m].organization
                if m in PROVIDER_MODELS else "",
                "domain": PROVIDER_MODELS[m].domain
                if m in PROVIDER_MODELS else "",
                "n_days": int(len(e)),
                "bss": point[m],
                "bss_lo": float(np.nanquantile(boot[m], 0.025)),
                "bss_hi": float(np.nanquantile(boot[m], 0.975)),
                "rank": rank_pos,
                "rank_lo": float(np.quantile(ranks[:, j], 0.025)),
                "rank_hi": float(np.quantile(ranks[:, j], 0.975)),
            })
    out = pd.DataFrame(rows)
    out.to_parquet(PROCESSED / "league_table.parquet", index=False)
    return out


def held_fixed_check(league: pd.DataFrame) -> dict:
    """Does the published (best_match) ranking survive pinning? Generalises
    the pop_provenance.py verdict logic to the full model set."""
    met_path = PROCESSED / "capitals_metrics.parquet"
    prov_path = PROCESSED / "pop_provenance_capitals.parquet"
    if not met_path.exists() or not prov_path.exists():
        return {"verdict": "inputs missing - run capitals.py and "
                           "pop_provenance.py capitals first"}

    published = pd.read_parquet(met_path)
    prov = pd.read_parquet(prov_path).set_index("city")

    # 1. Where best_match was identified as a league model, does its published
    #    BSS equal that model's pinned BSS? (Same city, same days.)
    diffs = []
    for city, row in prov.iterrows():
        m = row["best_match_is"]
        sel = league[(league.city == city) & (league.model == m)]
        pub = published[published.city == city]
        if not len(sel) or not len(pub):
            continue
        diffs.append({"city": city, "model": m,
                      "published_bss": float(pub.bss.iloc[0]),
                      "pinned_bss": float(sel.bss.iloc[0]),
                      "abs_diff": abs(float(pub.bss.iloc[0])
                                      - float(sel.bss.iloc[0]))})
    d = pd.DataFrame(diffs)

    # 2. Rank correlation between the published city ranking and the
    #    like-for-like ranking under the model best_match serves.
    spearman = None
    try:
        from scipy import stats as st
        pub_rank = published.set_index("city").bss.rank(ascending=False)
        pin_rank = league.groupby("city").apply(
            lambda g: g.set_index("model").bss.get(
                prov.loc[g.name, "best_match_is"], np.nan),
            include_groups=False).rank(ascending=False)
        both = pd.concat([pub_rank, pin_rank], axis=1).dropna()
        both.columns = ["published", "pinned"]
        if len(both) >= 5:
            spearman = float(both.published.corr(both.pinned, method="spearman"))
    except Exception:
        pass

    n_checked = len(d)
    n_match = int((d.abs_diff <= 0.02).sum()) if n_checked else 0
    max_diff = float(d.abs_diff.max()) if n_checked else None
    if n_checked and n_match / n_checked >= 0.8:
        verdict = ("the published values are the pinned models' values: the "
                   "city ranking is like-for-like after all, and the league "
                   "table refines rather than corrects it")
    elif spearman is not None and spearman >= 0.8:
        verdict = ("individual values shift under pinning but the city "
                   "ordering survives, so the published ranking measures "
                   "geography, not routing")
    else:
        verdict = ("the published ranking does NOT survive pinning: part of "
                   "the city-to-city spread was which model best_match "
                   "routed, and the league table is the like-for-like "
                   "correction")

    summary = {
        "n_cities_in_league": int(league.city.nunique()),
        "n_models_in_league": int(league.model.nunique()),
        "held_fixed": {
            "n_cities_checked": n_checked,
            "n_published_equals_pinned": n_match,
            "max_abs_bss_diff": max_diff,
            "spearman_published_vs_pinned": spearman,
            "verdict": verdict,
        },
        "per_city_matches": d.to_dict("records"),
    }
    (PROCESSED / "league_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> None:
    daily = load_daily()
    print(f"=== Task 5: provider league table over "
          f"{daily.city.nunique()} cities x {daily.model.nunique()} models ===")
    league = league_table(daily)

    show = league[["city", "rank", "display_name", "bss", "bss_lo", "bss_hi",
                   "rank_lo", "rank_hi", "n_days"]]
    for city, g in show.groupby("city"):
        g = g.sort_values("rank")
        print(f"\n{city}")
        print(g.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    summary = held_fixed_check(league)
    print(f"\nHeld-fixed check: {summary['held_fixed']['verdict']}")
    if summary["held_fixed"]["spearman_published_vs_pinned"] is not None:
        print(f"  Spearman(published, pinned) = "
              f"{summary['held_fixed']['spearman_published_vs_pinned']:.3f} "
              f"over {summary['held_fixed']['n_cities_checked']} cities")

    print("\nwrote data/processed/league_table.parquet")
    print("wrote data/processed/league_summary.json")


if __name__ == "__main__":
    main()
