"""
server/services/exceptions.py
Domain exceptions for GISIndia4Logistics Decision Workbench services.
"""

from typing import Optional, List, Dict, Any


class GISIndia4LogisticsError(Exception):
    """Base domain exception for all GISIndia4Logistics operations."""
    pass


class LocationNotFoundError(GISIndia4LogisticsError):
    """Raised when an origin, destination, or facility cannot be resolved."""
    def __init__(self, message: str, location_query: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.location_query = location_query or {}


class InvalidLocationError(GISIndia4LogisticsError):
    """Raised when a location query is malformed or out of valid bounds."""
    pass


class AmbiguousLocationError(GISIndia4LogisticsError):
    """Raised when a location query matches multiple entities across different states or categories."""
    def __init__(self, message: str, candidates: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.candidates = candidates or []


class RouteNotAvailableError(GISIndia4LogisticsError):
    """Raised when no connected highway route can be computed between origin and destination."""
    def __init__(self, message: str, origin_info: Optional[str] = None, destination_info: Optional[str] = None):
        super().__init__(message)
        self.origin_info = origin_info
        self.destination_info = destination_info


class UnsupportedScenarioError(GISIndia4LogisticsError):
    """Raised when an unsupported operational scenario (e.g. island-to-mainland freight) is requested."""
    pass


class ReportGenerationError(GISIndia4LogisticsError):
    """Raised when report generation fails due to missing optional dependencies or rendering errors."""
    pass
