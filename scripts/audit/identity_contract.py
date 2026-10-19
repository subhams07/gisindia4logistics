"""Pure district identity contract shared by audit and producer-side checks.

A record is a mapping with ``state``, ``district`` and ``district_code``, as made
by ``frame.to_dict("records")``. No I/O and no producer imports. Source labels and
codes are preserved literally: nothing here repairs, reassigns or corrects an
upstream identifier, and a shared code across states is a disclosed source
condition, not an error. Same-state duplicates are errors.
"""
import math
import numbers

import numpy as np
import pandas as pd


class IdentityError(ValueError):
    """A malformed or ambiguous stored district identity."""


def strict_code(value):
    """Positive integral finite real as int; None, pd.NA and NaN as None; else raise."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, numbers.Real):
        raise IdentityError("Invalid district code type")
    try:
        if math.isnan(value):
            return None
        if not math.isfinite(value) or value <= 0 or int(value) != value:
            raise IdentityError("Invalid district code value")
        return int(value)
    except OverflowError:
        raise IdentityError("Invalid district code magnitude") from None


def label_key(value):
    """Non-blank string label, stripped and casefolded."""
    if not isinstance(value, str) or not value.strip():
        raise IdentityError("Invalid district label")
    return value.strip().casefold()


def _field(record, name):
    try:
        return record[name]
    except (KeyError, IndexError, TypeError):
        raise IdentityError(f"Missing identity field {name}") from None


def _parts(record):
    state, district = label_key(_field(record, "state")), label_key(_field(record, "district"))
    return state, district, strict_code(_field(record, "district_code"))


def identity_key(record):
    """Qualified key; both labels are validated even on coded rows."""
    state, district, code = _parts(record)
    return ("coded", state, code) if code is not None else ("uncoded", state, district)


def validate_identities(records):
    """Keys in row order; duplicate qualified keys and malformed fields raise."""
    keys, seen = [], set()
    for row, record in enumerate(records):
        key = identity_key(record)
        if key in seen:
            raise IdentityError(f"Duplicate district identity at row {row}: {key}")
        seen.add(key)
        keys.append(key)
    return keys


def exact_identities_equal(left_records, right_records):
    """Ordered literal comparison: exact str labels, strict codes, aligned nulls.

    Malformed codes raise IdentityError rather than comparing equal or unequal.
    """
    left, right = list(left_records), list(right_records)
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        for name in ("state", "district"):
            x, y = _field(a, name), _field(b, name)
            if type(x) is not str or type(y) is not str or x != y:
                return False
        if strict_code(_field(a, "district_code")) != strict_code(_field(b, "district_code")):
            return False
    return True


def cross_state_codes(records):
    """{code: ascending rows} for codes under more than one state, first-appearance order."""
    rows, states = {}, {}
    for row, record in enumerate(records):
        state, _, code = _parts(record)
        if code is not None:
            rows.setdefault(code, []).append(row)
            states.setdefault(code, set()).add(state)
    return {code: found for code, found in rows.items() if len(states[code]) > 1}


def same_state_label_codes(records):
    """{(state_key, district_key): ascending coded rows} with more than one distinct code."""
    rows, codes = {}, {}
    for row, record in enumerate(records):
        state, district, code = _parts(record)
        if code is not None:
            rows.setdefault((state, district), []).append(row)
            codes.setdefault((state, district), set()).add(code)
    return {key: found for key, found in rows.items() if len(codes[key]) > 1}


def _loose_code(value):
    # Native integers keep every digit; only non-integral input is coerced through float.
    if isinstance(value, numbers.Integral) and not isinstance(value, (bool, np.bool_)):
        return int(value)
    coerced = pd.to_numeric(value, errors="coerce")
    if isinstance(coerced, numbers.Integral) and not isinstance(coerced, (bool, np.bool_)):
        return int(coerced)
    return float(coerced)


def _loose(record):
    try:
        state = str(record["state"]).strip().casefold()
        district = str(record["district"]).strip().casefold()
        code = _loose_code(record["district_code"])
        finite = math.isfinite(code)
    except Exception:
        return None
    return (state, district, code) if finite else None


def collision_rows(records):
    """Permissive sampler grouping; never raises. Rows ordered by (code, row).

    Union of codes shared across states and same-state labels with several codes.
    Unlike the strict helpers, code 0 and other legacy values are grouped as-is.
    """
    by_code, by_label = {}, {}
    for row, record in enumerate(records):
        loose = _loose(record)
        if loose is not None:
            state, district, code = loose
            by_code.setdefault(code, []).append((row, state))
            by_label.setdefault((state, district), []).append((row, code))
    found = set()
    for code, members in by_code.items():
        if len({state for _, state in members}) > 1:
            found.update((code, row) for row, _ in members)
    for members in by_label.values():
        if len({code for _, code in members}) > 1:
            found.update((code, row) for row, code in members)
    return [row for _, row in sorted(found)]
