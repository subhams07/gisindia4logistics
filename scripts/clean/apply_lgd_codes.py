"""apply_lgd_codes.py — Re-key the current boundary layers to official LGD district codes.

The SoI ABDB archive numbers districts by their Census 2011 code (older
districts) or by a scheme of its own (newer ones); only a handful carry their
actual Local Government Directory code. This script maps every district of
india_districts_lgd to the official LGD directory snapshot
(data/administrative/lgd_districts.csv, scripts/fetch/fetch_lgd_directory.py):

  1. the LGD district whose Census 2011 code equals the ABDB code, else
  2. the same-state LGD district with the same normalised name, else
  3. the closest same-state name (difflib >= 0.8), else
  4. MANUAL below (decided by hand; None = not in the LGD directory).

and writes data/administrative/district_code_crosswalk.csv. It then sets
`district_code` to the LGD code in the district and sub-district layers
(GeoJSON, Parquet, GPKG), keeps the ABDB code as `abdb_district_code` and adds
`census2011_code` on the district layer, and moves Dadra & Nagar Haveli and
Daman & Diu to LGD state code 38. Idempotent: an already re-keyed layer is
mapped again from its `abdb_district_code`.

Usage: python scripts/clean/apply_lgd_codes.py
"""
from __future__ import annotations

import difflib
import json
import pathlib
import re
import sys
import unicodedata

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from standardize import DATA_DIR  # noqa: E402

ADMIN = DATA_DIR / "administrative"
LGD_PATH = ADMIN / "lgd_districts.csv"
CROSSWALK_PATH = ADMIN / "district_code_crosswalk.csv"
STATE_CODE_FIXES = {26: 38}  # Dadra & Nagar Haveli and Daman & Diu (merged UT, LGD 38)
MANUAL = {
    ("Assam", "SOUTH SALMARA"): 707,                # LGD: South Salmara Mancachar
    ("Chhattisgarh", "BALODA BAZAR"): 644,          # LGD: Balodabazar-Bhatapara
    ("Chhattisgarh", "BALRAMPUR"): 649,             # LGD: Balrampur-Ramanujganj
    ("Telangana", "KUMURAM BHEEM"): 699,            # LGD: Kumuram Bheem Asifabad
    ("Delhi", "SHAHDARA"): None,                    # not in LGD after Delhi's 2025 reorganisation
    ("Delhi", "NAZUL"): None,                       # not a district
    ("Jammu and Kashmir", "MIRPUR"): None,          # not in the LGD directory
    ("Jammu and Kashmir", "MUZAFFARABAD"): None,    # not in the LGD directory
}


def norm(name) -> str:
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    s = s.replace(">", "A").replace("@", "U").replace("\\", "I").replace("&", "AND")
    s = re.sub(r"\(.*?\)", "", s)
    return re.sub(r"[^A-Z0-9]", "", s)


def build_crosswalk(districts: pd.DataFrame, lgd: pd.DataFrame) -> pd.DataFrame:
    """One row per district polygon: ABDB code -> LGD code, with the rule that decided it."""
    source = districts["abdb_district_code"] if "abdb_district_code" in districts else districts["district_code"]
    rows = []
    for (_, r), abdb in zip(districts.iterrows(), source):
        state_code = int(r.state_code)
        state_code = {38: 26}.get(state_code, state_code)  # accept an already re-keyed layer
        lgd_state = STATE_CODE_FIXES.get(state_code, state_code)
        cand = lgd[lgd.state_code == lgd_state]
        key = (r.state, str(r.district).strip())
        if key in MANUAL:
            code, method = MANUAL[key], "manual"
        else:
            by_census = cand[cand.census2011_code == abdb] if pd.notna(abdb) else cand.iloc[:0]
            names = {norm(n): c for n, c in zip(cand.district_name, cand.district_code)}
            close = difflib.get_close_matches(norm(r.district), list(names), 1, 0.8)
            if len(by_census) == 1:
                code, method = int(by_census.district_code.iloc[0]), "census2011_code"
            elif norm(r.district) in names:
                code, method = names[norm(r.district)], "name"
            elif close:
                code, method = names[close[0]], "name_fuzzy"
            else:
                raise SystemExit(f"no LGD district for {key}; add it to MANUAL")
        match = cand[cand.district_code == code] if code is not None else cand.iloc[:0]
        rows.append({
            "state": r.state, "district": r.district, "lgd_state_code": lgd_state,
            "abdb_district_code": abdb, "district_code": code,
            "lgd_district_name": match.district_name.iloc[0] if len(match) else None,
            "census2011_code": match.census2011_code.iloc[0] if len(match) else None,
            "method": method,
        })
    xw = pd.DataFrame(rows)
    for col in ("abdb_district_code", "district_code", "census2011_code"):
        xw[col] = pd.to_numeric(xw[col]).astype("Int64")
    coded = xw.dropna(subset=["district_code"])
    if coded.duplicated("district_code").any():
        raise SystemExit("two polygons map to one LGD district; refusing to write")
    return xw


def code_maps(xw: pd.DataFrame):
    """(state_code, abdb code) -> LGD code, and abdb code -> census 2011 code, for coded source rows."""
    src = xw.dropna(subset=["abdb_district_code"])
    abdb_state = src.lgd_state_code.map(lambda c: {38: 26}.get(c, c))
    to_lgd = {(int(s), int(a)): (None if pd.isna(c) else int(c))
              for s, a, c in zip(abdb_state, src.abdb_district_code, src.district_code)}
    return to_lgd


def fixed_state(code) -> int:
    return STATE_CODE_FIXES.get(int(code), int(code))


def rewrite_geojson(path: pathlib.Path, edit) -> None:
    """Change feature properties only; geometry text and formatting are kept byte-for-byte."""
    fc = json.loads(path.read_bytes())
    for feature in fc["features"]:
        feature["properties"] = edit(feature["properties"])
    path.write_text(json.dumps(fc, separators=(",", ":")))


def main() -> None:
    lgd = pd.read_csv(LGD_PATH)
    lgd["census2011_code"] = lgd.census2011_code.astype("Int64")
    districts = gpd.read_parquet(ADMIN / "india_districts_lgd.parquet")
    xw = build_crosswalk(districts, lgd)
    xw.to_csv(CROSSWALK_PATH, index=False)
    print(f"crosswalk: {len(xw)} districts, methods {xw.method.value_counts().to_dict()}, "
          f"without LGD code: {int(xw.district_code.isna().sum())} -> {CROSSWALK_PATH}")
    to_lgd = code_maps(xw)

    unknown = set()

    def lgd_code(state_code, abdb):
        abdb = pd.to_numeric(abdb, errors="coerce")  # the GeoJSON spells some missing codes "NOT AVAILABLE"
        if pd.isna(abdb):
            return None
        key = ({38: 26}.get(int(state_code), int(state_code)), int(abdb))
        if key not in to_lgd:
            unknown.add(key)
            return None
        return to_lgd[key]

    # districts: row-aligned GeoJSON and Parquet
    src_codes = (districts["abdb_district_code"] if "abdb_district_code" in districts else districts["district_code"]).tolist()
    by_row = list(zip(xw.district_code, xw.census2011_code, src_codes))

    def edit_district(props, it=iter(by_row)):
        code, c2011, abdb = next(it)
        out = {}
        for k, v in props.items():
            if k in ("abdb_district_code", "census2011_code"):
                continue
            out[k] = fixed_state(v) if k == "state_code" else v
            if k == "district_code":
                out[k] = None if pd.isna(code) else int(code)
                out["census2011_code"] = None if pd.isna(c2011) else int(c2011)
                out["abdb_district_code"] = None if pd.isna(abdb) else int(abdb)
        return out
    rewrite_geojson(ADMIN / "india_districts_lgd.geojson", edit_district)
    districts["abdb_district_code"] = pd.Series(src_codes, index=districts.index, dtype="float64")
    districts["district_code"] = xw.district_code.astype("float64").to_numpy()
    districts["census2011_code"] = xw.census2011_code.astype("float64").to_numpy()
    districts["state_code"] = districts.state_code.map(fixed_state).astype("int32")
    cols = [c for c in districts.columns if c not in ("census2011_code", "abdb_district_code", "geometry")]
    at = cols.index("district_code") + 1
    districts = districts[cols[:at] + ["census2011_code", "abdb_district_code"] + cols[at:] + ["geometry"]]
    districts.to_parquet(ADMIN / "india_districts_lgd.parquet", engine="pyarrow", compression="snappy", index=False)

    # sub-districts: district_code follows the district layer (strings in the GeoJSON),
    # with the ABDB code kept as abdb_district_code
    def edit_subdistrict(props):
        abdb = props.get("abdb_district_code", props["district_code"])
        code = lgd_code(props["state_code"], abdb)
        out = {}
        for k, v in props.items():
            if k == "abdb_district_code":
                continue
            out[k] = str(fixed_state(v)) if k == "state_code" else v
            if k == "district_code":
                out[k] = None if code is None else str(code)
                out["abdb_district_code"] = abdb
        return out
    rewrite_geojson(ADMIN / "india_subdistricts_lgd.geojson", edit_subdistrict)
    for path in (ADMIN / "india_subdistricts_lgd.parquet", ADMIN / "india_subdistricts_lgd.gpkg"):
        sub = gpd.read_parquet(path) if path.suffix == ".parquet" else gpd.read_file(path)
        abdb = sub["abdb_district_code"] if "abdb_district_code" in sub else sub["district_code"]
        codes = [lgd_code(s, c) for s, c in zip(sub.state_code, abdb)]
        cols = [c for c in sub.columns if c != "abdb_district_code"]
        sub = sub.assign(abdb_district_code=abdb.astype("float64"),
                         district_code=pd.to_numeric(pd.Series(codes, index=sub.index)).astype("float64"),
                         state_code=sub.state_code.map(fixed_state).astype("int64"))
        at = cols.index("district_code") + 1
        sub = sub[cols[:at] + ["abdb_district_code"] + cols[at:]]
        if path.suffix == ".parquet":
            sub.to_parquet(path, engine="pyarrow", compression="snappy", index=False)
        else:
            sub.to_file(path, layer="subdistricts", driver="GPKG")
    if unknown:
        raise SystemExit(f"sub-district district codes absent from the district layer: {sorted(unknown)}")
    print("re-keyed: india_districts_lgd (.geojson/.parquet), india_subdistricts_lgd (.geojson/.parquet/.gpkg)")


if __name__ == "__main__":
    main()
