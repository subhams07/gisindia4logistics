"""
GIS4Logistics (Legacy Alias) -> Redirects to GISIndia4Logistics
"""

from gisindia4logistics import __all__ as __all__, __version__ as __version__


def __getattr__(name):
    """Keep the legacy package lazy while preserving canonical function identity."""
    if name in __all__:
        from importlib import import_module
        value = getattr(import_module("gisindia4logistics"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
