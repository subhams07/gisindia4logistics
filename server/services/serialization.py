"""JSON encoding with explicit observational missing-value policy."""

import math
from typing import Any

import numpy as np
import pandas as pd
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel


NEAREST_OBSERVATIONAL_FIELDS = frozenset({
    "name", "state", "city", "operator", "nh_number", "toll_type",
})


def serialize_nearest_facilities(value: Any) -> Any:
    """Null only missing nearest-facility observations, never computed numerics."""
    return serialize_result(value, nan_fields=NEAREST_OBSERVATIONAL_FIELDS)


def serialize_result(
    value: Any, *, missing_nan: bool = False, nan_fields: frozenset[str] = frozenset()
) -> Any:
    """Encode nested results; never silently null non-finite calculated values.

    ``missing_nan`` is only for wholly observational responses. Mixed responses
    must name their observational fields with ``nan_fields`` instead. Infinity
    is always invalid. Check models before JSON mode can hide NaN via serializers.
    """
    def encode(item: Any, path: str, allow_nan: bool) -> Any:
        if isinstance(item, BaseModel):
            # Validate actual fields, before even Python-mode field serializers
            # can turn invalid arithmetic into a seemingly successful null.
            for name in type(item).model_fields:
                encode(getattr(item, name), f"{path}.{name}", allow_nan or name in nan_fields)
            if item.model_extra:
                encode(item.model_extra, path, allow_nan)
            return encode(item.model_dump(mode="json"), path, allow_nan)
        if isinstance(item, dict):
            return {
                key: encode(val, f"{path}.{key}", allow_nan or key in nan_fields)
                for key, val in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [encode(val, f"{path}[{index}]", allow_nan) for index, val in enumerate(item)]
        if item is pd.NA or item is pd.NaT:
            if allow_nan:
                return None
            raise ValueError(f"Missing value in calculated result at {path}")
        if isinstance(item, np.generic):
            return encode(item.item(), path, allow_nan)
        if isinstance(item, float) and not math.isfinite(item):
            if allow_nan and math.isnan(item):
                return None
            raise ValueError(f"Non-finite value in result at {path}")
        encoded = jsonable_encoder(item)
        # Dates, UUIDs, Decimal, etc. may be transformed by the encoder.
        if type(encoded) is not type(item):
            return encode(encoded, path, allow_nan)
        return encoded

    return encode(value, "$", missing_nan)
