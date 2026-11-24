"""Fetch official Census 2011 Sub-District (Tehsil/Taluka) PCA & Table A-01 data.

Authoritative GODL-India sources from the Office of the Registrar General &
Census Commissioner, India (ORGI) Census Digital Library (NADA):
  1. Catalog #42559 — Primary Census Abstract (India / State / District /
     Sub-District / Town): ``2011-IndiaStateDistSbDistTwn-0000.xlsx``
  2. Catalog #42526 — Table A-01 (Number of Villages, Towns, Households,
     Population and Area): ``A-1_NO_OF_VILLAGES_TOWNS_HOUSEHOLDS_POPULATION_AND_AREA.xlsx``

Parses both ``.xlsx`` workbooks using Python stdlib ``zipfile`` +
``xml.etree.ElementTree`` (zero extra dependencies), joins Total / Rural /
Urban rows and Table A-01 area/village/town counts for all 5,988 Census 2011
sub-districts, and enforces a hard validation gate reconciling 100% exactly
against ``data/demographic/census2011_district_key_indicators.csv`` (640/640
districts, 1,210,854,977 total population, 0 diff).

Output:
  data/demographic/census2011_subdistrict_key_indicators.csv

Usage:
  python scripts/fetch/fetch_subdistrict_census2011.py
"""
from __future__ import annotations

import argparse
import math
import pathlib
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from typing import Iterator

import pandas as pd

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
if __name__ == "__main__" and str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.clean.standardize import DATA_DIR, clean_state_name, http_session

PCA_URL = (
    "https://censusindia.gov.in/nada/index.php/catalog/42559/download/46185/"
    "2011-IndiaStateDistSbDistTwn-0000.xlsx"
)
A1_URL = (
    "https://censusindia.gov.in/nada/index.php/catalog/42526/download/46152/"
    "A-1_NO_OF_VILLAGES_TOWNS_HOUSEHOLDS_POPULATION_AND_AREA.xlsx"
)

RAW_DIR = DATA_DIR / "raw" / "census2011"
PCA_RAW = RAW_DIR / "2011-IndiaStateDistSbDistTwn-0000.xlsx"
A1_RAW = RAW_DIR / "A-1_NO_OF_VILLAGES_TOWNS_HOUSEHOLDS_POPULATION_AND_AREA.xlsx"
DISTRICT_CSV = DATA_DIR / "demographic" / "census2011_district_key_indicators.csv"
OUT_CSV = DATA_DIR / "demographic" / "census2011_subdistrict_key_indicators.csv"

OFFICIAL_SUBDISTRICTS = 5_988
OFFICIAL_DISTRICTS = 640
OFFICIAL_TOTAL_POP = 1_210_854_977
OFFICIAL_NON_SUBDISTRICT_ROWS = 30

COMPOSITE_KEY = [
    "census_state_code",
    "census_district_code",
    "census_subdistrict_code",
]

OUTPUT_COLUMNS = [
    "census_state_code",
    "census_district_code",
    "census_subdistrict_code",
    "state",
    "district",
    "sub_district",
    "is_non_subdistrict_area",
    "households",
    "population",
    "male",
    "female",
    "rural_population",
    "urban_population",
    "pop_0_6",
    "sc_pop",
    "st_pop",
    "literates",
    "illiterates",
    "total_workers",
    "main_workers",
    "marginal_workers",
    "non_workers",
    "cultivators",
    "agricultural_labourers",
    "household_industry_workers",
    "other_workers",
    "area_sqkm",
    "inhabited_villages",
    "uninhabited_villages",
    "towns",
]

_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_ROW_TAG = f"{{{_SHEET_NS}}}row"
_C_TAG = f"{{{_SHEET_NS}}}c"
_V_TAG = f"{{{_SHEET_NS}}}v"
_IS_TAG = f"{{{_SHEET_NS}}}is"
_T_TAG = f"{{{_SHEET_NS}}}t"
_SI_TAG = f"{{{_SHEET_NS}}}si"
_COL_RE = re.compile(r"([A-Z]+)")


def _col_to_idx(col_letters: str) -> int:
    idx = 0
    for ch in col_letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def iter_xlsx_rows(
    path: pathlib.Path,
    sheet: str = "xl/worksheets/sheet1.xml",
) -> Iterator[dict[int, str | None]]:
    """Stream rows from an ``.xlsx`` worksheet as ``{col_index: value}`` dicts."""
    with zipfile.ZipFile(path) as zf:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(_SI_TAG):
                shared_strings.append(
                    "".join(t.text or "" for t in si.iter(_T_TAG))
                )
        with zf.open(sheet) as fh:
            for _event, elem in ET.iterparse(fh, events=("end",)):
                if elem.tag != _ROW_TAG:
                    continue
                row: dict[int, str | None] = {}
                for cell in elem.findall(_C_TAG):
                    ref = cell.attrib.get("r", "")
                    m = _COL_RE.match(ref)
                    if not m:
                        continue
                    ci = _col_to_idx(m.group(1))
                    ct = cell.attrib.get("t")
                    v = cell.find(_V_TAG)
                    if ct == "s" and v is not None and v.text is not None:
                        val: str | None = shared_strings[int(v.text)]
                    elif ct == "inlineStr":
                        is_elem = cell.find(_IS_TAG)
                        val = (
                            "".join(t.text or "" for t in is_elem.iter(_T_TAG))
                            if is_elem is not None
                            else ""
                        )
                    elif v is not None:
                        val = v.text
                    else:
                        val = None
                    row[ci] = val
                yield row
                elem.clear()


def _to_int(val: str | None) -> int:
    if val is None or str(val).strip() == "":
        return 0
    return int(round(float(val)))


def _to_float_or_nan(val: str | None) -> float:
    if val is None:
        return math.nan
    s = str(val).strip()
    if not s or s.upper() in {"NA", "N.A.", "N/A", "-"}:
        return math.nan
    return round(float(s), 2)


def download_if_missing(url: str, dest: pathlib.Path, session=None) -> pathlib.Path:
    """Download ``url`` to ``dest`` unless a valid cached workbook already exists."""
    if dest.exists() and zipfile.is_zipfile(dest):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    sess = session or http_session()
    part = dest.with_suffix(dest.suffix + ".part")
    print(f"Downloading {url} -> {dest} ...")
    with sess.get(url, timeout=180, stream=True) as resp:
        resp.raise_for_status()
        with open(part, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if chunk:
                    fh.write(chunk)
    if not zipfile.is_zipfile(part):
        part.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded file is not a valid xlsx zip: {url}")
    part.replace(dest)
    return dest


def parse_pca_subdistricts(pca_path: pathlib.Path) -> pd.DataFrame:
    """Parse ORGI #42559 PCA workbook into one row per Census 2011 sub-district."""
    headers: dict[str, int] = {}
    state_names: dict[str, str] = {}
    district_names: dict[tuple[str, str], str] = {}
    totals: dict[tuple[str, str, str], dict] = {}
    rural_pop: dict[tuple[str, str, str], int] = {}
    urban_pop: dict[tuple[str, str, str], int] = {}

    for row_idx, row in enumerate(iter_xlsx_rows(pca_path)):
        if row_idx == 0:
            headers = {str(v).strip(): k for k, v in row.items() if v is not None}
            continue
        lvl = str(row.get(headers["Level"]) or "").strip()
        tru = str(row.get(headers["TRU"]) or "").strip()
        st = str(row.get(headers["State"]) or "").strip().zfill(2)
        dt = str(row.get(headers["District"]) or "").strip().zfill(3)
        sd = str(row.get(headers["Subdistt"]) or "").strip().zfill(5)
        name = str(row.get(headers["Name"]) or "").strip()

        if lvl == "STATE" and tru == "Total":
            canonical = clean_state_name(name)
            if canonical is None:
                raise ValueError(f"Unmapped Census 2011 state name: {name!r} (code {st})")
            state_names[st] = canonical
        elif lvl == "DISTRICT" and tru == "Total":
            district_names[(st, dt)] = name
        elif lvl == "SUB-DISTRICT":
            key = (st, dt, sd)
            if tru == "Total":
                if key in totals:
                    raise ValueError(f"Duplicate SUB-DISTRICT Total key in PCA: {key}")
                totals[key] = {
                    "census_state_code": st,
                    "census_district_code": dt,
                    "census_subdistrict_code": sd,
                    "sub_district": name,
                    "is_non_subdistrict_area": sd == "99999",
                    "households": _to_int(row.get(headers["No_HH"])),
                    "population": _to_int(row.get(headers["TOT_P"])),
                    "male": _to_int(row.get(headers["TOT_M"])),
                    "female": _to_int(row.get(headers["TOT_F"])),
                    "pop_0_6": _to_int(row.get(headers["P_06"])),
                    "sc_pop": _to_int(row.get(headers["P_SC"])),
                    "st_pop": _to_int(row.get(headers["P_ST"])),
                    "literates": _to_int(row.get(headers["P_LIT"])),
                    "illiterates": _to_int(row.get(headers["P_ILL"])),
                    "total_workers": _to_int(row.get(headers["TOT_WORK_P"])),
                    "main_workers": _to_int(row.get(headers["MAINWORK_P"])),
                    "marginal_workers": _to_int(row.get(headers["MARGWORK_P"])),
                    "non_workers": _to_int(row.get(headers["NON_WORK_P"])),
                    "cultivators": (
                        _to_int(row.get(headers["MAIN_CL_P"]))
                        + _to_int(row.get(headers["MARG_CL_P"]))
                    ),
                    "agricultural_labourers": (
                        _to_int(row.get(headers["MAIN_AL_P"]))
                        + _to_int(row.get(headers["MARG_AL_P"]))
                    ),
                    "household_industry_workers": (
                        _to_int(row.get(headers["MAIN_HH_P"]))
                        + _to_int(row.get(headers["MARG_HH_P"]))
                    ),
                    "other_workers": (
                        _to_int(row.get(headers["MAIN_OT_P"]))
                        + _to_int(row.get(headers["MARG_OT_P"]))
                    ),
                }
            elif tru == "Rural":
                rural_pop[key] = _to_int(row.get(headers["TOT_P"]))
            elif tru == "Urban":
                urban_pop[key] = _to_int(row.get(headers["TOT_P"]))

    records = []
    for key, rec in totals.items():
        st, dt, _sd = key
        rec["state"] = state_names.get(st)
        rec["district"] = district_names.get((st, dt))
        rec["rural_population"] = rural_pop.get(key, 0)
        rec["urban_population"] = urban_pop.get(key, 0)
        records.append(rec)

    df = pd.DataFrame(records)
    for col in COMPOSITE_KEY:
        df[col] = df[col].astype("string")
    return df


def parse_a1_subdistricts(a1_path: pathlib.Path) -> pd.DataFrame:
    """Parse ORGI #42526 Table A-01 for sub-district area, village and town counts."""
    records = []
    seen: set[tuple[str, str, str]] = set()
    for row_idx, row in enumerate(iter_xlsx_rows(a1_path)):
        if row_idx < 4:
            continue
        lvl = str(row.get(3) or "").strip()
        tru = str(row.get(5) or "").strip()
        if lvl.upper() != "SUB-DISTRICT" or tru != "Total":
            continue
        st = str(row.get(0) or "").strip().zfill(2)
        dt = str(row.get(1) or "").strip().zfill(3)
        sd = str(row.get(2) or "").strip().zfill(5)
        key = (st, dt, sd)
        if key in seen:
            raise ValueError(f"Duplicate SUB-DISTRICT Total key in Table A-01: {key}")
        seen.add(key)
        records.append({
            "census_state_code": st,
            "census_district_code": dt,
            "census_subdistrict_code": sd,
            "area_sqkm": _to_float_or_nan(row.get(13)),
            "inhabited_villages": _to_int(row.get(6)),
            "uninhabited_villages": _to_int(row.get(7)),
            "towns": _to_int(row.get(8)),
        })

    df = pd.DataFrame(records)
    for col in COMPOSITE_KEY:
        df[col] = df[col].astype("string")
    return df


def build_subdistrict_table(
    pca_path: pathlib.Path,
    a1_path: pathlib.Path,
) -> pd.DataFrame:
    """Merge PCA and Table A-01 sub-district extracts on the composite Census key."""
    pca = parse_pca_subdistricts(pca_path)
    a1 = parse_a1_subdistricts(a1_path)
    merged = pca.merge(a1, on=COMPOSITE_KEY, how="outer", validate="one_to_one", indicator=True)
    if (merged["_merge"] != "both").any():
        counts = merged["_merge"].value_counts().to_dict()
        raise ValueError(f"PCA and Table A-01 sub-district keys do not match 1:1: {counts}")
    merged = merged.drop(columns=["_merge"])
    merged = merged[OUTPUT_COLUMNS].sort_values(COMPOSITE_KEY).reset_index(drop=True)
    return merged


def read_subdistrict_csv(path: pathlib.Path) -> pd.DataFrame:
    """Read the committed sub-district CSV preserving zero-padded string keys."""
    return pd.read_csv(
        path,
        dtype={col: "string" for col in COMPOSITE_KEY},
    )


def validation_checks(
    df: pd.DataFrame,
    district_df: pd.DataFrame,
) -> dict[str, bool]:
    """Validation gate: sub-district PCA table must reconcile 100% with official totals."""
    key_cols_present = all(c in df.columns for c in COMPOSITE_KEY)
    key_unique = (
        key_cols_present
        and bool(df[COMPOSITE_KEY].notna().all().all())
        and not bool(df.duplicated(COMPOSITE_KEY).any())
    )

    # Reconcile every one of the 640 Census 2011 districts against district_df
    dist_ref = (
        district_df.assign(
            _dcode=district_df["census_district_code"].astype(int).map(lambda c: f"{c:03d}")
        )
        .groupby("_dcode")["Population"]
        .sum()
    )
    sub_by_dist = (
        df.assign(
            _dcode=df["census_district_code"].astype(str).str.strip().str.zfill(3)
        )
        .groupby("_dcode")["population"]
        .sum()
    )
    districts_match = (
        len(dist_ref) == OFFICIAL_DISTRICTS
        and len(sub_by_dist) == OFFICIAL_DISTRICTS
        and set(dist_ref.index) == set(sub_by_dist.index)
        and bool((sub_by_dist.reindex(dist_ref.index) == dist_ref).all())
    )

    checks = {
        "subdistricts == 5988": len(df) == OFFICIAL_SUBDISTRICTS,
        "composite key unique": key_unique,
        "population == 1,210,854,977": int(df["population"].sum()) == OFFICIAL_TOTAL_POP,
        "male + female == population": bool((df["male"] + df["female"] == df["population"]).all()),
        "rural + urban == population": bool(
            (df["rural_population"] + df["urban_population"] == df["population"]).all()
        ),
        "640/640 districts match district table": districts_match,
        "no null states or districts": bool(df["state"].notna().all() and df["district"].notna().all()),
        "non-subdistrict (99999) flag consistent": (
            int(df["is_non_subdistrict_area"].sum()) == OFFICIAL_NON_SUBDISTRICT_ROWS
            and bool(
                (df["is_non_subdistrict_area"] == (df["census_subdistrict_code"] == "99999")).all()
            )
        ),
        "literates + illiterates == population": bool(
            (df["literates"] + df["illiterates"] == df["population"]).all()
        ),
        "workers + non_workers == population": bool(
            (df["total_workers"] + df["non_workers"] == df["population"]).all()
        ),
        "main + marginal == total_workers": bool(
            (df["main_workers"] + df["marginal_workers"] == df["total_workers"]).all()
        ),
        "worker categories == total_workers": bool(
            (
                df["cultivators"]
                + df["agricultural_labourers"]
                + df["household_industry_workers"]
                + df["other_workers"]
                == df["total_workers"]
            ).all()
        ),
    }
    return {k: bool(v) for k, v in checks.items()}


def main() -> None:
    argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    ).parse_args()

    session = http_session()
    pca_path = download_if_missing(PCA_URL, PCA_RAW, session=session)
    a1_path = download_if_missing(A1_URL, A1_RAW, session=session)

    df = build_subdistrict_table(pca_path, a1_path)
    district_df = pd.read_csv(DISTRICT_CSV)

    checks = validation_checks(df, district_df)
    print("Validation:")
    for name, passed in checks.items():
        print(f"  {name}: {passed}")
    if not all(checks.values()):
        raise SystemExit("Validation FAILED — refusing to write sub-district Census 2011 table")

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False, lineterminator="\n")
    print(
        f"Wrote {len(df):,} sub-districts across {df['census_district_code'].nunique()} districts "
        f"(total pop {int(df['population'].sum()):,}) -> {OUT_CSV}"
    )


if __name__ == "__main__":
    main()
