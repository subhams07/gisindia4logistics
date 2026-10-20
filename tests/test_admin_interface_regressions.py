"""HTTP regressions using isolated data, never the production store."""
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.dependencies import get_data_store
from server.routers import admin, hubs


@pytest.fixture
def api(tmp_path):
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    pd.DataFrame([
        {"district": "Ambala", "village": f"Village {i}", "dist_icd_km": None}
        for i in range(7)
    ]).to_csv(analysis / "haryana_village_access.csv", index=False)
    store = SimpleNamespace(
        data_dir=tmp_path,
        states_df=pd.DataFrame([{"state": "Haryana"}, {"state": "Punjab"}]),
        districts_df=pd.DataFrame([
            {"state": "Haryana", "district": "Ambala", "district_code": 1},
            {"state": "Punjab", "district": "Ambala", "district_code": 1},
            {"state": "Haryana", "district": "Bracket [ Town", "district_code": 2},
        ]),
        travel_time_df=None, district_access_df=None,
        rail_stations_df=pd.DataFrame([
            {"station_name": "One", "station_code": "AAA", "category": "NSG1", "zone": "NR"},
            {"station_name": "Two", "station_code": "BBB", "category": "SG2", "zone": "WR"},
            {"station_name": "Three", "station_code": "CCC", "category": None, "zone": "NR"},
        ]),
    )
    app = FastAPI()
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(hubs.router, prefix="/api/v1")
    app.dependency_overrides[get_data_store] = lambda: store
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, store


def test_villages_http_success_canonical_state_and_pagination(api):
    client, _ = api
    response = client.get("/api/v1/admin/villages", params={"state": "hArYaNa", "limit": 5})
    assert response.status_code == 200, response.text
    assert len(response.json()) == 5
    assert all(row["state"] == "Haryana" and row["dist_icd_km"] is None for row in response.json())
    response = client.get("/api/v1/admin/villages", params={"state": "Haryana", "offset": 5, "district": "ambala"})
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.parametrize("state", ["../secret", "..\\secret", "C:\\secret", "/secret", "\\\\host\\share", "%2e%2e%2fsecret", "%252e%252e%255csecret", "Haryana/..", "Haryana%00", "imaginary"])
@pytest.mark.parametrize("endpoint", ["villages", "villages/geojson", "villages/map.html"])
def test_malicious_state_never_reads_csv(api, monkeypatch, state, endpoint):
    client, _ = api
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid geography must not cause a CSV read")
    monkeypatch.setattr(pd, "read_csv", forbidden)
    response = client.get(f"/api/v1/admin/{endpoint}", params={"state": state, "district": "Ambala"})
    assert response.status_code == 422, response.text
    assert "secret" not in response.text


def test_village_resolved_path_escape_never_reads(api, monkeypatch):
    client, store = api
    from pathlib import Path
    original = Path.resolve
    target = store.data_dir / "analysis" / "haryana_village_access.csv"
    def escaped(path, *args, **kwargs):
        return store.data_dir.parent / "private.csv" if path == target else original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", escaped)
    monkeypatch.setattr(pd, "read_csv", lambda *a, **k: pytest.fail("Escaping path read"))
    response = client.get("/api/v1/admin/villages", params={"state": "Haryana"})
    assert response.status_code == 422
    assert "private.csv" not in response.text


def test_district_search_is_literal(api):
    client, _ = api
    response = client.get("/api/v1/admin/districts", params={"search": "["})
    assert response.status_code == 200, response.text
    assert [r["district"] for r in response.json()] == ["Bracket [ Town"]


@pytest.mark.parametrize("category,expected", [("nsg1", ["AAA"]), ("SG2", ["BBB"]), ("HG1", []), ("unknown", [])])
def test_rail_category_filter(api, category, expected):
    client, _ = api
    response = client.get("/api/v1/hubs/rail/stations", params={"category": category})
    assert response.status_code == 200
    assert [r["station_code"] for r in response.json()] == expected


def test_rail_categories_from_separate_roster(api):
    client, store = api
    store.rail_stations_df = store.rail_stations_df.drop(columns="category")
    (store.data_dir / "rail").mkdir()
    pd.DataFrame([{"station_code": "AAA", "category": "NSG1"}]).to_csv(store.data_dir / "rail" / "station_categories.csv", index=False)
    response = client.get("/api/v1/hubs/rail/stations", params={"category": "NSG1"})
    assert response.status_code == 200, response.text
    assert [r["station_code"] for r in response.json()] == ["AAA"]
    assert response.json()[0]["category"] == "NSG1"


@pytest.mark.parametrize("identifier", ["Ambala", "1"])
def test_scorecard_ambiguity_is_explicit(api, identifier):
    client, _ = api
    response = client.get(f"/api/v1/admin/districts/{identifier}")
    assert response.status_code == 409, response.text
    assert len(response.json()["detail"]["candidates"]) == 2


def test_scorecard_state_disambiguation_and_conflict(api):
    client, _ = api
    response = client.get("/api/v1/admin/districts/1", params={"state": "Haryana"})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "Haryana"
    response = client.get("/api/v1/admin/districts/2", params={"state": "Punjab"})
    assert response.status_code == 422


@pytest.mark.parametrize("table", ["districts_df", "travel_time_df", "district_access_df"])
def test_scorecard_duplicate_rows_never_pick_first(api, table):
    client, store = api
    row = {"state": "Haryana", "district": "Bracket [ Town", "district_code": 2}
    setattr(store, table, pd.DataFrame([row, row]))
    response = client.get("/api/v1/admin/districts/2")
    assert response.status_code == 409, response.text


@pytest.mark.parametrize("identifier", ["Ambala", "1"])
def test_scorecard_same_state_duplicates_remain_ambiguous(api, identifier):
    client, store = api
    row = {"state": "Haryana", "district": "Ambala", "district_code": 1}
    store.districts_df = pd.DataFrame([row, row])
    response = client.get(f"/api/v1/admin/districts/{identifier}", params={"state": "Haryana"})
    assert response.status_code == 409


def test_scorecard_direct_call_preserves_store_positional_argument(api):
    _, store = api
    assert admin.get_district_scorecard("2", store)["district"] == "Bracket [ Town"
    assert admin.get_district_scorecard("Ambala", store, state="haryana")["state"] == "Haryana"


@pytest.mark.parametrize("table", ["travel_time_df", "district_access_df"])
def test_scorecard_nonmatching_enrichment_code_is_not_attached(api, table):
    client, store = api
    setattr(store, table, pd.DataFrame([
        {"state": "Haryana", "district": "Bracket [ Town", "district_code": 99}
    ]))
    response = client.get("/api/v1/admin/districts/2")
    assert response.status_code == 200
    assert response.json()["nearest_highway_km"] is None
    assert response.json()["share_villages_within_10km_rail"] is None


@pytest.mark.parametrize("identifier,status", [("missing", 404), ("999", 404), (" ", 422), ("²", 422), ("0", 422)])
def test_scorecard_missing_and_bad_input(api, identifier, status):
    client, _ = api
    assert client.get(f"/api/v1/admin/districts/{identifier}").status_code == status


def test_explicit_code_does_not_fall_back_to_numeric_name(api):
    client, store = api
    store.districts_df.loc[2, "district"] = "999"
    assert client.get("/api/v1/admin/districts/999").status_code == 404


def test_valid_state_without_access_data_is_missing_resource(api):
    client, _ = api
    assert client.get("/api/v1/admin/villages", params={"state": "Punjab"}).status_code == 404


@pytest.mark.parametrize("endpoint", ["villages/geojson", "villages/map.html"])
def test_village_map_helpers_receive_canonical_state(api, monkeypatch, endpoint):
    from scripts.analyze import plot_villages

    client, store = api
    monkeypatch.setattr(plot_villages, "DATA_DIR", store.data_dir)
    geo = {"type": "FeatureCollection", "features": []}

    def load(*, state, district):
        assert (state, district) == ("Haryana", "Ambala")
        return SimpleNamespace(__geo_interface__=geo)

    monkeypatch.setattr(plot_villages, "load_district_villages_gdf", load)
    monkeypatch.setattr(plot_villages, "generate_leaflet_html", lambda **kw: "<html>map</html>")
    response = client.get(f"/api/v1/admin/{endpoint}", params={"state": "haryana", "district": "Ambala"})
    assert response.status_code == 200
    if endpoint.endswith("geojson"):
        assert response.json() == geo
    else:
        assert response.text == "<html>map</html>"


@pytest.mark.parametrize("endpoint", ["villages/geojson", "villages/map.html"])
@pytest.mark.parametrize("relative", ["analysis/haryana_village_access.csv", "administrative/villages/haryana_soi_villages.geojson", "rail/railway_stations.csv"])
def test_map_checks_actual_helper_root_before_loading(api, monkeypatch, endpoint, relative):
    from pathlib import Path

    from scripts.analyze import plot_villages

    client, store = api
    actual_root = store.data_dir / "helper_data"
    actual_root.mkdir()
    monkeypatch.setattr(plot_villages, "DATA_DIR", actual_root)
    target = actual_root / relative
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        return store.data_dir.parent / "private.csv" if path == target else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(plot_villages, "load_district_villages_gdf", lambda **kw: pytest.fail("Escaping helper path loaded"))
    response = client.get(f"/api/v1/admin/{endpoint}", params={"state": "Haryana", "district": "Ambala"})
    assert response.status_code == 422
    assert "private.csv" not in response.text


def test_bad_map_metric_rejected_before_load(api, monkeypatch):
    from scripts.analyze import plot_villages

    client, _ = api
    monkeypatch.setattr(plot_villages, "load_district_villages_gdf", lambda **kw: pytest.fail("Bad metric loaded data"))
    response = client.get("/api/v1/admin/villages/map.html", params={"state": "Haryana", "district": "Ambala", "metric": "invalid"})
    assert response.status_code == 422


@pytest.mark.parametrize("endpoint", ["villages/geojson", "villages/map.html"])
def test_missing_village_geometry_is_404_without_path_disclosure(api, monkeypatch, endpoint):
    from scripts.analyze import plot_villages

    client, store = api
    monkeypatch.setattr(plot_villages, "DATA_DIR", store.data_dir)

    def missing(**kw):
        raise FileNotFoundError("C:/private/missing.geojson")

    monkeypatch.setattr(plot_villages, "load_district_villages_gdf", missing)
    response = client.get(f"/api/v1/admin/{endpoint}", params={"state": "Haryana", "district": "Ambala"})
    assert response.status_code == 404
    assert "private" not in response.text


def test_rail_category_combines_with_search_zone_before_limit(api):
    client, _ = api
    response = client.get("/api/v1/hubs/rail/stations", params={"category": "SG2", "search": "Two", "zone": "wr", "limit": 1})
    assert response.status_code == 200
    assert [r["station_code"] for r in response.json()] == ["BBB"]
    response = client.get("/api/v1/hubs/rail/stations", params={"category": "SG2", "zone": "NR"})
    assert response.json() == []


def test_rail_no_category_preserves_unclassified_stations(api):
    client, store = api
    store.rail_stations_df = store.rail_stations_df.drop(columns="category")
    response = client.get("/api/v1/hubs/rail/stations")
    assert response.status_code == 200
    assert [r["station_code"] for r in response.json()] == ["AAA", "BBB", "CCC"]
    response = client.get("/api/v1/hubs/rail/stations", params={"category": "NSG1"})
    assert response.status_code == 404


def test_rail_roster_duplicates_do_not_multiply_results(api):
    client, store = api
    store.rail_stations_df = store.rail_stations_df.drop(columns="category")
    (store.data_dir / "rail").mkdir()
    pd.DataFrame([
        {"station_code": "aaa", "category": "NSG1"},
        {"station_code": "aaa", "category": "NSG1"},
        {"station_code": None, "category": "NSG1"},
    ]).to_csv(store.data_dir / "rail" / "station_categories.csv", index=False)
    response = client.get("/api/v1/hubs/rail/stations", params={"category": "nsg1"})
    assert response.status_code == 200
    assert [r["station_code"] for r in response.json()] == ["AAA"]


def test_rail_category_roster_escape_never_reads(api, monkeypatch):
    from pathlib import Path

    client, store = api
    store.rail_stations_df = store.rail_stations_df.drop(columns="category")
    original = Path.resolve
    target = store.data_dir / "rail" / "station_categories.csv"

    def escaped(path, *args, **kwargs):
        return store.data_dir.parent / "private.csv" if path == target else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", escaped)
    monkeypatch.setattr(pd, "read_csv", lambda *a, **k: pytest.fail("Escaping roster read"))
    response = client.get("/api/v1/hubs/rail/stations", params={"category": "NSG1"})
    assert response.status_code == 422
    assert "private.csv" not in response.text


def test_allowlisted_state_alias_preserves_valid_requests(api):
    client, store = api
    pd.DataFrame([{"district": "Cuttack", "village": "Example"}]).to_csv(
        store.data_dir / "analysis" / "odisha_village_access.csv", index=False
    )
    response = client.get("/api/v1/admin/villages", params={"state": "Orissa"})
    assert response.status_code == 200
    assert response.json()[0]["state"] == "Odisha"


@pytest.mark.parametrize("state", ["", "imaginary", "../Haryana"])
def test_scorecard_invalid_state_is_bad_input(api, state):
    client, _ = api
    assert client.get("/api/v1/admin/districts/2", params={"state": state}).status_code == 422


def test_overlong_explicit_code_is_bad_input_not_500(api):
    client, _ = api
    assert client.get("/api/v1/admin/districts/" + "9" * 5000).status_code == 422


def real_admin_store(store):
    from pathlib import Path

    store.data_dir = Path(__file__).resolve().parents[1] / "data"
    store.districts_df = pd.read_csv(store.data_dir / "demographic/district_population_estimates.csv")
    store.travel_time_df = pd.read_csv(store.data_dir / "analysis/nh_district_travel_time_summary.csv")
    store.district_access_df = pd.read_csv(store.data_dir / "analysis/india_district_access_summary.csv")


def test_real_delhi_listing_preserves_missing_names(api):
    client, store = api
    real_admin_store(store)
    source = pd.read_csv(store.data_dir / "analysis/delhi_village_access.csv")
    response = client.get("/api/v1/admin/villages", params={"state": " NCT of Delhi ", "limit": 500})
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == len(source)
    assert sum(row["village"] is None for row in rows) == source.village.isna().sum()


@pytest.mark.parametrize("code", [498, 598])
def test_real_dhule_code_before_enrichment_ambiguity(api, code):
    client, store = api
    real_admin_store(store)
    source = store.districts_df.loc[(store.districts_df.district_code == code) & (store.districts_df.state == "Maharashtra")].iloc[0]
    response = client.get(f"/api/v1/admin/districts/{code}", params={"state": " maharashtra "})
    assert response.status_code == 200, response.text
    assert response.json()["district_code"] == code
    assert response.json()["district"] == source.district
    assert client.get("/api/v1/admin/districts/Dhule", params={"state": "Maharashtra"}).status_code == 409
    travel = store.travel_time_df.loc[(store.travel_time_df.district_code == code) & (store.travel_time_df.state == "Maharashtra")].iloc[0]
    assert response.json()["nearest_highway_km"] == travel.highway_access_dist_km
    # The access rollup is keyed by (state, district_code): each of the two
    # same-state DHULE identities gets its own row, never the other's.
    rollup = store.district_access_df
    access = rollup.loc[(rollup.state == "Maharashtra") & (rollup.district_code == code)]
    assert len(access) == 1
    access = access.iloc[0]
    assert response.json()["share_villages_within_10km_rail"] == access.rail_station_within_10km_pct
    assert response.json()["share_villages_within_5km_rail"] == access.rail_station_within_10km_pct
    assert response.json()["share_villages_within_25km_rail"] == access.rail_station_within_25km_pct
    assert response.json()["share_villages_within_50km_icd"] == access.icd_within_50km_pct
    if code == 598:
        assert client.get("/api/v1/admin/districts/598").status_code == 409


@pytest.mark.parametrize("district", ["NAZUL", "MIRPUR", "MUZAFFARABAD"])
def test_real_null_code_district_retains_unique_travel_enrichment(api, district):
    client, store = api
    real_admin_store(store)
    source = store.travel_time_df[store.travel_time_df.district.str.upper() == district].iloc[0]
    assert pd.isna(source.district_code)
    assert pd.notna(source.highway_access_dist_km)
    response = client.get(f"/api/v1/admin/districts/{district}")
    assert response.status_code == 200, response.text
    assert response.json()["district_code"] is None
    assert response.json()["nearest_highway_km"] == source.highway_access_dist_km


@pytest.mark.parametrize("code_column", [False, True])
def test_unkeyed_enrichment_never_crosses_same_state_district_identities(api, code_column):
    client, store = api
    store.districts_df = pd.DataFrame([
        {"state": "Haryana", "district": "Shared", "district_code": 2},
        {"state": "Haryana", "district": "Shared", "district_code": None},
    ])
    access = {"state": "Haryana", "district": "Shared", "rail_station_within_10km_pct": 80}
    if code_column:
        access["district_code"] = None
    store.district_access_df = pd.DataFrame([access])
    response = client.get("/api/v1/admin/districts/2")
    assert response.status_code == 200, response.text
    assert response.json()["share_villages_within_10km_rail"] is None


def test_missing_village_and_district_names_remain_null(api):
    client, store = api
    pd.DataFrame([{"village": None, "district": None}]).to_csv(
        store.data_dir / "analysis/haryana_village_access.csv", index=False
    )
    response = client.get("/api/v1/admin/villages", params={"state": "Haryana"})
    assert response.status_code == 200, response.text
    assert response.json()[0]["village"] is None
    assert response.json()[0]["district"] is None


def test_sdk_scorecard_state_disambiguation(api, monkeypatch):
    from gisindia4logistics import sdk

    client, store = api
    real_admin_store(store)
    monkeypatch.setattr(sdk, "get_data_store", lambda: store)
    for name in ["Bilaspur", "Hamirpur"]:
        rows = store.districts_df[store.districts_df.district.str.lower() == name.lower()]
        assert len(rows) > 1
        assert client.get(f"/api/v1/admin/districts/{name}").status_code == 409
        for state in rows.state.unique():
            response = client.get(f"/api/v1/admin/districts/{name}", params={"state": f" {state.lower()} "})
            assert response.status_code == 200, response.text
            assert sdk.get_district(name, state=f" {state.lower()} ") == response.json()


def test_cli_district_state_disambiguation(api, monkeypatch, capsys):
    import json

    from gisindia4logistics import cli, sdk

    _, store = api
    real_admin_store(store)
    monkeypatch.setattr(sdk, "get_data_store", lambda: store)
    monkeypatch.setattr("sys.argv", ["gisindia4logistics", "district", "Bilaspur", "--state", "Himachal Pradesh"])
    cli.main()
    assert json.loads(capsys.readouterr().out) == sdk.get_district("Bilaspur", state="Himachal Pradesh")
    monkeypatch.setattr("sys.argv", ["gisindia4logistics", "district", "Bilaspur"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert capsys.readouterr().err == "error: Location is ambiguous; supply distinguishing identifiers.\n"


def test_district_listing_and_scorecard_preserve_null_name(api):
    client, store = api
    store.districts_df = pd.DataFrame([{"state": "Haryana", "district": None, "district_code": 2}])
    response = client.get("/api/v1/admin/districts", params={"state": " haryana "})
    assert response.status_code == 200, response.text
    assert response.json()[0]["district"] is None
    response = client.get("/api/v1/admin/districts/2")
    assert response.status_code == 200, response.text
    assert response.json()["district"] is None


@pytest.mark.parametrize("value", [float("inf"), -float("inf"), "bad", 1.5])
def test_scorecard_bad_stored_population_is_503(api, value):
    client, store = api
    store.districts_df["pop_2011"] = value
    assert client.get("/api/v1/admin/districts/2").status_code == 503


@pytest.mark.parametrize("value", [float("inf"), "bad", -1])
def test_scorecard_bad_nested_stored_number_is_503(api, value):
    client, store = api
    store.travel_time_df = pd.DataFrame([{
        "state": "Haryana", "district": "Bracket [ Town", "district_code": 2,
        "port_road_distance_km": value,
    }])
    assert client.get("/api/v1/admin/districts/2").status_code == 503


def test_state_alias_filters_across_admin_hubs_and_tolls(api):
    client, store = api
    store.districts_df = pd.DataFrame([{"state": "Odisha", "district": "Cuttack"}])
    hub = {"name": "Port", "state": "Odisha", "city": "Cuttack", "latitude": 20, "longitude": 86}
    store.hubs_dict = {"ports": pd.DataFrame([hub])}
    store.toll_plazas_df = pd.DataFrame([{**hub, "toll_type": "Public"}])
    for endpoint in ["admin/districts", "hubs", "hubs/roads/toll-plazas"]:
        response = client.get(f"/api/v1/{endpoint}", params={"state": " Orissa "})
        assert response.status_code == 200, response.text
        assert len(response.json()) == 1
        assert client.get(f"/api/v1/{endpoint}", params={"state": "invalid"}).status_code == 422

