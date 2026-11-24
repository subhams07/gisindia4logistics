"""fetch_toll_plazas.py — Fetch, cluster, and attribute nationwide Toll Plazas in India.

Queries OpenStreetMap Overpass for barrier=toll_booth within India (or loads a
saved Overpass JSON cache via ``--from-cache``), clusters multi-lane booths
(single-linkage 150m in EPSG:7755), intersects with LGD districts, snaps to the
National Highway network to determine the highway number, assigns deterministic
``toll_id`` codes (``TPL-0001``..), representative ``osm_node_id`` values, and
per-row OpenStreetMap ``source_url`` links.

Outputs:
  data/roads/toll_plazas.csv (~1,585 clustered Toll Plazas)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from scripts.clean.standardize import DATA_DIR, http_session

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_QUERY = """[out:json][timeout:120];
(
  node["barrier"="toll_booth"](6.5,68.0,37.5,97.5);
  way["barrier"="toll_booth"](6.5,68.0,37.5,97.5);
);
out center;
"""

OUTPUT_COLUMNS = [
    "toll_id",
    "osm_node_id",
    "name",
    "toll_type",
    "nh_number",
    "state",
    "district",
    "district_code",
    "latitude",
    "longitude",
    "booth_count",
    "source_url",
]


def fetch_raw_toll_booths() -> list[dict]:
    print("Fetching barrier=toll_booth from OSM Overpass ...")
    session = http_session()
    resp = session.post(OVERPASS_URL, data={"data": OVERPASS_QUERY}, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    elements = data.get("elements", [])
    print(f"  Retrieved {len(elements)} raw toll booth elements from Overpass")
    return elements


def cluster_labels(coords: np.ndarray, radius_m: float) -> np.ndarray:
    """Single-linkage clusters: points chained by gaps <= radius_m share a label
    (same partition as DBSCAN with eps=radius_m, min_samples=1)."""
    n = len(coords)
    pairs = cKDTree(coords).query_pairs(r=radius_m, output_type="ndarray")
    adj = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    return connected_components(adj, directed=False)[1]


def _select_representative_element(grp: pd.DataFrame) -> pd.Series:
    """Select one deterministic OSM representative element per cluster:
    prefer ``node`` over ``way``, closest to projected EPSG:7755 cluster centroid,
    tie-breaking on lowest ``osm_id``."""
    cx = float(grp["proj_x"].mean())
    cy = float(grp["proj_y"].mean())
    ranked = grp.assign(
        _type_rank=(grp["osm_type"] != "node").astype(int),
        _dist2=(grp["proj_x"] - cx) ** 2 + (grp["proj_y"] - cy) ** 2,
    ).sort_values(["_type_rank", "_dist2", "osm_id"], kind="stable")
    return ranked.iloc[0]


def _finalize_plazas_frame(plazas_df: pd.DataFrame) -> pd.DataFrame:
    """Apply deterministic ordering, ``toll_id`` assignment, dtypes, and canonical column order."""
    df = plazas_df.copy()
    df["osm_node_id"] = pd.to_numeric(df["osm_node_id"], errors="raise").astype("int64")
    df["booth_count"] = pd.to_numeric(df["booth_count"], errors="raise").astype("int64")
    df["district_code"] = pd.to_numeric(df["district_code"], errors="coerce").round().astype("Int64")
    df["latitude"] = pd.to_numeric(df["latitude"], errors="raise").round(6)
    df["longitude"] = pd.to_numeric(df["longitude"], errors="raise").round(6)

    df = df.sort_values(
        ["state", "district", "name", "latitude", "longitude", "osm_node_id"],
        kind="stable",
    ).reset_index(drop=True)
    df["toll_id"] = [f"TPL-{idx:04d}" for idx in range(1, len(df) + 1)]
    return df[OUTPUT_COLUMNS]


def build_plazas_from_elements(raw_elements: list[dict]) -> pd.DataFrame:
    """Cluster raw Overpass toll-booth elements, join LGD districts, and snap to NH routes."""
    pts = []
    for e in raw_elements:
        lat = e.get("lat")
        if lat is None:
            lat = e.get("center", {}).get("lat")
        lon = e.get("lon")
        if lon is None:
            lon = e.get("center", {}).get("lon")
        osm_id = e.get("id")
        if lat is None or lon is None or osm_id is None:
            continue
        osm_type = str(e.get("type") or "node").lower()
        if osm_type not in {"node", "way"}:
            osm_type = "node"
        tags = e.get("tags", {})
        name = tags.get("name") or tags.get("name:en") or tags.get("description")
        ref = tags.get("ref")
        pts.append({
            "osm_type": osm_type,
            "osm_id": int(osm_id),
            "raw_name": name,
            "ref": ref,
            "latitude": float(lat),
            "longitude": float(lon),
        })

    if not pts:
        raise ValueError("No toll booth elements with valid coordinates and OSM IDs.")

    gdf = gpd.GeoDataFrame(
        pts,
        geometry=gpd.points_from_xy([p["longitude"] for p in pts], [p["latitude"] for p in pts]),
        crs=4326,
    )

    # 1. Spatially clip to India boundary & attach District / State attributes
    districts = gpd.read_file(DATA_DIR / "administrative" / "india_districts_lgd.geojson")
    print("Spatially joining toll booths to LGD districts ...")
    gdf_in = gpd.sjoin(
        gdf,
        districts[["state", "district", "district_code", "geometry"]],
        how="inner",
        predicate="within",
    ).copy()
    print(f"  {len(gdf_in)} toll booths strictly within India LGD districts")

    # 2. Cluster multi-lane toll booths within 150 meters in projected CRS (EPSG:7755)
    gdf_proj = gdf_in.to_crs(7755)
    proj_x = gdf_proj.geometry.x.to_numpy(dtype=float)
    proj_y = gdf_proj.geometry.y.to_numpy(dtype=float)
    coords = np.column_stack([proj_x, proj_y])
    gdf_in["proj_x"] = proj_x
    gdf_in["proj_y"] = proj_y
    gdf_in["cluster"] = cluster_labels(coords, 150.0)

    # 3. Aggregate clusters into single discrete Toll Plazas
    plazas = []
    for _, grp in gdf_in.groupby("cluster"):
        named_rows = grp[grp["raw_name"].notna()]
        name = named_rows["raw_name"].iloc[0] if len(named_rows) else None

        ref_rows = grp[grp["ref"].notna()]
        ref = ref_rows["ref"].iloc[0] if len(ref_rows) else None

        rep_lat = float(grp["latitude"].mean())
        rep_lon = float(grp["longitude"].mean())
        rep = _select_representative_element(grp)
        rep_osm_id = int(rep["osm_id"])
        rep_osm_type = str(rep["osm_type"])
        state = rep["state"]
        district = rep["district"]
        district_code = rep["district_code"]

        if not name:
            name = f"{district} Toll Plaza" if district else "Toll Plaza"

        plazas.append({
            "osm_node_id": rep_osm_id,
            "name": name,
            "toll_type": "national_highway" if ref and "NH" in str(ref).upper() else "highway_toll",
            "nh_number": ref,
            "state": state,
            "district": district,
            "district_code": district_code,
            "latitude": round(rep_lat, 6),
            "longitude": round(rep_lon, 6),
            "booth_count": len(grp),
            "source_url": f"https://www.openstreetmap.org/{rep_osm_type}/{rep_osm_id}",
        })

    plazas_df = pd.DataFrame(plazas)

    # 4. Snap to National Highway network to enrich missing nh_number
    nh_path = DATA_DIR / "roads" / "india_nh_network.geojson"
    if nh_path.exists():
        print("Snapping Toll Plazas to National Highway network to determine NH numbers ...")
        nh = gpd.read_file(nh_path).to_crs(7755)
        p_gdf = gpd.GeoDataFrame(
            plazas_df,
            geometry=gpd.points_from_xy(plazas_df["longitude"], plazas_df["latitude"]),
            crs=4326,
        ).to_crs(7755)

        joined_nh = gpd.sjoin_nearest(
            p_gdf,
            nh[["nh", "name", "highway", "geometry"]],
            how="left",
            distance_col="nh_dist_m",
        )
        joined_nh = joined_nh[~joined_nh.index.duplicated(keep="first")]

        for idx, row in joined_nh.iterrows():
            if pd.isna(plazas_df.at[idx, "nh_number"]) and pd.notna(row["nh"]):
                plazas_df.at[idx, "nh_number"] = f"NH {row['nh']}"
            if row["nh_dist_m"] < 1000 and row["highway"] == "motorway":
                plazas_df.at[idx, "toll_type"] = "expressway_toll"
            elif row["nh_dist_m"] < 1000:
                plazas_df.at[idx, "toll_type"] = "national_highway"

    return _finalize_plazas_frame(plazas_df)


def load_plazas_from_cache(cache_path: Path) -> pd.DataFrame:
    """Rebuild toll plazas deterministically from a saved Overpass cache JSON.

    Supports both raw Overpass snapshots (``{"elements": [...]}`` or a JSON list
    of elements) and pre-clustered verified cache snapshots (``{"records": [...]}``).
    """
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        print(f"Loading {len(payload['records'])} verified clustered records from cache {cache_path} ...")
        df = pd.DataFrame(payload["records"])
        required = set(OUTPUT_COLUMNS) - {"toll_id"}
        missing = sorted(required - set(df.columns))
        if missing:
            raise ValueError(f"Cache records missing required columns: {missing}")
        return _finalize_plazas_frame(df)

    if isinstance(payload, dict) and isinstance(payload.get("elements"), list):
        elements = payload["elements"]
        print(f"Loaded {len(elements)} raw Overpass elements from cache {cache_path}")
        return build_plazas_from_elements(elements)

    if isinstance(payload, list):
        print(f"Loaded {len(payload)} raw Overpass elements from cache {cache_path}")
        return build_plazas_from_elements(payload)

    raise ValueError(
        f"Unsupported cache format in {cache_path}: expected JSON with 'elements' or 'records' list."
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--from-cache",
        type=Path,
        default=None,
        help="Path to a saved Overpass JSON cache (raw 'elements' or verified 'records') to rebuild offline.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DATA_DIR / "roads" / "toll_plazas.csv",
        help="Destination CSV path (default: data/roads/toll_plazas.csv).",
    )
    args = parser.parse_args(argv)

    if args.from_cache is not None:
        if not args.from_cache.exists():
            print(f"ERROR: Cache file not found: {args.from_cache}")
            sys.exit(1)
        plazas_df = load_plazas_from_cache(args.from_cache)
    else:
        raw_elements = fetch_raw_toll_booths()
        if not raw_elements:
            print("ERROR: No toll booth elements returned.")
            sys.exit(1)
        plazas_df = build_plazas_from_elements(raw_elements)

    out_csv = args.output
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    plazas_df.to_csv(out_csv, index=False, lineterminator="\n")
    print(f"\nWrote {len(plazas_df)} clustered Toll Plazas -> {out_csv}")
    print("Toll types distribution:")
    print(plazas_df["toll_type"].value_counts())
    print("Top states by toll plaza count:")
    print(plazas_df["state"].value_counts().head(10))


if __name__ == "__main__":
    main()
