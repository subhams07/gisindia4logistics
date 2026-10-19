"""
GISIndia4Logistics — Open Geospatial & Multimodal Freight Intelligence Platform for India
"""

__version__ = "1.1.0"

__all__ = [
    "get_district",
    "route_highway",
    "plan_highway_route",
    "calculate_freight_cost",
    "find_nearest",
    "simulate_port_catchment",
    "plot_villages",
    "get_data_store"
]


def __getattr__(name):
    """Resolve public functions by identity without loading SDK at CLI startup."""
    if name in __all__:
        from importlib import import_module
        value = getattr(import_module("gisindia4logistics.sdk"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
