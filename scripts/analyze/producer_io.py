"""Identity-safe producer enrichment and single-file atomic publication."""
from __future__ import annotations

import hashlib
import math
import numbers
import os
from pathlib import Path
import tempfile

import pandas as pd

ISLAND_STATES = frozenset({"andaman and nicobar islands", "lakshadweep"})


def is_island_state(state):
    return isinstance(state, str) and state.strip().casefold() in ISLAND_STATES


def _code(value):
    if value is None or value is pd.NA or (isinstance(value, numbers.Real) and math.isnan(value)):
        return None
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError("Invalid stored district code")
    if not math.isfinite(value) or value <= 0 or int(value) != value:
        raise ValueError("Invalid stored district code")
    return int(value)


def _pair(row):
    state, district = row["state"], row["district"]
    if not isinstance(state, str) or not isinstance(district, str) or not state.strip() or not district.strip():
        raise ValueError("Invalid stored district identity")
    return state.strip().casefold(), district.strip().casefold()


def _population(value):
    if value is None or value is pd.NA or (isinstance(value, numbers.Real) and math.isnan(value)):
        return float("nan")
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid stored population")
    return float(value)


def district_population(districts, population):
    """Code first, state-qualified for cross-state stored-code collisions.

    Same-state duplicate codes fail. Unique state/name fallback applies only to
    uncoded primary rows; missing matches stay null. Source codes are not fixed
    or reassigned here, including the observed Alappuzha/Dhule 598 collision.
    """
    for frame in (districts, population):
        if not {"district_code", "state", "district"}.issubset(frame.columns):
            raise ValueError("Missing district identity columns")
    if "pop_2011" not in population.columns:
        raise ValueError("Missing population column")
    primary_records = districts.to_dict("records")
    primary_states = {}
    for row in primary_records:
        code, pair = _code(row["district_code"]), _pair(row)
        if code is not None:
            states = primary_states.setdefault(code, set())
            if pair[0] in states:
                raise ValueError("Duplicate primary district code")
            states.add(pair[0])
    by_code, by_pair = {}, {}
    for row in population.to_dict("records"):
        code, pair, value = _code(row["district_code"]), _pair(row), _population(row["pop_2011"])
        if code is not None:
            states = by_code.setdefault(code, {})
            if pair[0] in states:
                raise ValueError("Duplicate population district code")
            states[pair[0]] = value
        by_pair.setdefault(pair, []).append(value)
    result = []
    for row in primary_records:
        code, pair = _code(row["district_code"]), _pair(row)
        if code is not None:
            states = by_code.get(code, {})
            result.append(next(iter(states.values())) if len(states) == 1 and len(primary_states[code]) == 1
                          else states.get(pair[0], float("nan")))
        else:
            matches = by_pair.get(pair, [])
            if len(matches) > 1:
                raise ValueError("Ambiguous uncoded district population")
            result.append(matches[0] if matches else float("nan"))
    return pd.Series(result, index=districts.index, name="pop_2011", dtype=float)


def _atomic_frame(frame, path, method, **kwargs):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".", suffix=path.suffix)
    os.close(fd)
    temporary = Path(name)
    try:
        getattr(frame, method)(temporary, **kwargs)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_csv(frame, path):
    _atomic_frame(frame, path, "to_csv", index=False)


def atomic_write_parquet(frame, path):
    _atomic_frame(frame, path, "to_parquet", index=False)


def atomic_write_geojson(frame, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Drivers may refuse an already-existing zero-byte file.
    with tempfile.TemporaryDirectory(dir=path.parent, prefix="." + path.name + ".") as directory:
        temporary = Path(directory) / path.name
        frame.to_file(temporary, driver="GeoJSON")
        os.replace(temporary, path)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
