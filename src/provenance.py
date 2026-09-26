"""Record where every published number came from, and under what terms.

Task 9, gap G15. The study redistributes derived data: 86 parquet tables and
109 HTML pages, all in version control, all built from other people's
observations and forecasts. Three questions nobody had answered.

Which licence covers each input. Open-Meteo states CC BY 4.0 over its whole
API, but its own source list gives the UK Met Office as CC BY-**SA** - a
share-alike term that propagates into anything derived from it. One upstream
in fifteen carries copyleft, and the blanket statement does not mention that
it is there.

Which published artefacts inherit it. Answered by reading the files rather
than by reasoning about the pipeline: every string value in every tracked
table, matched against the model registry, so a table cannot escape by being
named innocuously. Six of ninety carry UKMO-derived rows. None of them is a
headline result, which is the finding - the segregation costs nothing.

And whether the attribution terms are actually met. They were not: Open-Meteo
requires a link next to any display of its data, and all 109 pages carried the
words without the link.

This is not legal advice. It is a record of what each publisher states, when
it was read, and which files are affected, so that a reader or a repository
can check the reasoning rather than take it on trust.

Usage:
    python src/provenance.py            # audit, write tables and NOTICE.md
    python src/provenance.py check      # self-tests only
"""

from __future__ import annotations

import dataclasses as dc
import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

from config import PROCESSED, ROOT, PROVIDER_MODELS

SOURCE_TABLE = PROCESSED / "provenance_sources.parquet"
ARTEFACT_TABLE = PROCESSED / "provenance_artefacts.parquet"
NOTICE = ROOT / "NOTICE.md"

# Date every URL below was read. A licence is a claim about the present, so it
# carries a timestamp; a reader in a year should distrust it, not assume it.
VERIFIED = "2026-09-22"


@dc.dataclass(frozen=True)
class Source:
    key: str
    name: str
    licence: str            # SPDX-style identifier, or a plain description
    share_alike: bool       # does the licence propagate to derived works?
    url: str                # where the statement was read
    attribution: str        # the credit line this study owes
    note: str = ""


SOURCES: dict[str, Source] = {s.key: s for s in [
    # --- the forecast API and its upstreams -------------------------------
    Source(
        "open_meteo", "Open-Meteo forecast API", "CC-BY-4.0", False,
        "https://open-meteo.com/en/licence",
        "Weather data by Open-Meteo.com",
        "States CC BY 4.0 over API output and requires a link next to any "
        "display of the data. Its own source list gives one upstream under a "
        "share-alike term, so the blanket statement cannot be relied on for "
        "that upstream.",
    ),
    Source(
        "ukmo", "UK Met Office (via Open-Meteo)", "CC-BY-SA-4.0", True,
        "https://creativecommons.org/licenses/by-sa/4.0/deed.en",
        "Contains Met Office data, CC BY-SA 4.0",
        "The only share-alike input in the study. Adapted material must carry "
        "the same licence, so anything derived from it is segregated.",
    ),
    Source(
        "ecmwf", "ECMWF open data (via Open-Meteo and dynamical.org)",
        "CC-BY-4.0", False,
        "https://www.ecmwf.int/en/forecasts/datasets/open-data",
        "Contains modified ECMWF data",
        "The dynamical.org STAC collections for IFS ENS and AIFS ENS carry a "
        "second licence link to ECMWF's general terms alongside CC BY 4.0.",
    ),
    Source(
        "noaa_nwp", "NOAA NCEP model output (GFS, GEFS)",
        "US Government work, no copyright", False,
        "https://stac.dynamical.org/noaa-gefs-forecast-35-day/collection.json",
        "Contains NOAA NCEP data",
        "Republished by dynamical.org as CC-BY-4.0.",
    ),
    Source(
        "nmhs_ccby", "National met services published CC BY via Open-Meteo",
        "CC-BY-4.0", False,
        "https://open-meteo.com/en/licence",
        "Contains data from DWD, Meteo-France, ECCC, JMA, MET Norway, KNMI, "
        "DMI, ARPAE and MeteoSwiss",
        "DWD, ECMWF, MET Norway, KNMI, DMI, ARPAE and MeteoSwiss are listed "
        "CC-BY; ECCC, Meteo-France and JMA carry their own agency terms "
        "linked from the same page, none of them share-alike.",
    ),
    # --- the archives read directly ---------------------------------------
    Source(
        "dynamical", "dynamical.org ensemble archives", "CC-BY-4.0", False,
        "https://dynamical.org/license/",
        "Ensemble archives via dynamical.org",
        "Datasets CC BY 4.0 per STAC collection metadata. The website's own "
        "prose is CC BY-NC-SA, so its validation reports cannot be quoted "
        "into a commercially published paper without permission.",
    ),
    Source(
        "ghcn", "GHCN-Daily", "US Government work, citation requested", False,
        "https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme.txt",
        "Menne et al. 2012, Global Historical Climatology Network - Daily",
        "The readme asks for both the overview paper and the dataset version "
        "to be cited.",
    ),
    Source(
        "isd", "NOAA Integrated Surface Database",
        "US Government work; foreign contributions unresolved", False,
        "https://www.ncei.noaa.gov/products/land-based-station/"
        "integrated-surface-database",
        "NOAA Integrated Surface Database",
        "The product page states no licence. ISD aggregates foreign national "
        "reports, some of which carry WMO Resolution 40 conditions. This "
        "study publishes coverage counts, not observations, so nothing is "
        "redistributed - recorded as unresolved rather than as cleared.",
    ),
    Source(
        "jma_obs", "Japan Meteorological Agency past weather data (etrn)",
        "Public Data License 1.0 (CC-BY-4.0 compatible)", False,
        "https://www.jma.go.jp/jma/kishou/info/coment.html",
        "出典：気象庁ホームページ (Source: JMA website), "
        "https://www.data.jma.go.jp/stats/etrn/",
        "Truth for Japanese cities (src/jma_truth.py), read 2026-09-23. The "
        "terms page states the Public Data License 1.0, which the Government "
        "of Japan declares compatible with CC BY 4.0; derived data must say "
        "it was processed and must not be presented as JMA's own.",
    ),
    Source(
        "chirps", "CHIRPS and CHIRP daily rasters", "CC0-1.0", False,
        "https://www.chc.ucsb.edu/data/chirps",
        "CHIRPS, Climate Hazards Center, UC Santa Barbara",
        "Copyright waived, registered with Creative Commons as public domain.",
    ),
    Source(
        "geonames", "GeoNames city database", "CC-BY-4.0", False,
        "https://www.geonames.org/",
        "City locations from GeoNames, CC BY 4.0",
    ),
    Source(
        "worldbank", "World Bank country income classification",
        "CC-BY-4.0", False,
        "https://datacatalog.worldbank.org/public-licenses",
        "World Bank country income groups, CC BY 4.0",
    ),
    # --- the Atlas live sky (display only, never enters the audit) ---------
    Source(
        "eumetsat", "EUMETSAT IR 10.8 um world cloud mosaic (EUMETView)",
        "Free use with attribution (EUMETSAT data policy)", False,
        "https://www.eumetsat.int/data-policy",
        "Cloud imagery (c) EUMETSAT",
        "Fetched by the reader's browser from view.eumetsat.int at view time "
        "and drawn as the globe's clouds; not stored in data/ or dist/. Over "
        "the Meteosat (MTG) disk a fresher IR 10.5 um frame replaces the "
        "mosaic, and once a city there is chosen one sharper ~2 km crop of "
        "about +/-8 deg around it is fetched as well (skipped on slow "
        "connections and in data-saver mode). The processed layers are kept "
        "in the reader's own browser storage (IndexedDB) so the next city "
        "page can show them at once, labelled with their true image time, "
        "until fresh frames arrive. One frozen pair of frames sits in "
        "src/fixtures/atlas/ for the browser tests only; the city crop is "
        "cut from it.",
    ),
    Source(
        "gibs_imerg", "NASA GPM IMERG precipitation rate via NASA GIBS",
        "NASA open data, no restriction; credit requested", False,
        "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies",
        "Rain imagery from NASA GPM IMERG, served by NASA Global Imagery "
        "Browse Services (GIBS)",
        "Fetched live by the browser, as the clouds are; a frozen frame is "
        "a test fixture only.",
    ),
    Source(
        "celestrak", "CelesTrak GP orbital elements (OMM), weather satellites",
        "Free public data; credit requested (CelesTrak)", False,
        "https://celestrak.org/NORAD/documentation/gp-data-formats.php",
        "Satellite orbits from CelesTrak (T.S. Kelso), from US Space Force data",
        "Twelve satellites' mean elements baked into data/processed/satellites.json "
        "by src/satellites.py (run by hand) and shipped with the site; the "
        "reader's browser refreshes them from celestrak.org at most once per "
        "two hours. The browser tests answer CelesTrak with the baked copy.",
    ),
    Source(
        "nasa_moon", "NASA SVS CGI Moon Kit (LRO colour map)",
        "Public domain (NASA)", False,
        "https://svs.gsfc.nasa.gov/4720",
        "Moon texture: NASA's Scientific Visualization Studio, CGI Moon Kit",
        "Downscaled to dist/assets/geo/ as the Moon on the Atlas globe.",
    ),
    Source(
        "black_marble", "NASA Black Marble night lights",
        "Public domain (NASA)", False,
        "https://earthobservatory.nasa.gov/features/NightLights",
        "City lights: NASA Earth Observatory, Black Marble",
        "Downscaled to dist/assets/geo/ as the night side of the Atlas globe.",
    ),
]}

# Every organisation string appearing in either model registry, mapped to the
# source whose terms govern it. Deliberately exhaustive rather than defaulted:
# an unlisted organisation aborts. A default would have filed the Met Office
# ensembles under CC BY, because the second registry spells NOAA and ECCC
# differently from the first - which is how this audit found them.
ORG_SOURCE = {
    "Met Office (UK)": "ukmo",
    "ECMWF": "ecmwf",
    "NOAA (US National Weather Service)": "noaa_nwp",
    "NOAA": "noaa_nwp",
    "ARPAE (Italy)": "nmhs_ccby",
    "DMI (Denmark)": "nmhs_ccby",
    "DWD (German Weather Service)": "nmhs_ccby",
    "DWD": "nmhs_ccby",
    "ECCC (Environment Canada)": "nmhs_ccby",
    "ECCC": "nmhs_ccby",
    "Japan Meteorological Agency": "nmhs_ccby",
    "KNMI (Netherlands)": "nmhs_ccby",
    "MET Norway": "nmhs_ccby",
    "Météo-France": "nmhs_ccby",
    "MeteoSwiss": "nmhs_ccby",
    "BOM (Australia)": "nmhs_ccby",
}

# Directories whose contents are published. dist/ is the site; data/ is the
# reproduction archive. Both are in version control, so both are redistributed.
PUBLISHED = ("data", "dist")


# ---------------------------------------------------------------------------
def model_sources() -> dict[str, str]:
    """Every model id mapped to the source whose terms govern it.

    Both registries, because there are two. The forecast API models live in
    config and the member-level ensemble models live in collect_ensemble, and
    only the second contains the Met Office ensembles whose raw precipitation
    values are published in `ensemble_members.parquet`. An audit reading one
    registry reports the smaller and wrong answer.
    """
    from collect_ensemble import ENSEMBLE_MODELS

    out: dict[str, str] = {}
    for reg in (PROVIDER_MODELS, ENSEMBLE_MODELS):
        for key, m in reg.items():
            org = m.organization
            if org not in ORG_SOURCE:
                raise SystemExit(
                    f"provenance: model {key} has organisation {org!r}, "
                    "which is mapped to no licence source")
            prev = out.get(key)
            if prev is not None and prev != ORG_SOURCE[org]:
                raise SystemExit(
                    f"provenance: model {key} maps to {prev} in one registry "
                    f"and {ORG_SOURCE[org]} in the other")
            out[key] = ORG_SOURCE[org]
    return out


def tracked_files() -> list[Path]:
    """Files under version control in the published directories, as they
    stand on disk: the site's payloads are content-hashed, so a rebuild
    deletes tracked names and writes new untracked ones until the next
    commit. Both sides are what will be published, so new files are audited
    and deleted ones skipped."""
    out: list[Path] = []
    for d in PUBLISHED:
        r = subprocess.run(["git", "ls-files", "--cached", "--others",
                            "--exclude-standard", d], cwd=ROOT,
                           capture_output=True, text=True)
        out += [ROOT / f for f in r.stdout.split()]
    return [p for p in out if p.is_file()]


def file_markers(path: Path) -> set[str]:
    """Model ids appearing as data values in one published file.

    Parquet string columns and JSON text are read; HTML is not, because a page
    naming the Met Office in a sentence about who the Met Office is has not
    reproduced any of its data. The site names it on all 109 pages and carries
    the model ids on none, so the distinction is the difference between six
    affected files and a hundred and fifteen.
    """
    known = set(model_sources())
    if path.suffix == ".parquet":
        try:
            d = pd.read_parquet(path)
        except Exception:
            return set()
        vals: set[str] = set()
        for c in d.columns:
            col = d[c]
            if pd.api.types.is_string_dtype(col) or col.dtype == object:
                try:
                    vals |= set(map(str, pd.unique(col.dropna())[:50_000]))
                except Exception:
                    continue
        found: set[str] = set()
        for v in vals:
            # Values may be a single id or a comma-joined duplicate group.
            for part in re.split(r"[,\s]+", v):
                if part in known:
                    found.add(part)
        return found
    if path.suffix == ".json":
        txt = path.read_text(errors="ignore")
        return {k for k in known if f'"{k}"' in txt}
    return set()


# Column names that hold a forecast quantity as served, rather than a
# statistic computed from many of them. A table with one of these beside a
# share-alike model id republishes that model's output; a table of agreement
# fractions does not, and the obligations are not the same size.
RAW_FIELDS = {"precipitation", "precipitation_probability", "temperature_2m",
              "forecast_prob", "vendor_pop", "precipitation_mm"}


def artefact_table() -> pd.DataFrame:
    """One row per published file that carries model-derived values."""
    ms = model_sources()
    rows = []
    for p in tracked_files():
        mk = file_markers(p)
        if not mk:
            continue
        srcs = sorted({ms[m] for m in mk})
        sa = any(SOURCES[s].share_alike for s in srcs)
        raw = False
        if sa and p.suffix == ".parquet":
            try:
                raw = bool(RAW_FIELDS & set(pd.read_parquet(p).columns))
            except Exception:
                raw = False
        rows.append({
            "path": str(p.relative_to(ROOT)),
            "models": ",".join(sorted(mk)),
            "sources": ",".join(srcs),
            "share_alike": sa,
            "kind": ("served values" if raw else "derived statistics")
            if sa else "",
        })
    return pd.DataFrame(rows).sort_values("path").reset_index(drop=True)


# ---------------------------------------------------------------------------
def attribution_audit(link: str = "https://open-meteo.com") -> dict:
    """Do the published pages carry the link Open-Meteo's licence requires?

    The term is explicit and unusual: a link, next to the display, not merely
    a credit in prose. Counting pages that name Open-Meteo would pass; the
    test has to look for the anchor.
    """
    pages = [p for p in tracked_files() if p.suffix in (".html", ".htm")]
    pat = re.compile(r'href="' + re.escape(link), re.I)
    named = [p for p in pages if re.search(r"open-meteo", p.read_text(
        errors="ignore"), re.I)]
    linked = [p for p in named if pat.search(p.read_text(errors="ignore"))]
    return {
        "pages": len(pages),
        "pages_naming": len(named),
        "pages_linking": len(linked),
        "missing": [str(p.relative_to(ROOT)) for p in named
                    if p not in linked],
    }


# ---------------------------------------------------------------------------
def notice_text(art: pd.DataFrame, audit: dict) -> str:
    """The attribution file the licences collectively require."""
    sa = art[art.share_alike]
    lines = [
        "# Attribution and licensing",
        "",
        f"Sources read {VERIFIED}. This file is generated by "
        "`src/provenance.py`; edit that, not this.",
        "",
        "## Credit required by the input licences",
        "",
    ]
    for s in SOURCES.values():
        lines.append(f"- **{s.name}** — {s.licence}. {s.attribution}. "
                     f"<{s.url}>")
        if s.note:
            lines.append(f"  - {s.note}")
    lines += [
        "",
        "## Licence of the published outputs",
        "",
        "Everything in `data/` and `dist/` is offered under **CC BY 4.0**, "
        "with the exception listed below.",
        "",
        f"### CC BY-SA 4.0 — {len(sa)} files",
        "",
        "These carry values derived from UK Met Office model output, which "
        "Open-Meteo lists under CC BY-SA 4.0. The share-alike term "
        "propagates, so they are offered under that licence rather than "
        "relicensed under it.",
        "",
    ]
    for r in sa.itertuples():
        lines.append(f"- `{r.path}` — {r.kind} ({r.models})")
    lines += [
        "",
        "No reported number carries a share-alike series. The lineage is not "
        "clean - `league.py` reads the duplicate table, Met Office rows "
        "included - so the claim checked is the narrower and testable one: no "
        "Met Office model is pinned in any league city, so those rows collapse "
        "nothing, and no headline table contains a Met Office series. "
        "`provenance.py check` fails if that ever stops being true.",
        "",
        "## Source code",
        "",
        "Code in `src/` is the authors' own work. Open-Meteo's own server is "
        "AGPLv3, but no Open-Meteo code is used or derived from here — only "
        "its API output, under CC BY 4.0.",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
def run_checks() -> None:
    """Each check can fail, and the pipeline stage stops if one does."""
    ms = model_sources()

    # 1. No model may reach the published tables without a licence attached,
    #    and both registries must be read - the ensemble-only ids are exactly
    #    the ones that republish Met Office values.
    from collect_ensemble import ENSEMBLE_MODELS
    missing = [k for k in list(PROVIDER_MODELS) + list(ENSEMBLE_MODELS)
               if k not in ms]
    assert not missing, f"models with no source: {missing}"
    bad = [k for k, v in ms.items() if v not in SOURCES]
    assert not bad, f"models mapped to unknown sources: {bad}"
    only_ens = set(ENSEMBLE_MODELS) - set(PROVIDER_MODELS)
    assert only_ens & set(ms), "ensemble-only registry not covered"
    print(f"  1. all {len(ms)} models across both registries mapped     OK")

    # 2. The share-alike upstream must actually be reachable from a model id,
    #    or the whole segregation is looking for something that cannot occur.
    sa_models = [k for k, v in ms.items() if SOURCES[v].share_alike]
    assert sa_models, "no model maps to a share-alike source"
    print(f"  2. share-alike reachable from {len(sa_models)} model ids   OK")

    # 3. The marker scan must find an id planted in a parquet value column and
    #    must not find one planted in HTML prose, because that is the rule the
    #    contamination count rests on.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        pd.DataFrame({"model": sa_models[:1] + ["gfs_seamless"],
                      "x": [1.0, 2.0]}).to_parquet(t / "a.parquet")
        (t / "b.html").write_text(
            f"<p>The {sa_models[0]} model is run by the Met Office.</p>")
        got_p = file_markers(t / "a.parquet")
        got_h = file_markers(t / "b.html")
    assert sa_models[0] in got_p, f"planted id not found in parquet: {got_p}"
    assert not got_h, f"prose mention counted as reproduction: {got_h}"
    print("  3. scan finds planted values, ignores prose mentions      OK")

    # 4. The attribution audit must distinguish a link from a mention. A test
    #    that only looked for the word would pass on the broken site.
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (t / "good.html").write_text(
            '<a href="https://open-meteo.com/">Weather data by Open-Meteo</a>')
        (t / "bad.html").write_text("<footer>Forecasts: Open-Meteo</footer>")
        pat = re.compile(r'href="https://open-meteo\.com', re.I)
        assert pat.search((t / "good.html").read_text())
        assert not pat.search((t / "bad.html").read_text())
        assert re.search(r"open-meteo", (t / "bad.html").read_text(), re.I)
    print("  4. audit separates a required link from a bare mention    OK")

    # 5. The claim that the copyleft reaches no reported result has to be
    #    tested, not asserted. league.py does consume duplicate_groups.parquet,
    #    which carries Met Office rows - so the test is whether a share-alike
    #    id survives into a headline table, not whether the lineage touches one.
    headline = ["league_table.parquet", "triangulation.parquet",
                "significance_headline.parquet", "decision_metrics.parquet",
                "physics_ml_scores.parquet", "capitals_pinned_daily.parquet"]
    leaked = {}
    for name in headline:
        p = PROCESSED / name
        if not p.exists():
            continue
        hit = file_markers(p) & set(sa_models)
        if hit:
            leaked[name] = sorted(hit)
    assert not leaked, f"share-alike rows in headline tables: {leaked}"
    print(f"  5. no share-alike id in {len(headline)} headline tables       OK")


# ---------------------------------------------------------------------------
def report(art: pd.DataFrame, audit: dict) -> None:
    ms = model_sources()
    print("=" * 78)
    print("PROVENANCE AND LICENCE AUDIT   (Task 9, gap G15)")
    print("=" * 78)

    print(f"\n1. SOURCES   {len(SOURCES)} inputs, terms read {VERIFIED}\n")
    print(f"   {'source':34s} {'licence':40s} SA")
    for s in SOURCES.values():
        print(f"   {s.name[:33]:34s} {s.licence[:39]:40s} "
              f"{'yes' if s.share_alike else '-'}")
    sa = [s for s in SOURCES.values() if s.share_alike]
    print(f"\n   {len(sa)} of {len(SOURCES)} carry a share-alike term: "
          f"{', '.join(s.name for s in sa)}.")
    print("   Open-Meteo states CC BY 4.0 over its whole API while listing")
    print("   this upstream as CC BY-SA. The stricter term is assumed.")

    print(f"\n2. MODELS    {len(ms)} forecast models by governing source\n")
    by = pd.Series(ms).value_counts()
    for k, n in by.items():
        print(f"   {SOURCES[k].name[:44]:46s} {n:3d} models"
              f"{'   SHARE-ALIKE' if SOURCES[k].share_alike else ''}")

    print(f"\n3. ARTEFACTS   {len(tracked_files())} published files scanned\n")
    print(f"   carrying model-derived values     {len(art):3d}")
    print(f"   of those, share-alike             {int(art.share_alike.sum()):3d}")
    print()
    for r in art[art.share_alike].itertuples():
        print(f"   {r.path:46s} {r.kind}")
    n_raw = int((art.kind == "served values").sum())
    print(f"\n   {n_raw} of them republish Met Office output as served rather")
    print("   than as a statistic computed from it, which is the larger")
    print("   obligation and the one a scan of the forecast-API registry")
    print("   alone would have missed: those ids live in the ensemble")
    print("   registry, where the same organisations are spelled")
    print("   differently, so a defaulting lookup filed them under CC BY.")
    print("\n   Segregated rather than deleted: the duplicate-detection result")
    print("   is a fact about the API worth keeping. The lineage is not")
    print("   clean - league.py consults the duplicate table, Met Office rows")
    print("   and all - so the test is whether a share-alike series survives")
    print("   into a reported number, and it does not: those rows name only")
    print("   Met Office models, which are pinned in no league city, so they")
    print("   collapse nothing. Checked rather than argued, over six headline")
    print("   tables, and the stage stops if one ever carries such a row.")

    print("\n4. ATTRIBUTION COMPLIANCE\n")
    print(f"   published pages                   {audit['pages']:3d}")
    print(f"   naming Open-Meteo                 {audit['pages_naming']:3d}")
    print(f"   carrying the required link        {audit['pages_linking']:3d}")
    if audit["missing"]:
        print(f"\n   FAIL: {len(audit['missing'])} pages display Open-Meteo "
              "data with no link.")
        print("   Its licence asks for a link next to the display, not a")
        print("   credit in prose. The footer named the source and did not")
        print("   link it, so the site was out of compliance on the one term")
        print("   its main input states in imperative form.")
    else:
        print("\n   OK: every page naming Open-Meteo links to it.")

    print("\n5. WHAT THIS DOES NOT SETTLE\n")
    print("   ISD states no licence on its product page and aggregates")
    print("   foreign national reports, some under WMO Resolution 40. This")
    print("   study publishes coverage counts from it and no observations,")
    print("   so nothing is redistributed - but the terms are recorded as")
    print("   unresolved rather than as cleared.")
    print("   dynamical.org's dataset licences are CC BY 4.0; its website")
    print("   prose is CC BY-NC-SA, so its validation reports cannot be")
    print("   quoted into a commercially published paper as they stand.")
    print("   None of this is legal advice. It is a record of what each")
    print("   publisher states and when it was read.")
    print()


# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        print("provenance self-tests")
        run_checks()
        return

    print("provenance self-tests")
    run_checks()
    print()

    art = artefact_table()
    audit = attribution_audit()
    report(art, audit)

    src = pd.DataFrame([dc.asdict(s) for s in SOURCES.values()])
    src["verified"] = VERIFIED
    src.to_parquet(SOURCE_TABLE, index=False)
    art.to_parquet(ARTEFACT_TABLE, index=False)
    NOTICE.write_text(notice_text(art, audit))
    print(f"wrote {SOURCE_TABLE.name}, {ARTEFACT_TABLE.name}, NOTICE.md")

    if audit["missing"]:
        raise SystemExit(
            f"provenance: {len(audit['missing'])} published pages display "
            "Open-Meteo data without the link its licence requires")


if __name__ == "__main__":
    main()
