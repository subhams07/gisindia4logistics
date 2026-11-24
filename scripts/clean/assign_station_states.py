"""assign_station_states.py — Take each railway station's state from the state polygons.

The DataMeet base table (2016) has no state for 4,513 of 8,697 stations, and 176
labelled stations sit in another state (83 Telangana stations labelled Andhra
Pradesh, the pre-2014 state, and gross errors such as Kavi, Gujarat, at
Kolkata). This script sets `state` to the current state polygon
(india_states_lgd) containing the station, keeps the table's own value as
`source_state`, and writes a review list of every disagreement, the points
outside every state polygon and the stations that share coordinates:

    data/rail/railway_stations.csv            (state, source_state)
    docs/review/railway_station_state_review.csv

It also normalises the community station-category table's state spellings to
the 36 canonical names (HAR, Gujrat, WEST BEBGAL, names broken by line breaks,
...) and lists the category rows whose state disagrees with their station's
polygon state:

    data/rail/station_categories.csv          (state)
    docs/review/station_category_state_review.csv

Idempotent: a table that already has `source_state` is reassigned from it.

Usage: python scripts/clean/assign_station_states.py
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from standardize import DATA_DIR, REPO_ROOT  # noqa: E402

STATIONS = DATA_DIR / "rail" / "railway_stations.csv"
CATEGORIES = DATA_DIR / "rail" / "station_categories.csv"
REVIEW = REPO_ROOT / "docs" / "review" / "railway_station_state_review.csv"
CATEGORY_REVIEW = REPO_ROOT / "docs" / "review" / "station_category_state_review.csv"
STATE_SPELLINGS = {"chandigarh ut": "Chandigarh", "gujrat": "Gujarat", "har": "Haryana",
                   "west bebgal": "West Bengal", "arunachalpradesh": "Arunachal Pradesh"}
# International Maitree Express stations in Bangladesh (maintainer ruling 2026-11-24):
# retain Bangladesh coordinates with blank Indian `state` and exclude from India-polygon errors.
INTERNATIONAL_STATIONS = {"DSN", "DHCA"}
REVIEW_COLUMNS = ["station_code", "station_name", "source_state", "polygon_state",
                  "latitude", "longitude", "reason", "resolution", "note"]
CATEGORY_REVIEW_COLUMNS = ["station_code", "station_name", "category_state",
                           "polygon_state", "resolution", "note"]


def _key(value) -> str:
    return " ".join(str(value).lower().replace("&", " and ").split())


def _load_resolved_review(path: pathlib.Path, columns: list[str]) -> tuple[pd.DataFrame, set[str]]:
    """Return existing resolved rows (non-empty `resolution`) and their station codes."""
    if not path.exists():
        return pd.DataFrame(columns=columns), set()
    existing = pd.read_csv(path, dtype=str, keep_default_na=False)
    if "resolution" not in existing.columns:
        return pd.DataFrame(columns=columns), set()
    for col in columns:
        if col not in existing.columns:
            existing[col] = ""
    resolved = existing[existing["resolution"].str.strip() != ""][columns].copy()
    return resolved, set(resolved["station_code"])


def canonical_state(value, canon: dict) -> str:
    """Canonical state name for a spelling variant; unknown values are returned unchanged."""
    text = " ".join(str(value).split())
    for candidate in (_key(text), _key(text).replace(" ", "")):
        if candidate in canon:
            return canon[candidate]
        if candidate in STATE_SPELLINGS:
            return STATE_SPELLINGS[candidate]
    return text


HARD_REVIEW_REASONS = (
    "outside every state polygon",
    "shares coordinates with another station",
)


def filter_unresolved_station_reviews(
    live_review: list[dict],
    resolved_df: pd.DataFrame,
    cat_resolved_df: pd.DataFrame,
) -> list[dict]:
    """Keep live review rows that represent unresolved issues or regressions.

    Hard defects (outside every state polygon, shared coordinates) are never
    suppressed for non-international stations even if the code was previously
    reviewed. A source-vs-polygon disagreement is suppressed only when the code
    already has a recorded resolution.
    """
    resolved_codes = set(resolved_df["station_code"]) if len(resolved_df) else set()
    cat_coords_fixed = (
        set(cat_resolved_df.loc[cat_resolved_df["resolution"] == "coords_fixed", "station_code"])
        if len(cat_resolved_df)
        else set()
    )
    suppressed_disagreements = resolved_codes | cat_coords_fixed
    unresolved: list[dict] = []
    for row in live_review:
        reason = str(row.get("reason", ""))
        if any(hard in reason for hard in HARD_REVIEW_REASONS):
            unresolved.append(row)
            continue
        if row["station_code"] not in suppressed_disagreements:
            unresolved.append(row)
    return unresolved


def filter_unresolved_category_reviews(
    live: list[dict],
    resolved_df: pd.DataFrame,
) -> list[dict]:
    """Keep live category-state mismatches unless they match an accepted collision.

    Rows previously resolved via ``label_fixed`` or ``coords_fixed`` are expected
    to stop disagreeing; if they disagree again later, surface them as regressions.
    Accepted ``code_collision`` rows stay suppressed only while their exact
    ``(station_code, category_state, polygon_state)`` triple is unchanged.
    """
    accepted_collisions: set[tuple[str, str, str]] = set()
    if len(resolved_df):
        coll = resolved_df[resolved_df["resolution"].str.strip() == "code_collision"]
        for r in coll.itertuples(index=False):
            accepted_collisions.add((r.station_code, _key(r.category_state), _key(r.polygon_state)))
    return [
        row for row in live
        if (row["station_code"], _key(row["category_state"]), _key(row["polygon_state"]))
        not in accepted_collisions
    ]


def normalise_categories(polygon_state_by_code: dict, canon: dict) -> tuple[int, int]:
    table = pd.read_csv(CATEGORIES, dtype=str, keep_default_na=False)
    for col in table.columns:
        table[col] = table[col].str.replace("\r\n", "\n", regex=False).str.replace("\r", "", regex=False)
    fixed = [canonical_state(v, canon) for v in table.state]
    # an unresolvable label ("Gujarat/Maharashtra") takes the station's polygon state when the code joins
    table["state"] = [polygon_state_by_code.get(c) or f if _key(f) not in canon else f
                      for c, f in zip(table.station_code, fixed)]
    table.to_csv(CATEGORIES, index=False, lineterminator="\n")
    resolved_df, _ = _load_resolved_review(CATEGORY_REVIEW, CATEGORY_REVIEW_COLUMNS)
    live = [{"station_code": r.station_code, "station_name": r.station_name, "category_state": r.state,
             "polygon_state": polygon_state_by_code[r.station_code], "resolution": "", "note": ""}
            for r in table.itertuples()
            if polygon_state_by_code.get(r.station_code) and _key(r.state) != _key(polygon_state_by_code[r.station_code])]
    new_unresolved = filter_unresolved_category_reviews(live, resolved_df)
    out_df = pd.concat([resolved_df, pd.DataFrame(new_unresolved, columns=CATEGORY_REVIEW_COLUMNS)], ignore_index=True)
    out_df.to_csv(CATEGORY_REVIEW, index=False, lineterminator="\n")
    return len(live), len(new_unresolved)


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    table = pd.read_csv(STATIONS, dtype=str, keep_default_na=False)
    if "source_state" not in table.columns:
        at = list(table.columns).index("state") + 1
        table.insert(at, "source_state", table["state"])
    states = gpd.read_file(DATA_DIR / "administrative" / "india_states_lgd.geojson")[["state", "geometry"]]
    points = gpd.GeoDataFrame(table[["station_code"]], crs=4326, geometry=gpd.points_from_xy(
        pd.to_numeric(table.longitude), pd.to_numeric(table.latitude)))
    joined = gpd.sjoin(points, states.rename(columns={"state": "polygon_state"}), how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    polygon_state = joined["polygon_state"].reindex(table.index)
    table["state"] = polygon_state.fillna("").to_numpy()
    # Keep Bangladesh Maitree Express stations' state blank (not an Indian state)
    table.loc[table["station_code"].isin(INTERNATIONAL_STATIONS), "state"] = ""

    resolved_df, _ = _load_resolved_review(REVIEW, REVIEW_COLUMNS)
    cat_resolved_df, _ = _load_resolved_review(CATEGORY_REVIEW, CATEGORY_REVIEW_COLUMNS)

    live_review = []
    shared = table.duplicated(["latitude", "longitude"], keep=False)
    for i, row in table.iterrows():
        code = row.station_code
        if code in INTERNATIONAL_STATIONS:
            continue
        reasons = []
        if not row.state:
            reasons.append("outside every state polygon")
        elif row.source_state and _key(row.source_state) != _key(row.state):
            reasons.append("source state disagrees with the polygon")
        if shared.iloc[i]:
            reasons.append("shares coordinates with another station")
        if reasons:
            live_review.append({"station_code": code, "station_name": row.station_name,
                                "source_state": row.source_state, "polygon_state": row.state,
                                "latitude": row.latitude, "longitude": row.longitude,
                                "reason": "; ".join(reasons), "resolution": "", "note": ""})
    new_unresolved = filter_unresolved_station_reviews(live_review, resolved_df, cat_resolved_df)
    table.to_csv(STATIONS, index=False, lineterminator="\n")
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    out_df = pd.concat([resolved_df, pd.DataFrame(new_unresolved, columns=REVIEW_COLUMNS)], ignore_index=True)
    out_df.to_csv(REVIEW, index=False, lineterminator="\n")
    filled = int((table.state != "").sum())
    n_intl = int(table["station_code"].isin(INTERNATIONAL_STATIONS).sum())
    print(f"{filled}/{len(table)} stations have a polygon state ({n_intl} international excluded); "
          f"{len(new_unresolved)} unresolved ({len(out_df)} total in {REVIEW.name})")
    canon = {_key(s): s for s in states.state}
    by_code = {c: s for c, s in zip(table.station_code, table.state) if c and s}
    n_live, n_unres = normalise_categories(by_code, canon)
    print(f"station categories: states normalised; {n_live} live mismatches, {n_unres} unresolved ({CATEGORY_REVIEW.name})")


if __name__ == "__main__":
    main()
