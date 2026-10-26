"""Spatial assignment of villages / habitations to the CURRENT district polygons.

The project's district key is the Survey of India ABDB district code carried by
``data/administrative/india_districts_lgd.{parquet,geojson}``. The per-village
source files carry their own (real LGD) district name/code, which is stale or
truncated for the newest districts and uses mojibake spellings. Spatial
location wins: every village is assigned to the polygon that contains its
representative point, scoped to the village's own state.

Public API
----------
``assign_current_district(state, geoms_4326, source_codes=None)``
    index-aligned DataFrame with ``district``, ``district_code`` (Int64),
    ``district_assignment`` ('within' | 'nearest_in_state') and
    ``district_assignment_km`` (0 for 'within').
``reassign_frame(df, state, geoms_4326)``
    applies the assignment to a village-access style frame (``district`` /
    ``district_code`` hold the SOURCE values on input) and returns the frame
    with the new columns placed right after ``district_code``.

This module is deliberately NOT imported by any Stage 4 code-closure file.
"""
from __future__ import annotations

import functools
import pathlib

import geopandas as gpd
import numpy as np
import pandas as pd

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DISTRICTS_PARQUET = REPO_ROOT / "data" / "administrative" / "india_districts_lgd.parquet"
DISTRICTS_GEOJSON = REPO_ROOT / "data" / "administrative" / "india_districts_lgd.geojson"
PROJ = 7755  # metres (India NSF LCC)
MAX_NEAREST_KM = 2.0  # observed maximum is 0.96 km

PLACEHOLDER_NAME = "Disputed (Jharkhand)"
NEW_COLUMNS = ["source_district", "source_district_code", "district_assignment",
               "district_assignment_km", "exclude_from_district_stats"]


@functools.lru_cache(maxsize=1)
def _load_districts() -> gpd.GeoDataFrame:
    path = DISTRICTS_PARQUET if DISTRICTS_PARQUET.exists() else DISTRICTS_GEOJSON
    g = gpd.read_parquet(path) if path.suffix == ".parquet" else gpd.read_file(path)
    return g[["state", "district", "district_code", "geometry"]].reset_index(drop=True)


def _num(series) -> pd.Series:
    """Nullable Int64 from any numeric-ish series (floats, strings, blanks)."""
    return pd.to_numeric(pd.Series(series).reset_index(drop=True), errors="coerce").astype("Int64")


def _state_polygons(state: str, districts: gpd.GeoDataFrame | None) -> gpd.GeoDataFrame:
    d = _load_districts() if districts is None else districts
    sel = d[d["state"].astype(str).str.lower() == str(state).lower()]
    if sel.empty:
        raise ValueError(f"No district polygons for state {state!r}")
    sel = sel[["state", "district", "district_code", "geometry"]].reset_index(drop=True)
    sel["district_code"] = _num(sel["district_code"])
    sel["_pid"] = np.arange(len(sel))  # stable order = polygon layer order
    return sel


def _points_4326(geoms: gpd.GeoSeries) -> gpd.GeoSeries:
    """Representative point for polygons (as nearest_facility does), the point itself otherwise."""
    if geoms.isna().any() or geoms.is_empty.any():
        raise ValueError("Cannot assign districts: null or empty geometry present")
    is_pt = geoms.geom_type.isin(["Point"])
    pts = geoms.representative_point()
    return pts.where(~is_pt, geoms)


def assign_current_district(state: str, geoms_4326, source_codes=None, *,
                            districts: gpd.GeoDataFrame | None = None,
                            max_nearest_km: float = MAX_NEAREST_KM) -> pd.DataFrame:
    """Assign each geometry to a current district polygon of ``state``.

    ``geoms_4326`` : GeoSeries (or iterable of shapely geometries) in EPSG:4326.
    ``source_codes`` : optional per-geometry source district code; when a point lies
        in several polygons the polygon whose code equals it wins, otherwise the first
        polygon in layer order.
    ``districts`` : override polygon layer (tests); default is the committed layer.
    Fallback for points outside every polygon of the state is the nearest polygon of
    the SAME state (EPSG:7755); a distance above ``max_nearest_km`` raises.
    """
    geoms = geoms_4326 if isinstance(geoms_4326, gpd.GeoSeries) else gpd.GeoSeries(list(geoms_4326), crs=4326)
    index = geoms.index
    n = len(geoms)
    polys = _state_polygons(state, districts)
    if n == 0:
        return pd.DataFrame({"district": pd.Series([], dtype=object),
                             "district_code": pd.Series([], dtype="Int64"),
                             "district_assignment": pd.Series([], dtype=object),
                             "district_assignment_km": pd.Series([], dtype=float)}, index=index)
    src = _num(source_codes) if source_codes is not None else pd.Series([pd.NA] * n, dtype="Int64")
    if len(src) != n:
        raise ValueError("source_codes must be aligned with geoms")

    pts = _points_4326(geoms.reset_index(drop=True))
    P = gpd.GeoDataFrame({"_i": np.arange(n)}, geometry=pts.values, crs=4326)
    polys_4326 = polys.set_crs(4326, allow_override=True) if polys.crs is None else polys.to_crs(4326)
    hit = gpd.sjoin(P, polys_4326[["_pid", "geometry"]], how="inner", predicate="within")
    hit = hit[["_i", "_pid"]].copy()
    pid_code = polys["district_code"].to_numpy(dtype=object)
    srcv = src.to_numpy(dtype=object)
    hit["_match"] = [(pid_code[p] is not pd.NA and srcv[i] is not pd.NA and pid_code[p] == srcv[i])
                     for i, p in zip(hit["_i"], hit["_pid"])]
    hit = hit.sort_values(["_i", "_match", "_pid"], ascending=[True, False, True]).drop_duplicates("_i")
    pid = pd.Series(-1, index=range(n), dtype=int)
    km = pd.Series(0.0, index=range(n))
    how = pd.Series("within", index=range(n), dtype=object)
    pid.loc[hit["_i"].to_numpy()] = hit["_pid"].to_numpy()

    miss = pid[pid < 0].index.to_numpy()
    if len(miss):
        Pm = P.loc[miss].to_crs(PROJ)
        Q = polys_4326[["_pid", "geometry"]].to_crs(PROJ)
        nn = gpd.sjoin_nearest(Pm, Q, how="left", distance_col="_d")
        nn = nn.sort_values(["_i", "_d", "_pid"]).drop_duplicates("_i")
        far = nn[nn["_d"] > max_nearest_km * 1000]
        if len(far):
            raise ValueError(
                f"{len(far)} point(s) in {state!r} are farther than {max_nearest_km} km from every "
                f"district polygon of the state (max {far['_d'].max() / 1000:.2f} km)")
        pid.loc[nn["_i"].to_numpy()] = nn["_pid"].to_numpy().astype(int)
        km.loc[nn["_i"].to_numpy()] = (nn["_d"].to_numpy() / 1000).round(3)
        how.loc[nn["_i"].to_numpy()] = "nearest_in_state"

    chosen = polys.set_index("_pid").loc[pid.to_numpy()]
    out = pd.DataFrame({
        "district": chosen["district"].to_numpy(),
        "district_code": pd.array(chosen["district_code"].to_numpy(dtype=object), dtype="Int64"),
        "district_assignment": how.to_numpy(),
        "district_assignment_km": km.to_numpy(),
    }, index=index)
    return out


def is_placeholder(village, village_code) -> pd.Series:
    """True only for the SoI 'Disputed (Jharkhand)' placeholder (name AND code both match)."""
    v = pd.Series(village).astype(str).str.strip()
    c = pd.Series(village_code).astype(str).str.strip()
    return ((v == PLACEHOLDER_NAME) & (c == PLACEHOLDER_NAME)).to_numpy()


def reassign_frame(df: pd.DataFrame, state: str, geoms_4326, *,
                   districts: gpd.GeoDataFrame | None = None) -> pd.DataFrame:
    """Apply spatial assignment to a village-access style frame.

    On input ``df['district']`` / ``df['district_code']`` hold the SOURCE values (or, when the
    frame already has ``source_district`` columns from an earlier run, those are used as the
    source so the operation is idempotent). Returns a new frame whose ``district`` and
    ``district_code`` are the canonical polygon values, with ``NEW_COLUMNS`` right after
    ``district_code``.
    """
    df = df.copy()
    if "source_district" in df.columns:
        src_name, src_code = df["source_district"], df["source_district_code"]
    else:
        src_name, src_code = df["district"], df["district_code"]
    src_code = pd.to_numeric(src_code, errors="coerce").astype("Int64")
    geoms = gpd.GeoSeries(list(geoms_4326), crs=4326, index=df.index)
    asg = assign_current_district(state, geoms, source_codes=src_code, districts=districts)
    flag = is_placeholder(df["village"], df["village_code"])
    drop = [c for c in NEW_COLUMNS if c in df.columns]
    base = df.drop(columns=drop + ["district", "district_code"])
    pos = list(df.columns).index("district") if "district" in df.columns else 1
    new = pd.DataFrame({
        "district": asg["district"].to_numpy(),
        "district_code": asg["district_code"].to_numpy(),
        "source_district": src_name.to_numpy(),
        "source_district_code": src_code.to_numpy(),
        "district_assignment": asg["district_assignment"].to_numpy(),
        "district_assignment_km": asg["district_assignment_km"].to_numpy(),
        "exclude_from_district_stats": flag,
    }, index=df.index)
    cols = list(base.columns)
    pos = min(pos, len(cols))
    out = pd.concat([base[cols[:pos]], new, base[cols[pos:]]], axis=1)
    return out
