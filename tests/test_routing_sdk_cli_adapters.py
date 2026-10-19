"""Public SDK/CLI routing and safe legacy bridge regressions."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from gisindia4logistics import cli, sdk
from server.models.phase1 import CoordinateLocation, DistrictLocation, HubLocation, VehicleType
from server.services import routing_facade as facade
from server.services.error_contract import domain_error_from_status, route_error_contract
from server.services.exceptions import (
    AmbiguousLocationError, DataIntegrityError, InvalidLocationError,
    LocationNotFoundError, RouteNotAvailableError,
)
from server.services.routing_service import RoutingService
from tests import test_routing_facade as facade_tests

synthetic = facade_tests.synthetic


def coords(ref):
    return (ref.latitude, ref.longitude)


@pytest.mark.parametrize('vehicle', [v.value for v in VehicleType])
@pytest.mark.parametrize('detail,include', [('none', True), ('full', True), ('simplified', True), ('full', False)])
def test_one_engine_and_geometry(synthetic, vehicle, detail, include):
    store, refs = synthetic
    original = RoutingService.calculate_highway_route
    calls = []
    def spy(self, **kwargs):
        calls.append(kwargs)
        return original(self, **kwargs)
    with patch.object(facade, 'get_data_store', return_value=store), patch.object(RoutingService, 'calculate_highway_route', spy):
        rich = sdk.plan_highway_route(refs[0], refs[1].model_dump(), vehicle, include_route_geometry=include, geometry_detail=detail)
        assert len(calls) == 1
        assert calls[0]['include_route_geometry'] is include
        legacy = sdk.route_highway(coords(refs[0]), coords(refs[1]), vehicle)
        assert len(calls) == 2
    assert (rich['route_geometry'] is not None) == (include and detail != 'none')
    assert legacy['distance_km'] == round(rich['distance_km'], 1)
    assert legacy['drive_time_hours'] == round(rich['drive_time_hours'], 2)
    assert legacy['route_quality']['warnings'] == rich['route_quality']['warnings']
    json.dumps(rich, allow_nan=False)
    json.dumps(legacy, allow_nan=False)


@pytest.mark.parametrize('bad', ['string', 'bool', 'unknown', 'mutated', 'nested_mutated'])
def test_strict_before_store(bad):
    ref = CoordinateLocation(latitude=22, longitude=78)
    origin = ref.model_dump()
    if bad == 'string':
        origin['latitude'] = '22'
    if bad == 'bool':
        origin['latitude'] = True
    if bad == 'unknown':
        origin['extra'] = 0
    if bad == 'mutated':
        ref.latitude = '22'
        origin = ref
    if bad == 'nested_mutated':
        ref.latitude = True
        origin = {'type': 'coordinate', 'latitude': ref.latitude, 'longitude': 78}
    with patch.object(facade, 'get_data_store', side_effect=AssertionError('store acquired')):
        with pytest.raises(ValidationError):
            sdk.plan_highway_route(origin, {'type': 'coordinate', 'latitude': 22, 'longitude': 78})


def test_exports_identity():
    # Do not cache legacy bindings to a fixture-monkeypatched SDK in this process.
    result = subprocess.run([sys.executable, '-c',
        'import gisindia4logistics as c; import gis4logistics as l; '
        'from gisindia4logistics import sdk; from gis4logistics import sdk as old; '
        'assert c.plan_highway_route is l.plan_highway_route is old.plan_highway_route is sdk.plan_highway_route'],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('status', [400, 409, 422, 404, 503, 500])
@pytest.mark.parametrize('name,args,target', [
    ('get_district', ('Pune',), 'get_district_scorecard'),
    ('calculate_freight_cost', ('Pune',), 'simulate_freight_cost'),
    ('find_nearest', (22, 78), 'get_nearest_hubs'),
    ('simulate_port_catchment', (), 'simulate_port_gravity'),
])
def test_http_bridge(status, name, args, target):
    with patch.object(sdk, 'get_data_store', return_value=object()), patch.object(sdk, target, side_effect=HTTPException(status, detail='C:/private/source.csv')):
        with pytest.raises(type(domain_error_from_status(status))) as exc:
            getattr(sdk, name)(*args)
    assert not isinstance(exc.value, HTTPException)
    assert route_error_contract(exc.value) == route_error_contract(domain_error_from_status(status))
    assert 'private' not in str(exc.value)


def test_ambiguity_candidates():
    candidates = [{'district': 'Pune', 'district_code': 521, 'path': 'C:/private'}, {'district_code': True}]
    with patch.object(sdk, 'get_data_store', return_value=object()), patch.object(sdk, 'get_district_scorecard', side_effect=HTTPException(409, detail={'candidates': candidates, 'message': 'private'})):
        with pytest.raises(AmbiguousLocationError) as exc:
            sdk.get_district('Pune')
    assert exc.value.candidates == [{'district': 'Pune', 'district_code': 521}]


@pytest.mark.parametrize('field', ['distance_km', 'latitude'])
def test_nearest_calculated_nonfinite(field):
    with patch.object(sdk, 'get_data_store', return_value=object()), patch.object(sdk, 'get_nearest_hubs', return_value={'ports': [{field: float('nan')}]}):
        with pytest.raises(ValueError):
            sdk.find_nearest(22, 78)


def test_committed_nearest_strict_json():
    json.dumps(sdk.find_nearest(28.6139, 77.2090, 2), allow_nan=False)


@pytest.mark.parametrize('error,code', [
    (RouteNotAvailableError('C:/private'), 2), (DataIntegrityError('C:/private'), 1),
    (ValueError('C:/private'), 1), (RuntimeError('C:/private'), 1),
    (InvalidLocationError('C:/private'), 2), (LocationNotFoundError('C:/private'), 2),
    (AmbiguousLocationError('C:/private'), 2),
    (ValidationError.from_exception_data('C:/private', []), 2),
])
@pytest.mark.parametrize('command', ['route-plan', 'route', 'cost', 'district'])
def test_cli_error_matrix(monkeypatch, capsys, error, code, command):
    target, args = {
        'route-plan': ('plan_highway_route', ['--origin', '{"type":"district","district_code":521}', '--dest', '{"type":"hub","hub_type":"port","name":"JNPT"}']),
        'route': ('route_highway', ['--origin', '22,78', '--dest', '22,78']),
        'cost': ('calculate_freight_cost', ['--origin', 'Pune']),
        'district': ('get_district', ['Pune']),
    }[command]
    monkeypatch.setattr(sys, 'argv', ['gisindia4logistics', command, *args])
    with patch.object(sdk, target, side_effect=error), pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == code
    captured = capsys.readouterr()
    assert route_error_contract(error)['message'] in captured.err
    assert not captured.out
    assert 'Traceback' not in captured.err and 'C:/' not in captured.err


@pytest.mark.parametrize('vehicle', [v.value for v in VehicleType])
@pytest.mark.parametrize('detail,include', [('none', True), ('full', True), ('simplified', True), ('full', False)])
def test_cli_rich_exact_payload(synthetic, monkeypatch, capsys, vehicle, detail, include):
    store, refs = synthetic
    args = ['gisindia4logistics', 'route-plan', '--origin', json.dumps(refs[0].model_dump()), '--dest', json.dumps(refs[1].model_dump()), '--vehicle', vehicle, '--geometry', detail]
    if not include:
        args.append('--no-geometry')
    monkeypatch.setattr(sys, 'argv', args)
    with patch.object(facade, 'get_data_store', return_value=store):
        expected = sdk.plan_highway_route(*refs, vehicle, include_route_geometry=include, geometry_detail=detail)
        cli.main()
    actual = json.loads(capsys.readouterr().out)
    for result in (actual, expected):
        result['metadata'].pop('generated_at_utc')
    assert actual == expected


@pytest.mark.parametrize('origin', ['{bad', '{"type":"coordinate","latitude":NaN,"longitude":78}', '{"type":"coordinate","latitude":"22","longitude":78}', '{"type":"coordinate","latitude":true,"longitude":78}'])
def test_subprocess_invalid_input(origin):
    env = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME'}}
    result = subprocess.run([sys.executable, '-m', 'gisindia4logistics.cli', 'route-plan', '--origin', origin, '--dest', '{"type":"coordinate","latitude":22,"longitude":78}'], capture_output=True, text=True, env=env)
    assert result.returncode == 2
    assert 'Request input is invalid.' in result.stderr
    assert not result.stdout and 'Traceback' not in result.stderr and 'C:/' not in result.stderr


def test_committed_pune_jnpt_parity_fresh_timestamp():
    before = datetime.now(timezone.utc)
    rich = sdk.plan_highway_route({'type': 'district', 'district_code': 521}, {'type': 'hub', 'hub_type': 'port', 'name': 'JNPT'})
    legacy = sdk.route_highway((rich['origin']['latitude'], rich['origin']['longitude']), (rich['destination']['latitude'], rich['destination']['longitude']))
    after = datetime.now(timezone.utc)
    for result in (rich, legacy):
        timestamp = datetime.fromisoformat(result['metadata']['generated_at_utc'].replace('Z', '+00:00'))
        assert before <= timestamp <= after
    assert rich['metadata']['generated_at_utc'] != legacy['metadata']['generated_at_utc']
    assert legacy['distance_km'] == round(rich['distance_km'], 1)
    assert legacy['drive_time_hours'] == rich['drive_time_hours']
    assert legacy['route_quality'] == rich['route_quality']


@pytest.mark.parametrize('vehicle', [v.value for v in VehicleType])
def test_legacy_cli_old_flags_and_truthful_labels(synthetic, monkeypatch, capsys, vehicle):
    store, refs = synthetic
    monkeypatch.setattr(sys, 'argv', ['gisindia4logistics', 'route', '--origin',
        ','.join(map(str, coords(refs[0]))), '--dest', ','.join(map(str, coords(refs[0]))), '--vehicle', vehicle])
    with patch.object(facade, 'get_data_store', return_value=store):
        cli.main()
    output = capsys.readouterr().out
    assert 'distance-based heuristic' in output
    assert 'plazas encountered' not in output and 'FASTag' not in output
    assert 'Routing Scope' in output and 'Route Quality' in output
    assert 'Zero movement' in output and vehicle in output


def test_sdk_defaults_and_no_http_or_calculation_reclassification(synthetic):
    store, refs = synthetic
    with patch.object(facade, 'get_data_store', return_value=store), patch.object(
        RoutingService, 'calculate_highway_route', wraps=facade._service_for_store(store).calculate_highway_route
    ) as engine:
        result = sdk.plan_highway_route(*refs)
    assert engine.call_count == 1
    assert engine.call_args.kwargs['include_route_geometry'] is True
    assert engine.call_args.kwargs['geometry_detail'] == 'none'
    assert engine.call_args.kwargs['vehicle_type'] == VehicleType.MAV_20T
    assert result['route_geometry'] is None
    for function, args, target in [
        (sdk.plan_highway_route, refs, 'plan_highway_route'),
        (sdk.route_highway, [coords(r) for r in refs], 'legacy_highway_route'),
    ]:
        for error in [DataIntegrityError('private'), RouteNotAvailableError('private'), ValueError('private')]:
            with patch.object(facade, target, side_effect=error), pytest.raises(type(error)) as exc:
                function(*args)
            assert exc.value is error


@pytest.mark.parametrize('distance,count', [(0, 0), (39, 0), (40, 1), (65, 1), (130, 2)])
@pytest.mark.parametrize('vehicle', [v.value for v in VehicleType])
def test_sdk_legacy_precision_and_rates(synthetic, distance, count, vehicle):
    store, refs = synthetic
    result = facade.plan_highway_route({'origin': refs[0], 'destination': refs[1]}, store)
    result.distance_km = distance
    result.drive_time_hours = 1.23456
    with patch.object(facade, 'get_data_store', return_value=store), patch.object(
        RoutingService, 'calculate_highway_route', return_value=result
    ) as engine:
        legacy = sdk.route_highway(*[coords(r) for r in refs], vehicle)
    assert engine.call_count == 1
    assert legacy['distance_km'] == round(distance, 1)
    assert legacy['drive_time_hours'] == 1.23
    assert legacy['tolls_encountered_count'] == count
    rate = 340 if vehicle == 'MAV_20T' else 120 if vehicle == 'LMV' else 220
    assert legacy['estimated_toll_cost_inr'] == count * rate
    assert legacy['origin_snapped'] == [result.origin_snapped.latitude, result.origin_snapped.longitude]


@pytest.mark.parametrize('status,code', [(400, 2), (409, 2), (422, 2), (404, 2), (503, 1), (500, 1)])
def test_subprocess_exit_matrix(status, code):
    script = (
        'import sys; from unittest.mock import patch; from fastapi import HTTPException; '
        'from gisindia4logistics import cli, sdk; '
        'sys.argv=["gisindia4logistics","district","Pune"]; '
        f'ctx=patch.object(sdk,"get_district_scorecard",side_effect=HTTPException({status},detail="C:/private")); '
        'ctx.start(); patch.object(sdk,"get_data_store",return_value=object()).start(); cli.main()'
    )
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == code
    assert route_error_contract(domain_error_from_status(status))['message'] in result.stderr
    assert not result.stdout and 'Traceback' not in result.stderr and 'C:/' not in result.stderr


def test_subprocess_rich_committed_payload():
    origin = {'type': 'district', 'district_code': 521}
    destination = {'type': 'hub', 'hub_type': 'port', 'name': 'JNPT'}
    result = subprocess.run([sys.executable, '-m', 'gisindia4logistics.cli', 'route-plan',
        '--origin', json.dumps(origin), '--dest', json.dumps(destination)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    expected = sdk.plan_highway_route(origin, destination)
    for payload in (actual, expected):
        payload['metadata'].pop('generated_at_utc')
    assert actual == expected


def test_nearest_observations_and_only_http_caught():
    with patch.object(sdk, 'get_data_store', return_value=object()), patch.object(
        sdk, 'get_nearest_hubs', return_value={'ports': [{'name': float('nan'), 'distance_km': 1.2}]}
    ):
        assert sdk.find_nearest(22, 78) == {'ports': [{'name': None, 'distance_km': 1.2}]}
    error = ValueError('calculated failure')
    with patch.object(sdk, 'get_data_store', return_value=object()), patch.object(
        sdk, 'simulate_freight_cost', side_effect=error
    ), pytest.raises(ValueError) as exc:
        sdk.calculate_freight_cost('Pune')
    assert exc.value is error


def test_no_router_engine_import_and_mcp_command_stays_lazy():
    import ast
    from pathlib import Path
    sdk_tree = ast.parse(Path(sdk.__file__).read_text(encoding='utf-8'))
    assert not any(isinstance(node, ast.ImportFrom) and node.module in {
        'server.routers.routing', 'server.services.routing_service'
    } for node in ast.walk(sdk_tree))
    tree = ast.parse(Path(cli.__file__).read_text(encoding='utf-8'))
    command = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'cmd_mcp')
    imports = [node.module for node in ast.walk(command) if isinstance(node, ast.ImportFrom)]
    assert imports == ['mcp_server.server']


@pytest.mark.parametrize('package', ['gisindia4logistics', 'gis4logistics'])
def test_mcp_cli_handshake_does_not_import_sdk(package):
    script = (
        'import sys, types, runpy; '
        'fake=types.ModuleType("mcp_server.server"); '
        'fake.run_stdio_server=lambda: print("handshake sentinel"); '
        'sys.modules["mcp_server.server"]=fake; '
        f'sys.argv=["{package}","mcp"]; runpy.run_module("{package}.cli",run_name="__main__"); '
        'assert "gisindia4logistics.sdk" not in sys.modules'
    )
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'handshake sentinel\n'


@pytest.mark.parametrize('overrides', [
    {'include_route_geometry': 1}, {'include_route_geometry': 'false'},
    {'geometry_detail': 'invalid'}, {'vehicle_type': 'invalid'},
])
def test_rich_controls_strict_before_store(overrides):
    ref = {'type': 'coordinate', 'latitude': 22, 'longitude': 78}
    with patch.object(facade, 'get_data_store', side_effect=AssertionError('early store')):
        with pytest.raises(ValidationError):
            sdk.plan_highway_route(ref, ref, **overrides)


@pytest.mark.parametrize('kind', ['district', 'hub', 'coordinate'])
@pytest.mark.parametrize('value', [True, '521', float('nan')])
def test_mutable_typed_nested_revalidation(kind, value):
    if kind == 'district':
        ref = DistrictLocation(district_code=521)
        ref.district_code = value
    elif kind == 'hub':
        ref = HubLocation(hub_type='port', name='JNPT')
        if isinstance(value, str):
            ref.hub_type = value
        else:
            ref.name = value
    else:
        ref = CoordinateLocation(latitude=22, longitude=78)
        ref.latitude = value
    with patch.object(facade, 'get_data_store', side_effect=AssertionError('early store')):
        with pytest.raises(ValidationError):
            sdk.plan_highway_route(ref, {'type': 'coordinate', 'latitude': 22, 'longitude': 78})


@pytest.mark.parametrize('origin', [(22, 78, 0), (True, 78), ('22', 78), (float('inf'), 78)])
def test_legacy_strict_before_store(origin):
    with patch.object(facade, 'get_data_store', side_effect=AssertionError('early store')):
        with pytest.raises(ValidationError):
            sdk.route_highway(origin, (22, 78))


@pytest.mark.parametrize('surface', ['rich', 'legacy'])
def test_calculated_route_nonfinite_not_success(synthetic, surface):
    store, refs = synthetic
    result = facade.plan_highway_route({'origin': refs[0], 'destination': refs[1]}, store)
    result.distance_km = float('nan')
    with patch.object(facade, 'get_data_store', return_value=store), patch.object(
        RoutingService, 'calculate_highway_route', return_value=result
    ), pytest.raises(ValueError):
        if surface == 'rich':
            sdk.plan_highway_route(*refs)
        else:
            sdk.route_highway(*[coords(r) for r in refs])
