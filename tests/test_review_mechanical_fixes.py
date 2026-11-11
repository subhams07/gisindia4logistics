"""Regressions for the mechanical review fixes (toll NH filter, 503 mapping, store fallbacks,
MCP serverInfo version, plot-villages CLI errors, audit baseline/catalog/hub-type checks)."""
import json
import sys
import threading
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely.geometry import box

from server.dependencies import DataStore, get_data_store
from server.routers import admin, hubs


def _toll(name, nh):
    return {"name": name, "toll_type": "national_highway", "nh_number": nh, "state": "Haryana",
            "district": "Ambala", "district_code": 1.0, "latitude": 30.0, "longitude": 77.0,
            "booth_count": 1, "source_url": "https://example.org"}


@pytest.fixture
def api():
    store = SimpleNamespace(
        states_df=None, districts_df=None, rail_stations_df=None, hubs_dict={},
        toll_plazas_df=pd.DataFrame([_toll("A", "NH 44"), _toll("B", "NH 44"), _toll("C", "NH 48"),
                                     _toll("D", "NH 444"), _toll("E", None)]),
    )
    app = FastAPI()
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(hubs.router, prefix="/api/v1")
    app.dependency_overrides[get_data_store] = lambda: store
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, store


def test_toll_nh_filter_ignores_spacing_case_and_prefix(api):
    client, _ = api
    names = {}
    for query in ["NH44", "nh 44", "NH 44", " nh44 ", "44"]:
        response = client.get("/api/v1/hubs/roads/toll-plazas", params={"nh_number": query})
        assert response.status_code == 200, response.text
        names[query] = [row["name"] for row in response.json()]
    assert names["NH44"] == ["A", "B"]  # exact route only: NH 444 is a different highway
    assert all(found == names["NH44"] for found in names.values())
    assert client.get("/api/v1/hubs/roads/toll-plazas", params={"nh_number": "NH999"}).json() == []


@pytest.mark.parametrize("attribute,endpoint", [
    ("states_df", "admin/states"), ("districts_df", "admin/districts"), ("districts_df", "admin/districts/1"),
    ("rail_stations_df", "hubs/rail/stations"), ("toll_plazas_df", "hubs/roads/toll-plazas"),
])
def test_unloaded_tables_are_service_unavailable(api, attribute, endpoint):
    client, store = api
    setattr(store, attribute, None)
    assert client.get(f"/api/v1/{endpoint}").status_code == 503


def test_store_initializes_without_population_estimates(tmp_path, monkeypatch):
    admin_dir = tmp_path / "administrative"
    admin_dir.mkdir()
    gpd.GeoDataFrame(
        {"state": ["Haryana", "Haryana", "Punjab"], "district": ["Ambala", "Karnal", "Patiala"],
         "district_code": [1, 2, 3], "state_code": [6, 6, 3]},
        geometry=[box(0, 0, 1, 1)] * 3, crs=4326,
    ).to_file(admin_dir / "india_districts_lgd.geojson", driver="GeoJSON")
    monkeypatch.setattr(DataStore, "_build_highway_graph", lambda self: None)
    store = DataStore()
    store.data_dir = tmp_path
    store.initialize_store()  # used to raise KeyError: 'pop_2011'
    states = store.states_df.set_index("state")
    assert states.district_count.to_dict() == {"Haryana": 2, "Punjab": 1}
    assert "pop_2011_total" not in states.columns and "nh_node_list" not in vars(store)


def test_get_instance_initializes_once_under_concurrency(monkeypatch):
    monkeypatch.setattr(DataStore, "_instance", None)
    started, release, built = threading.Event(), threading.Event(), []

    def slow_init(self):
        built.append(self)
        started.set()
        release.wait(5)

    monkeypatch.setattr(DataStore, "initialize_store", slow_init)
    results = []
    threads = [threading.Thread(target=lambda: results.append(DataStore.get_instance())) for _ in range(4)]
    threads[0].start()
    assert started.wait(5)
    for thread in threads[1:]:
        thread.start()
    release.set()
    for thread in threads:
        thread.join(5)
    assert len(built) == 1 and len(results) == 4 and all(item is built[0] for item in results)


def test_mcp_server_info_reports_the_package_version():
    import gisindia4logistics
    from mcp_server.server import handle_rpc_request
    init = handle_rpc_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"] == {"name": "gisindia4logistics", "version": gisindia4logistics.__version__}


@pytest.mark.parametrize("error", [
    ValueError("District 'Nowhere' not found in state 'Haryana' village dataset"),
    FileNotFoundError("No village geometry file found for state 'Atlantis'"),
])
def test_plot_villages_cli_reports_user_errors_without_traceback(monkeypatch, capsys, error):
    from gisindia4logistics import cli, sdk

    def fail(**kwargs):
        raise error

    monkeypatch.setattr(sdk, "plot_villages", fail)
    monkeypatch.setattr(sys, "argv", ["gisindia4logistics", "plot-villages", "--state", "X", "--district", "Y"])
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert captured.err == f"error: {error}\n" and "Traceback" not in captured.err and captured.out == ""


def test_plot_villages_cli_rejects_an_unsupported_metric(monkeypatch, capsys):
    from gisindia4logistics import cli
    monkeypatch.setattr(sys, "argv", ["gisindia4logistics", "plot-villages", "--state", "Haryana",
                                      "--district", "Ambala", "--metric", "dist_moon_km"])
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 2
    assert capsys.readouterr().err.startswith("error: Unsupported accessibility metric 'dist_moon_km'")


def test_metric_lists_match_the_validator():
    from mcp_server.server import TOOLS_METADATA
    from scripts.analyze.plot_villages import METRIC_LABELS
    tool = next(t for t in TOOLS_METADATA if t["name"] == "gis_plot_villages_map")
    metric = tool["inputSchema"]["properties"]["metric"]
    assert set(metric["enum"]) == set(METRIC_LABELS)
    assert all(f"'{name}'" in metric["description"] for name in METRIC_LABELS)


# ---- audit script ----

@pytest.fixture
def audit_results():
    from scripts.audit import audit_all as audit
    results = []
    token = audit._RESULT_SINK.set(results)
    try:
        yield audit, results
    finally:
        audit._RESULT_SINK.reset(token)


def _baseline_world(tmp_path, baseline):
    (tmp_path / "rail").mkdir()
    pd.DataFrame({"v": [1, 2]}).to_csv(tmp_path / "rail" / "railway_stations.csv", index=False)
    (tmp_path / "audit_baseline.json").write_text(json.dumps(baseline))


def test_baseline_reports_a_series_that_disappeared(audit_results, tmp_path):
    audit, results = audit_results
    _baseline_world(tmp_path, {"rail:stations": 2, "rail:freight": 84, "admin:states": 36, "villages:x": 5})
    audit.check_baseline(tmp_path)
    assert results[-1]["status"] == "FAIL" and "rail:freight" in results[-1]["detail"]
    assert "admin:states" not in results[-1]["detail"]  # deep-only keys are never collected


def test_baseline_stable_and_separate_artifact_root(audit_results, tmp_path):
    audit, results = audit_results
    _baseline_world(tmp_path, {"rail:stations": 2, "admin:states": 36, "analysis:x_village_access": 9})
    staged = tmp_path / "staged"
    staged.mkdir()
    audit.check_baseline(tmp_path, staged)  # a staged bundle need not carry the village tables
    assert results[-1]["status"] == "PASS"
    audit.check_baseline(tmp_path)  # the data/analysis layout itself must still hold them
    assert results[-1]["status"] == "FAIL" and "analysis:x_village_access" in results[-1]["detail"]


def test_catalog_parse_error_is_a_failed_check(audit_results, tmp_path, monkeypatch):
    audit, results = audit_results
    (tmp_path / "catalog.yaml").write_text("datasets:\n  - name: x\n    notes: code 3606: collision\n")
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    audit.audit_catalog(tmp_path)
    assert [(r["check"], r["status"]) for r in results] == [("parses", "FAIL")]


def test_hub_type_values_are_checked(audit_results, tmp_path):
    audit, results = audit_results
    hubs_dir = tmp_path / "logistics_hubs"
    hubs_dir.mkdir()
    types = {"ports.csv": "major_port", "icps.csv": "icp", "icds.csv": "cfs", "air_cargo.csv": "air_cargo",
             "mmlps.csv": "mmlp", "inland_waterway_terminals.csv": "iw_terminal", "fci_depots.csv": "port"}
    for name, hub_type in types.items():
        pd.DataFrame([{"name": "n", "hub_type": hub_type, "state": "s", "city": "c", "latitude": 20.0,
                       "longitude": 78.0, "operator": "o", "capacity_notes": "", "source_url": "u"}]
                     ).to_csv(hubs_dir / name, index=False)
    try:
        audit.audit_hubs(tmp_path)
    except Exception:  # the later industrial-node checks need files this world does not have
        pass
    status = {r["check"]: r["status"] for r in results if r["check"].endswith("hub_type values")}
    assert status.pop("fci_depots.csv: hub_type values") == "FAIL"
    assert len(status) == 6 and set(status.values()) == {"PASS"}


def test_nearest_facility_all_fails_loudly_and_skips_the_composite(monkeypatch, tmp_path, capsys):
    from scripts.analyze import nearest_facility as nf
    admin_dir = tmp_path / "administrative"
    admin_dir.mkdir()
    gpd.GeoDataFrame({"state": ["Goa", "Sikkim"]}, geometry=[box(0, 0, 1, 1)] * 2, crs=4326).to_file(
        admin_dir / "india_states_lgd.geojson", driver="GeoJSON")
    monkeypatch.setattr(nf, "DATA_DIR", tmp_path)
    composite = []
    monkeypatch.setattr(nf, "build_national_summary", lambda: composite.append(1))

    def run_one(state, args):
        if state == "Sikkim":
            raise RuntimeError("boom")

    monkeypatch.setattr(nf, "run_one", run_one)
    monkeypatch.setattr(sys, "argv", ["nearest_facility.py", "--all"])
    with pytest.raises(SystemExit) as excinfo:
        nf.main()
    assert "Sikkim" in str(excinfo.value.code) and "Goa" not in str(excinfo.value.code) and composite == []
    monkeypatch.setattr(nf, "run_one", lambda state, args: None)
    nf.main()
    assert composite == [1]
