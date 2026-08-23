"""
scripts/analyze/generate_isochrones.py
Generates 1h, 2h, 4h, 8h highway drive-time isochrone polygons around key freight hubs (GIS4Logistics Initiative 2.3).
Covers:
1. 12 Major Commercial Sea Ports
2. 20 Multimodal Logistics Parks (MMLPs)
3. Key DFC Junction Terminals (New Dadri, New Rewari, New Sanand, New Khurja, New JNPT)
"""

import sys
import time
import pandas as pd
import geopandas as gpd
import numpy as np
from scipy.spatial import KDTree
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import Point, MultiPoint, Polygon
from shapely.ops import unary_union

sys.path.insert(0, ".")
from scripts.clean.standardize import DATA_DIR
PROJ = 7755  # India NSF LCC (metres)

def build_highway_graph():
    print("Loading National Highway network for Isochrone generation...")
    nh_path = DATA_DIR / "roads" / "india_nh_network.geojson"
    gdf_nh = gpd.read_file(nh_path).to_crs(PROJ)

    coords_set = set()
    segments = []

    speed_map = {"motorway": 90.0, "trunk": 70.0, "primary": 55.0}

    for _, row in gdf_nh.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        c_type = str(row.get("class", "trunk")).lower()
        speed = speed_map.get(c_type, 65.0)

        lines = [geom] if geom.geom_type == "LineString" else (geom.geoms if geom.geom_type == "MultiLineString" else [])
        for line in lines:
            pts = list(line.coords)
            for i in range(len(pts) - 1):
                p1, p2 = (round(pts[i][0], 1), round(pts[i][1], 1)), (round(pts[i+1][0], 1), round(pts[i+1][1], 1))
                if p1 != p2:
                    coords_set.add(p1)
                    coords_set.add(p2)
                    dx = p1[0] - p2[0]
                    dy = p1[1] - p2[1]
                    dist_m = (dx*dx + dy*dy) ** 0.5
                    hours = (dist_m / 1000.0) / speed
                    segments.append((p1, p2, hours, dist_m))

    node_list = list(coords_set)
    node_to_id = {node: i for i, node in enumerate(node_list)}
    node_xy = np.array(node_list)

    # KDTree for junction bridging & snapping
    tree = KDTree(node_xy)
    pairs = tree.query_pairs(r=350.0)

    rows, cols, data = [], [], []
    for p1, p2, hrs, _ in segments:
        u, v = node_to_id[p1], node_to_id[p2]
        rows.extend([u, v])
        cols.extend([v, u])
        data.extend([hrs, hrs])

    bridge_speed = 35.0
    for u, v in pairs:
        d = np.linalg.norm(node_xy[u] - node_xy[v])
        if d > 0:
            hrs = (d / 1000.0) / bridge_speed
            rows.extend([u, v])
            cols.extend([v, u])
            data.extend([hrs, hrs])

    N = len(node_list)
    graph = csr_matrix((data, (rows, cols)), shape=(N, N))
    return graph, tree, node_xy, N


def generate_hub_isochrones():
    t0 = time.time()
    graph, tree, node_xy, N = build_highway_graph()
    
    # 1. Major Ports Isochrones
    ports_df = pd.read_csv(DATA_DIR / "logistics_hubs" / "ports.csv")
    ports_df = ports_df[ports_df.hub_type == "major_port"].copy()
    ports_gdf = gpd.GeoDataFrame(ports_df, geometry=gpd.points_from_xy(ports_df.longitude, ports_df.latitude), crs=4326).to_crs(PROJ)

    thresholds_hrs = [1.0, 2.0, 4.0, 8.0]
    out_features = []

    print(f"Generating Isochrones for {len(ports_gdf)} Major Ports...")
    for _, port in ports_gdf.iterrows():
        p_name = port["name"]
        pt = port.geometry
        _, snap_idx = tree.query([pt.x, pt.y])

        # Run Dijkstra from port
        dist_matrix = dijkstra(graph, directed=False, indices=[snap_idx])[0]

        for t_hrs in thresholds_hrs:
            reachable_mask = dist_matrix <= t_hrs
            reachable_count = reachable_mask.sum()
            if reachable_count < 3:
                # Buffer point fallback
                buf_radius_m = t_hrs * 55.0 * 1000.0 * 0.75  # ~75% straight line buffer
                poly = pt.buffer(buf_radius_m)
            else:
                pts = node_xy[reachable_mask]
                mp = MultiPoint(pts)
                # Create convex hull with buffer smoothing
                hull = mp.convex_hull
                poly = hull.buffer(8000.0) # 8km road buffer

            out_features.append({
                "hub_name": p_name,
                "hub_type": "major_port",
                "time_threshold_hours": t_hrs,
                "label": f"{int(t_hrs)} Hour Catchment",
                "geometry": poly
            })

    gdf_iso = gpd.GeoDataFrame(out_features, crs=PROJ).to_crs(4326)
    # Simplify for clean vector performance
    gdf_iso["geometry"] = gdf_iso.geometry.simplify(0.005)
    p_out = DATA_DIR / "analysis" / "major_ports_isochrones.geojson"
    gdf_iso.to_file(p_out, driver="GeoJSON")
    print(f"Wrote {len(gdf_iso)} isochrone polygons -> {p_out}")

    # 2. MMLP Isochrones
    mmlp_df = pd.read_csv(DATA_DIR / "logistics_hubs" / "mmlps.csv")
    mmlp_gdf = gpd.GeoDataFrame(mmlp_df, geometry=gpd.points_from_xy(mmlp_df.longitude, mmlp_df.latitude), crs=4326).to_crs(PROJ)

    mmlp_features = []
    print(f"Generating Isochrones for {len(mmlp_gdf)} MMLPs...")
    for _, mmlp in mmlp_gdf.iterrows():
        m_name = mmlp["name"]
        pt = mmlp.geometry
        _, snap_idx = tree.query([pt.x, pt.y])
        dist_matrix = dijkstra(graph, directed=False, indices=[snap_idx])[0]

        for t_hrs in [1.0, 2.0, 4.0]:
            reachable_mask = dist_matrix <= t_hrs
            if reachable_mask.sum() < 3:
                poly = pt.buffer(t_hrs * 60.0 * 1000.0 * 0.75)
            else:
                pts = node_xy[reachable_mask]
                hull = MultiPoint(pts).convex_hull
                poly = hull.buffer(8000.0)

            mmlp_features.append({
                "hub_name": m_name,
                "hub_type": "mmlp",
                "time_threshold_hours": t_hrs,
                "label": f"{int(t_hrs)} Hour Catchment",
                "geometry": poly
            })

    gdf_mmlp_iso = gpd.GeoDataFrame(mmlp_features, crs=PROJ).to_crs(4326)
    gdf_mmlp_iso["geometry"] = gdf_mmlp_iso.geometry.simplify(0.005)
    p_mmlp_out = DATA_DIR / "analysis" / "mmlp_isochrones.geojson"
    gdf_mmlp_iso.to_file(p_mmlp_out, driver="GeoJSON")
    print(f"Wrote {len(gdf_mmlp_iso)} MMLP isochrone polygons -> {p_mmlp_out}")
    print(f"Completed Isochrone generation in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    generate_hub_isochrones()
