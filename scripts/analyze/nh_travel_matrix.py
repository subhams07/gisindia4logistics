"""National Highway network travel-time and catchment matrix.

Builds a topological spatial graph from the committed National Highway network
(data/roads/india_nh_network.geojson) in EPSG:7755 (India NSF LCC). Computes
network shortest-path drive times (hours/minutes) and road distances (km)
from all 781 current LGD district centers to key logistics hubs:
1. Major / Commercial Ports (22 ports)
2. Inland Container Depots (44 ICDs/CFSs)
3. Multimodal Logistics Parks (20 MMLPs)
4. Air Cargo Terminals (25 airports)
5. Integrated Check Posts (19 border ICPs)

Outputs:
- data/analysis/nh_district_travel_time_summary.csv (781 districts x nearest hubs)
- data/analysis/nh_district_port_matrix.csv (781 districts x major ports drive-time matrix)

Usage:
    python scripts/analyze/nh_travel_matrix.py
"""
from __future__ import annotations

import pathlib
import sys
import time
import geopandas as gpd
import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.spatial import cKDTree
from scipy.sparse.csgraph import dijkstra, connected_components

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR

PROJ = 7755
SPEEDS = {"motorway": 90.0, "trunk": 70.0, "primary": 55.0}
DEFAULT_SPEED = 50.0
ACCESS_SPEED = 35.0  # km/h on connecting/local roads to highway


def build_highway_graph(nh_gdf: gpd.GeoDataFrame) -> tuple[sp.csr_matrix, sp.csr_matrix, np.ndarray, np.ndarray]:
    """Builds time-weighted and distance-weighted sparse CSR graphs."""
    print("Extracting highway vertices and road segments...")
    
    # Snap vertices to 15m grid to ensure topological junction consistency
    def snap_pt(x: float, y: float) -> tuple[float, float]:
        return (round(x / 15.0) * 15.0, round(y / 15.0) * 15.0)

    node_map: dict[tuple[float, float], int] = {}
    node_coords: list[tuple[float, float]] = []

    def get_node(x: float, y: float) -> int:
        k = snap_pt(x, y)
        if k not in node_map:
            nid = len(node_coords)
            node_map[k] = nid
            node_coords.append(k)
            return nid
        return node_map[k]

    edges_dict: dict[tuple[int, int], tuple[float, float]] = {}
    linestring_endpoints: set[int] = set()

    for _, row in nh_gdf.iterrows():
        geom = row.geometry
        if geom.is_empty:
            continue
        spd = SPEEDS.get(row.get("highway"), DEFAULT_SPEED)
        coords = list(geom.coords)
        if len(coords) < 2:
            continue

        u_first = get_node(coords[0][0], coords[0][1])
        u_last = get_node(coords[-1][0], coords[-1][1])
        linestring_endpoints.add(u_first)
        linestring_endpoints.add(u_last)

        for i in range(len(coords) - 1):
            u = get_node(coords[i][0], coords[i][1])
            v = get_node(coords[i + 1][0], coords[i + 1][1])
            if u != v:
                pair = (min(u, v), max(u, v))
                d_km = np.hypot(coords[i + 1][0] - coords[i][0], coords[i + 1][1] - coords[i][1]) / 1000.0
                t_min = (d_km / spd) * 60.0
                if pair not in edges_dict or t_min < edges_dict[pair][0]:
                    edges_dict[pair] = (t_min, d_km)

    print(f"Base highway graph: {len(node_coords)} nodes, {len(edges_dict)} edges")

    # Bridge junction gaps: connect endpoints within 350m
    ep_list = sorted(linestring_endpoints)
    ep_coords = np.array([node_coords[i] for i in ep_list])
    ep_tree = cKDTree(ep_coords)
    ep_pairs = ep_tree.query_pairs(r=350.0)
    print(f"Connecting {len(ep_pairs)} junction gap bridges (<= 350m)...")

    for i, j in ep_pairs:
        u = ep_list[i]
        v = ep_list[j]
        if u != v:
            pair = (min(u, v), max(u, v))
            if pair not in edges_dict:
                d_km = np.hypot(node_coords[u][0] - node_coords[v][0], node_coords[u][1] - node_coords[v][1]) / 1000.0
                t_min = (d_km / ACCESS_SPEED) * 60.0
                edges_dict[pair] = (t_min, d_km)

    n_nodes = len(node_coords)
    u_idx = [e[0] for e in edges_dict] + [e[1] for e in edges_dict]
    v_idx = [e[1] for e in edges_dict] + [e[0] for e in edges_dict]
    w_time = [v[0] for v in edges_dict.values()] + [v[0] for v in edges_dict.values()]
    w_dist = [v[1] for v in edges_dict.values()] + [v[1] for v in edges_dict.values()]

    graph_time = sp.csr_matrix((w_time, (u_idx, v_idx)), shape=(n_nodes, n_nodes))
    graph_dist = sp.csr_matrix((w_dist, (u_idx, v_idx)), shape=(n_nodes, n_nodes))
    coords_arr = np.array(node_coords)

    n_comp, labels = connected_components(graph_time, directed=False)
    comp_sizes = np.bincount(labels)
    largest_label = comp_sizes.argmax()
    print(f"Graph topology: {n_comp} components, mainland network contains {comp_sizes.max()}/{n_nodes} nodes ({comp_sizes.max()/n_nodes*100:.1f}%)")

    return graph_time, graph_dist, coords_arr, labels


def main() -> None:
    t0 = time.time()
    print("=== GIS4Logistics National Highway Travel-Time Engine (Initiative 3a) ===")
    
    # 1. Load NH Network
    nh_path = DATA_DIR / "roads" / "india_nh_network.geojson"
    print(f"Loading National Highway network from {nh_path} ...")
    nh = gpd.read_file(nh_path).to_crs(PROJ)

    graph_time, graph_dist, node_coords, labels = build_highway_graph(nh)
    comp_sizes = np.bincount(labels)
    largest_label = comp_sizes.argmax()

    # Snap to the continuous mainland network
    main_node_indices = np.where(labels == largest_label)[0]
    main_coords = node_coords[main_node_indices]
    main_kdtree = cKDTree(main_coords)

    # 2. Load Districts
    dist_path = DATA_DIR / "administrative" / "india_districts_lgd.geojson"
    districts = gpd.read_file(dist_path).to_crs(PROJ)
    d_pts = np.array([[p.x, p.y] for p in districts.geometry.representative_point()])
    d_dists_m, d_local_ids = main_kdtree.query(d_pts)
    d_node_ids = main_node_indices[d_local_ids]

    # Population lookup
    pop_map = {}
    pop_path = DATA_DIR / "demographic" / "district_population_estimates.csv"
    if pop_path.exists():
        p_df = pd.read_csv(pop_path)
        for _, r in p_df.iterrows():
            pop_map[(r["state"].strip().lower(), str(r["district"]).strip().lower())] = r["pop_2011"]

    # 3. Load Hubs & Railway Stations
    hub_files = {
        "rail_station": (DATA_DIR / "rail" / "railway_stations.csv", "station_name"),
        "freight_terminal": (DATA_DIR / "rail" / "freight_terminals.csv", "terminal_name"),
        "port": (DATA_DIR / "logistics_hubs" / "ports.csv", "name"),
        "icd": (DATA_DIR / "logistics_hubs" / "icds.csv", "name"),
        "mmlp": (DATA_DIR / "logistics_hubs" / "mmlps.csv", "name"),
        "air_cargo": (DATA_DIR / "logistics_hubs" / "air_cargo.csv", "name"),
        "icp": (DATA_DIR / "logistics_hubs" / "icps.csv", "name"),
    }

    hubs_data = {}
    for k, (p, name_col) in hub_files.items():
        df = pd.read_csv(p)
        df = df[df.latitude.notna() & df.longitude.notna()].copy()
        if name_col != "name" and name_col in df.columns:
            df["name"] = df[name_col]
        gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.longitude, df.latitude), crs=4326).to_crs(PROJ)
        pts = np.array([[pt.x, pt.y] for pt in gdf.geometry])
        dists_m, local_ids = main_kdtree.query(pts)
        gdf["node_id"] = main_node_indices[local_ids]
        gdf["snap_dist_km"] = dists_m / 1000.0
        hubs_data[k] = gdf

    # 4. Compute Shortest Path Matrix from all 781 Districts
    print(f"\nComputing network shortest paths for {len(districts)} districts across all hubs...")
    t_calc = time.time()
    
    # Compute travel times for all district nodes
    dist_time_matrix = dijkstra(csgraph=graph_time, directed=False, indices=d_node_ids)
    dist_dist_matrix = dijkstra(csgraph=graph_dist, directed=False, indices=d_node_ids)
    print(f"Full network Dijkstra calculation completed in {time.time()-t_calc:.2f}s")

    # 5. Build District Catchment Summary
    summary_rows = []
    for i, d_row in districts.iterrows():
        st = d_row["state"]
        dt = d_row["district"]
        code = d_row.get("district_code")
        d_access_km = d_dists_m[i] / 1000.0
        d_access_time_min = (d_access_km / ACCESS_SPEED) * 60.0
        
        # Island detection (Andaman & Nicobar, Lakshadweep: > 150 km from mainland network)
        is_island = d_access_km > 150.0
        
        pop = pop_map.get((st.strip().lower(), dt.strip().lower()))
        
        row = {
            "state": st,
            "district": dt,
            "district_code": code,
            "pop_2011": pop,
            "is_island": is_island,
            "highway_access_dist_km": round(d_access_km, 1),
            "highway_access_time_min": round(d_access_time_min, 1),
        }

        d_pt = d_pts[i]

        for kind, h_gdf in hubs_data.items():
            if is_island:
                row[f"nearest_{kind}_name"] = None
                row[f"{kind}_straight_km"] = None
                row[f"{kind}_road_distance_km"] = None
                row[f"{kind}_drive_time_hours"] = None
                row[f"{kind}_drive_time_min"] = None
                continue

            h_nodes = h_gdf["node_id"].values
            h_snap_km = h_gdf["snap_dist_km"].values
            h_names = h_gdf["name"].values
            
            # Net travel times + access times
            raw_times = dist_time_matrix[i, h_nodes]
            raw_dists = dist_dist_matrix[i, h_nodes]
            
            tot_times_min = raw_times + d_access_time_min + (h_snap_km / ACCESS_SPEED) * 60.0
            tot_dists_km = raw_dists + d_access_km + h_snap_km
            
            best_idx = np.argmin(tot_times_min)
            best_time_min = tot_times_min[best_idx]
            best_dist_km = tot_dists_km[best_idx]
            best_geom = h_gdf.geometry.iloc[best_idx]
            straight_km = np.hypot(best_geom.x - d_pt[0], best_geom.y - d_pt[1]) / 1000.0
            
            if np.isinf(best_time_min):
                row[f"nearest_{kind}_name"] = None
                row[f"{kind}_straight_km"] = None
                row[f"{kind}_road_distance_km"] = None
                row[f"{kind}_drive_time_hours"] = None
                row[f"{kind}_drive_time_min"] = None
            else:
                row[f"nearest_{kind}_name"] = h_names[best_idx]
                row[f"{kind}_straight_km"] = round(straight_km, 1)
                row[f"{kind}_road_distance_km"] = round(best_dist_km, 1)
                row[f"{kind}_drive_time_hours"] = round(best_time_min / 60.0, 2)
                row[f"{kind}_drive_time_min"] = round(best_time_min, 0)

        summary_rows.append(row)

    summary_df = pd.DataFrame(summary_rows)
    out_summary = DATA_DIR / "analysis" / "nh_district_travel_time_summary.csv"
    summary_df.to_csv(out_summary, index=False)
    print(f"Wrote {len(summary_df)} district travel time summaries -> {out_summary}")

    # 6. Build Major Ports Matrix
    ports_gdf = hubs_data["port"]
    major_ports = ports_gdf[ports_gdf.hub_type == "major_port"].reset_index(drop=True)
    matrix_rows = []
    
    for i, d_row in districts.iterrows():
        p_row = {"state": d_row["state"], "district": d_row["district"], "district_code": d_row.get("district_code")}
        d_access_time = (d_dists_m[i] / 1000.0 / ACCESS_SPEED) * 60.0
        
        for _, port in major_ports.iterrows():
            p_node = port["node_id"]
            p_access_time = (port["snap_dist_km"] / ACCESS_SPEED) * 60.0
            t_net = dist_time_matrix[i, p_node]
            t_tot = t_net + d_access_time + p_access_time
            port_key = port["name"].replace("Port of ", "").replace(" Port", "").replace(" ", "_").lower()
            p_row[f"drive_hours_to_{port_key}"] = round(t_tot / 60.0, 2) if not np.isinf(t_tot) else None
        
        matrix_rows.append(p_row)

    matrix_df = pd.DataFrame(matrix_rows)
    out_matrix = DATA_DIR / "analysis" / "nh_district_port_matrix.csv"
    matrix_df.to_csv(out_matrix, index=False)
    print(f"Wrote {len(matrix_df)} district-to-major-ports matrix -> {out_matrix}")

    # Validation and Benchmark Metrics
    print("\n=== National Distance & Drive-Time Benchmarks (777 Mainland Districts) ===")
    valid = summary_df[~summary_df.is_island & summary_df.port_drive_time_hours.notna()]
    print(f"Mainland Districts analyzed: {len(valid)}/{len(summary_df)}")
    print(f"1. Nearest Railway Station:        Straight: {valid.rail_station_straight_km.median():5.1f} km | Road: {valid.rail_station_road_distance_km.median():5.1f} km | Drive Time: {valid.rail_station_drive_time_hours.median():.2f} hrs ({valid.rail_station_drive_time_min.median():.0f} min)")
    print(f"2. Nearest Freight Terminal (GCT): Straight: {valid.freight_terminal_straight_km.median():5.1f} km | Road: {valid.freight_terminal_road_distance_km.median():5.1f} km | Drive Time: {valid.freight_terminal_drive_time_hours.median():.2f} hrs ({valid.freight_terminal_drive_time_min.median():.0f} min)")
    print(f"3. Nearest ICD / CFS:              Straight: {valid.icd_straight_km.median():5.1f} km | Road: {valid.icd_road_distance_km.median():5.1f} km | Drive Time: {valid.icd_drive_time_hours.median():.2f} hrs ({valid.icd_drive_time_min.median():.0f} min)")
    print(f"4. Nearest MMLP:                   Straight: {valid.mmlp_straight_km.median():5.1f} km | Road: {valid.mmlp_road_distance_km.median():5.1f} km | Drive Time: {valid.mmlp_drive_time_hours.median():.2f} hrs ({valid.mmlp_drive_time_min.median():.0f} min)")
    print(f"5. Nearest Air Cargo Airport:      Straight: {valid.air_cargo_straight_km.median():5.1f} km | Road: {valid.air_cargo_road_distance_km.median():5.1f} km | Drive Time: {valid.air_cargo_drive_time_hours.median():.2f} hrs ({valid.air_cargo_drive_time_min.median():.0f} min)")
    print(f"6. Nearest Major / Sea Port:       Straight: {valid.port_straight_km.median():5.1f} km | Road: {valid.port_road_distance_km.median():5.1f} km | Drive Time: {valid.port_drive_time_hours.median():.2f} hrs ({valid.port_drive_time_min.median():.0f} min)")
    
    print(f"\nTotal pipeline execution time: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
