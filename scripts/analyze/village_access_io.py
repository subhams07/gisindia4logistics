"""village_access_io.py — Read a `<state>_village_access.csv` with explicit dtypes.

pandas' defaults are wrong for these tables, and differently so across pandas
versions:

- village codes are text, often zero-padded ('071955' in Rajasthan, Himachal,
  Uttarakhand, J&K...) or alphanumeric ('804064N003'); a default read parses an
  all-digit column as integers and drops the leading zeros;
- `.astype(str)` turns a missing value into the text 'nan' on pandas 2 but keeps
  it missing on pandas 3;
- the default NA tokens ('NA', 'None', 'null', ...) turn real text into missing
  values.

So text columns are read as the nullable "string" dtype, the district codes as
"Int64", distances and coordinates as float, and only an empty cell is missing.
The result is the same on pandas 2 and 3.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

TEXT_COLUMNS = frozenset({"village", "village_code", "unit", "district", "district_assignment", "source_district"})
INTEGER_COLUMNS = frozenset({"district_code", "source_district_code"})


def village_access_dtypes(columns) -> dict:
    dtypes = {}
    for column in columns:
        if column in TEXT_COLUMNS or column.startswith("nearest_"):
            dtypes[column] = "string"
        elif column in INTEGER_COLUMNS:
            dtypes[column] = "Int64"
        elif column.startswith("dist_") or column in ("latitude", "longitude"):
            dtypes[column] = "float64"
    return dtypes


def read_village_access_csv(path, usecols=None) -> pd.DataFrame:
    """A village access table with version-independent dtypes (see the module docstring)."""
    path = Path(path)
    header = pd.read_csv(path, nrows=0).columns
    columns = header if usecols is None else [c for c in header if c in set(usecols)]
    return pd.read_csv(path, usecols=usecols, dtype=village_access_dtypes(columns),
                       keep_default_na=False, na_values=[""])
