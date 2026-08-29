"""
server/services/location_resolver.py
Deterministic location resolution service for GISIndia4Logistics Decision Workbench.
Resolves CoordinateLocation, DistrictLocation, and HubLocation into canonical ResolvedLocation.
"""

import logging
from pathlib import Path
import re
from typing import Optional, Dict, Any, Tuple
import pandas as pd
import yaml

from server.dependencies import DataStore
from server.models.phase1 import (
    LocationReference,
    CoordinateLocation,
    DistrictLocation,
    HubLocation,
    ResolvedLocation,
)
from server.services.exceptions import (
    LocationNotFoundError,
    AmbiguousLocationError,
    InvalidLocationError,
)

LOGGER = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
ALIASES_PATH = REPO_ROOT / "data" / "aliases" / "location_aliases.yaml"


class LocationResolver:
    """Service for deterministic location parsing, validation, and alias matching."""
    _instance: Optional["LocationResolver"] = None

    def __init__(self, aliases_path: Path = ALIASES_PATH):
        self.aliases_path = aliases_path
        self._aliases, self._aliases_by_code = self._load_aliases()
        self._district_centroids: Dict[Tuple[str, str], Tuple[float, float]] = {}
        self._unique_code_centroids: Dict[int, Tuple[float, float]] = {}
        self._centroids_initialized = False

    @classmethod
    def get_instance(cls, aliases_path: Path = ALIASES_PATH) -> "LocationResolver":
        if cls._instance is None:
            cls._instance = cls(aliases_path=aliases_path)
        return cls._instance

    def _load_aliases(self) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        by_name: Dict[str, Dict[str, Any]] = {}
        by_code: Dict[str, Dict[str, Any]] = {}
        if self.aliases_path.exists():
            try:
                with open(self.aliases_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if isinstance(v, dict):
                                norm_k = str(k).strip().lower()
                                by_name[norm_k] = v
                                if "code" in v and v["code"]:
                                    code_k = str(v["code"]).strip().lower()
                                    by_code[code_k] = v
            except Exception as e:
                LOGGER.warning("Failed to load location aliases from %s: %s", self.aliases_path, e)
        return by_name, by_code

    def _ensure_centroids_initialized(self):
        if self._centroids_initialized:
            return
        dist_path = REPO_ROOT / "data" / "administrative" / "india_districts_lgd.geojson"
        if dist_path.exists():
            try:
                import geopandas as gpd
                gdf = gpd.read_file(dist_path)
                # Compute projected centroids in EPSG:7755 then reproject to EPSG:4326
                gdf_proj = gdf.to_crs(7755)
                reps_4326 = gdf_proj.geometry.representative_point().to_crs(4326)

                code_counts = gdf["district_code"].value_counts().to_dict()

                for idx, row in gdf.iterrows():
                    rep_pt = reps_4326.iloc[idx]
                    lat, lon = round(float(rep_pt.y), 5), round(float(rep_pt.x), 5)
                    st = str(row.get("state", "")).strip().lower()
                    dt = str(row.get("district", "")).strip().lower()
                    self._district_centroids[(st, dt)] = (lat, lon)

                    if pd.notna(row.get("district_code")):
                        try:
                            d_code = int(row["district_code"])
                            if code_counts.get(row["district_code"], 0) == 1:
                                self._unique_code_centroids[d_code] = (lat, lon)
                        except Exception:
                            pass
            except Exception as e:
                LOGGER.warning("Could not pre-calculate projected district centroids: %s", e)
        self._centroids_initialized = True

    def resolve(self, loc: LocationReference, store: DataStore) -> ResolvedLocation:
        """
        Resolves any LocationReference (coordinate, district, or hub) to a canonical ResolvedLocation.
        Raises AmbiguousLocationError, LocationNotFoundError, or InvalidLocationError.
        """
        if isinstance(loc, CoordinateLocation):
            return self._resolve_coordinate(loc, store)
        elif isinstance(loc, DistrictLocation):
            return self._resolve_district(loc, store)
        elif isinstance(loc, HubLocation):
            return self._resolve_hub(loc, store)
        else:
            raise InvalidLocationError(f"Unsupported location reference type: {type(loc)}")

    def _resolve_coordinate(self, loc: CoordinateLocation, store: DataStore) -> ResolvedLocation:
        if not (-90.0 <= loc.latitude <= 90.0 and -180.0 <= loc.longitude <= 180.0):
            raise InvalidLocationError(f"Coordinates ({loc.latitude}, {loc.longitude}) are out of valid geographic range")

        label = loc.label or f"Coord ({loc.latitude:.4f}, {loc.longitude:.4f})"
        return ResolvedLocation(
            type="coordinate",
            canonical_name=label,
            state=None,
            district=None,
            district_code=None,
            latitude=loc.latitude,
            longitude=loc.longitude,
            source_dataset="user_coordinate",
            match_method="coordinate",
        )

    def _resolve_district(self, loc: DistrictLocation, store: DataStore) -> ResolvedLocation:
        if store.districts_df is None or store.districts_df.empty:
            raise LocationNotFoundError("District database is not loaded in DataStore")

        df = store.districts_df

        # 1. Match by official integer district_code (LGD)
        if loc.district_code is not None:
            match_code = df[df.district_code == loc.district_code]
            if match_code.empty:
                raise LocationNotFoundError(
                    f"District code {loc.district_code} not found in official LGD register",
                    location_query={"district_code": loc.district_code, "state": loc.state}
                )

            if len(match_code) == 1:
                row = match_code.iloc[0]
                if loc.state:
                    if str(row["state"]).strip().lower() != loc.state.strip().lower():
                        raise LocationNotFoundError(
                            f"District code {loc.district_code} belongs to '{row['state']}', not specified state '{loc.state}'",
                            location_query={"district_code": loc.district_code, "state": loc.state}
                        )
                return self._build_district_resolved(row, match_method="exact_code")

            # Duplicate code exists across multiple states (e.g. 598 in Kerala & Maharashtra)
            if loc.state:
                st_match = match_code[match_code.state.str.lower() == loc.state.strip().lower()]
                if not st_match.empty:
                    return self._build_district_resolved(st_match.iloc[0], match_method="exact_code")
                raise LocationNotFoundError(
                    f"District code {loc.district_code} does not belong to state '{loc.state}'",
                    location_query={"district_code": loc.district_code, "state": loc.state}
                )

            # Ambiguous duplicate code without state specified
            candidates = [
                {
                    "district": r["district"],
                    "state": r["state"],
                    "district_code": int(r["district_code"])
                }
                for _, r in match_code.iterrows()
            ]
            states_list = ", ".join(f"'{c['state']}'" for c in candidates)
            raise AmbiguousLocationError(
                f"District code {loc.district_code} is shared across {len(candidates)} states ({states_list}). Please specify the state name.",
                candidates=candidates
            )

        # 2. Match by exact district name + state
        d_name = (loc.district or "").strip()
        st_name = (loc.state or "").strip()

        if not d_name:
            raise InvalidLocationError("District name or district_code must be provided for district location reference")

        if st_name:
            exact_match = df[(df.district.str.lower() == d_name.lower()) & (df.state.str.lower() == st_name.lower())]
            if not exact_match.empty:
                return self._build_district_resolved(exact_match.iloc[0], match_method="exact_name")

            # Try regex clean strip of punctuation
            clean_d = re.sub(r"[^\w\s]", "", d_name.lower())
            clean_match = df[
                (df.district.str.lower().apply(lambda x: re.sub(r"[^\w\s]", "", str(x))) == clean_d)
                & (df.state.str.lower() == st_name.lower())
            ]
            if not clean_match.empty:
                return self._build_district_resolved(clean_match.iloc[0], match_method="exact_name")

            raise LocationNotFoundError(
                f"District '{d_name}' in state '{st_name}' not found",
                location_query={"district": d_name, "state": st_name}
            )

        # 3. Match by district name only across all states
        global_matches = df[df.district.str.lower() == d_name.lower()]
        if len(global_matches) == 1:
            return self._build_district_resolved(global_matches.iloc[0], match_method="exact_name")

        if len(global_matches) > 1:
            candidates = [
                {
                    "district": r["district"],
                    "state": r["state"],
                    "district_code": int(r["district_code"]) if pd.notna(r.get("district_code")) else None
                }
                for _, r in global_matches.iterrows()
            ]
            states_list = ", ".join(f"'{c['state']}'" for c in candidates)
            raise AmbiguousLocationError(
                f"District '{d_name}' is ambiguous across {len(candidates)} states ({states_list}). Please specify the state name.",
                candidates=candidates
            )

        # Not found
        raise LocationNotFoundError(
            f"District '{d_name}' not found in official 781 LGD district register",
            location_query={"district": d_name}
        )

    def _get_district_centroid(self, d_code: Any, state: str, district: str) -> Tuple[float, float]:
        """Looks up representative point coordinates for a district."""
        self._ensure_centroids_initialized()

        # 1. Primary lookup by (state, district)
        key = (str(state).strip().lower(), str(district).strip().lower())
        if key in self._district_centroids:
            return self._district_centroids[key]

        # 2. Secondary lookup by unique district code
        if pd.notna(d_code):
            try:
                code_int = int(d_code)
                if code_int in self._unique_code_centroids:
                    return self._unique_code_centroids[code_int]
            except Exception:
                pass

        raise LocationNotFoundError(
            f"Representative coordinates for district '{district}', state '{state}' could not be resolved from boundary layer"
        )

    def _build_district_resolved(self, row: pd.Series, match_method: str) -> ResolvedLocation:
        d_name = str(row["district"]).strip()
        st_name = str(row["state"]).strip()
        d_code = int(row["district_code"]) if "district_code" in row and pd.notna(row["district_code"]) else None

        canonical_district = d_name.title() if d_name.isupper() else d_name
        canonical_state = st_name.title() if st_name.isupper() else st_name

        lat, lon = self._get_district_centroid(d_code=d_code, state=st_name, district=d_name)

        return ResolvedLocation(
            type="district",
            canonical_name=canonical_district,
            state=canonical_state,
            district=canonical_district,
            district_code=d_code,
            latitude=lat,
            longitude=lon,
            source_dataset="india_districts_lgd.geojson",
            match_method=match_method,
        )

    def _resolve_hub(self, loc: HubLocation, store: DataStore) -> ResolvedLocation:
        query_name = (loc.name or "").strip()
        query_code = (loc.code or "").strip()

        if not query_name and not query_code:
            raise InvalidLocationError("Hub name or facility code must be provided for hub location reference")

        # 1. If explicit code is provided, try exact code lookup first
        if query_code:
            hub_res = self._find_hub_by_code(code=query_code, hub_type=loc.hub_type, store=store)
            if hub_res is not None:
                return hub_res

        # 2. Check explicit alias registry
        alias_key = (query_name or query_code).lower()
        alias_entry = None
        if alias_key in self._aliases:
            alias_entry = self._aliases[alias_key]
        elif query_code and query_code.lower() in self._aliases_by_code:
            alias_entry = self._aliases_by_code[query_code.lower()]

        if alias_entry:
            alias_type = alias_entry.get("type")
            # Strict alias type enforcement
            if alias_type and alias_type != loc.hub_type:
                raise InvalidLocationError(
                    f"Alias '{query_name or query_code}' refers to category '{alias_type}', not requested category '{loc.hub_type}'"
                )

            canonical_name = alias_entry.get("canonical_name", query_name)
            hub_res = self._find_hub_in_store(
                name=canonical_name,
                code=alias_entry.get("code") or query_code,
                hub_type=loc.hub_type,
                store=store
            )
            if hub_res is not None:
                hub_res.match_method = "alias_lookup"
                return hub_res

        # 3. Search directly in DataStore by exact name
        if query_name:
            hub_res = self._find_hub_in_store(name=query_name, code="", hub_type=loc.hub_type, store=store)
            if hub_res is not None:
                return hub_res

        # 4. Not found
        raise LocationNotFoundError(
            f"Logistics hub '{query_name or query_code}' of category '{loc.hub_type}' not found",
            location_query={"hub_type": loc.hub_type, "name": query_name, "code": query_code}
        )

    def _find_hub_by_code(self, code: str, hub_type: str, store: DataStore) -> Optional[ResolvedLocation]:
        norm_code = code.strip().upper()

        # Rail station code lookup
        if hub_type == "rail_station" and store.rail_stations_df is not None:
            df = store.rail_stations_df
            match = df[df.station_code.astype(str).str.upper() == norm_code]
            if not match.empty:
                r = match.iloc[0]
                st_val = str(r["state"]).strip() if pd.notna(r.get("state")) and str(r.get("state")).strip().lower() != "nan" else None
                return ResolvedLocation(
                    type="hub",
                    canonical_name=f"{r.get('station_name', code)} ({r.get('station_code', code)})",
                    state=st_val,
                    district=None,
                    district_code=int(r["district_code"]) if "district_code" in r and pd.notna(r["district_code"]) else None,
                    latitude=float(r["latitude"]),
                    longitude=float(r["longitude"]),
                    source_dataset="railway_stations.csv",
                    match_method="exact_code",
                )

        # Freight terminal code lookup via matched_station_code
        if hub_type == "freight_terminal" and store.freight_terminals_df is not None:
            df = store.freight_terminals_df
            if "matched_station_code" in df.columns:
                match = df[df["matched_station_code"].astype(str).str.upper() == norm_code]
                if not match.empty:
                    r = match.iloc[0]
                    if pd.isna(r.get("latitude")) or pd.isna(r.get("longitude")):
                        raise LocationNotFoundError(
                            f"Freight terminal code '{code}' is catalogued but lacks geographic coordinates for routing",
                            location_query={"hub_type": hub_type, "code": code}
                        )
                    st_val = str(r["state"]).strip() if pd.notna(r.get("state")) and str(r.get("state")).strip().lower() != "nan" else None
                    name_col = "terminal_name" if "terminal_name" in df.columns else "name"
                    t_name = str(r[name_col])
                    return ResolvedLocation(
                        type="hub",
                        canonical_name=f"{t_name} ({norm_code})",
                        state=st_val,
                        district=None,
                        district_code=None,
                        latitude=float(r["latitude"]),
                        longitude=float(r["longitude"]),
                        source_dataset="freight_terminals.csv",
                        match_method="exact_code",
                    )

        # Reverse alias code lookup for ports/icds
        if code.lower() in self._aliases_by_code:
            entry = self._aliases_by_code[code.lower()]
            if entry.get("type") == hub_type:
                res = self._find_hub_in_store(name=entry.get("canonical_name", ""), code=norm_code, hub_type=hub_type, store=store)
                if res:
                    res.match_method = "exact_code"
                    return res

        return None

    def _find_hub_in_store(
        self,
        name: str,
        code: str,
        hub_type: str,
        store: DataStore
    ) -> Optional[ResolvedLocation]:
        norm_name = name.strip().lower() if name else ""
        norm_code = code.strip().upper() if code else ""

        # 1. Rail Stations
        if hub_type == "rail_station" and store.rail_stations_df is not None:
            df = store.rail_stations_df
            if norm_code:
                match = df[df.station_code.astype(str).str.upper() == norm_code]
                if not match.empty:
                    r = match.iloc[0]
                    st_val = str(r["state"]).strip() if pd.notna(r.get("state")) and str(r.get("state")).strip().lower() != "nan" else None
                    return ResolvedLocation(
                        type="hub",
                        canonical_name=f"{r.get('station_name', code)} ({r.get('station_code', code)})",
                        state=st_val,
                        district=None,
                        district_code=int(r["district_code"]) if "district_code" in r and pd.notna(r["district_code"]) else None,
                        latitude=float(r["latitude"]),
                        longitude=float(r["longitude"]),
                        source_dataset="railway_stations.csv",
                        match_method="exact_code",
                    )
            if norm_name:
                match = df[df.station_name.astype(str).str.lower() == norm_name]
                if not match.empty:
                    r = match.iloc[0]
                    st_val = str(r["state"]).strip() if pd.notna(r.get("state")) and str(r.get("state")).strip().lower() != "nan" else None
                    return ResolvedLocation(
                        type="hub",
                        canonical_name=f"{r.get('station_name', name)} ({r.get('station_code', '')})",
                        state=st_val,
                        district=None,
                        district_code=int(r["district_code"]) if "district_code" in r and pd.notna(r["district_code"]) else None,
                        latitude=float(r["latitude"]),
                        longitude=float(r["longitude"]),
                        source_dataset="railway_stations.csv",
                        match_method="exact_name",
                    )

        # 2. Freight Terminals (GCT)
        if hub_type == "freight_terminal" and store.freight_terminals_df is not None:
            df = store.freight_terminals_df
            name_col = "terminal_name" if "terminal_name" in df.columns else "name"
            if norm_name:
                match = df[df[name_col].astype(str).str.lower() == norm_name]
                if not match.empty:
                    r = match.iloc[0]
                    if pd.isna(r.get("latitude")) or pd.isna(r.get("longitude")):
                        raise LocationNotFoundError(
                            f"Freight terminal '{name}' is catalogued but lacks geographic coordinates for routing",
                            location_query={"hub_type": hub_type, "name": name}
                        )
                    st_val = str(r["state"]).strip() if pd.notna(r.get("state")) and str(r.get("state")).strip().lower() != "nan" else None
                    t_name = str(r[name_col])
                    return ResolvedLocation(
                        type="hub",
                        canonical_name=t_name,
                        state=st_val,
                        district=None,
                        district_code=None,
                        latitude=float(r["latitude"]),
                        longitude=float(r["longitude"]),
                        source_dataset="freight_terminals.csv",
                        match_method="exact_name",
                    )

        # 3. Logistics Hubs in hubs_dict
        hub_file_keys = {
            "port": ["ports"],
            "icd": ["icds"],
            "mmlp": ["mmlps"],
            "air_cargo": ["air_cargo"],
            "iw_terminal": ["iw_terminals"],
            "icp": ["icps"],
            "fci_depot": ["fci_depots"],
            "cold_chain": ["cold_chain"],
            "mandi": ["enam_mandis"]
        }
        keys = hub_file_keys.get(hub_type, ["ports", "icds", "mmlps", "air_cargo", "iw_terminals", "icps", "fci_depots"])

        for k in keys:
            if k in store.hubs_dict and store.hubs_dict[k] is not None:
                df = store.hubs_dict[k]
                if norm_name:
                    # Strict exact match
                    match = df[df.name.astype(str).str.lower() == norm_name]
                    if not match.empty:
                        r = match.iloc[0]
                        st_val = str(r["state"]).strip() if pd.notna(r.get("state")) and str(r.get("state")).strip().lower() != "nan" else None
                        return ResolvedLocation(
                            type="hub",
                            canonical_name=str(r["name"]),
                            state=st_val,
                            district=None,
                            district_code=None,
                            latitude=float(r["latitude"]),
                            longitude=float(r["longitude"]),
                            source_dataset=f"{k}.csv",
                            match_method="exact_name",
                        )

        return None


def get_location_resolver() -> LocationResolver:
    """Dependency injector for LocationResolver."""
    return LocationResolver.get_instance()
