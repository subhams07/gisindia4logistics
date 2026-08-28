"""
tests/test_package.py
Comprehensive test suite for the GISIndia4Logistics Python SDK and CLI tool.
"""

import subprocess
import gisindia4logistics as gis
import gis4logistics as legacy_gis


def test_import_and_version():
    """Verify package imports cleanly under both names and exposes version string."""
    assert hasattr(gis, "__version__")
    assert gis.__version__ == "1.0.0"
    assert hasattr(legacy_gis, "__version__")
    assert legacy_gis.__version__ == "1.0.0"


def test_get_district_sdk():
    """Verify get_district SDK function returns valid district scorecard."""
    pune = gis.get_district("Pune")
    assert pune["district"].lower() == "pune"
    assert pune["state"].lower() == "maharashtra"
    assert pune["pop_2011"] > 9_000_000
    assert "nearest_highway_km" in pune
    assert "nearest_port" in pune


def test_route_highway_sdk():
    """Verify route_highway SDK calculates distance, duration, and tolls."""
    route = gis.route_highway(
        origin=(18.5204, 73.8567),      # Pune
        destination=(18.9500, 72.9500), # JNPT
        vehicle_type="MAV_20T"
    )
    assert route["distance_km"] > 100.0
    assert route["drive_time_hours"] > 1.0
    assert route["tolls_encountered_count"] >= 1
    assert route["estimated_toll_cost_inr"] > 0.0


def test_calculate_freight_cost_sdk():
    """Verify calculate_freight_cost SDK performs multimodal optimization."""
    cost = gis.calculate_freight_cost(
        origin_district="Indore",
        payload_tons=24.0,
        road_linehaul_rate=3.80
    )
    assert cost["origin_district"].lower() == "indore"
    assert "road" in cost
    assert "conventional_rail" in cost
    assert cost["road"]["cost_per_ton_inr"] > 0
    assert any(m in cost["optimal_mode"].lower() for m in ["road", "rail", "dfc"])


def test_find_nearest_sdk():
    """Verify find_nearest SDK performs spatial KDTree lookups."""
    nearest = gis.find_nearest(latitude=28.6139, longitude=77.2090, top_k=2)
    assert "ports" in nearest
    assert "icds" in nearest
    assert "toll_plazas" in nearest
    assert len(nearest["icds"]) <= 2


def test_simulate_port_catchment_sdk():
    """Verify simulate_port_catchment SDK computes port market shares."""
    res = gis.simulate_port_catchment(alpha=0.85, beta=1.65)
    assert len(res) >= 10
    total_share = sum(item["market_share_districts_pct"] for item in res)
    assert 99.0 <= total_share <= 101.0


def test_cli_execution():
    """Verify both gisindia4logistics and gis4logistics CLI entry points execute cleanly."""
    res1 = subprocess.run(["gisindia4logistics", "--help"], capture_output=True, text=True)
    assert res1.returncode == 0
    assert "GISIndia4Logistics" in res1.stdout
    assert "serve" in res1.stdout

    res2 = subprocess.run(["gis4logistics", "--help"], capture_output=True, text=True)
    assert res2.returncode == 0
    assert "serve" in res2.stdout


if __name__ == "__main__":
    print("Running GISIndia4Logistics package test suite...")
    test_import_and_version()
    print("  [PASS] test_import_and_version (gisindia4logistics & gis4logistics)")
    test_get_district_sdk()
    print("  [PASS] test_get_district_sdk")
    test_route_highway_sdk()
    print("  [PASS] test_route_highway_sdk")
    test_calculate_freight_cost_sdk()
    print("  [PASS] test_calculate_freight_cost_sdk")
    test_find_nearest_sdk()
    print("  [PASS] test_find_nearest_sdk")
    test_simulate_port_catchment_sdk()
    print("  [PASS] test_simulate_port_catchment_sdk")
    test_cli_execution()
    print("  [PASS] test_cli_execution (both CLI entrypoints)")
    print("\n==== ALL 7 GISINDIA4LOGISTICS PACKAGE TESTS PASSED ====")
