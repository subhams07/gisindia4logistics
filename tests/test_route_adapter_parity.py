"""Coordinator acceptance: analytical parity, not physical corridor ground truth."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server.models.phase1 import HighwayRoutePlanRequest, VehicleType
from server.models.schemas import RouteRequest
from server.services import routing_facade
from server.services.error_contract import route_error_contract
from server.services.exceptions import (
    AmbiguousLocationError, DataIntegrityError, InvalidLocationError,
    LocationNotFoundError, RouteNotAvailableError, UnsupportedScenarioError,
)
from server.services.routing_service import RoutingService
from server.services.serialization import serialize_result
from tests.test_routing_edge_cases import graph, location


@pytest.fixture
def interfaces(tmp_path, monkeypatch):
    from gisindia4logistics import sdk
    from mcp_server import server as rpc, tools
    from server.app import app
    from server.dependencies import DataStore, get_data_store

    service = RoutingService()
    base = np.array(service.to_proj.transform(78, 22))
    xy = [base, base + (25000, 1000), base + (25100, 1000),
          base + (50000, 0), base + (65000, 0)]
    # A short marked synthetic edge makes warning propagation observable.
    store = graph(xy, [(0, 1), (1, 2), (2, 3), (3, 4)], bridges=[(1, 2)])
    store.data_dir = tmp_path
    (tmp_path / 'version_manifest.json').write_text(
        json.dumps({'data_version': 'adapter-parity-fixture'}), encoding='utf-8')
    refs = [dict(type='coordinate', latitude=p.latitude, longitude=p.longitude)
            for p in (location(service, xy[0]), location(service, xy[-1]))]
    monkeypatch.setattr(DataStore, '_instance', store)
    monkeypatch.setattr(sdk, 'get_data_store', lambda: store)
    monkeypatch.setattr(tools, 'store', store)
    original_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_data_store] = lambda: store
    # No lifespan startup; the injected synthetic generation is the test source.
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield sdk, rpc, client, store, refs
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)


def tool_call(rpc, name, arguments):
    response = rpc.handle_rpc_request(dict(jsonrpc='2.0', id=1, method='tools/call',
                                           params=dict(name=name, arguments=arguments)))
    assert 'error' not in response, response
    return response['result']


def without_timestamp(payload):
    result = json.loads(json.dumps(payload, allow_nan=False))
    result['metadata'].pop('generated_at_utc')
    return result


def assert_timestamps(payloads, before, after):
    for payload in payloads:
        stamp = datetime.fromisoformat(payload['metadata']['generated_at_utc'].replace('Z', '+00:00'))
        assert stamp.utcoffset().total_seconds() == 0
        assert before <= stamp <= after


@pytest.mark.parametrize('vehicle', [v.value for v in VehicleType])
@pytest.mark.parametrize('detail,include', [('none', True), ('full', True),
                                           ('simplified', True), ('full', False)])
def test_rich_surfaces_reach_one_engine_and_agree(interfaces, vehicle, detail, include):
    sdk, rpc, client, store, refs = interfaces
    request = dict(origin=refs[0], destination=refs[1], vehicle_type=vehicle,
                   geometry_detail=detail, include_route_geometry=include)
    original = RoutingService.calculate_highway_route
    calls = []

    def spy(self, *args, **kwargs):
        calls.append(kwargs)
        return original(self, *args, **kwargs)

    before = datetime.now(timezone.utc)
    with patch.object(RoutingService, 'calculate_highway_route', spy):
        response = client.post('/api/v1/route/highway/plan', json=request)
        assert response.status_code == 200, response.text
        rest = response.json()
        python = sdk.plan_highway_route(**request)
        tool = tool_call(rpc, 'gis_plan_highway_route', request)
        assert not tool.get('isError', False), tool
        mcp = json.loads(tool['content'][0]['text'])
        canonical = serialize_result(routing_facade.plan_highway_route(
            HighwayRoutePlanRequest.model_validate(request), store))
    after = datetime.now(timezone.utc)
    assert len(calls) == 4
    assert all(call['store'] is store for call in calls)
    outputs = [rest, python, mcp, canonical]
    assert all(without_timestamp(value) == without_timestamp(canonical) for value in outputs)
    assert_timestamps(outputs, before, after)
    assert (rest['route_geometry'] is not None) == (include and detail != 'none')
    assert response.headers['X-GIS4L-Data-Version'] == rest['metadata']['data_version']
    assert response.headers['X-GIS4L-Model-Version'] == rest['metadata']['model_version']


@pytest.mark.parametrize('vehicle', [v.value for v in VehicleType])
def test_legacy_surfaces_keep_quality_and_labelled_heuristic(interfaces, vehicle):
    sdk, rpc, client, store, refs = interfaces
    origin = [refs[0]['latitude'], refs[0]['longitude']]
    destination = [refs[1]['latitude'], refs[1]['longitude']]
    request = dict(origin=origin, destination=destination, vehicle_type=vehicle)
    before = datetime.now(timezone.utc)
    response = client.post('/api/v1/route/highway', json=request)
    assert response.status_code == 200, response.text
    python = sdk.route_highway(**request)
    tool = tool_call(rpc, 'gis_highway_route_and_tolls', dict(
        origin_lat=origin[0], origin_lon=origin[1], dest_lat=destination[0],
        dest_lon=destination[1], vehicle_type=vehicle))
    assert not tool.get('isError', False), tool
    mcp = json.loads(tool['content'][0]['text'])
    canonical = routing_facade.legacy_highway_route(RouteRequest.model_validate(request), store)
    after = datetime.now(timezone.utc)
    outputs = [response.json(), python, mcp, canonical]
    assert all(without_timestamp(value) == without_timestamp(canonical) for value in outputs)
    assert_timestamps(outputs, before, after)
    assert 'not official' in canonical['toll_estimation_method']
    assert canonical['route_quality']['warnings']
    assert response.headers['X-GIS4L-Data-Version'] == canonical['metadata']['data_version']


@pytest.mark.parametrize('error_class', [AmbiguousLocationError, InvalidLocationError,
                                         LocationNotFoundError, RouteNotAvailableError,
                                         DataIntegrityError, UnsupportedScenarioError])
def test_domain_errors_have_same_safe_contract(interfaces, error_class):
    sdk, rpc, client, _, refs = interfaces
    request = dict(origin=refs[0], destination=refs[1])
    error = error_class('C:/secret/private-source.csv')
    if isinstance(error, AmbiguousLocationError):
        error.candidates = [dict(district='Dhule', district_code=498, state='Maharashtra',
                                 path='C:/secret/private-source.csv')]
    expected = route_error_contract(error)
    with patch.object(RoutingService, 'calculate_highway_route', side_effect=error):
        response = client.post('/api/v1/route/highway/plan', json=request)
        assert response.status_code == expected['status']
        assert response.json()['detail'] == expected
        with pytest.raises(error_class) as caught:
            sdk.plan_highway_route(**request)
        assert route_error_contract(caught.value) == expected
        tool = tool_call(rpc, 'gis_plan_highway_route', request)
        assert tool['isError'] is True
        assert json.loads(tool['content'][0]['text']) == expected
    assert 'secret' not in response.text
    assert 'secret' not in tool['content'][0]['text']


def test_committed_corridor_parity_including_cli(tmp_path, monkeypatch):
    from gisindia4logistics import sdk
    from mcp_server import server as rpc, tools
    from server.app import app
    from server.dependencies import get_data_store

    store = get_data_store()
    monkeypatch.setattr(tools, 'store', store)
    request = dict(origin=dict(type='district', district_code=521),
                   destination=dict(type='hub', hub_type='port', name='JNPT'),
                   vehicle_type='LCV', geometry_detail='simplified')
    before = datetime.now(timezone.utc)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/api/v1/route/highway/plan', json=request)
    assert response.status_code == 200, response.text
    python = sdk.plan_highway_route(**request)
    tool = tool_call(rpc, 'gis_plan_highway_route', request)
    assert not tool.get('isError', False), tool
    mcp = json.loads(tool['content'][0]['text'])
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['GIS4LOGISTICS_CACHE_PATH'] = str(tmp_path / 'cli-cache')
    env['GIS4LOGISTICS_OUTPUT_PATH'] = str(tmp_path / 'outputs')
    command = [sys.executable, '-B', '-m', 'gisindia4logistics.cli', 'route-plan',
               '--origin', json.dumps(request['origin']), '--dest', json.dumps(request['destination']),
               '--vehicle', request['vehicle_type'], '--geometry', request['geometry_detail']]
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stderr
    cli = json.loads(completed.stdout)
    after = datetime.now(timezone.utc)
    outputs = [response.json(), python, mcp, cli]
    assert all(without_timestamp(value) == without_timestamp(python) for value in outputs)
    assert_timestamps(outputs, before, after)
    assert python['distance_km'] == 131.19  # Accepted model diagnostic, not corridor ground truth.
    assert python['route_quality']['synthetic_bridge_count'] == 5
    assert response.headers['X-GIS4L-Data-Version'] == python['metadata']['data_version']
