"""Fetch Census 2011 district key indicators and standardize them.

Source: community-digitized district table (RajaBhavesh/India_Census_2011_
Analysis_Using_Python), which reconciles exactly with official Census 2011
PCA totals (validated: 640 districts, total population 1,210,854,977,
state totals match). Official tables: censusindia.gov.in (Government Open
Data License — GODL-India).

Validation gate: script fails if totals do not reconcile with official figures.

Usage:
    python scripts/fetch/fetch_demographics.py
"""
from __future__ import annotations

import pathlib
import sys

import pandas as pd
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import clean_state_name, DATA_DIR  # noqa: E402

SOURCE_URL = ("https://raw.githubusercontent.com/RajaBhavesh/"
              "India_Census_2011_Analysis_Using_Python/main/Project5/file.csv")

OFFICIAL_2011 = {
    "districts": 640,
    "total_population": 1_210_854_977,
    "top_state_pop": {"Uttar Pradesh": 199_812_341, "Maharashtra": 112_374_333},
}

KEEP_COLS = ["census_district_code", "state", "district", "Population", "Male",
             "Female", "Literate", "Workers", "Cultivator_Workers",
             "Agricultural_Workers", "Secondary_Education", "Higher_Education",
             "Graduate_Education", "Age_Group_0_29", "Age_Group_30_49", "Age_Group_50"]


def main() -> None:
    out_dir = DATA_DIR / "demographic"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "census2011_district_raw.csv"
    if not raw_path.exists():
        print(f"Downloading {SOURCE_URL} ...")
        r = requests.get(SOURCE_URL, timeout=120)
        r.raise_for_status()
        raw_path.write_bytes(r.content)

    df = pd.read_csv(raw_path)
    df["state"] = df["State_name"].str.strip().str.lower().map(clean_state_name)
    if df["state"].isna().any():
        raise ValueError(f"Unmapped states: {df['State_name'][df.state.isna()].unique()}")

    # --- validation gate ---
    checks = {
        "districts == 640": len(df) == OFFICIAL_2011["districts"],
        "population reconciles": df["Population"].sum() == OFFICIAL_2011["total_population"],
        "male+female == total": (df["Male"].sum() + df["Female"].sum()
                                 == df["Population"].sum()),
    }
    state_pop = df.groupby("state")["Population"].sum()
    for st, pop in OFFICIAL_2011["top_state_pop"].items():
        checks[f"{st} pop == {pop:,}"] = state_pop.get(st) == pop
    print("Validation:")
    for k, v in checks.items():
        print(f"  {k}: {v}")
    if not all(checks.values()):
        raise SystemExit("Validation FAILED — source data does not reconcile; refusing to write")

    std = df.rename(columns={"District_code": "census_district_code",
                             "District_name": "district"})[KEEP_COLS]
    out = out_dir / "census2011_district_key_indicators.csv"
    std.to_csv(out, index=False)
    print(f"Wrote {len(std)} districts -> {out}")


if __name__ == "__main__":
    main()
