"""Pure identity contract regressions: no I/O, no producers."""
import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.audit import identity_contract as contract
from scripts.audit.identity_contract import (
    IdentityError, collision_rows, cross_state_codes, exact_identities_equal, identity_key,
    label_key, same_state_label_codes, strict_code, validate_identities)


def rec(state, district, code):
    return dict(state=state, district=district, district_code=code)


SHARED = [rec("Kerala", "Alappuzha", 598), rec("Maharashtra", "DHULE", 598), rec("Maharashtra", "DHULE", 498)]


def test_public_api_is_exact():
    public = {n for n in dir(contract) if not n.startswith("_") and callable(getattr(contract, n))
              and getattr(getattr(contract, n), "__module__", "") == contract.__name__}
    assert public == {"IdentityError", "strict_code", "label_key", "identity_key", "validate_identities",
                      "exact_identities_equal", "cross_state_codes", "same_state_label_codes", "collision_rows"}
    assert issubclass(IdentityError, ValueError)


def test_module_is_pure():
    tree = ast.parse(Path(contract.__file__).read_text())
    names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    names += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not [n for n in names if n.startswith(("scripts", "server", "geopandas", "pathlib", "os"))]


@pytest.mark.parametrize("value", [True, np.bool_(True), np.bool_(False), "598", "", 0, 0.0, -1, -598.0, 1.5, 598.5,
                                   float("inf"), float("-inf"), np.float64("inf"), 10 ** 400, -10 ** 400, 1 + 2j, [1], object()])
def test_strict_code_invalid(value):
    with pytest.raises(IdentityError):
        strict_code(value)


@pytest.mark.parametrize("value", [None, pd.NA, float("nan"), np.nan, np.float64("nan"), np.float32("nan")])
def test_strict_code_missing(value):
    assert strict_code(value) is None


@pytest.mark.parametrize("value,expected", [(598, 598), (598.0, 598), (np.int64(7), 7), (np.float64(498.0), 498), (1, 1)])
def test_strict_code_valid(value, expected):
    result = strict_code(value)
    assert result == expected and type(result) is int


def test_oversized_code_is_identity_error_not_overflow():
    with pytest.raises(IdentityError) as caught:
        strict_code(10 ** 400)
    assert not isinstance(caught.value, OverflowError)


@pytest.mark.parametrize("value", ["", "   ", None, np.nan, 5, b"x", pd.NA])
def test_label_key_invalid(value):
    with pytest.raises(IdentityError):
        label_key(value)


def test_label_key_normalizes():
    assert label_key("  DhuLE ") == "dhule"


@pytest.mark.parametrize("district", ["", "  ", None, np.nan, 7])
def test_bad_district_on_coded_row_raises(district):
    with pytest.raises(IdentityError):
        identity_key(rec("Kerala", district, 5))
    with pytest.raises(IdentityError):
        validate_identities([rec("Kerala", district, 5)])


@pytest.mark.parametrize("state", ["", None, 3])
def test_bad_state_raises(state):
    with pytest.raises(IdentityError):
        identity_key(rec(state, "A", 5))


def test_missing_fields_raise():
    with pytest.raises(IdentityError):
        identity_key(dict(state="A", district="B"))
    with pytest.raises(IdentityError):
        identity_key(None)


def test_keys_coded_and_uncoded():
    assert identity_key(rec(" Kerala ", "Alappuzha", 598.0)) == ("coded", "kerala", 598)
    assert identity_key(rec("Delhi", " Nazul ", None)) == ("uncoded", "delhi", "nazul")
    assert identity_key(rec("Delhi", "Nazul", float("nan"))) == identity_key(rec("delhi", "NAZUL", pd.NA))


def test_cross_state_shared_code_and_dhule_are_distinct():
    keys = validate_identities(SHARED)
    assert len(set(keys)) == 3
    assert keys[1] != keys[2] and keys[0] != keys[1]


def test_same_state_duplicate_code_raises():
    with pytest.raises(IdentityError):
        validate_identities([rec("Kerala", "A", 598), rec(" kerala ", "B", 598)])
    with pytest.raises(IdentityError):
        validate_identities([rec("Kerala", "A", 598), rec("Kerala", "A", 598)])


def test_duplicate_uncoded_pair_raises_and_null_uniqueness():
    with pytest.raises(IdentityError):
        validate_identities([rec("Delhi", "Nazul", None), rec("DELHI", " nazul", float("nan"))])
    assert len(validate_identities([rec("Delhi", "Nazul", None), rec("Delhi", "Other", None), rec("Assam", "Nazul", None)])) == 3


def test_same_label_with_different_codes_is_not_a_duplicate_key():
    assert len(validate_identities([rec("Maharashtra", "DHULE", 498), rec("Maharashtra", "DHULE", 598)])) == 2


def test_malformed_record_raises_in_validate():
    with pytest.raises(IdentityError):
        validate_identities([rec("A", "B", 1), rec("A", "C", "598")])


def test_exact_equal_literal_ordered():
    assert exact_identities_equal(SHARED, [dict(r) for r in SHARED])
    assert exact_identities_equal([rec("A", "B", None)], [rec("A", "B", float("nan"))])
    assert exact_identities_equal([rec("A", "B", 598)], [rec("A", "B", 598.0)])
    assert exact_identities_equal([], [])


def test_exact_unequal_cases():
    base = [rec("Maharashtra", "Dhule", 498)]
    assert not exact_identities_equal(base, [rec("Maharashtra", "DHULE", 498)])
    assert not exact_identities_equal(base, [rec("maharashtra", "Dhule", 498)])
    assert not exact_identities_equal(base, [rec("Maharashtra", "Dhule ", 498)])
    assert not exact_identities_equal(SHARED, [SHARED[1], SHARED[0], SHARED[2]])
    assert not exact_identities_equal([rec("A", "B", None)], [rec("A", "B", 5)])
    assert not exact_identities_equal([rec("A", "B", 5)], [rec("A", "B", None)])
    assert not exact_identities_equal(SHARED, SHARED[:2])
    assert not exact_identities_equal([rec("A", "B", 5)], [rec("A", "B", 6)])
    assert not exact_identities_equal([rec(np.str_("A"), "B", 5)], [rec("A", "B", 5)])
    assert not exact_identities_equal([rec(1, "B", 5)], [rec(1, "B", 5)])


def test_exact_equal_never_uses_semantic_shortcut_and_rejects_bad_codes():
    with pytest.raises(IdentityError):
        exact_identities_equal([rec("A", "B", "5")], [rec("A", "B", "5")])
    with pytest.raises(IdentityError):
        exact_identities_equal([rec("A", "B", True)], [rec("A", "B", True)])


def test_cross_state_codes_grouping():
    rows = [rec("Kerala", "A", 598), rec("Assam", "X", 7), rec("Maharashtra", "DHULE", 598), rec("Assam", "Y", 8),
            rec("Maharashtra", "Z", 7), rec(" kerala", "Q", 598.0)]
    assert cross_state_codes(rows) == {598: [0, 2, 5], 7: [1, 4]}
    assert list(cross_state_codes(rows)) == [598, 7]
    assert cross_state_codes([rec("A", "B", 1), rec("A", "C", 1)]) == {}
    assert cross_state_codes([rec("A", "B", None), rec("C", "D", None)]) == {}


def test_same_state_label_codes_grouping():
    rows = [rec("Maharashtra", "DHULE", 598), rec("Kerala", "Alappuzha", 598), rec(" maharashtra", "dhule ", 498),
            rec("Kerala", "Kollam", 5), rec("Kerala", "Kollam", 5)]
    assert same_state_label_codes(rows) == {("maharashtra", "dhule"): [0, 2]}
    assert same_state_label_codes([rec("A", "B", None), rec("A", "B", 3)]) == {}


@pytest.mark.parametrize("bad", ["598", True, 1.5, 10 ** 400])
def test_strict_groupers_raise_on_malformed(bad):
    rows = [rec("A", "B", 1), rec("A", "C", bad)]
    with pytest.raises(IdentityError):
        cross_state_codes(rows)
    with pytest.raises(IdentityError):
        same_state_label_codes(rows)


def test_grouping_consistency_with_collision_rows():
    rows = SHARED + [rec("Assam", "Q", 5), rec("Delhi", "R", None)]
    strict = {r for found in cross_state_codes(rows).values() for r in found}
    strict |= {r for found in same_state_label_codes(rows).values() for r in found}
    assert strict == set(collision_rows(rows)) == {0, 1, 2}


def test_collision_rows_ordered_by_code_then_row():
    rows = [rec("Kerala", "A", 598), rec("Maharashtra", "DHULE", 598), rec("Maharashtra", "DHULE", 498),
            rec("Assam", "X", 7), rec("Delhi", "Y", 7), rec("Delhi", "Z", 9)]
    assert collision_rows(rows) == [3, 4, 2, 0, 1]
    assert collision_rows(rows) == collision_rows(list(rows))


@pytest.mark.parametrize("low", [2 ** 53, 2 ** 63 - 2, 2 ** 64 - 2, 2 ** 70, 10 ** 30])
def test_adjacent_large_integer_codes_stay_distinct_in_strict_and_permissive_grouping(low):
    for first, second in ((low, low + 1), (np.int64(low) if low < 2 ** 63 - 1 else low, low + 1)):
        same_label = [rec("A", "x", 3), rec("A", "collision", first), rec("A", "collision", second)]
        assert len(validate_identities(same_label)) == 3
        assert same_state_label_codes(same_label) == {("a", "collision"): [1, 2]}
        assert collision_rows(same_label) == [1, 2]
        cross = [rec("A", "x", first), rec("B", "y", second)]
        assert cross_state_codes(cross) == {} and collision_rows(cross) == []
        shared = [rec("A", "x", first), rec("B", "y", first)]
        assert cross_state_codes(shared) == {first: [0, 1]} and collision_rows(shared) == [0, 1]


def test_large_integers_from_int64_frame_records_group_exactly():
    frame = pd.DataFrame(dict(state=["A", "A"], district=["c", "c"], district_code=np.array([2 ** 53, 2 ** 53 + 1], dtype="int64")))
    records = frame.to_dict("records")
    assert same_state_label_codes(records) == {("a", "c"): [0, 1]} and collision_rows(records) == [0, 1]


def test_collision_rows_accepts_code_zero_and_never_raises():
    assert collision_rows([rec("A", "B", 0), rec("C", "D", 0)]) == [0, 1]
    junk = [rec("A", "B", True), rec(None, None, "x"), rec("A", "B", 10 ** 400), {"state": "A"}, None, 7,
            rec("A", "B", float("inf")), rec("A", "B", np.bool_(True)), rec(["a"], {"b": 1}, [1])]
    assert collision_rows(junk) == collision_rows(junk) and isinstance(collision_rows(junk), list)
    assert collision_rows([]) == []
