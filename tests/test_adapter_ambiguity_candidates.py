"""Committed administrative ambiguity must survive all public bridges."""
import json
import os
import subprocess
import sys

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from server.routers.admin import unique_district_row
from server.services.error_contract import route_error_contract
from server.services.exceptions import AmbiguousLocationError


@pytest.mark.parametrize(('codes', 'dtype', 'expected'), [
    ([406, 30], 'object', [406, 30]),
    ([406.0, 30.0, None], 'float64', [406, 30, None]),
    ([406, pd.NA, 30], 'Int64', [406, None, 30]),
    ([406.0, None, pd.NA, float('nan'), 30], 'object', [406, None, None, None, 30]),
])
def test_ambiguity_codes_normalized_after_record_conversion(codes, dtype, expected):
    rows = pd.DataFrame({
        'state': [f'State {i}' for i in range(len(codes))],
        'district': ['Bilaspur'] * len(codes),
        'district_code': pd.Series(codes, dtype=dtype),
        'unrelated': ['private'] * len(codes),
    })
    before = rows.copy(deep=True)
    with pytest.raises(HTTPException) as caught:
        unique_district_row(rows)
    assert caught.value.status_code == 409
    detail = caught.value.detail
    assert set(detail) == {'message', 'candidates'}
    assert detail['message'] == 'Ambiguous district data; specify state or correct duplicate records'
    records = detail['candidates']
    assert records == [
        {'state': f'State {i}', 'district': 'Bilaspur', 'district_code': code}
        for i, code in enumerate(expected)
    ]
    assert all(record['district_code'] is None or type(record['district_code']) is int
               for record in records)
    assert route_error_contract(AmbiguousLocationError('ambiguous', candidates=records))['candidates'] == records
    json.dumps(records, allow_nan=False)
    pd.testing.assert_frame_equal(rows, before)


@pytest.mark.parametrize('code', [
    True, False, '406', '406.0', '', 406.5, float('inf'), float('-inf'), -406.0, 0.0, -406, 0,
])
def test_malformed_ambiguity_codes_are_not_legitimized(code):
    rows = pd.DataFrame({
        'state': ['Invalid state', 'Valid state'],
        'district': ['Bilaspur', 'Bilaspur'],
        'district_code': pd.Series([code, 30], dtype=object),
    })
    with pytest.raises(HTTPException) as caught:
        unique_district_row(rows)
    assert caught.value.status_code == 409
    records = caught.value.detail['candidates']
    assert type(records[0]['district_code']) is type(code)
    assert records[0]['district_code'] == code
    assert records[1] == {'state': 'Valid state', 'district': 'Bilaspur', 'district_code': 30}
    assert type(records[1]['district_code']) is int
    # The frozen shared contract, not the source fix, withholds malformed data.
    assert route_error_contract(AmbiguousLocationError('ambiguous', candidates=records))['candidates'] == records[1:]


def test_committed_bilaspur_candidates_across_surfaces(monkeypatch):
    from gisindia4logistics import sdk
    from mcp_server import server as rpc, tools
    from server.app import app
    from server.dependencies import get_data_store

    store = get_data_store()
    monkeypatch.setattr(sdk, 'get_data_store', lambda: store)
    monkeypatch.setattr(tools, 'store', store)
    monkeypatch.setattr(app, 'dependency_overrides', {get_data_store: lambda: store})
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/api/v1/admin/districts/Bilaspur')
    assert response.status_code == 409
    with pytest.raises(AmbiguousLocationError) as caught:
        sdk.get_district('Bilaspur')
    reply = rpc.handle_rpc_request(dict(jsonrpc='2.0', id=1, method='tools/call',
        params=dict(name='gis_get_district_scorecard',
                    arguments=dict(district_name_or_code='Bilaspur'))))
    assert reply['result']['isError']
    mcp = json.loads(reply['result']['content'][0]['text'])
    candidates = [response.json()['detail']['candidates'], caught.value.candidates, mcp['candidates']]
    for values in candidates:
        assert len(values) == 2
        assert {value['district_code'] for value in values} == {406, 30}
        assert {value['state'].lower() for value in values} == {'chhattisgarh', 'himachal pradesh'}
        assert all(type(value['district_code']) is int for value in values)
        assert all(value['district'].lower() == 'bilaspur' for value in values)
        assert all(set(value) == {'state', 'district', 'district_code'} for value in values)
    assert all(values == candidates[0] for values in candidates)
    json.dumps(candidates, allow_nan=False)


def test_committed_cli_ambiguity_is_safe(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['GIS4LOGISTICS_CACHE_PATH'] = str(tmp_path / 'cache')
    env['GIS4LOGISTICS_OUTPUT_PATH'] = str(tmp_path / 'outputs')
    result = subprocess.run([sys.executable, '-B', '-m', 'gisindia4logistics.cli',
        'district', 'Bilaspur'], env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 2
    assert result.stdout == ''
    assert 'error: Location is ambiguous; supply distinguishing identifiers.' in result.stderr
    assert 'Traceback' not in result.stderr
    assert 'C:/secret' not in result.stderr
