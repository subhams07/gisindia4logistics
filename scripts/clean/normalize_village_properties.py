"""normalize_village_properties.py — One attribute schema for all 36 village files.

The SoI (27 states) and NWDP (9 states) village files disagreed on their
attributes (audit item 14):

- `state` was missing (Chandigarh), empty (14 Maharashtra rows), carried a line
  break ('DELHI\\r\\n'), or was spelled per file: all caps, 'Lakshadweep-UT',
  'DADRA & NAGAR HAVELI & DAMAN & DIU', title case.
- Codes were text in 33 files but integers in one column each of Goa
  (district_code), Tripura (state_code) and Madhya Pradesh (sub_district_code),
  so concatenating states mixed int and str.
- Some names and codes carried embedded line breaks or repeated spaces.

This script sets, in every feature's properties:

- `state` = the LGD spelling of the file's state (standardize.clean_state_name,
  as in the boundary layers), added as the first property where missing;
- state_code / district_code / sub_district_code / village_code as text
  (str(int) for the integer columns; the source values are full-length);
- every text value with line breaks and repeated spaces collapsed;
- "not available" markers stored as text (NA, Null, <Null>, Not available,
  any case: 2,534 values in 9 files, mostly village codes) as null, the way
  the NWDP files store their 999999 code. pandas reads 'NA' and 'null' as
  missing from CSV but keeps them as text from Parquet, so the two formats
  disagreed. Real village names that look similar (Punjab's 'NAN', 'NONE',
  Uttar Pradesh's 'Nan') are kept.

Geometry is not touched: each file is re-serialised with the separators it was
written with (verified byte-identical on a load/dump round trip before
editing), so the coordinate text, including the Z = 0.0 of 15 states, is
unchanged. The GeoParquet twin of each changed file is regenerated
(gpd.read_file -> to_parquet, snappy), and its geometry WKB is checked
byte-identical to the previous twin.

Idempotent. Usage: python scripts/clean/normalize_village_properties.py
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import geopandas as gpd
import pyarrow.parquet as pq

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from standardize import DATA_DIR, clean_state_name  # noqa: E402

VILLAGES = DATA_DIR / "administrative" / "villages"
TWINS = DATA_DIR / "administrative" / "villages_parquet"
CODE_COLUMNS = ("state_code", "district_code", "sub_district_code", "village_code")
MISSING_MARKERS = frozenset({"na", "null", "<null>", "not available"})  # compared casefolded
SERIALISATIONS = (dict(separators=(",", ":")), dict(separators=(",", ":"), ensure_ascii=False))


def canonical_state_for(path: pathlib.Path) -> str:
    slug = path.stem.replace("_soi_villages", "")
    state = clean_state_name(slug.replace("_", " "))
    if state is None:
        raise SystemExit(f"{path.name}: no canonical state for slug '{slug}'")
    return state


def normalise_properties(props: dict, state: str) -> dict:
    out = {"state": state}
    for key, value in props.items():
        if key == "state":
            continue
        if key in CODE_COLUMNS and isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        if isinstance(value, str):
            value = " ".join(value.split())
            if value.casefold() in MISSING_MARKERS:
                value = None
        out[key] = value
    if "state" in props:  # keep the file's own key order
        return {key: out[key] for key in props}
    return out


def serialisation(raw: bytes, fc: dict) -> dict:
    for kw in SERIALISATIONS:
        if json.dumps(fc, **kw).encode("utf-8") == raw:
            return kw
    raise SystemExit("file does not round-trip byte-identically; refusing to rewrite it")


def regenerate_twin(geojson: pathlib.Path) -> None:
    twin = TWINS / f"{geojson.stem}.parquet"
    before = pq.read_table(twin, columns=["geometry"]).column("geometry").to_pylist() if twin.exists() else None
    gdf = gpd.read_file(geojson)
    tmp = twin.with_suffix(".parquet.tmp")
    gdf.to_parquet(tmp, engine="pyarrow", compression="snappy", index=False)
    after = pq.read_table(tmp, columns=["geometry"]).column("geometry").to_pylist()
    if before is not None and before != after:
        tmp.unlink()
        raise SystemExit(f"{twin.name}: geometry WKB would change; refusing to replace the twin")
    tmp.replace(twin)


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    changed = []
    for path in sorted(VILLAGES.glob("*_soi_villages.geojson")):
        raw = path.read_bytes()
        fc = json.loads(raw)
        kw = serialisation(raw, fc)
        state = canonical_state_for(path)
        geometry_before = [feature["geometry"] for feature in fc["features"]]
        for feature in fc["features"]:
            feature["properties"] = normalise_properties(feature["properties"], state)
        assert [feature["geometry"] for feature in fc["features"]] == geometry_before
        out = json.dumps(fc, **kw).encode("utf-8")
        if out != raw:
            path.write_bytes(out)
            regenerate_twin(path)
            changed.append(path.name)
        print(f"{path.name}: {'normalised' if out != raw else 'already canonical'} ({state})")
    print(f"{len(changed)} files rewritten (properties only) with their parquet twins")


if __name__ == "__main__":
    main()
