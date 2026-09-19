"""Task 26a: which of the vendor's "different models" are the same series?

The triangulation stage noticed, as a by-product, that two model ids returned
the same numbers, and flagged it from rounded aggregates at one lead in one
table. That is enough to raise the alarm and not enough to act on. This module
does the check properly: every model against every other model, at every city,
on the raw hourly series the vendor actually served, before any daily
reduction has had a chance to launder a difference away.

WHY IT MATTERS. The league table ranks forecasters within a city. If two rows
of that table are the same forecaster under two names, three things break at
once: the reader counts agreement between them as independent corroboration,
the rank interval is computed over a model set with a phantom member, and any
"N centres agree" claim is inflated. None of these fail loudly. A duplicate
pair looks exactly like two models that happen to be very good.

WHAT THE TEST IS. For a pair of models at a city, take the hours where both
served a value and ask what fraction of them are EXACTLY equal. Exact float
equality is the right test rather than a tolerance: both numbers arrive
through the same JSON encoding at the same rounding (integer percent for
probability, one decimal for millimetres), so two genuinely distinct models
agreeing to the last representable digit across twenty thousand hours is not
something that happens by chance. The tolerance-based alternative would have
to pick a threshold, and the whole point is that no threshold is needed.

PER CHANNEL, WHICH IS THE POINT. The two channels are tested separately, and
that separation is not bookkeeping - it is where the interesting answer lives.
A vendor can serve one centre's probability alongside another centre's
rainfall amount, and a test that demanded both channels match would call that
pair distinct and miss the duplication in exactly the field the league table
scores. The results here contain precisely that case.

THE FALSE-POSITIVE CONTROL. Probability is quantised to integer percent, so
independent models share values often - agreement well above half is
unremarkable, and two nested configurations of one centre's model can agree on
98% of hours while differing by 23 mm on the hours that matter. A threshold is
only credible if nothing sits near it. The report therefore counts the pairs
landing in the band just below the cut: an empty band means no pair's verdict
turned on where the line was drawn, and any cut inside that band would have
given the same answer. A populated band is printed as a warning, because at
that point the threshold is choosing rather than describing.

Close relatives are listed too, unflagged and excluded from nothing. Two
resolutions of one centre's model are genuinely two forecasts, but a reader
treating their agreement as two independent opinions is still being misled,
and the only honest handling is to show the number and let them judge.

CANONICAL CHOICE. Where a group of models is one series, the league table must
keep one of them. The rule is: prefer a model id naming a specific centre and
configuration over a `_seamless` alias, since the alias is the vendor's
routing label and the specific id is what is actually running; break
remaining ties alphabetically so the choice is reproducible. Duplicates are
not deleted from the record - they are recorded as aliases with the evidence
attached, because "these two names are one forecast" is itself a finding.

Inputs: data/raw/provider_pop_<city>_<model>.parquet, on disk from stage 11.
No network.

Outputs: data/processed/duplicate_pairs.parquet   (every comparable pair)
         data/processed/duplicate_groups.parquet  (city x channel equivalence
                                                   classes and their canonical
                                                   member)
         data/processed/duplicate_summary.json    (what league.py and the
                                                   report read)

Usage:  python src/duplicates.py          full run
        python src/duplicates.py check    correctness checks only
"""

from __future__ import annotations

import itertools
import json
import sys

import numpy as np
import pandas as pd

from config import PROCESSED, PROVIDER_MODELS, RAW, load_capitals

# The two fields the vendor serves that anything downstream scores. The order
# matters only for printing; `precipitation_probability` is the channel the
# league table and every calibration number in this study are computed from,
# so it leads.
CHANNELS = ("precipitation_probability", "precipitation")

# The channel whose duplication actually contaminates the league table.
SCORED_CHANNEL = "precipitation_probability"

# Fewer shared hours than this and the pair is reported as not comparable
# rather than as distinct. 500 hours is three weeks of continuous overlap:
# below it, a high agreement fraction is not evidence of anything.
MIN_OVERLAP_HOURS = 500

# Agreement at or above this is called duplication. Set at "all but one hour
# in a thousand" rather than 1.0 so that a single late-arriving revision or a
# one-hour gap-fill does not hide a duplicate pair; the false-positive control
# in report() checks that nothing genuine lands anywhere near it.
DUPLICATE_FRAC = 0.999

# The band immediately below the cut. A pair landing here would mean the
# threshold is adjudicating a continuum instead of separating two populations,
# so the count is reported every run rather than assumed to be zero.
GREY_BAND_LO = 0.99

# Agreement at or above this without reaching DUPLICATE_FRAC: close relatives,
# typically one centre's model at two resolutions. Reported as context, never
# excluded from anything.
NEAR_FRAC = 0.95

PAIRS_TABLE = PROCESSED / "duplicate_pairs.parquet"
GROUPS_TABLE = PROCESSED / "duplicate_groups.parquet"
SUMMARY_JSON = PROCESSED / "duplicate_summary.json"


def die(msg: str) -> None:
    raise SystemExit(f"DUPLICATE DETECTION FAILED: {msg}")


def _slug(name: str) -> str:
    return name.lower().replace(" ", "_")


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
def load_city_series(city_name: str) -> dict[str, pd.DataFrame]:
    """Every model's raw hourly series at one city, indexed by UTC hour.

    Missing and empty files are skipped silently: model coverage is genuinely
    patchy by domain (a UK 2 km model has nothing to say about Bucharest) and
    that is a collection fact, not an error.
    """
    out: dict[str, pd.DataFrame] = {}
    for model in PROVIDER_MODELS:
        path = RAW / f"provider_pop_{_slug(city_name)}_{model}.parquet"
        if not path.exists():
            continue
        d = pd.read_parquet(path)
        if d.empty or not set(CHANNELS) <= set(d.columns):
            continue
        d = d.copy()
        d["time"] = pd.to_datetime(d["time"], utc=True)
        d = d.drop_duplicates(subset="time").set_index("time").sort_index()
        out[model] = d[list(CHANNELS)]
    return out


# --------------------------------------------------------------------------
# The pairwise test
# --------------------------------------------------------------------------
def compare_pair(a: pd.Series, b: pd.Series) -> dict:
    """One model against another on one channel, over their shared hours.

    Returns the agreement fraction plus the size of the disagreement when
    there is one, because "identical except for one hour that differs by 12 mm"
    and "identical except for one hour that differs by 0.1 mm" are different
    claims and the table should let a reader tell them apart.
    """
    j = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    n = int(len(j))
    if n < MIN_OVERLAP_HOURS:
        return {"n_overlap": n, "frac_exact": np.nan, "max_abs_diff": np.nan,
                "mean_abs_diff": np.nan, "pearson_r": np.nan,
                "verdict": "not comparable"}
    av, bv = j.a.values.astype(float), j.b.values.astype(float)
    diff = np.abs(av - bv)
    frac = float(np.mean(av == bv))
    # A constant series (every hour zero, which happens for amount at dry
    # desert-adjacent cities over short windows) has undefined correlation;
    # report it as missing rather than letting numpy warn and return nan.
    if np.std(av) == 0 or np.std(bv) == 0:
        r = np.nan
    else:
        r = float(np.corrcoef(av, bv)[0, 1])
    return {
        "n_overlap": n,
        "frac_exact": frac,
        "max_abs_diff": float(diff.max()),
        "mean_abs_diff": float(diff.mean()),
        "pearson_r": r,
        "verdict": "duplicate" if frac >= DUPLICATE_FRAC else "distinct",
    }


def pairwise_table(cities: dict | None = None) -> pd.DataFrame:
    """Every (city, channel, model pair) in the collected archive."""
    cities = cities or load_capitals()
    rows = []
    for name in sorted(cities):
        series = load_city_series(name)
        for m1, m2 in itertools.combinations(sorted(series), 2):
            for ch in CHANNELS:
                res = compare_pair(series[m1][ch], series[m2][ch])
                rows.append({"city": name, "channel": ch,
                             "model_a": m1, "model_b": m2, **res})
    out = pd.DataFrame(rows)
    if out.empty:
        die("no vendor series on disk - run stage 11 (collect_ensemble.py) "
            "first; there is nothing to compare")
    return out


# --------------------------------------------------------------------------
# Equivalence classes
# --------------------------------------------------------------------------
def canonical_of(models: list[str]) -> str:
    """Which member of a duplicate group the league table should keep.

    A `_seamless` id is the vendor's routing label, not a forecast system: it
    resolves to whichever model covers the point, so when it coincides with a
    specific id, the specific id is the thing that ran. Preferring it means the
    surviving row is named after the system a reader can go and look up.
    """
    specific = sorted(m for m in models if not m.endswith("_seamless"))
    return specific[0] if specific else sorted(models)[0]


def group_table(pairs: pd.DataFrame) -> pd.DataFrame:
    """Connected components of the duplicate relation, per city and channel.

    Transitivity is applied deliberately: if a matches b and b matches c, all
    three are one series, and a group of three is the honest description even
    if a and c were never directly compared because their coverage windows
    did not overlap enough.
    """
    rows = []
    dup = pairs[pairs.verdict == "duplicate"]
    for (city, channel), g in dup.groupby(["city", "channel"]):
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for _, r in g.iterrows():
            ra, rb = find(r.model_a), find(r.model_b)
            if ra != rb:
                parent[rb] = ra
        members: dict[str, list[str]] = {}
        for m in parent:
            members.setdefault(find(m), []).append(m)
        for group in members.values():
            group = sorted(group)
            canon = canonical_of(group)
            sub = g[g.model_a.isin(group) & g.model_b.isin(group)]
            rows.append({
                "city": city, "channel": channel,
                "canonical": canon,
                "members": ",".join(group),
                "n_members": len(group),
                "aliases": ",".join(m for m in group if m != canon),
                "min_frac_exact": float(sub.frac_exact.min()),
                "n_overlap": int(sub.n_overlap.min()),
            })
    return pd.DataFrame(rows, columns=["city", "channel", "canonical",
                                       "members", "n_members", "aliases",
                                       "min_frac_exact", "n_overlap"])


def alias_map(groups: pd.DataFrame,
              channel: str = SCORED_CHANNEL) -> dict[str, dict[str, str]]:
    """{city: {alias_model: canonical_model}} for one channel.

    This is the interface league.py consumes. A model absent from the map is
    not a duplicate at that city, which is the common case - duplication is
    domain-dependent and a pair that is one series in Dublin can be two in
    Bucharest.
    """
    out: dict[str, dict[str, str]] = {}
    if groups.empty:
        return out
    for _, r in groups[groups.channel == channel].iterrows():
        d = out.setdefault(r.city, {})
        for alias in (r.aliases.split(",") if r.aliases else []):
            d[alias] = r.canonical
    return out


def load_alias_map(channel: str = SCORED_CHANNEL) -> dict[str, dict[str, str]]:
    """The alias map from disk, or an empty map if this stage has not run.

    Empty rather than fatal so a fresh checkout can still build the league
    table; league.py says out loud which of the two situations it is in, so a
    missing check can never be mistaken for a clean one.
    """
    if not GROUPS_TABLE.exists():
        return {}
    return alias_map(pd.read_parquet(GROUPS_TABLE), channel)


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
def separation(comparable: pd.DataFrame) -> dict:
    """Per channel, whether any pair's verdict turned on where the cut is.

    `n_in_grey_band` is the number that landed between GREY_BAND_LO and the
    threshold. Zero means the two populations are separated by empty space and
    the exact cut is immaterial; anything above zero means it is not, and the
    report says so rather than letting the table read as clean.
    """
    sep = {}
    for ch in CHANNELS:
        c = comparable[comparable.channel == ch]
        d, nd = c[c.verdict == "duplicate"], c[c.verdict == "distinct"]
        sep[ch] = {
            "n_pairs": int(len(c)),
            "n_duplicate": int(len(d)),
            "min_frac_among_duplicates": float(d.frac_exact.min())
            if len(d) else None,
            "max_frac_among_distinct": float(nd.frac_exact.max())
            if len(nd) else None,
            "grey_band_lo": GREY_BAND_LO,
            "n_in_grey_band": int((nd.frac_exact >= GREY_BAND_LO).sum()),
        }
    return sep


def summarise(pairs: pd.DataFrame, groups: pd.DataFrame) -> dict:
    """The cross-city picture: which pairs duplicate, and where.

    Reported as "duplicate at k of n comparable cities" because that ratio is
    the diagnosis. A pair at k = n is one series everywhere and the two ids
    are a labelling artefact; a pair at k < n is a routing fallback whose
    footprint is exactly the domain where the alias has nothing of its own to
    serve.
    """
    per_pair = []
    comparable = pairs[pairs.verdict != "not comparable"]
    for (ch, a, b), g in comparable.groupby(["channel", "model_a", "model_b"]):
        n_dup = int((g.verdict == "duplicate").sum())
        if not n_dup:
            continue
        where = sorted(g[g.verdict == "duplicate"].city)
        per_pair.append({
            "channel": ch, "model_a": a, "model_b": b,
            "n_cities_duplicate": n_dup,
            "n_cities_comparable": int(len(g)),
            "everywhere": n_dup == len(g),
            "cities": where,
            "min_frac_exact": float(g[g.verdict == "duplicate"].frac_exact.min()),
            "n_hours_min": int(g[g.verdict == "duplicate"].n_overlap.min()),
        })
    per_pair.sort(key=lambda d: (-d["n_cities_duplicate"], d["channel"]))

    # Does the threshold decide anything? Only if pairs sit near it.
    sep = separation(comparable)

    # Close relatives: two forecasts, but not two independent opinions.
    near = comparable[(comparable.verdict == "distinct")
                      & (comparable.frac_exact >= NEAR_FRAC)]
    near_pairs = []
    for (ch, a, b), g in near.groupby(["channel", "model_a", "model_b"]):
        near_pairs.append({
            "channel": ch, "model_a": a, "model_b": b,
            "n_cities": int(len(g)),
            "max_frac_exact": float(g.frac_exact.max()),
            "max_abs_diff": float(g.max_abs_diff.max()),
        })
    near_pairs.sort(key=lambda d: -d["max_frac_exact"])

    summary = {
        "threshold_frac_exact": DUPLICATE_FRAC,
        "min_overlap_hours": MIN_OVERLAP_HOURS,
        "n_cities": int(pairs.city.nunique()),
        "n_models": int(pd.concat([pairs.model_a, pairs.model_b]).nunique()),
        "duplicate_pairs": per_pair,
        "near_duplicate_pairs": near_pairs,
        "separation": sep,
        "scored_channel": SCORED_CHANNEL,
        "alias_map": alias_map(groups),
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2, sort_keys=True))
    return summary


# --------------------------------------------------------------------------
# Correctness checks
# --------------------------------------------------------------------------
def _fake(n: int, rng: np.random.Generator) -> pd.Series:
    """A plausible hourly PoP series: autocorrelated, quantised to percent."""
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.95 * x[i - 1] + rng.normal()
    p = 100 / (1 + np.exp(-x / 2))
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.Series(np.round(p), index=idx)


def selftest() -> None:
    rng = np.random.default_rng(11)
    n = 5000
    a = _fake(n, rng)

    # 1. A series against itself is a duplicate, with no disagreement at all.
    r = compare_pair(a, a.copy())
    assert r["verdict"] == "duplicate" and r["frac_exact"] == 1.0, r
    assert r["max_abs_diff"] == 0.0, r
    print("  ok  a series matched against itself is called duplicate")

    # 2. One altered hour in 5,000 must still read as duplication - a vendor
    #    revising a single value does not make two names two forecasts.
    b = a.copy()
    b.iloc[123] += 7
    r = compare_pair(a, b)
    assert r["verdict"] == "duplicate" and r["frac_exact"] < 1.0, r
    assert r["max_abs_diff"] == 7.0, r
    print("  ok  one altered hour in 5,000 is still duplication, and the "
          "size of the\n      disagreement is reported rather than hidden")

    # 3. The false-positive case this test exists for: two INDEPENDENT
    #    autocorrelated series quantised to integer percent. They agree on
    #    plenty of hours; they must not be called duplicates.
    c = _fake(n, np.random.default_rng(12))
    r = compare_pair(a, c)
    assert r["verdict"] == "distinct", r
    assert r["frac_exact"] < 0.5, r
    print(f"  ok  two independent quantised series are distinct "
          f"(agreement {r['frac_exact']:.3f}), so\n      integer-percent "
          f"rounding does not manufacture duplicates")

    # 4. A 2% perturbation - far less than any real model difference - is
    #    already distinct, so the test has no trouble with near-twins.
    d = a.copy()
    hit = rng.choice(n, size=n // 50, replace=False)
    d.iloc[hit] += 1
    r = compare_pair(a, d)
    assert r["verdict"] == "distinct", r
    print(f"  ok  a 2% perturbation reads as distinct "
          f"(agreement {r['frac_exact']:.3f})")

    # 5. Too little overlap is "not comparable", never "distinct". Silently
    #    calling a 100-hour overlap distinct is how a duplicate survives.
    r = compare_pair(a.iloc[:100], a.iloc[:100])
    assert r["verdict"] == "not comparable", r
    print("  ok  insufficient overlap is reported as not comparable")

    # 6. Grouping is transitive and the canonical choice prefers the specific
    #    id over the routing alias.
    fake_pairs = pd.DataFrame([
        {"city": "X", "channel": SCORED_CHANNEL, "model_a": "alpha_model",
         "model_b": "beta_seamless", "verdict": "duplicate",
         "frac_exact": 1.0, "n_overlap": 9000},
        {"city": "X", "channel": SCORED_CHANNEL, "model_a": "beta_seamless",
         "model_b": "gamma_seamless", "verdict": "duplicate",
         "frac_exact": 1.0, "n_overlap": 9000},
        {"city": "Y", "channel": SCORED_CHANNEL, "model_a": "alpha_model",
         "model_b": "beta_seamless", "verdict": "distinct",
         "frac_exact": 0.3, "n_overlap": 9000},
    ])
    g = group_table(fake_pairs)
    assert len(g) == 1 and g.iloc[0].n_members == 3, g
    assert g.iloc[0].canonical == "alpha_model", g
    print("  ok  a-b and b-c group into one class of three, and the "
          "canonical member is\n      the specific id rather than the "
          "seamless alias")

    # 7. The alias map is per city: the same pair distinct at Y must not
    #    inherit X's verdict. This is the property the league table depends on.
    am = alias_map(g)
    assert set(am) == {"X"}, am
    assert am["X"] == {"beta_seamless": "alpha_model",
                       "gamma_seamless": "alpha_model"}, am
    print("  ok  duplication is recorded per city, so a pair that is one "
          "series in one city\n      is not assumed to be one everywhere")

    # 8. The false-positive control must itself be able to fire. A pair placed
    #    deliberately between GREY_BAND_LO and the cut has to be counted, or
    #    the "nothing near the threshold" line in the report is unfalsifiable
    #    and therefore worthless.
    grey = pd.DataFrame([
        {"city": "X", "channel": SCORED_CHANNEL, "verdict": "duplicate",
         "frac_exact": 1.0, "max_abs_diff": 0.0},
        {"city": "X", "channel": SCORED_CHANNEL, "verdict": "distinct",
         "frac_exact": (GREY_BAND_LO + DUPLICATE_FRAC) / 2,
         "max_abs_diff": 1.0},
    ])
    s = separation(grey)[SCORED_CHANNEL]
    assert s["n_in_grey_band"] == 1, s
    clean = grey.copy()
    clean.loc[1, "frac_exact"] = 0.90
    assert separation(clean)[SCORED_CHANNEL]["n_in_grey_band"] == 0
    print("  ok  the threshold control fires on a pair placed just below the "
          "cut and stays\n      silent when none is, so its verdict on the "
          "real data can fail")


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def _fmt(df: pd.DataFrame, cols: list[str]) -> str:
    return df[cols].to_string(index=False,
                              float_format=lambda v: f"{v:.4f}")


def report(pairs: pd.DataFrame, groups: pd.DataFrame, summary: dict) -> None:
    print(f"\n=== Task 26a: duplicate-series detection, "
          f"{summary['n_cities']} cities x {summary['n_models']} models ===")
    comparable = pairs[pairs.verdict != "not comparable"]
    print(f"  {len(pairs)} city x channel x pair comparisons, "
          f"{len(comparable)} with at least "
          f"{MIN_OVERLAP_HOURS} shared hours.")

    print("\n--- Pairs serving one series, by channel ---")
    if not summary["duplicate_pairs"]:
        print("  None. Every model pair differs somewhere in every city.")
    for d in summary["duplicate_pairs"]:
        scope = (f"at ALL {d['n_cities_comparable']} cities where they can be "
                 f"compared" if d["everywhere"] else
                 f"at {d['n_cities_duplicate']} of "
                 f"{d['n_cities_comparable']} comparable cities")
        print(f"  {d['model_a']} = {d['model_b']}  [{d['channel']}]")
        print(f"    identical {scope}; worst agreement "
              f"{d['min_frac_exact']:.4f} over >= {d['n_hours_min']:,} hours")
        if not d["everywhere"]:
            print(f"    where: {', '.join(d['cities'])}")

    print("\n--- Is the threshold doing any work? ---")
    for ch, s in summary["separation"].items():
        lo, hi = s["min_frac_among_duplicates"], s["max_frac_among_distinct"]
        if lo is None:
            print(f"  {ch}: no duplicates; the most any two models agree is "
                  f"{hi:.4f}.")
            continue
        if s["n_in_grey_band"] == 0:
            print(f"  {ch}: every flagged pair agrees on {lo:.4f} of hours "
                  f"and NOTHING lands in\n    [{GREY_BAND_LO}, "
                  f"{DUPLICATE_FRAC}) - the cut is in empty space, so no "
                  f"verdict here turned on\n    where it was drawn. The "
                  f"closest unflagged pair is at {hi:.4f}.")
        else:
            print(f"  {ch}: WARNING - {s['n_in_grey_band']} pair(s) sit in "
                  f"[{GREY_BAND_LO}, {DUPLICATE_FRAC}).\n    The threshold is "
                  f"now adjudicating a continuum rather than separating two\n"
                  f"    populations, and these verdicts should not be relied "
                  f"on as they stand.")

    if summary["near_duplicate_pairs"]:
        print("\n--- Close relatives: kept, but not independent opinions ---")
        for d in summary["near_duplicate_pairs"]:
            where = (f"at {d['n_cities']} cities" if d["n_cities"] > 1
                     else "at one city")
            print(f"  {d['model_a']} vs {d['model_b']} [{d['channel']}]: "
                  f"agree on up to {d['max_frac_exact']:.3f} of hours "
                  f"{where},\n    but differ by up to "
                  f"{d['max_abs_diff']:.1f} where they disagree.")
        print("  These stay in the league table: they are two forecasts, not "
              "one. They are\n  printed because a reader counting them as "
              "two independent votes is still\n  wrong, and nothing else in "
              "the pipeline would tell them so.")

    if not groups.empty:
        print("\n--- What the league table must drop, per city ---")
        g = groups[groups.channel == SCORED_CHANNEL]
        print(f"  On {SCORED_CHANNEL} - the channel every calibration number "
              f"in this study is\n  computed from - {len(g)} city x group "
              f"collapses apply, removing "
              f"{int((g.n_members - 1).sum())} rows\n  from the league table "
              f"across {g.city.nunique()} cities.")
        print(_fmt(g.sort_values(["city"]),
                   ["city", "canonical", "aliases", "min_frac_exact",
                    "n_overlap"]))

    print("\n--- What this does and does not establish ---")
    print("  It establishes that the flagged ids are not independent evidence: "
          "agreement\n  between them is one forecast quoted twice, and any "
          "count of 'how many centres\n  say rain' that includes both is "
          "inflated by one.")
    print("  It does not establish WHY. The vendor documents neither the "
          "fallback rule nor\n  the licensing behind it, so whether an alias "
          "resolves to another centre by\n  design or by gap-filling is not "
          "observable from the served numbers.")
    print("  Note the asymmetry between channels above: a pair identical on "
          "probability and\n  distinct on amount is one forecast for every "
          "question this study asks and two\n  for a question about rainfall "
          "totals. Duplication is a property of the field\n  being read, not "
          "of the model id.")


# --------------------------------------------------------------------------
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    print("=== correctness checks ===")
    selftest()
    print("all checks passed")
    if mode in ("check", "selftest"):
        return

    pairs = pairwise_table()
    groups = group_table(pairs)
    summary = summarise(pairs, groups)

    pairs.to_parquet(PAIRS_TABLE, index=False)
    groups.to_parquet(GROUPS_TABLE, index=False)

    report(pairs, groups, summary)

    print(f"\nwrote {PAIRS_TABLE.relative_to(PAIRS_TABLE.parents[2])}")
    print(f"wrote {GROUPS_TABLE.relative_to(GROUPS_TABLE.parents[2])}")
    print(f"wrote {SUMMARY_JSON.relative_to(SUMMARY_JSON.parents[2])}")


if __name__ == "__main__":
    main()
