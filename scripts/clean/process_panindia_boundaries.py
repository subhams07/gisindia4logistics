"""Standardize the PAN INDIA State/District/Sub-district boundary dataset.

Input: LGD-coded shapefiles (national LCC projection) provided by the repo
maintainer — schema matches Survey of India's administrative boundary
database (STATE_LGD / DIST_LGD / SUBDIS_LGD columns). Source archive:
data/raw/panindia/State_District_Subdistrict_PAN INDIA.rar
(not committed; place the extracted folder under data/raw/panindia/)

Outputs (committed, simplified):
    data/administrative/india_states_lgd.geojson        (~36 features, current)
    data/administrative/india_districts_lgd.geojson     (780 current districts)
    data/administrative/india_subdistricts_lgd.gpkg     (~6,600 talukas/tehsils)

These are CURRENT administrative units — they complement the
Census-2011-vintage district file (india_districts.geojson) used for joining
Census tables. The source's DIST_LGD column is not the LGD district code: it
holds the Census 2011 code for older districts and a scheme of its own for
newer ones. After regenerating, refresh the Parquet copies
(scripts/clean/export_geoparquet.py) and then run
scripts/clean/apply_lgd_codes.py, which sets district_code to the official
LGD code (keeping the source value as abdb_district_code). 'DISPUTED' inter-state slivers are kept out of the states layer
and flagged in district/subdistrict layers via `remarks`.

Usage:
    python scripts/clean/process_panindia_boundaries.py
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from standardize import DATA_DIR, clean_state_name, write_geojson  # noqa: E402

SRC = DATA_DIR / "raw" / "panindia" / "State_District_Subdistrict_PAN INDIA"
OUT = DATA_DIR / "administrative"

SCHEMAS = {
    "states": {
        "path": SRC / "State Boundary" / "State Boundary.shp",
        "rename": {"STATE": "state"},
        "keep": ["state", "geometry"],
    },
    "districts": {
        "path": SRC / "District_Subdistrict_PAN INDIA" / "District Boundary.shp",
        "rename": {"STATE_UT": "state", "STATE_LGD": "state_code",
                   "DISTRICT": "district", "DIST_LGD": "district_code",
                   "REMARKS": "remarks"},
        "keep": ["state", "state_code", "district", "district_code", "remarks", "geometry"],
    },
    "subdistricts": {
        "path": SRC / "District_Subdistrict_PAN INDIA" / "Sub_district Boundary.shp",
        "rename": {"STATE_UT": "state", "STATE_LGD": "state_code",
                   "DISTRICT": "district", "DIST_LGD": "district_code",
                   "SUB_DIST": "sub_district", "SUBDIS_LGD": "sub_district_code",
                   "SUBDIS_TYP": "sub_district_type", "REMARKS": "remarks"},
        "keep": ["state", "state_code", "district", "district_code", "sub_district",
                 "sub_district_code", "sub_district_type", "remarks", "geometry"],
    },
}

# per-layer simplification tolerance (degrees) tuned to keep files < 10 MB
SIMPLIFY = {"states": 0.001, "districts": 0.0012, "subdistricts": 0.0012}

# provider's export mangled some name characters (legacy-font artifact):
# '<' stands for 'a', '>' for 'A', '#' for 'u' (e.g. BR>HMAUR=BRAHMAUR,
# Dh<rw<d=DHARWAD, Bengal#ru=BENGALURU) — verified on samples across states
NAME_CHAR_FIXES = {"<": "a", ">": "A", "#": "u"}
# '@' stands for u (DEHRAD@N=DEHRADUN, P@NCH=PUNCH) and a backslash for i (B<backslash>dar=Bidar);
# their case follows the neighbouring letters.
CASED_CHAR_FIXES = {"@": "u", "\\": "i"}
NAME_OVERRIDES = {"SOUTH 24PARGANAS": "SOUTH 24 PARGANAS"}

# sub-district rows whose DIST_LGD disagrees with the district layer:
# (state, district, wrong code) -> source code. DIST_LGD is the Census 2011 code
# for older districts; the source's 4 Dhule talukas (Dhule, Sakri, Shirpur,
# Sindkhede) carry 598, Kerala's Alappuzha in that numbering; Maharashtra's
# Census codes run 497-531 and the district layer has DHULE = 498 (LGD 474).
# (Malegaon taluka stays in Nashik 516 — the source has no Malegaon district.)
SUBDISTRICT_DISTRICT_CODE_FIXES = {("Maharashtra", "DHULE", 598): 498}


SUB_DISTRICT_CODE_PLACEHOLDERS = frozenset({"not available", "new mandal"})


def normalize_sub_district_code(value) -> str | None:
    """Normalize a raw ABDB SUBDIS_LGD value without truncating carveout suffixes.

    - NaN/None/blank and placeholders ('NOT AVAILABLE', 'NEW MANDAL', case-insensitive) -> None
    - Pure ASCII digits ('00285') -> str(int(s)) ('285') so numeric LGD joins stay intact
    - Alphanumeric carveout codes ('00285N001', '07314NOO1') -> stripped string verbatim
    """
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        raise TypeError(f"unexpected bool for sub_district_code: {value!r}")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    s = str(value).strip()
    if not s or s.casefold() in SUB_DISTRICT_CODE_PLACEHOLDERS:
        return None
    if re.fullmatch(r"[0-9]+(?:\.0+)?", s):
        return str(int(s.split(".", 1)[0]))
    return s


def fix_mojibake(s):
    import pandas as pd
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return s
    s = str(s)
    s = s.replace("|", "I")  # corrupted I in source names (PURBA MEDIN|PUR)
    for bad, good in NAME_CHAR_FIXES.items():
        s = s.replace(bad, good)
    chars = list(s)
    for i, ch in enumerate(chars):
        if ch in CASED_CHAR_FIXES:
            near = [c for c in (s[i - 1] if i else "", s[i + 1] if i + 1 < len(s) else "") if c.isalpha()]
            lower = any(c.islower() for c in near)
            chars[i] = CASED_CHAR_FIXES[ch] if lower else CASED_CHAR_FIXES[ch].upper()
    s = " ".join("".join(chars).split())
    return NAME_OVERRIDES.get(s, s)


def process(layer: str) -> gpd.GeoDataFrame:
    cfg = SCHEMAS[layer]
    gdf = gpd.read_file(cfg["path"]).rename(columns=cfg["rename"])
    gdf = gdf.to_crs(4326)

    # drop inter-state disputed slivers: named in the states layer, and in
    # district/subdistrict layers as rows with no state/district and a
    # REMARKS like "DISPUTED (RAJSTHAN & GUJRAT)"
    if layer == "states":
        n_disputed = int(gdf["state"].str.contains("DISPUTED", na=False).sum())
        gdf = gdf[~gdf["state"].str.contains("DISPUTED", na=False)]
    else:
        rem = gdf["remarks"].astype(str)
        n_disputed = int((gdf["state"].isna() & rem.str.contains("DISPUTED", na=False)).sum())
        gdf = gdf[gdf["state"].notna()]
    gdf["state"] = gdf["state"].map(clean_state_name)
    # .title() capitalizes "and" — restore canonical LGD spellings
    gdf["state"] = gdf["state"].str.replace(" And ", " and ", regex=False)
    n_unmapped = int(gdf["state"].isna().sum())
    assert n_unmapped == 0, f"[{layer}] unmapped state names remain"

    # State and district codes arrive as zero-padded strings ("095") -> Int64;
    # non-numeric values (Mirpur and Muzaffarabad, J&K, carry "NOT AVAILABLE") become <NA>
    for code_col in ("district_code", "state_code"):
        if code_col in gdf:
            gdf[code_col] = pd.to_numeric(
                gdf[code_col].astype("string").str.extract(r"^(\d+)", expand=False),
                errors="coerce").astype("Int64")
    # Sub-district codes include alphanumeric ABDB carveouts (e.g. "00285N001", "07314NOO1");
    # preserve them verbatim as nullable strings instead of truncating leading digits to Int64.
    if "sub_district_code" in gdf:
        gdf["sub_district_code"] = pd.Series(
            [normalize_sub_district_code(v) for v in gdf["sub_district_code"]],
            index=gdf.index,
            dtype="string",
        )

    for col in ("district", "sub_district"):
        if col in gdf:
            gdf[col] = gdf[col].map(fix_mojibake).map(
                lambda s: s.strip() if isinstance(s, str) else s)  # strip stray \r
    if "remarks" in gdf:
        gdf["remarks"] = gdf["remarks"].map(fix_mojibake)

    if layer == "subdistricts":
        for (state, district, bad), good in SUBDISTRICT_DISTRICT_CODE_FIXES.items():
            hit = gdf.state.eq(state) & gdf.district.eq(district) & gdf.district_code.eq(bad)
            assert hit.any(), f"[{layer}] code fix {state}/{district}/{bad} matched no rows"
            gdf.loc[hit, "district_code"] = good
            print(f"[{layer}] {state}/{district}: district_code {bad} -> {good} ({int(hit.sum())} rows)")

    print(f"[{layer}] raw={len(gdf) + n_disputed} disputed_dropped={n_disputed} kept={len(gdf)}")
    gdf.geometry = gdf.geometry.simplify(SIMPLIFY[layer])
    gdf = gdf[cfg["keep"]].sort_values(
        [c for c in ("state", "district", "sub_district") if c in gdf.columns]).reset_index(drop=True)
    return gdf


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for layer, fname in (("states", "india_states_lgd.geojson"),
                         ("districts", "india_districts_lgd.geojson"),
                         ("subdistricts", "india_subdistricts_lgd.gpkg")):
        gdf = process(layer)
        if layer == "districts":
            gdf = reconcile_districts(gdf)
        if layer == "subdistricts":
            n0 = len(gdf)
            gdf = gdf.drop_duplicates(subset=["state", "district", "sub_district",
                                              "sub_district_code"], keep="first")
            if len(gdf) < n0:
                print(f"[subdistricts] dropped {n0 - len(gdf)} exact duplicate rows")
        key = {"states": "state", "districts": "district_code",
               "subdistricts": "sub_district_code"}[layer]
        dup = int(gdf[key].duplicated().sum()) if key in gdf else 0
        if fname.endswith(".gpkg"):
            path = OUT / fname
            gdf.to_file(path, layer="subdistricts", driver="GPKG")  # large layer -> GeoPackage (regular blob, no LFS)
        else:
            fc = json.loads(gdf.to_json())
            fc["name"] = fname.replace(".geojson", "")
            fc["attribution"] = ("Administrative boundaries: Survey of India, Administrative Boundary "
                                 "Database (ABDB), 2024-25; district codes: Local Government Directory "
                                 "(lgdirectory.gov.in). Simplified and indicative; not an authoritative "
                                 "depiction of India's boundaries")
            path = OUT / fname
            write_geojson(path, fc)
        print(f"[{layer}] wrote {len(gdf)} features, dup {key}={dup}, "
              f"valid_geom={bool(gdf.geometry.is_valid.all())}, "
              f"{path.stat().st_size/1e6:.1f} MB -> {path}")


def reconcile_districts(districts: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """The source's subdistrict layer may be newer than its district layer.
    Derive a district that only exists in the subdistrict layer by
    dissolving its subdistrict polygons so the two layers join cleanly.

    A (state, code) pair absent from the district layer is only treated as a
    new district when neither its name already exists in that state nor its
    code belongs to another state — otherwise it is a coding error in the
    source (e.g. Dhule 598) that must be fixed in
    SUBDISTRICT_DISTRICT_CODE_FIXES, not dissolved into a duplicate polygon."""
    sub = process("subdistricts").dropna(subset=["district_code"])
    have = set(zip(districts.state, districts.district_code))
    missing = sub[~sub.apply(lambda r: (r.state, r.district_code) in have, axis=1)]
    if not len(missing):
        return districts
    names = set(zip(districts.state, districts.district))
    codes = districts.dropna(subset=["district_code"]).groupby("district_code")["state"].agg(set)
    rows = []
    for (state, code), grp in missing.groupby(["state", "district_code"]):
        name = grp["district"].mode().iloc[0]
        clash = [f"name {name!r} already in {state}"] if (state, name) in names else []
        if code in codes.index:
            clash.append(f"code {code} belongs to {sorted(codes[code])}")
        if clash:
            raise ValueError(f"[districts] subdistricts of {state}/{name} carry code {code} "
                             f"absent from the district layer, but {'; '.join(clash)} — "
                             "add a SUBDISTRICT_DISTRICT_CODE_FIXES entry")
        rows.append({"state": state, "state_code": grp.state_code.iloc[0],
                     "district": name, "district_code": code,
                     "remarks": "derived from subdistrict dissolve (absent from "
                                "source district layer)", "geometry": grp.geometry.union_all()})
        print(f"[districts] derived missing district: {name} ({code}) from "
              f"{len(grp)} subdistricts")
    out = pd.concat([districts, gpd.GeoDataFrame(rows, crs=4326)], ignore_index=True)
    return out.sort_values(["state", "district"]).reset_index(drop=True)


if __name__ == "__main__":
    main()
