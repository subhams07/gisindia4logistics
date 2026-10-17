"""Tiny SYNTHETIC inputs for the 4b suite. Generated in private scratch roots; no repository data is read."""
import json
import os

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Polygon

# the twelve major-port keys the gravity model requires (names such that port_key(name) == key)
PORT_KEYS = [
    "paradip", "deendayal_(kandla)", "jawaharlal_nehru_(jnpt/navi_mumbai)", "visakhapatnam",
    "kolkata_(syama_prasad_mookerjee)_incl._haldia_dock_complex", "mumbai", "chennai", "kamarajar_(ennore)",
    "new_mangalore", "v.o._chidambaranar_(tuticorin)", "cochin", "mormugao",
]
LON0, LAT0, STEP = 78.0, 20.0, 0.05

# state, district, code, population (None = null), population-table state label
DISTRICTS = [
    ("Maharashtra", "Alpha", 598), ("Maharashtra", "Beta", 599), ("MAHARASHTRA", "Gamma ", None),
    ("Maharashtra", "Delta", 600), ("Karnataka", "Epsilon", 601), ("Karnataka", "Zeta", 598),
    ("Karnataka", "Eta", None), ("Gujarat", "Iota", 600),
]
POPULATION = [  # district_code, state, district, pop_2011
    (598, "Maharashtra", "Alpha", 1000.0), (598, "Karnataka", "Zeta", 2000.0), (599, "Maharashtra", "Beta", 1500.0),
    (None, "Maharashtra", "Gamma", 1200.0), (600, "Madras", "Delta", 1700.0), (601, "Karnataka", "Epsilon", None),
    (None, "Karnataka", "Eta", 1300.0),
]


def _node(i, j):
    return (round(LON0 + i * STEP, 6), round(LAT0 + j * STEP, 6))


def network_gdf():
    """A grid of roads on shared vertices (junctions are exact vertex matches)."""
    lines, kinds = [], ["motorway", "trunk", "primary"]
    k = 0
    for j in (0, 4, 8, 12):  # horizontal lines
        lines.append(("NH %d" % (j + 1), kinds[k % 3], LineString([_node(i, j) for i in range(17)])))
        k += 1
    for i in (0, 5, 10, 16):  # verticals
        lines.append(("NH %d" % (50 + i), kinds[k % 3], LineString([_node(i, j) for j in range(13)])))
        k += 1
    return gpd.GeoDataFrame(
        {"highway": [x[1] for x in lines], "ref": [x[0] for x in lines], "osm_id": list(range(1, len(lines) + 1)),
         "name": [f"Road {n}" for n in range(len(lines))], "nh": [x[0] for x in lines]},
        geometry=[x[2] for x in lines], crs=4326)


def district_gdf(variant=None, rows=None):
    rows = list(rows or DISTRICTS)
    if variant == "big_code":
        rows[1] = (rows[1][0], rows[1][1], 9007199254740993)  # 2**53 + 1: not representable in float64
        # no nulls: a null would make the source reader return float64 and hide the producer's own cast
        rows = [(s_, d_, (602 + n if c_ is None else c_)) for n, (s_, d_, c_) in enumerate(rows)]
    if variant == "same_state_dup":
        rows.append(("Karnataka", "Theta", 601))  # same state and code as Epsilon, outside any small prefix
    geoms = []
    for n in range(len(rows)):
        c, r = n % 4, n // 4
        x0, y0 = LON0 + 0.05 + c * 0.2, LAT0 + 0.05 + r * 0.25
        geoms.append(Polygon([(x0, y0), (x0 + 0.15, y0), (x0 + 0.15, y0 + 0.15), (x0, y0 + 0.15)]))
    df = pd.DataFrame(rows, columns=["state", "district", "district_code"])
    if variant == "big_code":
        df["district_code"] = df["district_code"].astype("Int64")
    return gpd.GeoDataFrame(df, geometry=geoms, crs=4326)


def write_district_geojson(path, gdf):
    """Write properties by hand so integer codes and nulls keep their literal JSON form."""
    feats = []
    for rec in gdf.to_dict("records"):
        geom = rec.pop("geometry")
        props = {k: (None if pd.isna(v) else (int(v) if k == "district_code" else v)) for k, v in rec.items()}
        feats.append({"type": "Feature", "properties": props, "geometry": json.loads(gpd.GeoSeries([geom]).to_json())["features"][0]["geometry"]})
    with open(path, "w", encoding="ascii") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)


def port_rows():
    rows = []
    for n, key in enumerate(PORT_KEYS):
        lon, lat = _node(n + 1, 4 if n % 2 == 0 else 8)
        rows.append({"name": key.replace("_", " "), "hub_type": "major_port", "latitude": lat, "longitude": lon})
    rows.append({"name": "Minor Harbour", "hub_type": "minor_port", "latitude": _node(2, 2)[1], "longitude": _node(2, 2)[0]})
    return pd.DataFrame(rows)


def make_data_dir(root, variant=None, rows=None, population=None, network=None):
    os.makedirs(os.path.join(root, "roads"), exist_ok=True)
    os.makedirs(os.path.join(root, "administrative"), exist_ok=True)
    os.makedirs(os.path.join(root, "demographic"), exist_ok=True)
    os.makedirs(os.path.join(root, "logistics_hubs"), exist_ok=True)
    (network if network is not None else network_gdf()).to_file(os.path.join(root, "roads", "india_nh_network.geojson"), driver="GeoJSON")
    write_district_geojson(os.path.join(root, "administrative", "india_districts_lgd.geojson"), district_gdf(variant, rows))
    pop = pd.DataFrame(population if population is not None else POPULATION,
                       columns=["district_code", "state", "district", "pop_2011"])
    if variant == "big_code":
        pop.loc[pop.district == "Beta", "district_code"] = 9007199254740993
        pop["district_code"] = pop["district_code"].astype("Int64")
    pop.to_csv(os.path.join(root, "demographic", "district_population_estimates.csv"), index=False)
    port_rows().to_csv(os.path.join(root, "logistics_hubs", "ports.csv"), index=False)
    pd.DataFrame([{"name": "Terminal One", "latitude": _node(3, 4)[1], "longitude": _node(3, 4)[0]},
                  {"name": "Terminal Two", "latitude": _node(12, 8)[1], "longitude": _node(12, 8)[0]}]
                 ).to_csv(os.path.join(root, "logistics_hubs", "mmlps.csv"), index=False)
    return root
