"""
server/services/location_resolver.py
Deterministic location resolution service for GISIndia4Logistics Decision Workbench.
Resolves CoordinateLocation, DistrictLocation, and HubLocation into canonical ResolvedLocation.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
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
        self._aliases: Dict[str, Dict[str, Any]] = self._load_aliases()

    @classmethod
    def get_instance(cls, aliases_path: Path = ALIASES_PATH) -> "LocationResolver":
        if cls._instance is None:
            cls._instance = cls(aliases_path=aliases_path)
        return cls._instance

    def _load_aliases(self) -> Dict[str, Dict[str, Any]]:
        if self.aliases_path.exists():
            try:
                with open(self.aliases_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        # Normalize keys to lower-case for case-insensitive lookup
                        return {str(k).strip().lower(): v for k, v in data.items() if isinstance(v, dict)}
            except Exception as e:
                LOGGER.warning("Failed to load location aliases from %s: %s", self.aliases_path, e)
        return {}

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
        # Validate coordinates bounds
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
            if not match_code.empty:
                # If state was also specified, verify match
                if loc.state:
                    st_match = match_code[match_code.state.str.lower() == loc.state.strip().lower()]
                    if not st_match.empty:
                        row = st_match.iloc[0]
                        return self._build_district_resolved(row, match_method="exact_code")
                else:
                    row = match_code.iloc[0]
                    return self._build_district_resolved(row, match_method="exact_code")

            raise LocationNotFoundError(
                f"District code {loc.district_code} not found in official LGD register",
                location_query={"district_code": loc.district_code, "state": loc.state}
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

            # Try clean strip without special chars
            clean_match = df[(df.district.str.lower().str.replace(r"[^\w\s]", "", regex=True) == d_name.lower().replace(r"[^\w\s]", "")) & 
                             (df.state.str.lower() == st_name.lower())]
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

    def _get_district_centroid(self, d_code: Any, state: str, district: str) -> tuple[float, float]:
        """Looks up or computes the centroid coordinates for a district."""
        if not hasattr(self, "_district_centroids"):
            self._district_centroids: Dict[Any, tuple[float, float]] = {}
            dist_path = REPO_ROOT / "data" / "administrative" / "india_districts_lgd.geojson"
            if dist_path.exists():
                try:
                    import geopandas as gpd
                    gdf = gpd.read_file(dist_path)
                    for _, row in gdf.iterrows():
                        centroid = row.geometry.centroid
                        lat, lon = round(float(centroid.y), 5), round(float(centroid.x), 5)
                        if pd.notna(row.get("district_code")):
                            self._district_centroids[int(row["district_code"])] = (lat, lon)
                        st = str(row.get("state", "")).strip().lower()
                        dt = str(row.get("district", "")).strip().lower()
                        self._district_centroids[(st, dt)] = (lat, lon)
                except Exception as e:
                    LOGGER.warning("Could not pre-calculate district centroids: %s", e)

        # 1. By code
        if pd.notna(d_code):
            try:
                code_int = int(d_code)
                if code_int in self._district_centroids:
                    return self._district_centroids[code_int]
            except Exception:
                pass

        # 2. By state + district
        key = (str(state).strip().lower(), str(district).strip().lower())
        if key in self._district_centroids:
            return self._district_centroids[key]

        return 20.5937, 78.9629

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
            hub_res = self._find_hub_in_store(name="", code=query_code, hub_type=loc.hub_type, store=store)
            if hub_res is not None:
                return hub_res

        # 2. Check explicit alias registry
        alias_key = (query_name or query_code).lower()
        if alias_key in self._aliases:
            alias_entry = self._aliases[alias_key]
            alias_type = alias_entry.get("type")
            canonical_name = alias_entry.get("canonical_name", query_name)
            hub_res = self._find_hub_in_store(
                name=canonical_name,
                code=alias_entry.get("code") or query_code,
                hub_type=alias_type or loc.hub_type,
                store=store
            )
            if hub_res is not None:
                hub_res.match_method = "alias_lookup"
                return hub_res

        # 3. Search directly in DataStore by name
        if query_name:
            hub_res = self._find_hub_in_store(name=query_name, code="", hub_type=loc.hub_type, store=store)
            if hub_res is not None:
                return hub_res

        # 4. Not found
        raise LocationNotFoundError(
            f"Logistics hub '{query_name or query_code}' of category '{loc.hub_type}' not found",
            location_query={"hub_type": loc.hub_type, "name": query_name, "code": query_code}
        )

    def _find_hub_in_store(
        self,
        name: str,
        code: str,
        hub_type: str,
        store: DataStore
    ) -> Optional[ResolvedLocation]:
        # Handle rail_station separately
        if hub_type == "rail_station" and store.rail_stations_df is not None:
            df = store.rail_stations_df
            if code:
                match = df[df.station_code.astype(str).str.upper() == code.upper()]
                if not match.empty:
                    r = match.iloc[0]
                    st_val = str(r["state"]).strip() if pd.notna(r.get("state")) and str(r.get("state")).strip().lower() != "nan" else None
                    dt_val = str(r["district"]).strip() if pd.notna(r.get("district")) and str(r.get("district")).strip().lower() != "nan" else None
                    return ResolvedLocation(
                        type="hub",
                        canonical_name=f"{r.get('station_name', code)} ({r.get('station_code', code)})",
                        state=st_val,
                        district=dt_val,
                        district_code=int(r["district_code"]) if "district_code" in r and pd.notna(r["district_code"]) else None,
                        latitude=float(r["latitude"]),
                        longitude=float(r["longitude"]),
                        source_dataset="railway_stations.csv",
                        match_method="exact_code",
                    )
            if name:
                match = df[df.station_name.astype(str).str.lower() == name.lower()]
                if not match.empty:
                    r = match.iloc[0]
                    st_val = str(r["state"]).strip() if pd.notna(r.get("state")) and str(r.get("state")).strip().lower() != "nan" else None
                    dt_val = str(r["district"]).strip() if pd.notna(r.get("district")) and str(r.get("district")).strip().lower() != "nan" else None
                    return ResolvedLocation(
                        type="hub",
                        canonical_name=f"{r.get('station_name', name)} ({r.get('station_code', '')})",
                        state=st_val,
                        district=dt_val,
                        district_code=int(r["district_code"]) if "district_code" in r and pd.notna(r["district_code"]) else None,
                        latitude=float(r["latitude"]),
                        longitude=float(r["longitude"]),
                        source_dataset="railway_stations.csv",
                        match_method="exact_name",
                    )

        # Handle other hubs in hubs_dict
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
                if name:
                    match = df[df.name.astype(str).str.lower() == name.lower()]
                    if match.empty:
                        match = df[df.name.astype(str).str.lower().str.startswith(name.lower())]
                    if not match.empty:
                        r = match.iloc[0]
                        st_val = str(r["state"]).strip() if pd.notna(r.get("state")) and str(r.get("state")).strip().lower() != "nan" else None
                        dt_val = str(r["city"]).strip() if pd.notna(r.get("city")) and str(r.get("city")).strip().lower() != "nan" else None
                        return ResolvedLocation(
                            type="hub",
                            canonical_name=str(r["name"]),
                            state=st_val,
                            district=dt_val,
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
