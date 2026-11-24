"""add_hub_codes_precision.py — Stable codes and coordinate precision for the transport hub files.

Audit items 46 and 52. For the seven transport hub files in data/logistics_hubs/
(ports, icps, icds, air_cargo, mmlps, inland_waterway_terminals, fci_depots):

- `hub_code`: a repository-assigned stable identifier, `<PREFIX>-<NNN>` in row
  order (PRT, ICP, ICD, AIR, MMLP, IWT, FCI). An existing code is never changed;
  new rows get the next free number. It is not an official code.
- `unlocode` (ports.csv only): the official UNECE UN/LOCODE for all 22 ports
  (12 major ports matching data/freight/port_throughput_annual.csv, plus all 10
  non-major ports verified against the UNECE UN/LOCODE directory).
- `coord_precision`: what the point represents, from evidence only:
    pending     no coordinates
    station / town / village   the row's notes say the point is that node
    site        ports and airports confirmed against an OSM harbour/port or
                aerodrome feature within 3 km (docs/review/hub_site_check.csv)
    city        the notes say city-level / approximate, or the point is shared
                with a hub in another file (a city centre, or a port's or
                airport's point reused); ports and airports are not
                downgraded for being copied
    unverified  no evidence either way

Idempotent. Usage: python scripts/clean/add_hub_codes_precision.py
"""
from __future__ import annotations

import argparse
import collections
import csv
import pathlib
import re
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from standardize import DATA_DIR, REPO_ROOT  # noqa: E402

HUBS = DATA_DIR / "logistics_hubs"
SITE_CHECK = REPO_ROOT / "docs" / "review" / "hub_site_check.csv"
PREFIXES = {"ports": "PRT", "icps": "ICP", "icds": "ICD", "air_cargo": "AIR", "mmlps": "MMLP",
            "inland_waterway_terminals": "IWT", "fci_depots": "FCI"}
MAJOR_PORT_UNLOCODES = {
    "Deendayal Port (Kandla)": "INIXY", "Mumbai Port": "INBOM",
    "Jawaharlal Nehru Port (JNPT/Navi Mumbai)": "INNSA", "Mormugao Port": "INMRM",
    "New Mangalore Port": "INNML", "Cochin Port": "INCOK",
    "V.O. Chidambaranar Port (Tuticorin)": "INTUT", "Chennai Port": "INMAA",
    "Kamarajar Port (Ennore)": "INENR", "Visakhapatnam Port": "INVTZ", "Paradip Port": "INPRT",
    "Kolkata Port (Syama Prasad Mookerjee Port) incl. Haldia Dock Complex": "INCCU",
}
NON_MAJOR_PORT_UNLOCODES = {
    "Mundra Port": "INMUN", "Pipavav Port": "INPAV", "Hazira Port": "INHZA",
    "Dahej Port": "INDAH", "Dhamra Port": "INDMQ", "Gangavaram Port": "INGGV",
    "Krishnapatnam Port": "INKRI", "Karaikal Port": "INKRK",
    "Vizhinjam International Seaport": "INVZJ", "Kattupalli Port": "INKAT",
}
PORT_UNLOCODES = {**MAJOR_PORT_UNLOCODES, **NON_MAJOR_PORT_UNLOCODES}
PRECISION_VALUES = ("site", "station", "village", "town", "city", "unverified", "pending")
# A specific node named when the point was re-located (batch C/D) wins over the
# older generic "coordinates approximate" wording in the same note.
NODE_WORDS = (("station", r"\bstation\b"), ("village", r"\bvillage\b"), ("town", r"\btown\b"))
CITY_WORDS = r"city-level|city level|city coordinates|city approximation|approximate|city centre|city center"


def _key(lat, lon):
    return round(float(lat), 3), round(float(lon), 3)


def precision(row, stem: str, shared: set, sites: set) -> str:
    if not str(row.latitude).strip() or not str(row.longitude).strip():
        return "pending"
    if stem in ("ports", "air_cargo") and (stem, row["name"]) in sites:
        return "site"
    notes = str(row.capacity_notes).lower()
    if "nominatim" in notes or "openstreetmap" in notes:
        for value, pattern in NODE_WORDS:
            if re.search(pattern, notes):
                return value
    # A point shared with another file is a copy (a city centre, or a port's or
    # airport's point reused for an MMLP or depot); the port or airport that owns
    # it is not downgraded by being copied.
    copied = stem not in ("ports", "air_cargo") and _key(row.latitude, row.longitude) in shared
    if re.search(CITY_WORDS, notes) or copied:
        return "city"
    return "unverified"


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    with open(SITE_CHECK, encoding="utf-8") as fh:
        sites = {(r["file"].removesuffix(".csv"), r["name"]) for r in csv.DictReader(fh) if r["result"] == "site"}
    tables, raws = {}, {}
    for stem in PREFIXES:
        path = HUBS / f"{stem}.csv"
        raws[stem] = path.read_text(encoding="utf-8")
        tables[stem] = pd.read_csv(path, dtype=str, keep_default_na=False)
    owners = collections.defaultdict(set)
    for stem, table in tables.items():
        for lat, lon in zip(table.latitude, table.longitude):
            if lat.strip() and lon.strip():
                owners[_key(lat, lon)].add(stem)
    shared = {k for k, files in owners.items() if len(files) > 1}

    for stem, table in tables.items():
        prefix = PREFIXES[stem]
        if "hub_code" not in table.columns:
            table.insert(0, "hub_code", "")
        used = [int(c.split("-")[1]) for c in table.hub_code if re.fullmatch(rf"{prefix}-\d{{3}}", c)]
        nxt = max(used, default=0) + 1
        for i in table.index[table.hub_code == ""]:
            table.at[i, "hub_code"] = f"{prefix}-{nxt:03d}"
            nxt += 1
        if table.hub_code.duplicated().any():
            raise SystemExit(f"{stem}.csv: duplicate hub_code {sorted(table.hub_code[table.hub_code.duplicated()])}")
        if stem == "ports":
            if "unlocode" not in table.columns:
                table.insert(1, "unlocode", "")
            table["unlocode"] = [PORT_UNLOCODES.get(n, u) for n, u in zip(table["name"], table["unlocode"])]
        table["coord_precision"] = [precision(r, stem, shared, sites) for _, r in table.iterrows()]
        cols = [c for c in table.columns if c != "coord_precision"]
        cols.insert(cols.index("longitude") + 1, "coord_precision")
        table = table[cols]
        table.to_csv(HUBS / f"{stem}.csv", index=False,
                     lineterminator="\r\n" if "\r\n" in raws[stem] else "\n")
        print(f"{stem}.csv: {table.coord_precision.value_counts().to_dict()}")


if __name__ == "__main__":
    main()
