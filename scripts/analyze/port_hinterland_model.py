"""Pure per-district Huff allocation and root-scoped atomic CSV adapter.

Defaults: alpha=1.2, beta=1.5; utility arithmetic matches the live consumer
for the same explicit parameters. Missing routes/population stay observational
nulls; source corruption is fatal before publication. Islands are geographic.
"""
from __future__ import annotations

import argparse
import json
import math
from numbers import Real
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.analyze import producer_io
from scripts.clean.standardize import DATA_DIR

# Deliberately server-independent; equality/order against the frozen consumer
# PORT_MATRIX_DATA is guarded by durable tests.
PORT_MATRIX_DATA = {
    "drive_hours_to_paradip": ("Paradip Port", 145.38),
    "drive_hours_to_deendayal_(kandla)": ("Deendayal Port (Kandla)", 132.50),
    "drive_hours_to_jawaharlal_nehru_(jnpt/navi_mumbai)": ("Jawaharlal Nehru Port (JNPT)", 86.00),
    "drive_hours_to_visakhapatnam": ("Visakhapatnam Port", 81.00),
    "drive_hours_to_kolkata_(syama_prasad_mookerjee)_incl._haldia_dock_complex": ("Syama Prasad Mookerjee Port (Kolkata/Haldia)", 66.00),
    "drive_hours_to_mumbai": ("Mumbai Port", 65.00),
    "drive_hours_to_chennai": ("Chennai Port", 54.50),
    "drive_hours_to_kamarajar_(ennore)": ("Kamarajar Port (Ennore)", 48.00),
    "drive_hours_to_new_mangalore": ("New Mangalore Port", 46.00),
    "drive_hours_to_v.o._chidambaranar_(tuticorin)": ("V.O. Chidambaranar Port (Tuticorin)", 41.50),
    "drive_hours_to_cochin": ("Cochin Port", 36.50),
    "drive_hours_to_mormugao": ("Mormugao Port", 20.50),
}


def _number(value, name, *, positive=False):
    if (isinstance(value, (bool, np.bool_)) or not isinstance(value, Real)
            or not math.isfinite(value) or value < 0 or (positive and value == 0)):
        raise ValueError(f"Invalid {name}: expected finite {'positive' if positive else 'nonnegative'} number")
    return float(value)


def _missing(value):
    return value is None or value is pd.NA or (isinstance(value, Real) and math.isnan(value))


def _power(base, exponent):
    try:
        value = base ** exponent
    except (OverflowError, ZeroDivisionError):
        raise ValueError("Gravity input exceeds numerical range") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Gravity input exceeds numerical range")
    return value


def _capacities(overrides):
    capacities = {key: cap for key, (_, cap) in PORT_MATRIX_DATA.items()}
    if overrides is not None:
        if not isinstance(overrides, dict):
            raise ValueError("Port capacities must be a mapping")
        if set(overrides) - set(capacities):
            raise ValueError("Unknown port capacity key")
        for key, value in overrides.items():
            capacities[key] = _number(value, "port capacity", positive=True)
    return capacities


def build_port_hinterland(df_matrix, population, port_data=None, alpha=1.2, beta=1.5):
    """Pure allocation; port_data is {matrix_time_column: positive_capacity}.

    Partial capacity overrides retain all twelve ports and fixed source order.
    All twelve time/km pairs are required. Paired nulls are valid unreachability;
    partial nulls and malformed/negative/nonfinite/bool numbers are corruption.
    Zero times are excluded, matching live gravity; zero distances remain valid.
    Population enrichment uses the primary matrix identities through W0.
    """
    alpha, beta = _number(alpha, "alpha"), _number(beta, "beta")
    capacities = _capacities(port_data)
    required = {"state", "district", "district_code"}
    for col in PORT_MATRIX_DATA:
        required.update((col, col.replace("drive_hours_to_", "road_km_to_", 1)))
    if not required.issubset(df_matrix.columns):
        raise ValueError("Missing required matrix identity or paired time/km columns")
    populations = producer_io.district_population(df_matrix, population).tolist()
    powers = {col: _power(cap, alpha) for col, cap in capacities.items()}
    results = []
    for row, pop in zip(df_matrix.to_dict("records"), populations):
        utilities, times = {}, {}
        for col, (name, _) in PORT_MATRIX_DATA.items():
            hours, km = row[col], row[col.replace("drive_hours_to_", "road_km_to_", 1)]
            missing_hours, missing_km = _missing(hours), _missing(km)
            if missing_hours != missing_km:
                raise ValueError("Partial null matrix route pair")
            if missing_hours:
                continue
            hours = _number(hours, "stored port drive time")
            _number(km, "stored port road distance")
            if hours == 0:
                continue
            utility = powers[col] / _power(hours / 5.0, beta)
            if not math.isfinite(utility) or utility <= 0:
                raise ValueError("Gravity input exceeds numerical range")
            utilities[name], times[name] = utility, hours
        island = producer_io.is_island_state(row["state"])
        result = dict(state=row["state"], district=row["district"], district_code=row["district_code"],
            pop_2011=pop, is_island=island, primary_port="N/A (Island)" if island else "N/A",
            primary_port_probability=np.nan, primary_port_drive_hours=np.nan,
            secondary_port="N/A", secondary_port_probability=np.nan, secondary_port_drive_hours=np.nan,
            hinterland_category="Island / Isolated" if island else "Isolated", contestability_index=np.nan)
        if utilities and not island:
            total = sum(utilities.values())
            if not math.isfinite(total) or total <= 0:
                raise ValueError("Gravity utility total exceeds numerical range")
            probabilities = {name: utility / total for name, utility in utilities.items()}
            ranked = sorted(probabilities.items(), key=lambda pair: pair[1], reverse=True)
            p1, prob1 = ranked[0]
            p2, prob2 = ranked[1] if len(ranked) > 1 else ("None", 0.0)
            category = ("Captive Hinterland" if prob1 >= 0.75 else
                        "Dominant / Mildly Contested" if prob1 >= 0.50 else
                        "Highly Contested / Split Hinterland")
            result.update(primary_port=p1, primary_port_probability=round(prob1 * 100.0, 1),
                primary_port_drive_hours=round(times[p1], 2), secondary_port=p2,
                secondary_port_probability=round(prob2 * 100.0, 1),
                secondary_port_drive_hours=round(times.get(p2, 0.0), 2),
                hinterland_category=category, contestability_index=round(1.0 - (prob1 - prob2), 3))
        results.append(result)
    columns = ["state", "district", "district_code", "pop_2011", "is_island", "primary_port",
        "primary_port_probability", "primary_port_drive_hours", "secondary_port",
        "secondary_port_probability", "secondary_port_drive_hours", "hinterland_category", "contestability_index"]
    return pd.DataFrame(results, columns=columns)


def run_port_gravity_model(alpha=1.2, beta=1.5, custom_capacities=None, output_csv=None,
                           *, data_dir=None, matrix_csv=None, population_csv=None):
    """Preserve positional calls and display-name custom-capacity compatibility.

    Keyword root/staged overrides never fall back to repository data. Publication
    occurs only after validating/calculating every row. Return the built frame.
    """
    root = Path(data_dir) if data_dir is not None else Path(os.environ.get("GIS4LOGISTICS_DATA_PATH", DATA_DIR))
    matrix_path = Path(matrix_csv) if matrix_csv is not None else root / "analysis/nh_district_port_matrix.csv"
    population_path = Path(population_csv) if population_csv is not None else root / "demographic/district_population_estimates.csv"
    overrides = None
    if custom_capacities is not None:
        if not isinstance(custom_capacities, dict):
            raise ValueError("Port capacities must be a mapping")
        names = {name: col for col, (name, _) in PORT_MATRIX_DATA.items()}
        if set(custom_capacities) - set(names):
            raise ValueError("Unknown custom port capacity key")
        overrides = {names[name]: value for name, value in custom_capacities.items()}
    frame = build_port_hinterland(pd.read_csv(matrix_path), pd.read_csv(population_path), overrides, alpha, beta)
    path = Path(output_csv) if output_csv is not None else root / "analysis/district_port_hinterland_catchment.csv"
    producer_io.atomic_write_csv(frame, path)
    return frame


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alpha", type=float, default=1.2)
    parser.add_argument("--beta", type=float, default=1.5)
    parser.add_argument("--config", type=Path, help="JSON with alpha, beta and port_capacities")
    parser.add_argument("--output-csv", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--matrix-csv", type=Path)
    parser.add_argument("--population-csv", type=Path)
    args = parser.parse_args(argv)
    cfg = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
    return run_port_gravity_model(cfg.get("alpha", args.alpha), cfg.get("beta", args.beta),
        cfg.get("port_capacities"), args.output_csv, data_dir=args.data_dir,
        matrix_csv=args.matrix_csv, population_csv=args.population_csv)


if __name__ == "__main__":
    main()