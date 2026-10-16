"""
server/dependencies.py
In-memory data store and spatial indexing for ultra-fast API response times (< 15ms).
"""

import logging
import time
from threading import RLock

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.spatial import KDTree

from server.config import settings

PROJ = 7755  # India NSF LCC (metres)
LOGGER = logging.getLogger(__name__)

class DataStore:
    _instance = None

    def __init__(self):
        self._graph_lock = RLock()
        self._highway_snapshot = None
        self.data_dir = settings.DATA_PATH
        self.districts_df: pd.DataFrame | None = None
        self.states_df: pd.DataFrame | None = None
        self.district_access_df: pd.DataFrame | None = None
        self.travel_time_df: pd.DataFrame | None = None
        self.port_matrix_df: pd.DataFrame | None = None
        
        # Hubs cache
        self.hubs_dict: dict[str, pd.DataFrame] = {}
        self.rail_stations_df: pd.DataFrame | None = None
        self.dfc_stations_df: pd.DataFrame | None = None
        self.freight_terminals_df: pd.DataFrame | None = None
        self.toll_plazas_df: pd.DataFrame | None = None
        self.toll_tree: KDTree | None = None
        
        # Highway graph for routing
        self.nh_graph: csr_matrix | None = None
        self.nh_distance_graph: csr_matrix | None = None
        self.nh_bridge_mask: csr_matrix | None = None
        self.nh_tree: KDTree | None = None
        self.nh_node_xy: np.ndarray | None = None
        self.nh_node_list: list | None = None
        self.nh_comp_labels: np.ndarray | None = None

    def __setattr__(self, name, value):
        # Legacy assignments remain possible but invalidate request acquisition.
        # Publication and request capture share this lock, preventing torn reads.
        lock = self.__dict__.get('_graph_lock')
        if lock is not None and name.startswith('nh_'):
            with lock:
                object.__setattr__(self, name, value)
        else:
            object.__setattr__(self, name, value)

    def publish_highway_graph(self, graph_time, graph_dist, coords, labels, tree, bridge_mask):
        """Validate/detach a complete reload and atomically publish its generation.

        Caller must not mutate builder inputs concurrently with publication.
        Failed validation leaves the previous publication intact. Individual nh_*
        replacement is not a reload: use this method with all six graph fields.
        """
        from server.services.graph_snapshot import GRAPH_FIELDS, GraphSnapshot

        with self._graph_lock:
            generation = 1 if self._highway_snapshot is None else self._highway_snapshot.generation + 1
            snapshot = GraphSnapshot(graph_time, graph_dist, coords, labels, tree,
                                     bridge_mask, generation=generation)
            node_list = tuple(tuple(pt) for pt in snapshot.nh_node_xy)
            component_labels, counts = np.unique(snapshot.nh_comp_labels, return_counts=True)
            # Sorted unique labels retain bincount's smallest-label tie break
            # without allocating storage proportional to the largest label.
            main_label = int(component_labels[np.argmax(counts)])
            for name in GRAPH_FIELDS:
                object.__setattr__(self, name, getattr(snapshot, name))
            self.nh_node_list = node_list
            self.main_comp_label = main_label
            self._highway_snapshot = snapshot
            return snapshot

    def get_highway_snapshot(self):
        """O(1) coherence check; never revalidate a published immutable graph."""
        from server.services.exceptions import DataIntegrityError
        from server.services.graph_snapshot import GRAPH_FIELDS

        with self._graph_lock:
            snapshot = self._highway_snapshot
            if snapshot is None or any(getattr(self, name, None) is not getattr(snapshot, name)
                                       for name in GRAPH_FIELDS):
                raise DataIntegrityError('Invalid highway graph metadata: unpublished or replaced generation')
            snapshot.check_integrity()
            return snapshot

    @classmethod
    def get_instance(cls) -> "DataStore":
        if cls._instance is None:
            instance = cls()
            instance.initialize_store()
            cls._instance = instance
        return cls._instance

    def initialize_store(self):
        t0 = time.time()
        LOGGER.info("Initializing GIS4Logistics in-memory store...")

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

        p_gct = self.data_dir / "rail" / "freight_terminals.csv"
        if p_gct.exists():
            self.freight_terminals_df = pd.read_csv(p_gct)

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
        LOGGER.info("DataStore initialized in %.2fs", time.time() - t0)

    def _build_highway_graph(self):
        from scripts.clean.graph_builder import load_highway_source, load_or_build_cached_graph

        source = load_highway_source(self.data_dir)
        if source is None:
            return
        gdf_nh, _selected_path = source

        cache_dir = settings.CACHE_PATH
        graph = load_or_build_cached_graph(
            gdf_nh, cache_dir=cache_dir
        )
        self.publish_highway_graph(*graph)


def get_data_store() -> DataStore:
    return DataStore.get_instance()
