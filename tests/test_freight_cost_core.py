"""W2-a pure foundation gate; deliberately not a live/batch integration claim."""
from dataclasses import asdict, fields, replace
import inspect
import math
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts.analyze import freight_cost_core as core
from scripts.analyze import intermodal_cost_engine as engine
from scripts.clean.standardize import clean_state_name
from server.models.schemas import FreightCostSimulationRequest
from server.routers import simulation


DEFAULTS = {
    "road_linehaul_rate": 3.30, "road_handling_cost": 140.0,
    "toll_cost_per_plaza": 340.0, "truck_payload_tons": 20.0,
    "toll_spacing_km": 65.0, "rail_base_class_rate": 1.55,
    "rail_first_mile_rate": 4.20, "rail_first_mile_handling": 120.0,
    "rail_handling_and_siding": 220.0, "rail_commercial_speed_kmh": 25.0,
    "rail_yard_detention_hours": 12.0, "rail_circuity_factor": 1.08,
    "dfc_linehaul_rate": 1.12, "dfc_handling_cost": 180.0,
    "dfc_commercial_speed_kmh": 60.0, "dfc_yard_transfer_hours": 3.0,
    "dfc_circuity_factor": 1.02, "inventory_holding_rate": 7.50,
}
POSITIVE = {"truck_payload_tons", "toll_spacing_km", "rail_commercial_speed_kmh",
            "dfc_commercial_speed_kmh", "rail_circuity_factor", "dfc_circuity_factor"}


def calculate(**kwargs):
    return core.calculate_unit_costs(**{
        "road_km": 150.0, "drive_hours": 3.0, "terminal_km": 30.0,
        "state": "Maharashtra", **kwargs,
    })


def zero_parameters(**kwargs):
    return core.FreightCostParameters(**{
        **{key: 0.0 for key in DEFAULTS if key not in POSITIVE}, **kwargs,
    })


def test_import_provenance_reexports_defaults_and_constructor_shape():
    root = Path(__file__).resolve().parents[1]
    for module in (core, engine, simulation):
        assert Path(module.__file__).resolve().is_relative_to(root)
    assert engine.FreightCostParameters is core.FreightCostParameters
    assert engine.ir_telescopic_freight_rate is core.ir_telescopic_freight_rate
    assert asdict(core.FreightCostParameters()) == DEFAULTS
    signature = inspect.signature(core.FreightCostParameters)
    assert list(signature.parameters) == list(DEFAULTS)
    assert all(p.default == DEFAULTS[name] for name, p in signature.parameters.items())
    assert asdict(core.FreightCostParameters(*DEFAULTS.values())) == DEFAULTS
    tariff = inspect.signature(core.ir_telescopic_freight_rate)
    assert list(tariff.parameters) == ["distance_km", "base_class_rate"]
    assert tariff.parameters["base_class_rate"].default == 1.55


# Frozen outputs from the pre-relocation live implementation with only its
# transport round() bypassed. Not an oracle copied from the new core formula.
@pytest.mark.parametrize("inputs,costs,times,winner,savings,pct,marker", [
    ((150., 3., 30., "Maharashtra"), (696.7307692307693, 919.5600000000001, 638.985),
     (3., 18.48, 5.55), "Dedicated Freight Corridor (DFC)", 57.74576923076927, 8.288103781396638, 210.),
    ((1250., 20., None, "Gujarat"), (4741.923076923077, 3040.625, 2119.875),
     (20., 66., 24.25), "Dedicated Freight Corridor (DFC)", 2622.048076923077, 55.295036093762675, 210.),
    ((450., 7., 150., "Tamil Nadu"), (1795.1923076923076, 1863.18, None),
     (7., 31.44, None), "Road Trucking", 0., 0., 380.),
    ((0., 0., None, "Bihar"), (140., 640., 532.5),
     (0., 12., 3.), "Road Trucking", 0., 0., 210.),
])
def test_frozen_live_defaults(inputs, costs, times, winner, savings, pct, marker):
    result = core.calculate_unit_costs(*inputs)
    for mode, cost, time in zip((result.road, result.conventional_rail, result.dfc_rail), costs, times):
        if cost is None:
            assert mode is None
        else:
            assert mode.cost_per_ton_inr == cost
            assert mode.transit_time_hours == time
    assert result.optimal_mode == winner
    assert result.modal_shift_savings_per_ton_inr == savings
    assert result.modal_shift_savings_pct == pct
    assert result.break_even_distance_km == marker


@pytest.mark.parametrize("state", [" maharashtra ", "Tamil Nadu", "Orissa"])
@pytest.mark.parametrize("terminal", [None, float("nan"), 0., 30., 100., 150.])
@pytest.mark.parametrize("overrides", [{}, {"road_linehaul_rate": 2.5, "truck_payload_tons": 30.,
                                          "rail_base_class_rate": 1.7, "inventory_holding_rate": 8.}])
def test_existing_live_synthetic_store_comparison(state, terminal, overrides, monkeypatch):
    store = SimpleNamespace(travel_time_df=pd.DataFrame([{
        "district": "Synthetic", "state": state, "nearest_port_name": "Test",
        "port_road_distance_km": 725.1234, "port_drive_time_hours": 11.2345,
        "freight_terminal_road_distance_km": terminal,
    }]))
    # Bypass rounding only; the actual pre-existing live calculation is intact.
    monkeypatch.setattr(simulation, "round", lambda value, digits: value, raising=False)
    live = simulation.simulate_freight_cost(FreightCostSimulationRequest(
        origin_district="Synthetic", payload_tons=37., custom_parameters=overrides), store)
    result = core.calculate_unit_costs(725.1234, 11.2345, terminal,
                                       core.normalize_freight_state(state), overrides)
    shipment = core.scale_shipment(result, 37.)
    for name in ("road", "conventional_rail", "dfc_rail"):
        mode = getattr(result, name)
        if mode is None:
            assert live[name] is None
            assert getattr(shipment, name) is None
        else:
            assert mode.cost_per_ton_inr == live[name].cost_per_ton_inr
            assert mode.transit_time_hours == live[name].transit_time_hours
            assert getattr(shipment, name) == live[name].total_shipment_cost_inr
    for name in ("optimal_mode", "modal_shift_savings_per_ton_inr", "modal_shift_savings_pct",
                 "break_even_distance_km"):
        assert getattr(result, name) == live[name]
    assert shipment.modal_shift_savings_total_inr == live["modal_shift_savings_total_inr"]


@pytest.mark.parametrize("distance,expected", [
    (0., 0.), (99., 207.1575), (100., 209.25), (101., 210.955),
    (499., 889.545), (500., 891.25), (501., 892.645),
    (999., 1587.355), (1000., 1588.75), (1001., 1589.9125),
])
def test_tariff_breakpoints(distance, expected):
    assert core.ir_telescopic_freight_rate(distance) == pytest.approx(expected)
    assert core.ir_telescopic_freight_rate(distance, 3.1) == pytest.approx(expected * 2)


def test_legacy_tariff_is_relocated_not_revalidated():
    assert core.ir_telescopic_freight_rate(-1.) == -2.0925000000000002
    assert math.isnan(core.ir_telescopic_freight_rate(float("nan")))


@pytest.mark.parametrize("field", list(DEFAULTS))
@pytest.mark.parametrize("value", ["3.5", True, False, float("nan"), float("inf"), -float("inf"), -1., None])
def test_mutated_dataclass_validated_at_boundary(field, value):
    params = core.FreightCostParameters()
    setattr(params, field, value)
    with pytest.raises(core.ParameterValidationError) as exc:
        calculate(parameters=params)
    assert exc.value.category == "parameters"


@pytest.mark.parametrize("field", sorted(POSITIVE))
def test_positive_fields_reject_zero(field):
    with pytest.raises(core.ParameterValidationError):
        calculate(parameters={field: 0.})


@pytest.mark.parametrize("field", sorted(set(DEFAULTS) - POSITIVE))
def test_nondivisor_zero_accepted(field):
    calculate(parameters={field: 0.})


def test_none_overrides_unknown_keys_and_parameter_type():
    overrides = {key: None for key in DEFAULTS}
    assert calculate(parameters=overrides) == calculate()
    assert overrides == dict.fromkeys(DEFAULTS)
    assert calculate(parameters={"road_linehaul_rate": None, "road_handling_cost": 0.}).road.cost_per_ton_inr == calculate().road.cost_per_ton_inr - 140.
    for invalid in ({"unknown": None}, {"unknown": 1.}, [], "parameters"):
        with pytest.raises(core.ParameterValidationError):
            calculate(parameters=invalid)
    with pytest.raises(TypeError):
        core.FreightCostParameters(unknown=1.)


@pytest.mark.parametrize("field", ["road_km", "drive_hours", "terminal_km"])
@pytest.mark.parametrize("value", ["1", True, False, float("inf"), -float("inf"), -1., 1j])
def test_source_corruption_is_not_fallback(field, value):
    with pytest.raises(core.SourceValidationError) as exc:
        calculate(**{field: value})
    assert exc.value.category == "source"


@pytest.mark.parametrize("field", ["road_km", "drive_hours"])
@pytest.mark.parametrize("value", [None, float("nan")])
def test_required_source_missing_is_invalid(field, value):
    with pytest.raises(core.SourceValidationError):
        calculate(**{field: value})


@pytest.mark.parametrize("terminal,used,fallback", [
    (None, 50., True), (float("nan"), 50., True), (0., 0., False),
    (50., 50., False), (99.5, 99.5, False), (100., 100., False), (150., 100., False),
])
def test_terminal_observation_rule(terminal, used, fallback):
    result = calculate(terminal_km=terminal)
    assert result.first_mile_km == used
    assert result.first_mile_fallback_used is fallback
    assert result.conventional_rail.cost_per_ton_inr == calculate(terminal_km=used).conventional_rail.cost_per_ton_inr


@pytest.mark.parametrize("raw", [" maharashtra ", "Orissa", "NCT of Delhi", "Tamil Nadu"])
def test_existing_helper_normalization_and_canonical_boundary(raw):
    state = core.normalize_freight_state(raw)
    assert state == clean_state_name(raw)
    result = calculate(state=state)
    assert (result.dfc_rail is not None) == (state == "Maharashtra")
    if raw != state:
        with pytest.raises(core.SourceValidationError):
            calculate(state=raw)


@pytest.mark.parametrize("state", [None, "", " ", "Atlantis", 123, True])
def test_invalid_state_rejected(state):
    with pytest.raises(core.SourceValidationError):
        core.normalize_freight_state(state)
    with pytest.raises(core.SourceValidationError):
        calculate(state=state)


@pytest.mark.parametrize("state", ["Uttar Pradesh", "Haryana", "Rajasthan", "Gujarat", "Maharashtra",
                                   "Punjab", "Bihar", "Jharkhand", "West Bengal"])
def test_dfc_eligibility_and_markers(state):
    assert calculate(state=state).dfc_rail is not None
    assert calculate(state=state).break_even_distance_km == 210.


def test_unavailable_dfc_is_none_not_nan():
    result = calculate(state="Odisha")
    assert result.dfc_rail is None
    assert core.scale_shipment(result, 20.).dfc_rail is None
    assert result.break_even_distance_km == 380.


def test_exact_ties_and_zero_baseline():
    result = calculate(parameters=zero_parameters())
    assert result.optimal_mode == "Road Trucking"
    assert result.road.cost_per_ton_inr == result.conventional_rail.cost_per_ton_inr == result.dfc_rail.cost_per_ton_inr == 0.
    assert result.modal_shift_savings_pct == result.modal_shift_savings_per_ton_inr == 0.
    assert calculate(parameters=zero_parameters(road_handling_cost=1.)).optimal_mode == "Conventional Rail"
    assert calculate(parameters=zero_parameters(road_handling_cost=1., rail_handling_and_siding=1.)).optimal_mode == "Dedicated Freight Corridor (DFC)"
    assert core.scale_shipment(result, 1e308).modal_shift_savings_total_inr == 0.


def test_zero_distance_and_movement_costs():
    result = calculate(road_km=0., drive_hours=0., terminal_km=0.)
    assert result.road.cost_per_ton_inr == 140.
    assert result.conventional_rail.distance_km == result.dfc_rail.distance_km == 0.
    assert result.road.transit_time_hours == 0.
    zero = calculate(parameters=zero_parameters(road_handling_cost=2., rail_handling_and_siding=3., dfc_handling_cost=4.))
    assert (zero.road.cost_per_ton_inr, zero.conventional_rail.cost_per_ton_inr, zero.dfc_rail.cost_per_ton_inr) == (2., 3., 4.)


def test_shipment_scaling_capacity_and_continuous_tolls():
    unit = calculate()
    small, large = core.scale_shipment(unit, 20.), core.scale_shipment(unit, 40.)
    for name in ("road", "conventional_rail", "dfc_rail", "modal_shift_savings_total_inr"):
        assert getattr(large, name) == 2 * getattr(small, name)
    capacity = calculate(parameters={"truck_payload_tons": 40.})
    assert capacity.conventional_rail == unit.conventional_rail
    assert capacity.dfc_rail == unit.dfc_rail
    assert unit.road.cost_per_ton_inr - capacity.road.cost_per_ton_inr == pytest.approx(150 / 65 * 340 / 40)
    assert calculate(road_km=32.5, drive_hours=0., parameters=zero_parameters(toll_cost_per_plaza=340.)).road.cost_per_ton_inr == 8.5


def test_configurable_circuity_and_no_rounding():
    result = calculate(road_km=100.12345, parameters={"rail_circuity_factor": 2., "dfc_circuity_factor": 3.})
    assert result.conventional_rail.distance_km == 200.2469
    assert result.dfc_rail.distance_km == 100.12345 * 3.
    assert result.road.distance_km == 100.12345
    assert result.road.cost_per_ton_inr != round(result.road.cost_per_ton_inr, 1)


@pytest.mark.parametrize("value", ["20", True, False, None, 0., -1., float("nan"), float("inf")])
def test_invalid_shipment_quantity(value):
    with pytest.raises(core.ShipmentValidationError) as exc:
        core.scale_shipment(calculate(), value)
    assert exc.value.category == "shipment"


@pytest.mark.parametrize("overrides", [
    {"road_linehaul_rate": 1e308}, {"toll_spacing_km": 1e-308}, {"truck_payload_tons": 1e-308},
    {"inventory_holding_rate": 1e308}, {"rail_first_mile_rate": 1e308},
    {"rail_first_mile_handling": 1e308, "rail_handling_and_siding": 1e308},
    {"rail_base_class_rate": 1e308}, {"rail_commercial_speed_kmh": 1e-308},
    {"dfc_linehaul_rate": 1e308}, {"dfc_commercial_speed_kmh": 1e-308},
    {"rail_circuity_factor": 1e308}, {"dfc_circuity_factor": 1e308},
    {"road_handling_cost": 1e308, "road_linehaul_rate": 1e306},
    {"dfc_handling_cost": 1e308, "dfc_linehaul_rate": 1e306},
])
def test_each_cost_time_distance_overflow_branch(overrides):
    with pytest.raises(core.CalculationError) as exc:
        calculate(parameters=overrides)
    assert exc.value.category == "calculation"


@pytest.mark.parametrize("name", ["road", "conventional_rail", "dfc_rail"])
def test_each_shipment_cost_overflow_branch(name):
    unit = calculate(parameters=zero_parameters())
    unit = replace(unit, **{name: replace(getattr(unit, name), cost_per_ton_inr=1e308)})
    with pytest.raises(core.CalculationError):
        core.scale_shipment(unit, 2.)


def test_shipment_savings_overflow_and_tampered_result():
    unit = replace(calculate(parameters=zero_parameters()), modal_shift_savings_per_ton_inr=1e308)
    with pytest.raises(core.CalculationError):
        core.scale_shipment(unit, 2.)
    for invalid in (float("nan"), -1., "20", True):
        unit = replace(calculate(), road=replace(calculate().road, cost_per_ton_inr=invalid))
        with pytest.raises(core.CalculationError):
            core.scale_shipment(unit, 20.)


def test_parameters_not_mutated_or_cached():
    params = core.FreightCostParameters()
    before = asdict(params)
    first = calculate(parameters=params)
    assert asdict(params) == before
    params.road_linehaul_rate = 0.
    assert calculate(parameters=params).road.cost_per_ton_inr < first.road.cost_per_ton_inr
    assert {field.name for field in fields(params)} == set(DEFAULTS)


@pytest.mark.parametrize("overrides,expected_branch", [
    ({"inventory_holding_rate": 1e308}, "rail time cost"),
    ({"inventory_holding_rate": 1e308, "rail_commercial_speed_kmh": 1e308,
      "rail_yard_detention_hours": 0.}, "DFC time cost"),
    ({"toll_spacing_km": 1e-306}, "road tolls"),
    ({"rail_commercial_speed_kmh": 1., "rail_yard_detention_hours": 1e308,
      "rail_circuity_factor": 1e306}, "rail time"),
    ({"dfc_commercial_speed_kmh": 1., "dfc_yard_transfer_hours": 1e308,
      "dfc_circuity_factor": 1e306}, "DFC time"),
])
def test_isolated_intermediate_overflow_not_masked_by_earlier_modes(overrides, expected_branch):
    with pytest.raises(core.CalculationError, match=expected_branch):
        calculate(drive_hours=0., parameters=overrides)


@pytest.mark.parametrize("field", ["road_km", "drive_hours", "terminal_km"])
def test_nonrepresentable_integer_sources_are_categorized(field):
    with pytest.raises(core.SourceValidationError):
        calculate(**{field: 10 ** 1000})


@pytest.mark.parametrize("field", list(DEFAULTS))
@pytest.mark.parametrize("value", ["3.5", True, float("inf"), -1.])
def test_invalid_override_mapping_values(field, value):
    with pytest.raises(core.ParameterValidationError):
        calculate(parameters={field: value})
