"""Deterministic, root-scoped location and identity resolution."""
import logging
import math
from pathlib import Path
import re
from typing import Optional

import pandas as pd
import yaml

from scripts.clean.standardize import clean_state_name
from server.config import settings
from server.dependencies import DataStore
from server.models.phase1 import (
    LocationReference, CoordinateLocation, DistrictLocation, HubLocation, ResolvedLocation,
)
from server.services.exceptions import (
    LocationNotFoundError, AmbiguousLocationError, InvalidLocationError, DataIntegrityError,
)

LOGGER = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]
ALIASES_PATH = REPO_ROOT / "data" / "aliases" / "location_aliases.yaml"


def _text(value):
    return str(value).strip().lower() if pd.notna(value) else ""


def _state(value):
    return _text(clean_state_name(value) or value)


def _clean_name(value):
    return re.sub(r"[^\w\s]", "", _text(value))


class LocationResolver:
    """Caches are bound to a data root; explicit alias paths remain overrides.

    Files are treated as a snapshot for an instance's lifetime. Root switches use
    separate instances, never another root's aliases or representative points.
    """
    _instance: Optional["LocationResolver"] = None

    def __init__(self, aliases_path: Optional[Path] = None, *, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir if data_dir is not None else settings.DATA_PATH).resolve()
        self._explicit_aliases_path = Path(aliases_path).resolve() if aliases_path is not None else None
        self.aliases_path = self._explicit_aliases_path or self.data_dir / "aliases/location_aliases.yaml"
        self._aliases, self._aliases_by_code = self._load_aliases()
        self._district_centroids = {}
        self._code_state_centroids = {}
        self._centroids_initialized = False
        self._root_resolvers = {}

    @classmethod
    def get_instance(cls, aliases_path: Optional[Path] = None, *, data_dir: Optional[Path] = None):
        root = Path(data_dir if data_dir is not None else settings.DATA_PATH).resolve()
        override = Path(aliases_path).resolve() if aliases_path is not None else None
        if (cls._instance is None or cls._instance.data_dir != root
                or cls._instance._explicit_aliases_path != override):
            cls._instance = cls(aliases_path=aliases_path, data_dir=root)
        return cls._instance

    def _load_aliases(self):
        by_name, by_code = {}, {}
        if self.aliases_path.exists():
            try:
                data = yaml.safe_load(self.aliases_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for name, entry in data.items():
                        if isinstance(entry, dict):
                            by_name[_text(name)] = entry
                            if entry.get("code"):
                                by_code.setdefault(_text(entry["code"]), []).append(entry)
            except Exception as exc:
                LOGGER.warning("Failed to load location aliases from %s: %s", self.aliases_path, exc)
        return by_name, by_code

    def _ensure_centroids_initialized(self):
        if self._centroids_initialized:
            return
        path = self.data_dir / "administrative/india_districts_lgd.geojson"
        if path.exists():
            try:
                import geopandas as gpd
                frame = gpd.read_file(path)
                points = frame.to_crs(7755).geometry.representative_point().to_crs(4326)
                names, codes = {}, {}
                # Positional iteration also handles non-contiguous frame indices.
                for position, (_, row) in enumerate(frame.iterrows()):
                    point = points.iloc[position]
                    coords = (round(float(point.y), 5), round(float(point.x), 5))
                    if not all(math.isfinite(v) for v in coords):
                        continue
                    state = _state(row.get("state"))
                    names.setdefault((state, _text(row.get("district"))), []).append(coords)
                    if pd.notna(row.get("district_code")):
                        codes.setdefault((int(row["district_code"]), state), []).append(coords)
                self._district_centroids = {key: values[0] for key, values in names.items() if len(values) == 1}
                self._code_state_centroids = {key: values[0] for key, values in codes.items() if len(values) == 1}
            except Exception as exc:
                LOGGER.warning("Could not pre-calculate projected district centroids: %s", exc)
        self._centroids_initialized = True

    def resolve(self, loc: LocationReference, store: DataStore) -> ResolvedLocation:
        # Coordinates need neither aliases for a different root nor boundaries.
        if isinstance(loc, CoordinateLocation):
            return self._resolve_coordinate(loc, store)
        root = Path(getattr(store, "data_dir", settings.DATA_PATH)).resolve()
        if root != self.data_dir:
            if root not in self._root_resolvers:
                self._root_resolvers[root] = type(self)(self._explicit_aliases_path, data_dir=root)
            return self._root_resolvers[root].resolve(loc, store)
        if isinstance(loc, DistrictLocation):
            return self._resolve_district(loc, store)
        if isinstance(loc, HubLocation):
            return self._resolve_hub(loc, store)
        raise InvalidLocationError(f"Unsupported location reference type: {type(loc)}")

    def _resolve_coordinate(self, loc, store):
        if not (-90 <= loc.latitude <= 90 and -180 <= loc.longitude <= 180):
            raise InvalidLocationError("Coordinates are out of valid geographic range")
        return ResolvedLocation(
            type="coordinate", canonical_name=loc.label or f"Coord ({loc.latitude:.4f}, {loc.longitude:.4f})",
            state=None, district=None, district_code=None, latitude=loc.latitude, longitude=loc.longitude,
            source_dataset="user_coordinate", match_method="coordinate",
        )

    def _resolve_district(self, loc, store):
        df = getattr(store, "districts_df", None)
        if (not isinstance(df, pd.DataFrame) or df.empty
                or not {"district_code", "district", "state"}.issubset(df.columns)):
            raise DataIntegrityError("District identity source is unavailable or invalid")
        name = _text(loc.district)
        if loc.district_code is not None:
            matches = df[pd.to_numeric(df.district_code, errors="coerce") == loc.district_code]
            method = "exact_code"
        elif name:
            if self._district_name_matches(df, name).empty:
                raise LocationNotFoundError("District not found in official LGD register", location_query=loc.model_dump())
            matches = df
            method = "exact_name"
        else:
            raise InvalidLocationError("District name or district_code must be provided")
        if matches.empty:
            raise LocationNotFoundError("District not found in official LGD register", location_query=loc.model_dump())
        if loc.state:
            matches = matches[matches.state.map(_state) == _state(loc.state)]
        if name:
            matches = self._district_name_matches(matches, name)
        if matches.empty:
            raise InvalidLocationError("Supplied district code, name and state constraints conflict")
        if len(matches) != 1:
            candidates = [dict(district=r["district"], state=r["state"],
                               district_code=int(r["district_code"]) if pd.notna(r.get("district_code")) else None)
                          for _, r in matches.iterrows()]
            raise AmbiguousLocationError("District matches multiple records; supply distinguishing identifiers", candidates=candidates)
        return self._build_district_resolved(matches.iloc[0], method)

    @staticmethod
    def _district_name_matches(df, name):
        exact = df[df.district.map(_text) == name]
        return exact if not exact.empty else df[df.district.map(_clean_name) == _clean_name(name)]

    def _get_district_centroid(self, d_code, state, district):
        self._ensure_centroids_initialized()
        # Known codes must never be assigned another same-name identity's point.
        if pd.notna(d_code):
            coords = self._code_state_centroids.get((int(d_code), _state(state)))
        else:
            coords = self._district_centroids.get((_state(state), _text(district)))
        if coords is None:
            raise LocationNotFoundError(
                f"Representative coordinates for district '{district}', state '{state}' could not be resolved uniquely from boundary layer")
        return coords

    def _build_district_resolved(self, row, match_method):
        name, state = str(row["district"]).strip(), str(row["state"]).strip()
        code = int(row["district_code"]) if pd.notna(row.get("district_code")) else None
        lat, lon = self._get_district_centroid(code, state, name)
        canonical = name.title() if name.isupper() else name
        return ResolvedLocation(
            type="district", canonical_name=canonical, district=canonical,
            state=clean_state_name(state) or (state.title() if state.isupper() else state),
            district_code=code, latitude=lat, longitude=lon,
            source_dataset="india_districts_lgd.geojson", match_method=match_method,
        )

    def _hub_frame(self, hub_type, store):
        if hub_type == "rail_station":
            return getattr(store, "rail_stations_df", None), "station_name", ["station_code"], "railway_stations.csv"
        if hub_type == "freight_terminal":
            df = getattr(store, "freight_terminals_df", None)
            name = "terminal_name" if isinstance(df, pd.DataFrame) and "terminal_name" in df else "name"
            return df, name, ["matched_station_code", "station_code"], "freight_terminals.csv"
        keys = {"port": "ports", "icd": "icds", "mmlp": "mmlps", "air_cargo": "air_cargo",
                "iw_terminal": "iw_terminals", "icp": "icps", "fci_depot": "fci_depots",
                "cold_chain": "cold_chain", "mandi": "enam_mandis"}
        key = keys.get(hub_type)
        hubs = getattr(store, "hubs_dict", None)
        df = hubs.get(key) if isinstance(hubs, dict) else None
        return df, "name", ["code", "unlocode", "station_code"], f"{key}.csv"

    def _check_alias_type(self, entry, query, hub_type):
        if entry.get("type") and entry["type"] != hub_type:
            raise InvalidLocationError(
                f"Alias '{query}' refers to category '{entry['type']}', not requested category '{hub_type}'")

    def _resolve_hub(self, loc, store):
        name, code = _text(loc.name), _text(loc.code)
        if not name and not code:
            raise InvalidLocationError("Hub name or facility code must be provided")
        df, name_col, code_cols, source = self._hub_frame(loc.hub_type, store)
        if not isinstance(df, pd.DataFrame) or name_col not in df.columns:
            raise DataIntegrityError("Logistics hub identity source is unavailable or invalid")
        if df.empty:
            raise LocationNotFoundError("Logistics hub identity not found", location_query=loc.model_dump())
        alias = self._aliases.get(name)
        if alias and alias.get("type") and alias["type"] != loc.hub_type:
            # A literal name in the requested table is not an alias to another category.
            if df is not None and name_col in df and (df[name_col].map(_text) == name).any():
                alias = None
            else:
                self._check_alias_type(alias, name, loc.hub_type)
        code_aliases = self._aliases_by_code.get(code, []) if code else []
        literal_code = bool(code and df is not None and any(
            (df[col].map(_text) == code).any() for col in code_cols if col in df))
        compatible_code_aliases = []
        for entry in code_aliases:
            if literal_code and entry.get("type") and entry["type"] != loc.hub_type:
                continue
            self._check_alias_type(entry, code, loc.hub_type)
            compatible_code_aliases.append(entry)
        code_aliases = compatible_code_aliases
        # Reset indices so duplicate source indices cannot bypass row identity checks.
        df = df.reset_index(drop=True)
        matches = df
        if code:
            mask = pd.Series(False, index=df.index)
            for col in code_cols:
                if col in df:
                    mask |= df[col].map(_text) == code
            # Registry codes identify canonical rows where no source code exists.
            uncoded = pd.Series(True, index=df.index)
            for col in code_cols:
                if col in df:
                    uncoded &= df[col].map(_text) == ""
            for entry in code_aliases:
                mask |= uncoded & (df[name_col].map(_text) == _text(entry.get("canonical_name")))
            matches = df[mask]
            if matches.empty:
                raise LocationNotFoundError("Logistics hub code not found", location_query=loc.model_dump())
        if name:
            wanted = _text(alias.get("canonical_name")) if alias else name
            name_mask = matches[name_col].map(_text) == wanted
            if alias and alias.get("code"):
                # Alias name AND its coded identity must agree, where source codes exist.
                alias_code = _text(alias["code"])
                available = [col for col in code_cols if col in matches]
                if available:
                    coded = pd.Series(False, index=matches.index)
                    uncoded = pd.Series(True, index=matches.index)
                    for col in available:
                        coded |= matches[col].map(_text) == alias_code
                        uncoded &= matches[col].map(_text) == ""
                    name_mask &= coded | uncoded
            matches = matches[name_mask]
            if matches.empty:
                if code:
                    raise InvalidLocationError("Supplied hub code and name constraints conflict")
                raise LocationNotFoundError("Logistics hub name not found", location_query=loc.model_dump())
        if len(matches) != 1:
            candidates = [{"name": str(r[name_col]), "source_dataset": source,
                           "code": next((str(r[col]) for col in code_cols if col in r and pd.notna(r[col])), None)}
                          for _, r in matches.iterrows()]
            raise AmbiguousLocationError("Logistics hub matches multiple records", candidates=candidates)
        row = matches.iloc[0]
        try:
            lat, lon = float(row.get("latitude")), float(row.get("longitude"))
        except (TypeError, ValueError):
            lat, lon = math.nan, math.nan
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            raise LocationNotFoundError("Logistics hub is catalogued but lacks geographic coordinates for routing", location_query=loc.model_dump())
        canonical = str(row[name_col])
        if loc.hub_type == "rail_station":
            canonical += f" ({row.get('station_code', loc.code or '')})"
        elif loc.hub_type == "freight_terminal" and code:
            canonical += f" ({code.upper()})"
        state = row.get("state")
        return ResolvedLocation(
            type="hub", canonical_name=canonical,
            state=str(state).strip() if pd.notna(state) else None, district=None,
            district_code=int(row["district_code"]) if loc.hub_type == "rail_station" and pd.notna(row.get("district_code")) else None,
            latitude=lat, longitude=lon, source_dataset=source,
            match_method="exact_code" if code else "alias_lookup" if alias else "exact_name",
        )


def get_location_resolver() -> LocationResolver:
    """Dependency injector honoring the current configured data root."""
    return LocationResolver.get_instance()
