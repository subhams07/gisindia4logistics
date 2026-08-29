"""
scripts/analyze/plot_villages.py
Interactive and Static Map Plotting Engine for India's 578,000+ Villages & Settlements.

Generates:
1. Interactive Leaflet HTML maps with choropleth village polygons, popups, and facility overlays.
2. High-resolution cartographic static PNG maps.
"""

import sys
import json
import html
import re
import argparse
from pathlib import Path
from typing import Any
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt

sys.path.insert(0, ".")
from scripts.clean.standardize import DATA_DIR

METRIC_LABELS = {
    "dist_rail_station_km": "Distance to Nearest Railway Station (km)",
    "dist_nh_km": "Distance to Nearest National Highway (km)",
    "dist_icd_km": "Distance to Nearest Inland Container Depot (km)",
    "dist_freight_terminal_km": "Distance to Nearest Freight Terminal (GCT) (km)",
    "dist_port_km": "Distance to Nearest Commercial Sea Port (km)",
    "dist_air_cargo_km": "Distance to Nearest Air Cargo Terminal (km)",
    "dist_mmlp_km": "Distance to Nearest Multimodal Logistics Park (km)",
    "dist_toll_plaza_km": "Distance to Nearest Toll Plaza (km)"
}


def safe_filename_component(value: str) -> str:
    """Convert a geography name to a safe output filename component."""
    component = re.sub(r"[^a-z0-9_-]+", "_", value.strip().lower()).strip("_-")
    if not component:
        raise ValueError("A non-empty state or district name is required")
    return component


def validate_metric(metric: str) -> str:
    if metric not in METRIC_LABELS:
        supported = ", ".join(sorted(METRIC_LABELS))
        raise ValueError(f"Unsupported accessibility metric '{metric}'. Supported values: {supported}")
    return metric


def _json_for_script(value: Any) -> str:
    """Serialize JSON without allowing embedded data to terminate a script tag."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def load_district_villages_gdf(state: str, district: str) -> gpd.GeoDataFrame:
    """Loads village geometries for a district and merges pre-computed accessibility metrics."""
    slug = safe_filename_component(state)
    admin_dir = (DATA_DIR / "administrative").resolve()
    
    # 1. Geometry File (GeoParquet preferred, GeoJSON fallback)
    p_poly_pq = (admin_dir / "villages_parquet" / f"{slug}_soi_villages.parquet").resolve()
    p_pts_pq = (admin_dir / "villages_parquet" / f"{slug}_habitations.parquet").resolve()
    p_poly = (admin_dir / "villages" / f"{slug}_soi_villages.geojson").resolve()
    p_pts = (admin_dir / "villages" / f"{slug}_habitations.geojson").resolve()

    # Path traversal check
    for p in [p_poly_pq, p_pts_pq, p_poly, p_pts]:
        if not str(p).startswith(str(admin_dir)):
            raise ValueError(f"Invalid state path traversal attempt: '{state}'")
    
    if p_poly_pq.exists():
        gdf = gpd.read_parquet(p_poly_pq)
    elif p_pts_pq.exists():
        gdf = gpd.read_parquet(p_pts_pq)
    elif p_poly.exists():
        gdf = gpd.read_file(p_poly)
    elif p_pts.exists():
        gdf = gpd.read_file(p_pts)
    else:
        raise FileNotFoundError(f"No village geometry file found for state '{state}'")

    # Filter district
    d_col = "district" if "district" in gdf.columns else ("dtname" if "dtname" in gdf.columns else None)
    if not d_col:
        raise ValueError("Could not find district column in village dataset")

    gdf_dist = gdf[gdf[d_col].str.lower() == district.lower()].copy()
    if gdf_dist.empty:
        raise ValueError(f"District '{district}' not found in state '{state}' village dataset")

    # 2. Access metrics table
    p_acc = DATA_DIR / "analysis" / f"{slug}_village_access.csv"
    if p_acc.exists():
        acc_df = pd.read_csv(p_acc)
        # Match by village_code or village name
        acc_dist = acc_df[acc_df.district.str.lower() == district.lower()]
        if not acc_dist.empty:
            if "village_code" in gdf_dist.columns and "village_code" in acc_dist.columns:
                gdf_dist["vcode_str"] = gdf_dist["village_code"].astype(str)
                acc_dist["vcode_str"] = acc_dist["village_code"].astype(str)
                merged = gdf_dist.merge(acc_dist, on="vcode_str", how="left", suffixes=("", "_acc"))
                if not merged.empty:
                    gdf_dist = merged
            elif "village" in gdf_dist.columns and "village" in acc_dist.columns:
                merged = gdf_dist.merge(acc_dist, on="village", how="left", suffixes=("", "_acc"))
                if not merged.empty:
                    gdf_dist = merged

    return gdf_dist


def get_color_for_value(val: float, metric: str) -> str:
    """Returns hex color based on distance thresholds."""
    if pd.isna(val):
        return "#cccccc"
    if val <= 5.0:
        return "#1a9850"   # Dark green (excellent)
    elif val <= 10.0:
        return "#91cf60"  # Light green (good)
    elif val <= 25.0:
        return "#fee08b"  # Yellow (moderate)
    elif val <= 50.0:
        return "#fc8d59"  # Orange (distant)
    else:
        return "#d73027"   # Red (isolated)


def generate_leaflet_html(gdf: gpd.GeoDataFrame, state: str, district: str, metric: str = "dist_rail_station_km") -> str:
    """Generates a standalone, responsive Leaflet.js interactive HTML map."""
    validate_metric(metric)
    centroid = gdf.geometry.union_all().centroid
    center_lat, center_lon = centroid.y, centroid.x
    metric_title = METRIC_LABELS[metric]
    state_html = html.escape(state)
    district_html = html.escape(district)
    district_upper_html = html.escape(district.upper())
    metric_title_html = html.escape(metric_title)

    # Convert to GeoJSON dict
    geojson_data = json.loads(gdf.to_json())

    # Build facilities overlay for stations and hubs in the district
    facilities = []
    p_stations = DATA_DIR / "rail" / "railway_stations.csv"
    if p_stations.exists():
        st_df = pd.read_csv(p_stations)
        bounds = gdf.total_bounds # [minx, miny, maxx, maxy]
        nearby = st_df[
            st_df.longitude.between(bounds[0]-0.15, bounds[2]+0.15) &
            st_df.latitude.between(bounds[1]-0.15, bounds[3]+0.15)
        ]
        for _, r in nearby.iterrows():
            facilities.append({
                "name": r.get("station_name", "Railway Station"),
                "type": "Rail Station",
                "code": r.get("station_code", ""),
                "lat": float(r["latitude"]),
                "lon": float(r["longitude"])
            })

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GIS4Logistics — {district_html} Village Map ({metric_title_html})</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body, html {{ margin: 0; padding: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
        #map {{ width: 100%; height: 100%; }}
        .info-panel {{
            position: absolute; top: 10px; right: 10px; z-index: 1000;
            background: white; padding: 14px 18px; border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.2); max-width: 320px;
        }}
        .legend {{
            position: absolute; bottom: 25px; right: 10px; z-index: 1000;
            background: white; padding: 10px 14px; border-radius: 6px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2); font-size: 12px; line-height: 18px;
        }}
        .legend i {{ width: 16px; height: 16px; float: left; margin-right: 8px; opacity: 0.85; border-radius: 3px; }}
        .header-title {{ font-size: 16px; font-weight: 700; margin-bottom: 4px; color: #1e293b; }}
        .header-subtitle {{ font-size: 12px; color: #64748b; margin-bottom: 8px; }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="info-panel">
        <div class="header-title">{district_upper_html} ({state_html})</div>
        <div class="header-subtitle">{len(gdf):,} Villages & Habitations</div>
        <div style="font-size: 13px; font-weight: 600; color: #0284c7;">{metric_title_html}</div>
        <div id="village-detail" style="margin-top: 8px; font-size: 12px; color: #334155;">
            Hover or click on any village to inspect accessibility metrics.
        </div>
    </div>
    <div class="legend">
        <b>{metric_title_html}</b><br>
        <i style="background: #1a9850"></i> &lt; 5 km (Excellent)<br>
        <i style="background: #91cf60"></i> 5 &ndash; 10 km (Good)<br>
        <i style="background: #fee08b"></i> 10 &ndash; 25 km (Moderate)<br>
        <i style="background: #fc8d59"></i> 25 &ndash; 50 km (Distant)<br>
        <i style="background: #d73027"></i> &gt; 50 km (Isolated)
    </div>

    <script>
        const map = L.map('map').setView([{center_lat}, {center_lon}], 10);
        
        // CartoDB Positron Basemap
        L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>, &copy; <a href="https://carto.com/">CARTO</a> | Survey of India / GIS4Logistics',
            maxZoom: 19
        }}).addTo(map);

        const villageData = {_json_for_script(geojson_data)};
        const metricKey = {_json_for_script(metric)};

        function escapeHtml(value) {{
            return String(value ?? 'N/A')
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;')
                .replaceAll('"', '&quot;')
                .replaceAll("'", '&#039;');
        }}

        function getColor(d) {{
            if (d === undefined || d === null || isNaN(d)) return '#cccccc';
            return d <= 5  ? '#1a9850' :
                   d <= 10 ? '#91cf60' :
                   d <= 25 ? '#fee08b' :
                   d <= 50 ? '#fc8d59' :
                             '#d73027';
        }}

        function style(feature) {{
            const val = feature.properties[metricKey];
            return {{
                fillColor: getColor(val),
                weight: 1,
                opacity: 0.8,
                color: '#475569',
                fillOpacity: 0.7
            }};
        }}

        function highlightFeature(e) {{
            const layer = e.target;
            layer.setStyle({{ weight: 3, color: '#0f172a', fillOpacity: 0.9 }});
            layer.bringToFront();
            const p = layer.feature.properties;
            const val = p[metricKey] !== undefined ? p[metricKey] + ' km' : 'N/A';
            document.getElementById('village-detail').innerHTML = `
                <b>${{escapeHtml(p.village || p.name || 'Village')}}</b><br>
                Sub-district: ${{escapeHtml(p.sub_district)}}<br>
                LGD Code: ${{escapeHtml(p.village_code)}}<br>
                <b>${{escapeHtml(metricKey.replace(/_/g, ' '))}}:</b> <span style="color:#0284c7; font-weight:700;">${{escapeHtml(val)}}</span><br>
                Nearest Rail: ${{escapeHtml(p.nearest_rail_station)}}<br>
                Nearest ICD: ${{escapeHtml(p.nearest_icd)}}
            `;
        }}

        function resetHighlight(e) {{
            geojsonLayer.resetStyle(e.target);
        }}

        const geojsonLayer = L.geoJson(villageData, {{
            style: style,
            onEachFeature: function(feature, layer) {{
                layer.on({{
                    mouseover: highlightFeature,
                    mouseout: resetHighlight,
                    click: highlightFeature
                }});
            }}
        }}).addTo(map);

        // Fit map to layer bounds
        map.fitBounds(geojsonLayer.getBounds());

        // Overlay facilities
        const facilities = {_json_for_script(facilities)};
        const railIcon = L.divIcon({{
            className: 'custom-icon',
            html: '<div style="background:#2563eb; color:white; font-size:10px; font-weight:700; padding:2px 5px; border-radius:4px; border:1px solid white; white-space:nowrap;">🚂</div>',
            iconSize: [20, 20]
        }});

        facilities.forEach(f => {{
            L.marker([f.lat, f.lon], {{ icon: railIcon }})
             .bindPopup(`<b>${{escapeHtml(f.name)}}</b> (${{escapeHtml(f.code)}})<br>${{escapeHtml(f.type)}}`)
             .addTo(map);
        }});
    </script>
</body>
</html>
"""
    return html_content


def plot_villages_static(gdf: gpd.GeoDataFrame, state: str, district: str, metric: str = "dist_rail_station_km", output_png: Path = None):
    """Renders a publication-quality static PNG map of district villages using Matplotlib."""
    validate_metric(metric)
    if output_png is None:
        output_png = DATA_DIR / "analysis" / f"{safe_filename_component(district)}_{metric}.png"

    fig, ax = plt.subplots(figsize=(10, 10), dpi=300)
    metric_title = METRIC_LABELS.get(metric, metric)

    # Plot choropleth
    gdf.plot(
        column=metric,
        ax=ax,
        cmap="RdYlGn_r", # Green = near, Red = far
        legend=True,
        legend_kwds={"label": metric_title, "orientation": "horizontal", "shrink": 0.6, "pad": 0.05},
        edgecolor="#475569",
        linewidth=0.4
    )

    ax.set_title(f"{district.upper()} ({state.title()}) — Village Accessibility\n{metric_title}", fontsize=14, fontweight="bold", pad=12)
    ax.axis("off")
    plt.tight_layout()
    plt.savefig(output_png, bbox_inches="tight")
    plt.close()
    print(f"Wrote static map -> {output_png}")
    return output_png


def main():
    parser = argparse.ArgumentParser(description="GIS4Logistics Village Plotting Engine")
    parser.add_argument("--state", type=str, required=True, help="State name (e.g. 'Haryana', 'Maharashtra')")
    parser.add_argument("--district", type=str, required=True, help="District name (e.g. 'Ambala', 'Pune')")
    parser.add_argument("--metric", type=str, default="dist_rail_station_km", help="Accessibility metric column")
    parser.add_argument("--format", type=str, choices=["html", "png", "both"], default="both", help="Output format")
    parser.add_argument("--output", type=str, help="Output destination path")

    args = parser.parse_args()

    gdf = load_district_villages_gdf(state=args.state, district=args.district)
    print(f"Loaded {len(gdf)} villages for {args.district}, {args.state}")

    if args.format in ["html", "both"]:
        html_code = generate_leaflet_html(gdf=gdf, state=args.state, district=args.district, metric=args.metric)
        out_html = Path(args.output) if args.output and args.output.endswith(".html") else (DATA_DIR / "analysis" / f"{safe_filename_component(args.district)}_village_{args.metric}_map.html")
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html_code)
        print(f"Wrote interactive Leaflet map -> {out_html}")

    if args.format in ["png", "both"]:
        out_png = Path(args.output) if args.output and args.output.endswith(".png") else (DATA_DIR / "analysis" / f"{safe_filename_component(args.district)}_village_{args.metric}_map.png")
        plot_villages_static(gdf=gdf, state=args.state, district=args.district, metric=args.metric, output_png=out_png)


if __name__ == "__main__":
    main()
