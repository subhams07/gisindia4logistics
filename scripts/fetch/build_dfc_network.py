"""
scripts/fetch/build_dfc_network.py
Builds Dedicated Freight Corridor (DFC) Network GeoJSON + GeoParquet and DFC Junction Stations CSV.

Corridors:
- Western Dedicated Freight Corridor (WDFC, ~1,506 km nominal, Dadri to JNPT)
  Surveyed alignment from OpenStreetMap relation 12145132 (ODbL).
- Eastern Dedicated Freight Corridor (EDFC, ~1,337 km nominal, Sahnewal/Ludhiana to Sonnagar)
  Surveyed alignment from OpenStreetMap relation 11154478 (ODbL).
- Dadri - Khurja Connecting Link (~46 km nominal, New Dadri to New Khurja)
  Surveyed alignment from OpenStreetMap relation 11154478 (ODbL).

Usage:
    python scripts/fetch/build_dfc_network.py
    python scripts/fetch/build_dfc_network.py --cache-dir path/to/osm_cache
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyproj
import shapely
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.clean.standardize import DATA_DIR, http_session, write_geojson  # noqa: E402

OSM_API_BASE = "https://api.openstreetmap.org/api/0.6/relation"
WDFC_RELATION_ID = 12145132
EDFC_RELATION_ID = 11154478
SIMPLIFY_TOL_DEG = 0.0005

GEOD = pyproj.Geod(ellps="WGS84")

# Key DFC Junction Stations & Crossing Yards with official coordinates & chainage
DFC_STATIONS_DATA = [
    # WDFC (Dadri - JNPT)
    {"station_name": "New Dadri", "station_code": "NDAD", "corridor": "WDFC", "state": "Uttar Pradesh", "district": "Gautam Buddha Nagar", "latitude": 28.5510, "longitude": 77.5680, "station_type": "Junction / Terminus", "feeder_connection": "EDFC Interchange / ICD Dadri", "chainage_km": 0.0},
    {"station_name": "New Prithla", "station_code": "NPRT", "corridor": "WDFC", "state": "Haryana", "district": "Palwal", "latitude": 28.2560, "longitude": 77.2940, "station_type": "Junction", "feeder_connection": "Palwal Junction / IR Delhi-Agra", "chainage_km": 46.0},
    {"station_name": "New Taoru", "station_code": "NTAU", "corridor": "WDFC", "state": "Haryana", "district": "Nuh", "latitude": 28.2850, "longitude": 76.9530, "station_type": "Crossing", "feeder_connection": "Gurugram Logistics Zone", "chainage_km": 82.0},
    {"station_name": "New Rewari", "station_code": "NREW", "corridor": "WDFC", "state": "Haryana", "district": "Rewari", "latitude": 28.2120, "longitude": 76.6210, "station_type": "Junction", "feeder_connection": "IR Rewari Junction / Concor Bawal", "chainage_km": 127.0},
    {"station_name": "New Ateli", "station_code": "NATL", "corridor": "WDFC", "state": "Haryana", "district": "Mahendragarh", "latitude": 28.0280, "longitude": 76.2650, "station_type": "Crossing", "feeder_connection": "Mahendragarh Feeder", "chainage_km": 165.0},
    {"station_name": "New Dabla", "station_code": "NDBL", "corridor": "WDFC", "state": "Rajasthan", "district": "Sikar", "latitude": 27.8930, "longitude": 75.9810, "station_type": "Crossing", "feeder_connection": "Sikar Feeder", "chainage_km": 204.0},
    {"station_name": "New Neem Ka Thana", "station_code": "NNKT", "corridor": "WDFC", "state": "Rajasthan", "district": "Sikar", "latitude": 27.7340, "longitude": 75.7820, "station_type": "Crossing", "feeder_connection": "Neem Ka Thana", "chainage_km": 236.0},
    {"station_name": "New Ringas", "station_code": "NRGS", "corridor": "WDFC", "state": "Rajasthan", "district": "Sikar", "latitude": 27.3520, "longitude": 75.5680, "station_type": "Junction", "feeder_connection": "IR Ringas / Jaipur link", "chainage_km": 288.0},
    {"station_name": "New Phulera", "station_code": "NPHL", "corridor": "WDFC", "state": "Rajasthan", "district": "Jaipur", "latitude": 26.8740, "longitude": 75.2410, "station_type": "Junction", "feeder_connection": "IR Phulera Junction / Jaipur ICD", "chainage_km": 348.0},
    {"station_name": "New Kishangarh", "station_code": "NKSG", "corridor": "WDFC", "state": "Rajasthan", "district": "Ajmer", "latitude": 26.5820, "longitude": 74.8730, "station_type": "Crossing", "feeder_connection": "Kishangarh Marble Cluster", "chainage_km": 395.0},
    {"station_name": "New Madar", "station_code": "NMDR", "corridor": "WDFC", "state": "Rajasthan", "district": "Ajmer", "latitude": 26.5010, "longitude": 74.6850, "station_type": "Junction", "feeder_connection": "IR Ajmer / Madar", "chainage_km": 420.0},
    {"station_name": "New Marwar", "station_code": "NMWR", "corridor": "WDFC", "state": "Rajasthan", "district": "Pali", "latitude": 25.7310, "longitude": 73.6120, "station_type": "Junction", "feeder_connection": "IR Marwar Junction / Jodhpur link", "chainage_km": 542.0},
    {"station_name": "New Falna", "station_code": "NFLN", "corridor": "WDFC", "state": "Rajasthan", "district": "Pali", "latitude": 25.2210, "longitude": 73.2380, "station_type": "Crossing", "feeder_connection": "Falna Industrial Area", "chainage_km": 612.0},
    {"station_name": "New Abu Road", "station_code": "NABR", "corridor": "WDFC", "state": "Rajasthan", "district": "Sirohi", "latitude": 24.4750, "longitude": 72.7760, "station_type": "Junction", "feeder_connection": "IR Abu Road", "chainage_km": 685.0},
    {"station_name": "New Palanpur", "station_code": "NPNU", "corridor": "WDFC", "state": "Gujarat", "district": "Banas Kantha", "latitude": 24.1720, "longitude": 72.4310, "station_type": "Junction", "feeder_connection": "IR Palanpur / Mundra & Kandla Ports feeder", "chainage_km": 742.0},
    {"station_name": "New Mehsana", "station_code": "NMSN", "corridor": "WDFC", "state": "Gujarat", "district": "Mahesana", "latitude": 23.5930, "longitude": 72.3950, "station_type": "Junction", "feeder_connection": "IR Mehsana / Maruti Suzuki plant link", "chainage_km": 807.0},
    {"station_name": "New Sanand", "station_code": "NSND", "corridor": "WDFC", "state": "Gujarat", "district": "Ahmadabad", "latitude": 22.9840, "longitude": 72.3680, "station_type": "Junction", "feeder_connection": "Sanand Auto Hub / Viramgam / Pipavav link", "chainage_km": 880.0},
    {"station_name": "New Makarpura", "station_code": "NMKP", "corridor": "WDFC", "state": "Gujarat", "district": "Vadodara", "latitude": 22.2410, "longitude": 73.1950, "station_type": "Junction", "feeder_connection": "IR Vadodara / GIDC Makarpura", "chainage_km": 995.0},
    {"station_name": "New Bharuch", "station_code": "NBHR", "corridor": "WDFC", "state": "Gujarat", "district": "Bharuch", "latitude": 21.7120, "longitude": 72.9980, "station_type": "Crossing", "feeder_connection": "Dahej PCPIR / Port link", "chainage_km": 1072.0},
    {"station_name": "New Gothangam (Surat)", "station_code": "NGOT", "corridor": "WDFC", "state": "Gujarat", "district": "Surat", "latitude": 21.2640, "longitude": 72.8750, "station_type": "Junction", "feeder_connection": "IR Surat / Hazira Port feeder", "chainage_km": 1142.0},
    {"station_name": "New Udhna / Sachin", "station_code": "NSCH", "corridor": "WDFC", "state": "Gujarat", "district": "Surat", "latitude": 21.0820, "longitude": 72.8830, "station_type": "Crossing", "feeder_connection": "Surat Textile & Diamond SEZ", "chainage_km": 1165.0},
    {"station_name": "New Valsad", "station_code": "NBLS", "corridor": "WDFC", "state": "Gujarat", "district": "Valsad", "latitude": 20.6120, "longitude": 72.9350, "station_type": "Crossing", "feeder_connection": "Vapi / Valsad Industrial Area", "chainage_km": 1228.0},
    {"station_name": "New Vapi", "station_code": "NVAP", "corridor": "WDFC", "state": "Gujarat", "district": "Valsad", "latitude": 20.3750, "longitude": 72.9120, "station_type": "Crossing", "feeder_connection": "Vapi Chemical Hub", "chainage_km": 1260.0},
    {"station_name": "New Dahanu Road", "station_code": "NDHN", "corridor": "WDFC", "state": "Maharashtra", "district": "Palghar", "latitude": 19.9720, "longitude": 72.7410, "station_type": "Crossing", "feeder_connection": "Vadhavan Port Zone", "chainage_km": 1318.0},
    {"station_name": "New Vaitarna", "station_code": "NVTN", "corridor": "WDFC", "state": "Maharashtra", "district": "Palghar", "latitude": 19.5210, "longitude": 72.8450, "station_type": "Crossing", "feeder_connection": "Palghar / Boisar MIDC", "chainage_km": 1380.0},
    {"station_name": "New Vasai Road", "station_code": "NVSR", "corridor": "WDFC", "state": "Maharashtra", "district": "Palghar", "latitude": 19.3820, "longitude": 72.8350, "station_type": "Junction", "feeder_connection": "IR Vasai Road / Konkan Railway link", "chainage_km": 1405.0},
    {"station_name": "New Kopar / Diva", "station_code": "NKPR", "corridor": "WDFC", "state": "Maharashtra", "district": "Thane", "latitude": 19.1950, "longitude": 73.0420, "station_type": "Junction", "feeder_connection": "Central Railway / Panvel link", "chainage_km": 1445.0},
    {"station_name": "New Panvel", "station_code": "NPNL", "corridor": "WDFC", "state": "Maharashtra", "district": "Raigarh", "latitude": 18.9950, "longitude": 73.1210, "station_type": "Junction", "feeder_connection": "IR Panvel / Navi Mumbai Airport Hub", "chainage_km": 1475.0},
    {"station_name": "New JNPT (Nhava Sheva)", "station_code": "NJNP", "corridor": "WDFC", "state": "Maharashtra", "district": "Raigarh", "latitude": 18.9510, "longitude": 72.9550, "station_type": "Terminus / Port Railhead", "feeder_connection": "Jawaharlal Nehru Port Container Terminals", "chainage_km": 1506.0},

    # EDFC (Sahnewal/Ludhiana - Sonnagar)
    {"station_name": "New Sahnewal (Ludhiana)", "station_code": "NSNW", "corridor": "EDFC", "state": "Punjab", "district": "Ludhiana", "latitude": 30.8450, "longitude": 75.9850, "station_type": "Terminus / Junction", "feeder_connection": "IR Ludhiana / Concor Dhandari Kalan", "chainage_km": 0.0},
    {"station_name": "New Sirhind", "station_code": "NSIR", "corridor": "EDFC", "state": "Punjab", "district": "Fatehgarh Sahib", "latitude": 30.6380, "longitude": 76.3850, "station_type": "Crossing", "feeder_connection": "IR Sirhind", "chainage_km": 42.0},
    {"station_name": "New Shambhu (Ambala)", "station_code": "NSHB", "corridor": "EDFC", "state": "Punjab", "district": "Patiala", "latitude": 30.4320, "longitude": 76.7150, "station_type": "Junction", "feeder_connection": "IR Ambala Cantt link", "chainage_km": 88.0},
    {"station_name": "New Kalanaur (Yamunanagar)", "station_code": "NKAL", "corridor": "EDFC", "state": "Haryana", "district": "Yamunanagar", "latitude": 30.0820, "longitude": 77.3420, "station_type": "Crossing", "feeder_connection": "Yamunanagar Industrial Hub", "chainage_km": 155.0},
    {"station_name": "New Pilkhani (Saharanpur)", "station_code": "NPKI", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Saharanpur", "latitude": 29.9820, "longitude": 77.4650, "station_type": "Junction", "feeder_connection": "IR Saharanpur Junction", "chainage_km": 178.0},
    {"station_name": "New Muzaffarnagar", "station_code": "NMOZ", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Muzaffarnagar", "latitude": 29.4750, "longitude": 77.7120, "station_type": "Crossing", "feeder_connection": "Muzaffarnagar Steel Hub", "chainage_km": 242.0},
    {"station_name": "New Meerut", "station_code": "NMTC", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Meerut", "latitude": 28.9850, "longitude": 77.7050, "station_type": "Crossing", "feeder_connection": "Meerut Industrial Area", "chainage_km": 305.0},
    {"station_name": "New Hapur", "station_code": "NHPU", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Hapur", "latitude": 28.7310, "longitude": 77.7780, "station_type": "Junction", "feeder_connection": "IR Hapur Junction / Moradabad link", "chainage_km": 340.0},
    {"station_name": "New Khurja", "station_code": "NKRJ", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Bulandshahr", "latitude": 28.2550, "longitude": 77.8520, "station_type": "Junction / WDFC Link", "feeder_connection": "Dadri-Khurja Link / IR Khurja", "chainage_km": 397.0},
    {"station_name": "New Daud Khan (Aligarh)", "station_code": "NDDK", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Aligarh", "latitude": 27.8520, "longitude": 78.1150, "station_type": "Junction", "feeder_connection": "IR Aligarh Junction", "chainage_km": 445.0},
    {"station_name": "New Hathras", "station_code": "NHTR", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Hathras", "latitude": 27.5950, "longitude": 78.0520, "station_type": "Crossing", "feeder_connection": "Hathras Industrial Area", "chainage_km": 482.0},
    {"station_name": "New Tundla", "station_code": "NTDL", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Firozabad", "latitude": 27.2150, "longitude": 78.2420, "station_type": "Junction", "feeder_connection": "IR Tundla / Agra Link", "chainage_km": 535.0},
    {"station_name": "New Shikohabad", "station_code": "NSKB", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Firozabad", "latitude": 27.1080, "longitude": 78.5820, "station_type": "Crossing", "feeder_connection": "Firozabad Glass Cluster", "chainage_km": 572.0},
    {"station_name": "New Etawah", "station_code": "NETW", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Etawah", "latitude": 26.7850, "longitude": 79.0280, "station_type": "Junction", "feeder_connection": "IR Etawah Junction", "chainage_km": 630.0},
    {"station_name": "New Bhaupur (Kanpur)", "station_code": "NBHP", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Kanpur Dehat", "latitude": 26.4720, "longitude": 80.1250, "station_type": "Junction", "feeder_connection": "IR Kanpur Central / Panki Logistics Park", "chainage_km": 748.0},
    {"station_name": "New Prempur", "station_code": "NPRM", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Fatehpur", "latitude": 26.2210, "longitude": 80.5820, "station_type": "Crossing", "feeder_connection": "Fatehpur", "chainage_km": 805.0},
    {"station_name": "New Fatehpur", "station_code": "NFTP", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Fatehpur", "latitude": 25.9280, "longitude": 80.8120, "station_type": "Crossing", "feeder_connection": "Fatehpur Logistics Feeder", "chainage_km": 848.0},
    {"station_name": "New Manauri (Prayagraj West)", "station_code": "NMNR", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Kaushambi", "latitude": 25.4850, "longitude": 81.6780, "station_type": "Junction", "feeder_connection": "Prayagraj West", "chainage_km": 945.0},
    {"station_name": "New Karchana (Prayagraj East)", "station_code": "NKCN", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Prayagraj", "latitude": 25.3210, "longitude": 81.9250, "station_type": "Junction", "feeder_connection": "IR Chheoki / Naini Industrial Area", "chainage_km": 985.0},
    {"station_name": "New Chunar", "station_code": "NCAR", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Mirzapur", "latitude": 25.1250, "longitude": 82.8850, "station_type": "Junction", "feeder_connection": "IR Chunar / Mirzapur Carpet & Stone Cluster", "chainage_km": 1088.0},
    {"station_name": "New DDU (Pt Deen Dayal Upadhyay)", "station_code": "NDDU", "corridor": "EDFC", "state": "Uttar Pradesh", "district": "Chandauli", "latitude": 25.2850, "longitude": 83.1250, "station_type": "Major Junction / Yard", "feeder_connection": "IR DDU (Mughalsarai) Freight Marshalling Yard", "chainage_km": 1135.0},
    {"station_name": "New Bhabua Road", "station_code": "NBUR", "corridor": "EDFC", "state": "Bihar", "district": "Kaimur (Bhabua)", "latitude": 25.0450, "longitude": 83.6120, "station_type": "Crossing", "feeder_connection": "Kaimur Feeder", "chainage_km": 1190.0},
    {"station_name": "New Sasaram", "station_code": "NSSM", "corridor": "EDFC", "state": "Bihar", "district": "Rohtas", "latitude": 24.9520, "longitude": 84.0250, "station_type": "Junction", "feeder_connection": "IR Sasaram Junction", "chainage_km": 1240.0},
    {"station_name": "New Dehri-on-Sone", "station_code": "NDOS", "corridor": "EDFC", "state": "Bihar", "district": "Rohtas", "latitude": 24.9120, "longitude": 84.1850, "station_type": "Crossing", "feeder_connection": "Dehri Industrial Zone", "chainage_km": 1262.0},
    {"station_name": "New Sonnagar", "station_code": "NSER", "corridor": "EDFC", "state": "Bihar", "district": "Aurangabad", "latitude": 24.8820, "longitude": 84.2850, "station_type": "Terminus / Junction", "feeder_connection": "IR Sonnagar / Coalfields feeder to Dankuni", "chainage_km": 1337.0},
]


def _fetch_osm_relation(rel_id: int, cache_dir: Path | None = None) -> dict:
    cache_file = cache_dir / f"osm_rel_{rel_id}.json" if cache_dir else None
    if cache_file and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    url = f"{OSM_API_BASE}/{rel_id}/full.json"
    print(f"Fetching OSM relation {rel_id} from {url} ...")
    resp = http_session().get(url, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    return data


def _merged_parts_from_relation(data: dict, rel_id: int) -> list[LineString]:
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in data["elements"] if e["type"] == "node"}
    ways = {e["id"]: e for e in data["elements"] if e["type"] == "way"}
    rel = next(e for e in data["elements"] if e["type"] == "relation" and e["id"] == rel_id)
    lines = []
    for m in rel["members"]:
        if m["type"] == "way" and m["ref"] in ways:
            coords = [nodes[nid] for nid in ways[m["ref"]].get("nodes", []) if nid in nodes]
            if len(coords) >= 2:
                lines.append(LineString(coords))
    merged = linemerge(lines)
    parts = [merged] if isinstance(merged, LineString) else list(merged.geoms)
    return sorted(parts, key=lambda g: -GEOD.geometry_length(g))


def _prep_line(geom: LineString | MultiLineString) -> LineString | MultiLineString:
    cleaned = geom.simplify(SIMPLIFY_TOL_DEG, preserve_topology=True)
    cleaned = shapely.set_precision(cleaned, 1e-5)
    cleaned = shapely.make_valid(cleaned)
    if not cleaned.is_valid or cleaned.is_empty:
        raise ValueError("DFC geometry failed validity check after simplification")
    return cleaned


def _extract_corridor_geometries(wdfc_data: dict, edfc_data: dict) -> tuple[LineString, MultiLineString, LineString]:
    """Extract clean single-track centerlines for WDFC, EDFC (Sahnewal-Sonnagar), and Dadri-Khurja link."""
    wdfc_parts = _merged_parts_from_relation(wdfc_data, WDFC_RELATION_ID)
    edfc_parts = _merged_parts_from_relation(edfc_data, EDFC_RELATION_ID)

    # 1. WDFC: longest continuous surveyed centerline (JNPT <-> New Dadri, ~1,461 km)
    wdfc_line = _prep_line(wdfc_parts[0])

    # 2. Dadri-Khurja Link: part connecting New Dadri (~28.526, 77.548) to New Khurja (~28.215, 77.811)
    link_candidates = [
        p for p in edfc_parts
        if 30_000 <= GEOD.geometry_length(p) <= 60_000
        and any(28.48 <= lat <= 28.58 and 77.50 <= lon <= 77.62 for lon, lat in (p.coords[0], p.coords[-1]))
        and any(28.15 <= lat <= 28.28 and 77.75 <= lon <= 77.88 for lon, lat in (p.coords[0], p.coords[-1]))
    ]
    if not link_candidates:
        raise ValueError("Could not locate Dadri-Khurja connecting link in OSM EDFC relation")
    link_line = _prep_line(link_candidates[0])

    # 3. EDFC mainline (Sahnewal/Ludhiana -> Pilkhani -> Khurja -> Kanpur -> Prayagraj -> DDU -> Sonnagar):
    # Trim the Dadri-Khurja spur off the longest Sonnagar-Dadri line at New Khurja (~28.215, 77.811)
    edfc_main = edfc_parts[0]
    coords = list(edfc_main.coords)
    khurja_lon, khurja_lat = 77.811, 28.215
    split_idx = min(range(len(coords)), key=lambda i: (coords[i][0] - khurja_lon) ** 2 + (coords[i][1] - khurja_lat) ** 2)
    # Keep the longer side (Sonnagar -> Khurja, ~855 km)
    seg_a = LineString(coords[: split_idx + 1])
    seg_b = LineString(coords[split_idx:])
    edfc_south = seg_a if GEOD.geometry_length(seg_a) >= GEOD.geometry_length(seg_b) else seg_b

    # Khurja -> Pilkhani (~211 km) and Pilkhani -> Sahnewal/Ludhiana (~192 km)
    north_parts = [
        p for p in edfc_parts[1:]
        if GEOD.geometry_length(p) >= 150_000
        and max(lat for _, lat in p.coords) > 29.5
    ]
    if len(north_parts) < 2:
        raise ValueError(f"Expected Khurja-Pilkhani and Pilkhani-Sahnewal EDFC segments, found {len(north_parts)}")
    edfc_geom = _prep_line(MultiLineString([edfc_south] + north_parts[:2]))
    return wdfc_line, edfc_geom, link_line


def build_dfc(cache_dir: Path | None = None) -> None:
    out_dir_rail = DATA_DIR / "rail"
    out_dir_rail.mkdir(parents=True, exist_ok=True)

    # 1. Save DFC stations CSV
    df_st = pd.DataFrame(DFC_STATIONS_DATA)
    csv_path = out_dir_rail / "dfc_stations.csv"
    df_st.to_csv(csv_path, index=False)
    print(f"Wrote {len(df_st)} DFC stations -> {csv_path}")

    # 2. Fetch real surveyed DFC alignments from OpenStreetMap
    wdfc_data = _fetch_osm_relation(WDFC_RELATION_ID, cache_dir=cache_dir)
    edfc_data = _fetch_osm_relation(EDFC_RELATION_ID, cache_dir=cache_dir)
    wdfc_geom, edfc_geom, link_geom = _extract_corridor_geometries(wdfc_data, edfc_data)

    rows = [
        {
            "name": "Western Dedicated Freight Corridor (WDFC)",
            "corridor": "WDFC",
            "route": "Dadri - Rewari - Phulera - Palanpur - Sanand - Surat - JNPT",
            "length_km": round(GEOD.geometry_length(wdfc_geom) / 1000.0, 1),
            "official_length_km": 1506,
            "status": "Operational / Commissioned (Vaitarna-JNPT final link)",
            "geometry_source": f"OpenStreetMap relation {WDFC_RELATION_ID} (ODbL)",
            "geometry": wdfc_geom,
        },
        {
            "name": "Eastern Dedicated Freight Corridor (EDFC)",
            "corridor": "EDFC",
            "route": "Sahnewal (Ludhiana) - Khurja - Kanpur - Prayagraj - DDU - Sonnagar",
            "length_km": round(GEOD.geometry_length(edfc_geom) / 1000.0, 1),
            "official_length_km": 1337,
            "status": "Operational / Commissioned",
            "geometry_source": f"OpenStreetMap relation {EDFC_RELATION_ID} (ODbL)",
            "geometry": edfc_geom,
        },
        {
            "name": "Dadri - Khurja DFC Connecting Link",
            "corridor": "WDFC-EDFC Link",
            "route": "New Dadri to New Khurja",
            "length_km": round(GEOD.geometry_length(link_geom) / 1000.0, 1),
            "official_length_km": 46,
            "status": "Operational",
            "geometry_source": f"OpenStreetMap relation {EDFC_RELATION_ID} (ODbL)",
            "geometry": link_geom,
        },
    ]

    gdf_lines = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    geojson_path = out_dir_rail / "dfc_network.geojson"
    parquet_path = out_dir_rail / "dfc_network.parquet"
    write_geojson(geojson_path, json.loads(gdf_lines.to_json(drop_id=True)))
    gdf_lines.to_parquet(parquet_path, engine="pyarrow", compression="snappy", index=False)

    # Re-read both outputs and verify geometry validity
    re_gj = gpd.read_file(geojson_path)
    re_pq = gpd.read_parquet(parquet_path)
    assert len(re_gj) == len(re_pq) == 3, "Row count mismatch after write"
    assert bool(re_gj.geometry.is_valid.all()) and bool(re_pq.geometry.is_valid.all()), "Invalid DFC geometry on re-read"
    for r in re_gj.itertuples():
        print(f"  {r.corridor:16s} | geodesic={r.length_km:6.1f} km (official {r.official_length_km} km) | valid={r.geometry.is_valid}")
    print(f"Wrote {len(re_gj)} DFC corridors -> {geojson_path} & {parquet_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir", type=Path, default=None, help="Optional directory to cache OSM relation JSON responses")
    args = ap.parse_args()
    build_dfc(cache_dir=args.cache_dir)
