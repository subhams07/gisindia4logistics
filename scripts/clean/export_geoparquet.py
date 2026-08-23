"""
scripts/clean/export_geoparquet.py
High-performance GeoParquet conversion and benchmarking pipeline for GIS4Logistics.

Converts heavy GeoJSON and GPKG layers to OGC-compliant GeoParquet (.parquet)
with Snappy compression, measuring file size savings and read speedups.
"""

import sys
import time
from pathlib import Path
from typing import Dict, Any, List
import pandas as pd
import geopandas as gpd

sys.path.insert(0, ".")
from scripts.clean.standardize import DATA_DIR


def benchmark_conversion(src_path: Path, dst_path: Path, layer_name: str = "") -> Dict[str, Any]:
    """Converts a spatial dataset to GeoParquet and logs compression and speedup metrics."""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 1. Read source
    t0 = time.perf_counter()
    if src_path.suffix == ".gpkg":
        gdf = gpd.read_file(src_path)
    elif src_path.suffix in [".geojson", ".json"]:
        gdf = gpd.read_file(src_path)
    elif src_path.suffix == ".csv":
        df = pd.read_csv(src_path)
        t_read_src = time.perf_counter() - t0
        src_size_mb = src_path.stat().st_size / (1024 * 1024)
        
        # Write tabular parquet
        t1 = time.perf_counter()
        df.to_parquet(dst_path, engine="pyarrow", compression="snappy", index=False)
        t_write = time.perf_counter() - t1
        
        # Read parquet benchmark
        t2 = time.perf_counter()
        df_pq = pd.read_parquet(dst_path, engine="pyarrow")
        t_read_pq = time.perf_counter() - t2
        dst_size_mb = dst_path.stat().st_size / (1024 * 1024)
        
        return {
            "name": layer_name or src_path.name,
            "rows": len(df),
            "src_size_mb": round(src_size_mb, 2),
            "pq_size_mb": round(dst_size_mb, 2),
            "compression_pct": round((1.0 - (dst_size_mb / max(src_size_mb, 0.001))) * 100, 1),
            "t_read_src_sec": round(t_read_src, 3),
            "t_read_pq_sec": round(t_read_pq, 3),
            "speedup": round(t_read_src / max(t_read_pq, 0.001), 1)
        }
    else:
        raise ValueError(f"Unsupported format: {src_path.suffix}")

    t_read_src = time.perf_counter() - t0
    src_size_mb = src_path.stat().st_size / (1024 * 1024)

    # 2. Write GeoParquet
    t1 = time.perf_counter()
    gdf.to_parquet(dst_path, engine="pyarrow", compression="snappy", index=False)
    t_write = time.perf_counter() - t1

    # 3. Read GeoParquet benchmark
    t2 = time.perf_counter()
    gdf_pq = gpd.read_parquet(dst_path)
    t_read_pq = time.perf_counter() - t2
    dst_size_mb = dst_path.stat().st_size / (1024 * 1024)

    return {
        "name": layer_name or src_path.name,
        "rows": len(gdf),
        "src_size_mb": round(src_size_mb, 2),
        "pq_size_mb": round(dst_size_mb, 2),
        "compression_pct": round((1.0 - (dst_size_mb / max(src_size_mb, 0.001))) * 100, 1),
        "t_read_src_sec": round(t_read_src, 3),
        "t_read_pq_sec": round(t_read_pq, 3),
        "speedup": round(t_read_src / max(t_read_pq, 0.001), 1)
    }


def export_all_geoparquet():
    """Runs GeoParquet export for all heavy spatial datasets and analysis tables."""
    print("=" * 80)
    print("  GIS4LOGISTICS GEOPARQUET EXPORT & BENCHMARK PIPELINE")
    print("=" * 80)

    tasks = [
        # 1. National Highway Network
        (
            DATA_DIR / "roads" / "india_nh_network.geojson",
            DATA_DIR / "roads" / "india_nh_network.parquet",
            "National Highway Network (142k segments)"
        ),
        # 2. Sub-districts (GPKG -> Parquet)
        (
            DATA_DIR / "administrative" / "india_subdistricts_lgd.gpkg",
            DATA_DIR / "administrative" / "india_subdistricts_lgd.parquet",
            "Sub-districts LGD (6,636 features)"
        ),
        # 3. Districts LGD
        (
            DATA_DIR / "administrative" / "india_districts_lgd.geojson",
            DATA_DIR / "administrative" / "india_districts_lgd.parquet",
            "Districts LGD (781 features)"
        ),
        # 4. States LGD
        (
            DATA_DIR / "administrative" / "india_states_lgd.geojson",
            DATA_DIR / "administrative" / "india_states_lgd.parquet",
            "States LGD (36 features)"
        ),
        # 5. DFC Rail Network
        (
            DATA_DIR / "rail" / "dfc_network.geojson",
            DATA_DIR / "rail" / "dfc_network.parquet",
            "DFC Network (3 corridors)"
        ),
        # 6. Toll Plazas Table
        (
            DATA_DIR / "roads" / "toll_plazas.csv",
            DATA_DIR / "roads" / "toll_plazas.parquet",
            "Toll Plazas (1,536 plazas)"
        ),
        # 7. Railway Stations Table
        (
            DATA_DIR / "rail" / "railway_stations.csv",
            DATA_DIR / "rail" / "railway_stations.parquet",
            "Railway Stations (8,697 stations)"
        ),
        # 8. Travel Time Summary Matrix
        (
            DATA_DIR / "analysis" / "nh_district_travel_time_summary.csv",
            DATA_DIR / "analysis" / "nh_district_travel_time_summary.parquet",
            "NH Travel Time Summary (781 districts)"
        ),
        # 9. National District Access Summary
        (
            DATA_DIR / "analysis" / "india_district_access_summary.csv",
            DATA_DIR / "analysis" / "india_district_access_summary.parquet",
            "District Access Summary (817 rows)"
        )
    ]

    results = []
    for src, dst, label in tasks:
        if src.exists():
            print(f"Converting: {label} ...")
            res = benchmark_conversion(src, dst, layer_name=label)
            results.append(res)
            print(f"  -> Original: {res['src_size_mb']} MB ({res['t_read_src_sec']}s) | Parquet: {res['pq_size_mb']} MB ({res['t_read_pq_sec']}s) | {res['speedup']}x faster ({res['compression_pct']}% smaller)")
        else:
            print(f"  [SKIP] Source not found: {src}")

    # 10. Process Village Layers
    v_dir = DATA_DIR / "administrative" / "villages"
    pq_v_dir = DATA_DIR / "administrative" / "villages_parquet"
    pq_v_dir.mkdir(parents=True, exist_ok=True)
    
    v_files = sorted(list(v_dir.glob("*.geojson")))
    print(f"\nConverting {len(v_files)} State Village GeoJSONs to GeoParquet...")
    
    total_src_mb = 0.0
    total_pq_mb = 0.0
    v_count = 0

    for vf in v_files:
        dst_v = pq_v_dir / f"{vf.stem}.parquet"
        res = benchmark_conversion(vf, dst_v, layer_name=vf.name)
        total_src_mb += res["src_size_mb"]
        total_pq_mb += res["pq_size_mb"]
        v_count += res["rows"]

    print(f"  -> Converted {len(v_files)} village datasets ({v_count:,} total habitations)")
    print(f"  -> Total Village Size: {round(total_src_mb, 1)} MB GeoJSON -> {round(total_pq_mb, 1)} MB GeoParquet ({round((1.0 - total_pq_mb/total_src_mb)*100, 1)}% smaller)")

    print("\n" + "=" * 80)
    print(f"  GEOPARQUET EXPORT COMPLETED: ALL DATASETS ACCELERATED")
    print("=" * 80)


if __name__ == "__main__":
    export_all_geoparquet()
