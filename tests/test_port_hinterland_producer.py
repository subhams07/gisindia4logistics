"""Huff formula/source-validation TDD; never touches published datasets."""
import inspect
import math

import pandas as pd
import pytest

from scripts.analyze import port_hinterland_model as gravity
from server.routers.simulation import PORT_MATRIX_DATA


@pytest.fixture
def inputs():
    row = dict(state="Mainland", district="District", district_code=1)
    for i, col in enumerate(PORT_MATRIX_DATA):
        row[col] = 5.0 + i
        row[col.replace("drive_hours_to_", "road_km_to_", 1)] = 100.0 + i
    matrix = pd.DataFrame([row])
    population = matrix[["state", "district", "district_code"]].assign(pop_2011=100)
    return matrix, population


def build(inputs, **kwargs):
    return gravity.build_port_hinterland(*inputs, kwargs.pop("port_data", None),
                                        kwargs.pop("alpha", 1.2), kwargs.pop("beta", 1.5), **kwargs)


def test_defaults_order_formula_and_exact_oracle(inputs):
    assert gravity.PORT_MATRIX_DATA == PORT_MATRIX_DATA
    assert list(gravity.PORT_MATRIX_DATA) == list(PORT_MATRIX_DATA)
    assert inspect.signature(gravity.run_port_gravity_model).parameters["alpha"].default == 1.2
    assert inspect.signature(gravity.run_port_gravity_model).parameters["beta"].default == 1.5
    result = build(inputs).iloc[0]
    row = inputs[0].iloc[0]
    utility = {name: cap ** 1.2 / ((row[col] / 5) ** 1.5) for col, (name, cap) in PORT_MATRIX_DATA.items()}
    ordered = sorted(utility, key=utility.get, reverse=True)
    total = sum(utility.values())
    assert result.primary_port == ordered[0] and result.secondary_port == ordered[1]
    assert result.primary_port_probability == round(100 * utility[ordered[0]] / total, 1)
    assert result.secondary_port_probability == round(100 * utility[ordered[1]] / total, 1)
    assert result.contestability_index == round(1 - (utility[ordered[0]] - utility[ordered[1]]) / total, 3)
    assert result.pop_2011 == 100


def test_equal_capacity_time_stable_tie_and_single_port(inputs):
    matrix, population = inputs
    caps = {key: 100.0 for key in PORT_MATRIX_DATA}
    matrix[list(PORT_MATRIX_DATA)] = 5.0
    result = build(inputs, port_data=caps).iloc[0]
    assert result.primary_port == next(iter(PORT_MATRIX_DATA.values()))[0]
    assert result.secondary_port == list(PORT_MATRIX_DATA.values())[1][0]
    assert result.primary_port_probability == round(100 / 12, 1)
    for col in list(PORT_MATRIX_DATA)[1:]:
        matrix[col] = None
        matrix[col.replace("drive_hours_to_", "road_km_to_", 1)] = None
    result = build((matrix, population), port_data=caps).iloc[0]
    assert result.primary_port_probability == 100 and result.secondary_port_probability == 0
    assert result.secondary_port == "None"


@pytest.mark.parametrize("field", ["alpha", "beta"])
@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), True, "1.2", None])
def test_exponent_constraints(inputs, field, value):
    with pytest.raises(ValueError):
        build(inputs, **{field: value})


@pytest.mark.parametrize("alpha,beta", [(0., 1.5), (1.2, 0.), (0., 0.)])
def test_zero_exponents_preserve_formula_live_winner_and_ties(inputs, alpha, beta):
    from types import SimpleNamespace
    from server.models.schemas import PortGravitySimulationRequest
    from server.routers.simulation import simulate_port_gravity

    matrix, population = inputs
    for i, col in enumerate(PORT_MATRIX_DATA):
        matrix[col] = 17. - i
    result = build(inputs, alpha=alpha, beta=beta).iloc[0]
    row = matrix.iloc[0]
    utility = {name: cap ** alpha / ((row[col] / 5.) ** beta)
               for col, (name, cap) in PORT_MATRIX_DATA.items()}
    ranked = sorted(utility, key=utility.get, reverse=True)
    assert result.primary_port == ranked[0] and result.secondary_port == ranked[1]
    assert result.primary_port_probability == round(100 * utility[ranked[0]] / sum(utility.values()), 1)
    live = simulate_port_gravity(PortGravitySimulationRequest(alpha=alpha, beta=beta),
        SimpleNamespace(port_matrix_df=matrix, districts_df=population))
    winner = next(port for port in live if port["captured_districts_count"] == 1)
    assert winner["port_name"] == result.primary_port
    if alpha == beta == 0.:
        assert result.primary_port == next(iter(PORT_MATRIX_DATA.values()))[0]
        assert result.primary_port_probability == round(100 / 12, 1)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "100", None])
def test_capacity_constraints(inputs, value):
    with pytest.raises(ValueError):
        build(inputs, port_data={next(iter(PORT_MATRIX_DATA)): value})


def test_unknown_capacity_fails(inputs):
    with pytest.raises(ValueError, match="[Uu]nknown|[Uu]nrecognized"):
        build(inputs, port_data={"not a port": 100.0})


@pytest.mark.parametrize("value", [-1, float("inf"), True, "bad"])
@pytest.mark.parametrize("kind", ["drive_hours_to_", "road_km_to_"])
def test_corruption_pair_fail(inputs, value, kind):
    col = next(iter(PORT_MATRIX_DATA)).replace("drive_hours_to_", kind, 1)
    inputs[0][col] = pd.Series([value], dtype=object)
    with pytest.raises(ValueError):
        build(inputs)


def test_partial_null_and_required_columns(inputs):
    matrix, population = inputs
    col = next(iter(PORT_MATRIX_DATA))
    distance = col.replace("drive_hours_to_", "road_km_to_", 1)
    with pytest.raises(ValueError):
        build((matrix.drop(columns=[distance]), population))
    matrix[col] = None
    with pytest.raises(ValueError):
        build(inputs)
    matrix[distance] = None
    assert build(inputs).iloc[0].primary_port != PORT_MATRIX_DATA[col][0]


def test_zero_time_excluded_zero_distance_allowed(inputs):
    matrix, _ = inputs
    col = next(iter(PORT_MATRIX_DATA))
    matrix[col] = 0.0
    matrix[col.replace("drive_hours_to_", "road_km_to_", 1)] = 0.0
    assert build(inputs).iloc[0].primary_port != PORT_MATRIX_DATA[col][0]
    matrix[col] = 5.0
    assert build(inputs).iloc[0].primary_port == PORT_MATRIX_DATA[col][0]


def test_geographic_four_islands_and_mainland_isolated(inputs):
    matrix, _ = inputs
    frames = []
    for i, state in enumerate(["Andaman and Nicobar Islands"] * 3 + [" lakshadweep ", "Mainland"]):
        row = matrix.copy().assign(state=state, district=str(i), district_code=i + 1, is_island=False)
        for col in PORT_MATRIX_DATA:
            row[col] = None
            row[col.replace("drive_hours_to_", "road_km_to_", 1)] = None
        frames.append(row)
    matrix = pd.concat(frames, ignore_index=True)
    population = matrix[["state", "district", "district_code"]].assign(pop_2011=100)
    result = build((matrix, population))
    assert result.is_island.tolist() == [True] * 4 + [False]
    assert result.hinterland_category.tolist() == ["Island / Isolated"] * 4 + ["Isolated"]


def test_population_code_first_null_pair_missing_and_identity(inputs):
    matrix, _ = inputs
    matrix = pd.concat([matrix, matrix.assign(district=" Uncoded ", district_code=None),
                        matrix.assign(district="Missing", district_code=3)], ignore_index=True)
    population = pd.DataFrame([dict(state="Other", district="Different", district_code=1, pop_2011=500),
        dict(state="mainland", district="uncoded", district_code=2, pop_2011=200)])
    result = build((matrix, population))
    assert len(result) == 3 and result.district.tolist() == matrix.district.tolist()
    assert result.pop_2011.iloc[:2].tolist() == [500, 200]
    assert math.isnan(result.pop_2011.iloc[2]) and pd.isna(result.district_code.iloc[1])
    with pytest.raises(ValueError, match="Duplicate"):
        build((matrix, pd.concat([population, population.iloc[:1]])))
    ambiguous = pd.concat([population, population.iloc[1:].assign(district_code=4)])
    with pytest.raises(ValueError, match="Ambiguous"):
        build((matrix, ambiguous))


def test_identity_781_synthetic_preserved(inputs):
    row = inputs[0]
    matrix = pd.concat([row.assign(district=str(i), district_code=i + 1 if i else None)
                        for i in range(781)], ignore_index=True)
    population = matrix[["state", "district", "district_code"]].assign(pop_2011=100)
    result = build((matrix, population))
    assert len(result) == 781 and result.district.tolist() == matrix.district.tolist()
    assert pd.isna(result.district_code.iloc[0])


def test_selected_root_staged_overrides_atomic_and_corruption_before_write(inputs, tmp_path):
    root = tmp_path / "root"
    (root / "analysis").mkdir(parents=True)
    (root / "demographic").mkdir()
    matrix, population = inputs
    matrix.to_csv(root / "analysis/nh_district_port_matrix.csv", index=False)
    population.to_csv(root / "demographic/district_population_estimates.csv", index=False)
    staged_matrix = tmp_path / "staged.csv"
    staged_pop = tmp_path / "population.csv"
    matrix.assign(district="Staged").to_csv(staged_matrix, index=False)
    population.assign(pop_2011=900).to_csv(staged_pop, index=False)
    out = tmp_path / "outputs/out.csv"
    result = gravity.run_port_gravity_model(1.2, 1.5, None, out, data_dir=root,
        matrix_csv=staged_matrix, population_csv=staged_pop)
    assert result.district.tolist() == ["Staged"] and result.pop_2011.tolist() == [900]
    assert pd.read_csv(out).district.tolist() == ["Staged"]
    before = out.read_bytes()
    col = next(iter(PORT_MATRIX_DATA))
    matrix[col] = -1
    matrix.to_csv(staged_matrix, index=False)
    with pytest.raises(ValueError):
        gravity.run_port_gravity_model(output_csv=out, data_dir=root, matrix_csv=staged_matrix, population_csv=staged_pop)
    assert out.read_bytes() == before
    with pytest.raises(ValueError):
        gravity.run_port_gravity_model(custom_capacities={"unknown": 12}, output_csv=out, data_dir=root)
    assert out.read_bytes() == before
    with pytest.raises(FileNotFoundError):
        gravity.run_port_gravity_model(output_csv=out, data_dir=tmp_path / "missing")
    assert out.read_bytes() == before


def test_overflow_fails(inputs):
    with pytest.raises(ValueError):
        build(inputs, alpha=1000)


def test_two_port_shares_sum_100_rounding_and_live_winner(inputs):
    from types import SimpleNamespace
    from server.models.schemas import PortGravitySimulationRequest
    from server.routers.simulation import simulate_port_gravity

    matrix, population = inputs
    for col in list(PORT_MATRIX_DATA)[2:]:
        matrix[col] = None
        matrix[col.replace("drive_hours_to_", "road_km_to_", 1)] = None
    result = build(inputs).iloc[0]
    assert result.primary_port_probability + result.secondary_port_probability == pytest.approx(100, abs=0.1)
    live = simulate_port_gravity(PortGravitySimulationRequest(alpha=1.2, beta=1.5),
        SimpleNamespace(port_matrix_df=matrix, districts_df=population))
    assert live[0]["port_name"] == result.primary_port
    assert live[0]["captured_districts_count"] == 1


def test_last_row_corrupt_no_first_row_publication(inputs, tmp_path, monkeypatch):
    matrix, population = inputs
    col = next(iter(PORT_MATRIX_DATA))
    corrupt = matrix.assign(district="Corrupt", district_code=2)
    corrupt[col] = float("inf")
    matrix = pd.concat([matrix, corrupt], ignore_index=True)
    matrix.to_csv(tmp_path / "matrix.csv", index=False)
    population.to_csv(tmp_path / "population.csv", index=False)
    writes = []
    monkeypatch.setattr(gravity.producer_io, "atomic_write_csv", lambda *args: writes.append(args))
    with pytest.raises(ValueError):
        gravity.run_port_gravity_model(output_csv=tmp_path / "out.csv", data_dir=tmp_path,
            matrix_csv=tmp_path / "matrix.csv", population_csv=tmp_path / "population.csv")
    assert not writes and not (tmp_path / "out.csv").exists()


def test_cli_capacity_display_names_and_missing_population_fatal(inputs, tmp_path):
    matrix, population = inputs
    matrix.to_csv(tmp_path / "matrix.csv", index=False)
    population.to_csv(tmp_path / "population.csv", index=False)
    import json
    name = list(PORT_MATRIX_DATA.values())[-1][0]
    (tmp_path / "params.json").write_text(json.dumps({"port_capacities": {name: 10000}}))
    out = tmp_path / "out.csv"
    result = gravity.main(["--data-dir", str(tmp_path), "--matrix-csv", str(tmp_path / "matrix.csv"),
        "--population-csv", str(tmp_path / "population.csv"), "--output-csv", str(out),
        "--config", str(tmp_path / "params.json")])
    assert result.primary_port.tolist() == [name]
    assert pd.read_csv(out).primary_port.tolist() == [name]
    with pytest.raises(FileNotFoundError):
        gravity.run_port_gravity_model(data_dir=tmp_path, matrix_csv=tmp_path / "matrix.csv",
            population_csv=tmp_path / "absent.csv", output_csv=tmp_path / "missing.csv")
    assert not (tmp_path / "missing.csv").exists()
