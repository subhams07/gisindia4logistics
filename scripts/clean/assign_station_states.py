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


def _key(value) -> str:
    return " ".join(str(value).lower().replace("&", " and ").split())


def canonical_state(value, canon: dict) -> str:
    """Canonical state name for a spelling variant; unknown values are returned unchanged."""
    text = " ".join(str(value).split())
    for candidate in (_key(text), _key(text).replace(" ", "")):
        if candidate in canon:
            return canon[candidate]
        if candidate in STATE_SPELLINGS:
            return STATE_SPELLINGS[candidate]
    return text


def normalise_categories(polygon_state_by_code: dict, canon: dict) -> int:
    raw = CATEGORIES.read_text(encoding="utf-8")
    table = pd.read_csv(CATEGORIES, dtype=str, keep_default_na=False)
    fixed = [canonical_state(v, canon) for v in table.state]
    # an unresolvable label ("Gujarat/Maharashtra") takes the station's polygon state when the code joins
    table["state"] = [polygon_state_by_code.get(c) or f if _key(f) not in canon else f
                      for c, f in zip(table.station_code, fixed)]
    table.to_csv(CATEGORIES, index=False, lineterminator="\r\n" if "\r\n" in raw else "\n")
    review = [{"station_code": r.station_code, "station_name": r.station_name, "category_state": r.state,
               "polygon_state": polygon_state_by_code[r.station_code]}
              for r in table.itertuples()
              if polygon_state_by_code.get(r.station_code) and _key(r.state) != _key(polygon_state_by_code[r.station_code])]
    pd.DataFrame(review, columns=["station_code", "station_name", "category_state", "polygon_state"]).to_csv(
        CATEGORY_REVIEW, index=False)
    return len(review)


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    raw = STATIONS.read_text(encoding="utf-8")
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

    review = []
    shared = table.duplicated(["latitude", "longitude"], keep=False)
    for i, row in table.iterrows():
        reasons = []
        if not row.state:
            reasons.append("outside every state polygon")
        elif row.source_state and _key(row.source_state) != _key(row.state):
            reasons.append("source state disagrees with the polygon")
        if shared.iloc[i]:
            reasons.append("shares coordinates with another station")
        if reasons:
            review.append({"station_code": row.station_code, "station_name": row.station_name,
                           "source_state": row.source_state, "polygon_state": row.state,
                           "latitude": row.latitude, "longitude": row.longitude, "reason": "; ".join(reasons)})
    table.to_csv(STATIONS, index=False, lineterminator="\r\n" if "\r\n" in raw else "\n")
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(review).to_csv(REVIEW, index=False)
    filled = int((table.state != "").sum())
    print(f"{filled}/{len(table)} stations have a polygon state; {len(review)} rows to review -> {REVIEW}")
    canon = {_key(s): s for s in states.state}
    by_code = {c: s for c, s in zip(table.station_code, table.state) if c and s}
    n = normalise_categories(by_code, canon)
    print(f"station categories: states normalised; {n} rows disagree with the polygon -> {CATEGORY_REVIEW}")


if __name__ == "__main__":
    main()
