"""Pure, unrounded freight model shared by live and batch adapters.

Costs are INR/tonne, distances km, times hours, shipment quantity tonnes.
Tolls are a continuous distance/spacing heuristic, not matched toll plazas.
Telescopic rates and DFC eligibility are model assumptions, not official tariffs
or verified rail routes. The 210/380 km strategic markers are fixed model labels,
not solved break-even distances. State normalization uses the existing helper at
an explicit boundary; calculate_unit_costs requires its canonical spelling.
"""

from dataclasses import dataclass, fields
from collections.abc import Mapping
import math
from numbers import Real


@dataclass
class FreightCostParameters:
    # Road Trucking Parameters
    road_linehaul_rate: float = 3.30        # INR / tonne-km
    road_handling_cost: float = 140.0       # INR / tonne (loading + unloading)
    toll_cost_per_plaza: float = 340.0      # INR per plaza per commercial truck
    truck_payload_tons: float = 20.0        # Commercial truck payload capacity in MT
    toll_spacing_km: float = 65.0           # Average distance between highway toll plazas in km

    # Conventional Indian Railways Parameters
    rail_base_class_rate: float = 1.55      # Base telescopic rate factor (IR Class 120/140)
    rail_first_mile_rate: float = 4.20      # INR / tonne-km to nearest Goods Shed / GCT
    rail_first_mile_handling: float = 120.0 # INR / tonne for feeder handling
    rail_handling_and_siding: float = 220.0 # INR / tonne for siding charges + terminal handling
    rail_commercial_speed_kmh: float = 25.0 # Average commercial freight train speed in km/h
    rail_yard_detention_hours: float = 12.0 # Marshalling & yard detention delay in hours
    rail_circuity_factor: float = 1.08      # Rail network distance circuity vs highway

    # Dedicated Freight Corridor (DFC) Parameters
    dfc_linehaul_rate: float = 1.12         # INR / tonne-km (heavy haul double-stack container traction)
    dfc_handling_cost: float = 180.0        # INR / tonne for automated MMLP / GCT handling
    dfc_commercial_speed_kmh: float = 60.0  # Commercial average speed in km/h
    dfc_yard_transfer_hours: float = 3.0    # Transfer & feeder interchange delay in hours
    dfc_circuity_factor: float = 1.02       # DFC alignment circuity vs highway

    # Financial & Working Capital Parameters
    inventory_holding_rate: float = 7.50    # INR / tonne-hour (cargo time value & working capital)


def ir_telescopic_freight_rate(distance_km: float, base_class_rate: float = 1.55) -> float:
    """Computes Indian Railways telescopic freight tariff per tonne."""
    if distance_km <= 100:
        return distance_km * (base_class_rate * 1.35)
    elif distance_km <= 500:
        return 100 * (base_class_rate * 1.35) + (distance_km - 100) * (base_class_rate * 1.10)
    elif distance_km <= 1000:
        return 100 * (base_class_rate * 1.35) + 400 * (base_class_rate * 1.10) + (distance_km - 500) * (base_class_rate * 0.90)
    else:
        return 100 * (base_class_rate * 1.35) + 400 * (base_class_rate * 1.10) + 500 * (base_class_rate * 0.90) + (distance_km - 1000) * (base_class_rate * 0.75)


class FreightCoreError(ValueError):
    """Core-only failure; transport adapters decide public policy separately."""


class ParameterValidationError(FreightCoreError):
    category = "parameters"


class SourceValidationError(FreightCoreError):
    category = "source"


class ShipmentValidationError(FreightCoreError):
    category = "shipment"


class CalculationError(FreightCoreError):
    category = "calculation"


@dataclass(frozen=True)
class ModeUnitCost:
    cost_per_ton_inr: float
    transit_time_hours: float
    distance_km: float


@dataclass(frozen=True)
class FreightUnitResult:
    road: ModeUnitCost
    conventional_rail: ModeUnitCost
    dfc_rail: ModeUnitCost | None
    optimal_mode: str
    modal_shift_savings_per_ton_inr: float
    modal_shift_savings_pct: float
    break_even_distance_km: float
    first_mile_km: float
    first_mile_fallback_used: bool


@dataclass(frozen=True)
class ShipmentCosts:
    """Total INR for independently supplied shipment quantity, not capacity."""

    shipment_tons: float
    road: float
    conventional_rail: float
    dfc_rail: float | None
    modal_shift_savings_total_inr: float


_POSITIVE_PARAMETERS = frozenset({
    "truck_payload_tons", "toll_spacing_km", "rail_commercial_speed_kmh",
    "dfc_commercial_speed_kmh", "rail_circuity_factor", "dfc_circuity_factor",
})
_DFC_STATES = frozenset({
    "Uttar Pradesh", "Haryana", "Rajasthan", "Gujarat", "Maharashtra",
    "Punjab", "Bihar", "Jharkhand", "West Bengal",
})


def _number(value, name, error_type, *, positive=False):
    # Do not silently coerce strings, booleans, complex or missing values.
    if isinstance(value, bool) or not isinstance(value, Real):
        raise error_type(f"{name} must be a real finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError, TypeError):
        raise error_type(f"{name} exceeds numerical range") from None
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        bound = "positive" if positive else "nonnegative"
        raise error_type(f"{name} must be finite and {bound}")
    return number


def resolve_parameters(parameters=None) -> FreightCostParameters:
    """Copy/validate current values, never cache a mutable dataclass.

    A mapping is an override boundary: None entries retain defaults and unknown
    keys fail even when their value is None. A dataclass is a complete parameter
    set: every actual field must be valid (including after caller mutation).
    The relocated legacy constructor itself remains unchanged/unvalidated.
    """
    defaults = FreightCostParameters()
    names = {field.name for field in fields(defaults)}
    if parameters is None:
        values = {name: getattr(defaults, name) for name in names}
    elif isinstance(parameters, FreightCostParameters):
        values = {name: getattr(parameters, name) for name in names}
    elif isinstance(parameters, Mapping):
        if any(name not in names for name in parameters):
            raise ParameterValidationError("Unknown freight parameter key")
        values = {
            name: parameters[name] if name in parameters and parameters[name] is not None
            else getattr(defaults, name) for name in names
        }
    else:
        raise ParameterValidationError("Expected FreightCostParameters or an override mapping")
    return FreightCostParameters(**{
        name: _number(value, name, ParameterValidationError,
                      positive=name in _POSITIVE_PARAMETERS)
        for name, value in values.items()
    })


def normalize_freight_state(state: str) -> str:
    """Pure boundary using the actual shared alias policy, never a new table."""
    from scripts.clean.standardize import clean_state_name

    if not isinstance(state, str) or not state.strip():
        raise SourceValidationError("state must be a recognized state name")
    normalized = clean_state_name(state)
    if normalized is None:
        raise SourceValidationError("state must be a recognized state name")
    return normalized


def _calculated(value, name):
    return _number(value, name, CalculationError)


def calculate_unit_costs(
    road_km: float,
    drive_hours: float,
    terminal_km: float | None,
    state: str,
    parameters: FreightCostParameters | Mapping | None = None,
) -> FreightUnitResult:
    """Validate source/model inputs and calculate unrounded per-tonne costs.

    State must already be canonical (normalize_freight_state is the separate
    boundary). Only None/numeric NaN terminal observations are missing; all
    other invalid observations fail. First-mile observations are capped at
    100 km, with 50 km used for missing values. Rail times match the legacy
    model and do not add an invented feeder travel time.
    """
    p = resolve_parameters(parameters)
    road_km = _number(road_km, "road_km", SourceValidationError)
    drive_hours = _number(drive_hours, "drive_hours", SourceValidationError)
    canonical = normalize_freight_state(state)
    if state != canonical:
        raise SourceValidationError("state must use canonical spelling; normalize at the boundary")
    fallback = terminal_km is None
    if not fallback and isinstance(terminal_km, Real) and not isinstance(terminal_km, bool):
        try:
            fallback = math.isnan(terminal_km)
        except (OverflowError, TypeError, ValueError):
            raise SourceValidationError("terminal_km exceeds numerical range") from None
    first_mile_km = 50.0 if fallback else min(
        _number(terminal_km, "terminal_km", SourceValidationError), 100.0)

    # Check intermediates too: even zero rates must not hide distance/time or
    # divisor overflow. Keep arithmetic order identical to existing live model.
    road_linehaul = _calculated(road_km * p.road_linehaul_rate, "road linehaul")
    toll_count = _calculated(road_km / p.toll_spacing_km, "continuous toll count")
    toll_per_ton = _calculated(p.toll_cost_per_plaza / p.truck_payload_tons, "toll per tonne")
    road_tolls = _calculated(toll_count * toll_per_ton, "road tolls")
    road_time = _calculated(drive_hours * p.inventory_holding_rate, "road time cost")
    road_cost = _calculated(road_linehaul + road_tolls + road_time + p.road_handling_cost, "road cost")
    first_mile_cost = _calculated(
        first_mile_km * p.rail_first_mile_rate + p.rail_first_mile_handling, "first mile cost")
    rail_dist = _calculated(road_km * p.rail_circuity_factor, "rail distance")
    rail_tariff = _calculated(ir_telescopic_freight_rate(rail_dist, p.rail_base_class_rate), "rail tariff")
    rail_hours = _calculated(rail_dist / p.rail_commercial_speed_kmh + p.rail_yard_detention_hours, "rail time")
    rail_time = _calculated(rail_hours * p.inventory_holding_rate, "rail time cost")
    rail_cost = _calculated(first_mile_cost + rail_tariff + p.rail_handling_and_siding + rail_time, "rail cost")
    road = ModeUnitCost(road_cost, drive_hours, road_km)
    rail = ModeUnitCost(rail_cost, rail_hours, rail_dist)
    dfc = None
    modes = {"Road Trucking": road_cost, "Conventional Rail": rail_cost}
    eligible = canonical in _DFC_STATES
    if eligible:
        dfc_dist = _calculated(road_km * p.dfc_circuity_factor, "DFC distance")
        dfc_tariff = _calculated(dfc_dist * p.dfc_linehaul_rate, "DFC linehaul")
        dfc_hours = _calculated(dfc_dist / p.dfc_commercial_speed_kmh + p.dfc_yard_transfer_hours, "DFC time")
        dfc_time = _calculated(dfc_hours * p.inventory_holding_rate, "DFC time cost")
        dfc_cost = _calculated(first_mile_cost + dfc_tariff + p.dfc_handling_cost + dfc_time, "DFC cost")
        dfc = ModeUnitCost(dfc_cost, dfc_hours, dfc_dist)
        modes["Dedicated Freight Corridor (DFC)"] = dfc_cost
    winner = min(modes, key=modes.get)
    savings = _calculated(max(road_cost - modes[winner], 0.0), "unit savings")
    savings_pct = _calculated((savings / road_cost) * 100.0 if road_cost > 0 else 0.0, "savings percent")
    return FreightUnitResult(road, rail, dfc, winner, savings, savings_pct,
                             210.0 if eligible else 380.0, first_mile_km, fallback)


def scale_shipment(result: FreightUnitResult, shipment_tons: float) -> ShipmentCosts:
    """Scale validated unit costs separately; capacity never changes here.

    Quantity must be positive and finite. Nonfinite totals are calculation
    failures, not source/parameter errors; no public error mapping is implied.
    """
    quantity = _number(shipment_tons, "shipment_tons", ShipmentValidationError, positive=True)
    if not isinstance(result, FreightUnitResult):
        raise CalculationError("Expected FreightUnitResult")

    def total(mode, name):
        if not isinstance(mode, ModeUnitCost):
            raise CalculationError(f"Invalid {name} result")
        cost = _calculated(mode.cost_per_ton_inr, f"{name} unit cost")
        return _calculated(cost * quantity, f"{name} shipment cost")

    savings = _calculated(result.modal_shift_savings_per_ton_inr, "unit savings")
    return ShipmentCosts(
        quantity, total(result.road, "road"), total(result.conventional_rail, "rail"),
        None if result.dfc_rail is None else total(result.dfc_rail, "DFC"),
        _calculated(savings * quantity, "shipment savings"),
    )
