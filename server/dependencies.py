"""
server/dependencies.py
In-memory data store and spatial indexing for ultra-fast API response times (< 15ms).
"""

import time
from typing import Dict, Optional
import pandas as pd
import geopandas as gpd
import numpy as np
from scipy.spatial import KDTree
from scipy.sparse import csr_matrix

from server.config import settings

PROJ = 7755  # India NSF LCC (metres)

class DataStore:
    _instance = None

    def __init__(self):
        self.data_dir = settings.DATA_PATH
        self.districts_df: Optional[pd.DataFrame] = None
        self.states_df: Optional[pd.DataFrame] = None
        self.district_access_df: Optional[pd.DataFrame] = None
        self.travel_time_df: Optional[pd.DataFrame] = None
        self.port_matrix_df: Optional[pd.DataFrame] = None
        
        # Hubs cache
        self.hubs_dict: Dict[str, pd.DataFrame] = {}
        self.rail_stations_df: Optional[pd.DataFrame] = None
        self.dfc_stations_df: Optional[pd.DataFrame] = None
        self.toll_plazas_df: Optional[pd.DataFrame] = None
        self.toll_tree: Optional[KDTree] = None
        
        # Highway graph for routing
        self.nh_graph: Optional[csr_matrix] = None
        self.nh_distance_graph: Optional[csr_matrix] = None
        self.nh_tree: Optional[KDTree] = None
        self.nh_node_xy: Optional[np.ndarray] = None
        self.nh_node_list: Optional[list] = None

    @classmethod
    def get_instance(cls) -> "DataStore":
        if cls._instance is None:
            cls._instance = DataStore()
            cls._instance.initialize_store()
        return cls._instance

    def initialize_store(self):
        t0 = time.time()
        print("Initializing GIS4Logistics in-memory store...")

        # 1. Load Districts with Population Estimates
        p_pop = self.data_dir / "demographic" / "district_population_estimates.csv"
        if p_pop.exists():
            self.districts_df = pd.read_csv(p_pop)
        else:
            p_dist = self.data_dir / "administrative" / "india_districts_lgd.geojson"
            if p_dist.exists():
                self.districts_df = gpd.read_file(p_dist)[["state", "district", "district_code", "state_code"]]

        # 2. Load Accessibility and Travel Time Rollups
        p_access = self.data_dir / "analysis" / "india_district_access_summary.csv"
        if p_access.exists():
            self.district_access_df = pd.read_csv(p_access)

        p_travel = self.data_dir / "analysis" / "nh_district_travel_time_summary.csv"
        if p_travel.exists():
            self.travel_time_df = pd.read_csv(p_travel)

        p_matrix = self.data_dir / "analysis" / "nh_district_port_matrix.csv"
        if p_matrix.exists():
            self.port_matrix_df = pd.read_csv(p_matrix)

        # 3. Load Hubs
        hub_files = {
            "ports": "ports.csv",
            "icds": "icds.csv",
            "mmlps": "mmlps.csv",
            "air_cargo": "air_cargo.csv",
            "icps": "icps.csv",
            "iw_terminals": "inland_waterway_terminals.csv",
            "fci_depots": "fci_depots.csv",
            "industrial_nodes": "industrial_nodes.csv",
            "pm_mitra_parks": "pm_mitra_parks.csv",
            "cold_chain": "cold_chain_storages.csv",
            "enam_mandis": "enam_mandis.csv"
        }
        for k, fname in hub_files.items():
            p = self.data_dir / "logistics_hubs" / fname
            if p.exists():
                self.hubs_dict[k] = pd.read_csv(p)

        # 4. Load Rail & Tolls
        p_rail = self.data_dir / "rail" / "railway_stations.csv"
        if p_rail.exists():
            self.rail_stations_df = pd.read_csv(p_rail)

        p_dfc = self.data_dir / "rail" / "dfc_stations.csv"
        if p_dfc.exists():
            self.dfc_stations_df = pd.read_csv(p_dfc)

        p_tolls = self.data_dir / "roads" / "toll_plazas.csv"
        if p_tolls.exists():
            self.toll_plazas_df = pd.read_csv(p_tolls)
            coords = self.toll_plazas_df[["longitude", "latitude"]].values
            # Build projected KDTree for toll queries
            self.toll_tree = KDTree(coords)

        # 5. Build States Aggregate Summary
        if self.districts_df is not None:
            agg_dict = {
                "district_count": ("district", "count"),
                "pop_2011_total": ("pop_2011", "sum")
            }
            if "state_code" in self.districts_df.columns:
                agg_dict["state_code"] = ("state_code", "first")
            states_grp = self.districts_df.groupby("state").agg(**agg_dict).reset_index()
            if "state_code" not in states_grp.columns:
                states_grp["state_code"] = None

            # Join village counts from district access rollup if present
            if self.district_access_df is not None and "villages" in self.district_access_df.columns:
                st_rows = self.district_access_df[self.district_access_df.district == "__STATE__"]
                st_vills = st_rows.set_index("state")["villages"].to_dict()
                states_grp["village_count"] = states_grp["state"].map(st_vills).fillna(0).astype(int)
            else:
                states_grp["village_count"] = 0
            self.states_df = states_grp

        # 6. Build or Lazy Load Highway Graph
        self._build_highway_graph()
        print(f"DataStore initialized in {time.time()-t0:.2f}s")

    def _build_highway_graph(self):
        from scripts.clean.graph_builder import load_or_build_cached_graph

        nh_pq = self.data_dir / "roads" / "india_nh_network.parquet"
        nh_geojson = self.data_dir / "roads" / "india_nh_network.geojson"

        if nh_pq.exists():
            gdf_nh = gpd.read_parquet(nh_pq).to_crs(PROJ)
        elif nh_geojson.exists():
            gdf_nh = gpd.read_file(nh_geojson).to_crs(PROJ)
        else:
            return

        cache_dir = settings.CACHE_PATH
        self.nh_graph, self.nh_distance_graph, self.nh_node_xy, self.nh_comp_labels, self.nh_tree = load_or_build_cached_graph(
            gdf_nh, cache_dir=cache_dir
        )
        self.nh_node_list = [tuple(pt) for pt in self.nh_node_xy]
        self.main_comp_label = int(np.argmax(np.bincount(self.nh_comp_labels)))


def get_data_store() -> DataStore:
    return DataStore.get_instance()
