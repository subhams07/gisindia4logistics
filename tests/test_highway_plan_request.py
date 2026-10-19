"""Strict shared rich request contract for future transport adapters."""
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from server.models.phase1 import CoordinateLocation, HighwayRoutePlanRequest, VehicleType


def payload():
    return dict(origin=dict(type="coordinate", latitude=22, longitude=78),
                destination=dict(type="district", district_code=521))


@pytest.mark.parametrize("vehicle", [v.value for v in VehicleType])
@pytest.mark.parametrize("transport", ["mapping", "json", "adapter"])
def test_valid_transport(vehicle, transport):
    data = payload() | {"vehicle_type": vehicle}
    if transport == "json":
        request = HighwayRoutePlanRequest.model_validate_json(json.dumps(data))
    elif transport == "adapter":
        request = TypeAdapter(HighwayRoutePlanRequest).validate_python(data)
    else:
        request = HighwayRoutePlanRequest.model_validate(data)
    assert request.vehicle_type.value == vehicle
    assert request.geometry_detail == "none"
    assert request.include_route_geometry is True


@pytest.mark.parametrize("transport", ["mapping", "json"])
@pytest.mark.parametrize("field,value", [("latitude", "22"), ("latitude", True),
                                          ("longitude", False), ("latitude", float("nan"))])
def test_strict_coordinates(field, value, transport):
    data = payload()
    data["origin"][field] = value
    with pytest.raises(ValidationError):
        if transport == "json":
            HighwayRoutePlanRequest.model_validate_json(json.dumps(data))
        else:
            HighwayRoutePlanRequest.model_validate(data)


@pytest.mark.parametrize("value", [True, "521", 521.0])
def test_strict_code(value):
    data = payload()
    data["destination"]["district_code"] = value
    with pytest.raises(ValidationError):
        HighwayRoutePlanRequest.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("value", ["true", "false", 1, 0])
def test_strict_switch(value):
    with pytest.raises(ValidationError):
        HighwayRoutePlanRequest.model_validate(payload() | {"include_route_geometry": value})


@pytest.mark.parametrize("where", ["top", "origin", "destination", "hub", "discriminator"])
def test_unknown_and_missing_tag(where):
    data = payload()
    if where == "top":
        data["unknown"] = 1
    elif where == "discriminator":
        del data["origin"]["type"]
    elif where == "hub":
        data["origin"] = dict(type="hub", hub_type="port", name="JNPT", unknown=1)
    else:
        data[where]["unknown"] = 1
    with pytest.raises(ValidationError):
        HighwayRoutePlanRequest.model_validate_json(json.dumps(data))


def test_typed_objects_and_discriminated_schema():
    loc = CoordinateLocation(latitude=22, longitude=78)
    req = HighwayRoutePlanRequest(origin=loc, destination=loc)
    assert req.origin is loc
    schema = req.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["origin"]["discriminator"]["propertyName"] == "type"
    assert set(schema["$defs"]["VehicleType"]["enum"]) == {v.value for v in VehicleType}
    assert all(schema["$defs"][name]["additionalProperties"] is False
               for name in ("CoordinateLocation", "DistrictLocation", "HubLocation"))


@pytest.mark.parametrize("vehicle", ["TRUCK", "lmv", 1, True, None])
def test_invalid_vehicle(vehicle):
    with pytest.raises(ValidationError):
        HighwayRoutePlanRequest.model_validate_json(json.dumps(payload() | {"vehicle_type": vehicle}))


def test_adapter_json_and_hub_valid():
    data = payload()
    data["destination"] = dict(type="hub", hub_type="port", name="JNPT")
    request = TypeAdapter(HighwayRoutePlanRequest).validate_json(json.dumps(data))
    assert request.destination.name == "JNPT"
