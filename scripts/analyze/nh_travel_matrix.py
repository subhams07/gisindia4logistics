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
from scipy.spatial import cKDTree
from scipy.sparse.csgraph import dijkstra

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR
from graph_builder import load_or_build_cached_graph

PROJ = 7755
SPEEDS = {"motorway": 90.0, "trunk": 70.0, "primary": 55.0}
DEFAULT_SPEED = 50.0
ACCESS_SPEED = 35.0  # km/h on connecting/local roads to highway
ISLAND_DISTRICT_NAMES = {"nicobars", "north and middle andaman", "south andamans", "lakshadweep"}


def main() -> None:
    t0 = time.time()
    print("=== GISIndia4Logistics National Highway Travel-Time Engine ===")
    
    # 1. Load NH Network
    nh_path = DATA_DIR / "roads" / "india_nh_network.geojson"
    print(f"Loading National Highway network from {nh_path} ...")
    nh = gpd.read_file(nh_path).to_crs(PROJ)

    cache_dir = DATA_DIR / "cache"
    graph_time, graph_dist, node_coords, labels, tree = load_or_build_cached_graph(nh, cache_dir=cache_dir)
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
        "toll_plaza": (DATA_DIR / "roads" / "toll_plazas.csv", "name"),
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

    # Add access-controlled expressways target layer
    exp_gdf_raw = nh[nh.highway == "motorway"].copy()
    exp_pts = []
    for _, r in exp_gdf_raw.iterrows():
        exp_name = str(r["name"]) if pd.notna(r["name"]) else ("NE " + str(r["nh"]) if pd.notna(r["nh"]) else "Expressway")
        pt = r.geometry.interpolate(0.5, normalized=True)
        exp_pts.append({"name": exp_name, "geometry": pt})
    if exp_pts:
        exp_gdf = gpd.GeoDataFrame(exp_pts, crs=PROJ)
        pts = np.array([[pt.x, pt.y] for pt in exp_gdf.geometry])
        dists_m, local_ids = main_kdtree.query(pts)
        exp_gdf["node_id"] = main_node_indices[local_ids]
        exp_gdf["snap_dist_km"] = dists_m / 1000.0
        hubs_data["expressway"] = exp_gdf

    # 4. Compute Shortest Path Matrix from all 781 Districts
    print(f"\nComputing network shortest paths for {len(districts)} districts across all hubs...")
    t_calc = time.time()
    
    # Compute paths in bounded chunks and retain only hub-node results. Keeping
    # two full 781 x network-node matrices can require several GiB of RAM.
    target_nodes = np.array(
        sorted({int(node_id) for h_gdf in hubs_data.values() for node_id in h_gdf["node_id"].values}),
        dtype=int,
    )
    target_node_to_col = {node_id: col for col, node_id in enumerate(target_nodes)}
    chunk_size = 32
    dist_time_targets = np.empty((len(d_node_ids), len(target_nodes)), dtype=np.float64)
    dist_dist_targets = np.empty((len(d_node_ids), len(target_nodes)), dtype=np.float64)

    for start in range(0, len(d_node_ids), chunk_size):
        end = min(start + chunk_size, len(d_node_ids))
        district_chunk = d_node_ids[start:end]
        dist_time_targets[start:end, :] = dijkstra(
            csgraph=graph_time,
            directed=False,
            indices=district_chunk,
        )[:, target_nodes]
        dist_dist_targets[start:end, :] = dijkstra(
            csgraph=graph_dist,
            directed=False,
            indices=district_chunk,
        )[:, target_nodes]
        print(f"  Dijkstra districts {start + 1}-{end}/{len(d_node_ids)}")
    print(f"Chunked network Dijkstra calculation completed in {time.time()-t_calc:.2f}s")

    # 5. Build District Catchment Summary
    summary_rows = []
    for i, d_row in districts.iterrows():
        st = d_row["state"]
        dt = d_row["district"]
        code = d_row.get("district_code")
        d_access_km = d_dists_m[i] / 1000.0
        d_access_hours = d_access_km / ACCESS_SPEED
        d_access_time_min = d_access_hours * 60.0
        
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
            h_target_cols = np.array([target_node_to_col[int(node)] for node in h_nodes])
            h_snap_km = h_gdf["snap_dist_km"].values
            h_snap_hours = h_snap_km / ACCESS_SPEED
            h_names = h_gdf["name"].values
            
            # Net travel times (hours) + access times (hours)
            raw_times_hours = dist_time_targets[i, h_target_cols]
            raw_dists_km = dist_dist_targets[i, h_target_cols]
            
            tot_times_hours = raw_times_hours + d_access_hours + h_snap_hours
            tot_dists_km = raw_dists_km + d_access_km + h_snap_km
            
            best_idx = np.argmin(tot_times_hours)
            best_time_hours = tot_times_hours[best_idx]
            best_dist_km = tot_dists_km[best_idx]
            best_geom = h_gdf.geometry.iloc[best_idx]
            straight_km = np.hypot(best_geom.x - d_pt[0], best_geom.y - d_pt[1]) / 1000.0
            
            if np.isinf(best_time_hours):
                row[f"nearest_{kind}_name"] = None
                row[f"{kind}_straight_km"] = None
                row[f"{kind}_road_distance_km"] = None
                row[f"{kind}_drive_time_hours"] = None
                row[f"{kind}_drive_time_min"] = None
            else:
                row[f"nearest_{kind}_name"] = h_names[best_idx]
                row[f"{kind}_straight_km"] = round(straight_km, 1)
                row[f"{kind}_road_distance_km"] = round(best_dist_km, 1)
                row[f"{kind}_drive_time_hours"] = round(best_time_hours, 2)
                row[f"{kind}_drive_time_min"] = round(best_time_hours * 60.0, 0)

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
        d_name_lower = str(d_row["district"]).lower()
        is_island_district = bool(d_row.get("is_island", False)) or (d_name_lower in ISLAND_DISTRICT_NAMES)

        if is_island_district:
            for _, port in major_ports.iterrows():
                port_key = port["name"].replace("Port of ", "").replace(" Port", "").replace(" ", "_").lower()
                p_row[f"drive_hours_to_{port_key}"] = None
                p_row[f"road_km_to_{port_key}"] = None
            matrix_rows.append(p_row)
            continue

        d_access_km = d_dists_m[i] / 1000.0
        d_access_hours = d_access_km / ACCESS_SPEED
        
        for _, port in major_ports.iterrows():
            p_node = port["node_id"]
            p_target_col = target_node_to_col[int(p_node)]
            p_access_km = port["snap_dist_km"]
            p_access_hours = p_access_km / ACCESS_SPEED
            t_net_hours = dist_time_targets[i, p_target_col]
            d_net_km = dist_dist_targets[i, p_target_col]
            t_tot_hours = t_net_hours + d_access_hours + p_access_hours
            d_tot_km = d_net_km + d_access_km + p_access_km
            port_key = port["name"].replace("Port of ", "").replace(" Port", "").replace(" ", "_").lower()
            p_row[f"drive_hours_to_{port_key}"] = round(t_tot_hours, 2) if not np.isinf(t_tot_hours) else None
            p_row[f"road_km_to_{port_key}"] = round(d_tot_km, 1) if not np.isinf(d_tot_km) else None
        
        matrix_rows.append(p_row)

    matrix_df = pd.DataFrame(matrix_rows)
    out_matrix = DATA_DIR / "analysis" / "nh_district_port_matrix.csv"
    matrix_df.to_csv(out_matrix, index=False)
    print(f"Wrote {len(matrix_df)} district-to-major-ports matrix -> {out_matrix}")

    # Validation and Benchmark Metrics
    print("\n=== National Distance & Drive-Time Benchmarks (777 Mainland Districts) ===")
    valid = summary_df[~summary_df.is_island & summary_df.port_drive_time_hours.notna()]
    print(f"Mainland Districts analyzed: {len(valid)}/{len(summary_df)}")
    print(f"1. Nearest National Highway (Access): Straight: {valid.highway_access_dist_km.median():5.1f} km | Drive Time: {valid.highway_access_time_min.median():.0f} min")
    print(f"2. Nearest Toll Plaza (FASTag):       Straight: {valid.toll_plaza_straight_km.median():5.1f} km | Road: {valid.toll_plaza_road_distance_km.median():5.1f} km | Drive Time: {valid.toll_plaza_drive_time_hours.median():.2f} hrs ({valid.toll_plaza_drive_time_min.median():.0f} min)")
    print(f"3. Nearest Expressway (Motorway):     Straight: {valid.expressway_straight_km.median():5.1f} km | Road: {valid.expressway_road_distance_km.median():5.1f} km | Drive Time: {valid.expressway_drive_time_hours.median():.2f} hrs ({valid.expressway_drive_time_min.median():.0f} min)")
    print(f"4. Nearest Railway Station:           Straight: {valid.rail_station_straight_km.median():5.1f} km | Road: {valid.rail_station_road_distance_km.median():5.1f} km | Drive Time: {valid.rail_station_drive_time_hours.median():.2f} hrs ({valid.rail_station_drive_time_min.median():.0f} min)")
    print(f"5. Nearest Freight Terminal (GCT):    Straight: {valid.freight_terminal_straight_km.median():5.1f} km | Road: {valid.freight_terminal_road_distance_km.median():5.1f} km | Drive Time: {valid.freight_terminal_drive_time_hours.median():.2f} hrs ({valid.freight_terminal_drive_time_min.median():.0f} min)")
    print(f"6. Nearest ICD / CFS:                 Straight: {valid.icd_straight_km.median():5.1f} km | Road: {valid.icd_road_distance_km.median():5.1f} km | Drive Time: {valid.icd_drive_time_hours.median():.2f} hrs ({valid.icd_drive_time_min.median():.0f} min)")
    print(f"7. Nearest MMLP:                      Straight: {valid.mmlp_straight_km.median():5.1f} km | Road: {valid.mmlp_road_distance_km.median():5.1f} km | Drive Time: {valid.mmlp_drive_time_hours.median():.2f} hrs ({valid.mmlp_drive_time_min.median():.0f} min)")
    print(f"8. Nearest Air Cargo Airport:         Straight: {valid.air_cargo_straight_km.median():5.1f} km | Road: {valid.air_cargo_road_distance_km.median():5.1f} km | Drive Time: {valid.air_cargo_drive_time_hours.median():.2f} hrs ({valid.air_cargo_drive_time_min.median():.0f} min)")
    print(f"9. Nearest Major / Sea Port:          Straight: {valid.port_straight_km.median():5.1f} km | Road: {valid.port_road_distance_km.median():5.1f} km | Drive Time: {valid.port_drive_time_hours.median():.2f} hrs ({valid.port_drive_time_min.median():.0f} min)")
    
    print(f"\nTotal pipeline execution time: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
