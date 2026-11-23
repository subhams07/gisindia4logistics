"""Build data/rail/freight_terminals.csv — Gati Shakti Cargo Terminals (GCTs).

Source: Ministry of Railways answer in Lok Sabha via PIB release ID 1910049
(23 Mar 2023), Annexure: "Railway-wise list of identified locations of GCTs
expected to be developed in next three financial years (tentative data)".
https://www.pib.gov.in/PressReleasePage.aspx?PRID=1910049  (GODL-India)

Coordinates are joined from data/rail/railway_stations.csv by station name
(most GCT rows are "serving station" locations): exact name first, then
substring, then a close fuzzy match, then the port/ICD hub files. Every
candidate must lie inside the row's state polygon (india_states_lgd) — the
station table's own state label is missing for half its rows, and nationwide
matches once put six GCTs in the wrong state. Unmatched rows keep empty
coordinates (scripts/fetch/geocode_gcts.py can fill them, with the same check).

The annexure lists some locations under the state of their railway division;
`state` here is the location's own state (Lajkura: Odisha, Nipania:
Chhattisgarh, Samba: Jammu and Kashmir).

Usage: python scripts/fetch/build_gct_terminals.py
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR  # noqa: E402

SOURCE_URL = "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1910049"

# (rly_zone, division, location, state) — verbatim from the PIB annexure
GCT_ROWS = [
    ("ECoR", "WAT", "Vadalapudi", "Andhra Pradesh"), ("SCR", "SC", "Jaggayyapet", "Andhra Pradesh"),
    ("SCR", "BZA", "Krishnapatnam", "Andhra Pradesh"), ("NFR", "LMG", "Kamalajari", "Assam"),
    ("NFR", "LMG", "Ramnagar FCI GCT Terminal", "Assam"), ("NFR", "LMG", "Bihara", "Assam"),
    ("NFR", "Rangiya", "Jogighopa", "Assam"), ("NFR", "Rangiya", "Baihata", "Assam"),
    ("ECR", "SPJ", "Chamua", "Bihar"), ("ECR", "SPJ", "Budhma", "Bihar"),
    ("ECR", "SPJ", "Semra", "Bihar"), ("ECR", "SPJ", "Bhelwa", "Bihar"),
    ("ECR", "SEE", "Mansi", "Bihar"), ("ECR", "SEE", "Pasraha", "Bihar"),
    ("ECR", "SEE", "Khudiram Bose Pusa", "Bihar"), ("ECoR", "WAT", "Amagura", "Chhattisgarh"),
    ("SECR", "BSP", "Lajkura", "Odisha"), ("NR", "DLI", "Narela", "Delhi"),
    ("WR", "BRC", "Kayavarohan", "Gujarat"), ("WR", "ADI", "Virochan Nagar", "Gujarat"),
    ("WR", "RJT", "Makansar", "Gujarat"), ("NR", "DLI", "Farukh Nagar", "Haryana"),
    ("NWR", "BKN", "Lahli", "Haryana"), ("NWR", "BKN", "Bhattu", "Haryana"),
    ("NR", "DLI", "Naultha", "Haryana"), ("NR", "DLI", "Palwal", "Haryana"),
    ("NR", "UMB", "Chintpurni Marg", "Himachal Pradesh"), ("ECR", "DHN", "Shivpoor", "Jharkhand"),
    ("ECR", "DHN", "Phulbasiya", "Jharkhand"), ("ECR", "DHN", "Shivpur - Godawari Commodity Pvt. Ltd.", "Jharkhand"),
    ("ECR", "DHN", "Jarangdih", "Jharkhand"), ("SER", "CKP", "Gua", "Jharkhand"),
    ("CR", "SUR", "Hirenanduru", "Karnataka"), ("CR", "SUR", "Wadi", "Karnataka"),
    ("SR", "TVC", "Balaramapuram", "Kerala"), ("SECR", "R", "Nipania", "Chhattisgarh"),
    ("CR", "Mumbai", "Taloja Panchanand", "Maharashtra"), ("CR", "NGP", "New Makardhokda", "Maharashtra"),
    ("CR", "NGP", "Ghuggus", "Maharashtra"), ("CR", "NGP", "Moorsa", "Maharashtra"),
    ("CR", "NGP", "Sindi", "Maharashtra"), ("CR", "NGP", "Kalmeshwar", "Maharashtra"),
    ("CR", "Pune", "Patas", "Maharashtra"), ("CR", "BSL", "Varangaon", "Maharashtra"),
    ("CR", "Pune", "Lonavala-Malavli", "Maharashtra"), ("CR", "Mumbai", "Palasdari", "Maharashtra"),
    ("CR", "Mumbai", "Kalamboli", "Maharashtra"), ("SCR", "NED", "Dinegaon", "Maharashtra"),
    ("NFR", "LMG", "Khongsang", "Manipur"), ("ECoR", "KUR", "Machhapur", "Odisha"),
    ("ECoR", "KUR", "Talcher", "Odisha"), ("ECoR", "KUR", "Sukinda Road", "Odisha"),
    ("ECoR", "KUR", "Jaipur Keonjhar Road", "Odisha"), ("ECoR", "KUR", "Paradeep", "Odisha"),
    ("NR", "FZR", "Chhina", "Punjab"), ("NR", "FZR", "Kathunangal", "Punjab"),
    ("NR", "FZR", "Ladhuka", "Punjab"), ("NR", "FZR", "Sanehwal", "Punjab"),
    ("NR", "UMB", "Chajli", "Punjab"), ("NR", "FZR", "Samba", "Jammu and Kashmir"),
    ("NWR", "JU", "Bhadwasi", "Rajasthan"), ("NWR", "AII", "Ras Babra", "Rajasthan"),
    ("SR", "MAS", "Tondiarpet Marshalling Yard", "Tamil Nadu"), ("SR", "TPJ", "Uttangal Mangalam", "Tamil Nadu"),
    ("SR", "MAS", "Mappedu", "Tamil Nadu"), ("SCR", "SC", "Ramagundam", "Telangana"),
    ("SCR", "HYB", "Uppalvai", "Telangana"), ("SCR", "HYB", "Jankampet", "Telangana"),
    ("SCR", "HYB", "Vishnupuram", "Telangana"), ("ECR", "DHN", "Salaibanwa", "Uttar Pradesh"),
    ("NCR", "PRYJ", "Sathnaraini", "Uttar Pradesh"), ("NCR", "PRYJ", "Etah", "Uttar Pradesh"),
    ("NER", "LJN", "Tinich", "Uttar Pradesh"), ("NER", "BSB", "Lar Road", "Uttar Pradesh"),
    ("NER", "LJN", "Campieganj", "Uttar Pradesh"), ("NR", "DLI", "Noli", "Uttar Pradesh"),
    ("NR", "LKO", "Shriraj Nagar", "Uttar Pradesh"), ("NR", "MB", "Dhamora", "Uttar Pradesh"),
    ("ER", "HWH", "Janai Road", "West Bengal"), ("ER", "ASN", "Andal Jn", "West Bengal"),
    ("ER", "HWH", "Haripal", "West Bengal"), ("ER", "ASN", "Barabani", "West Bengal"),
    ("NFR", "Katihar", "Deotala", "West Bengal"), ("NFR", "Katihar", "Balurghat", "West Bengal"),
]


def norm(s: str) -> str:
    s = str(s).lower().strip()
    for suf in (" junction", " jn.", " jn", " road", " rd", " rs", " station", " yard"):
        s = s.replace(suf, "")
    return s.strip()


def load_states():
    import geopandas as gpd
    return gpd.read_file(DATA_DIR / "administrative" / "india_states_lgd.geojson")


def _state_key(name) -> str:
    return " ".join(str(name).lower().replace("&", " and ").split())


def in_state(lat, lon, state, states) -> bool:
    """True when (lat, lon) lies inside the named state's polygon."""
    from shapely.geometry import Point
    poly = states[states["state"].map(_state_key) == _state_key(state)]
    return bool(len(poly)) and bool(poly.geometry.contains(Point(float(lon), float(lat))).any())


def match_station(key, state, stations, states):
    """First station whose point lies in `state`: exact name, then substring, then fuzzy (>= 0.82)."""
    import difflib
    inside = lambda frame: frame[[in_state(r.latitude, r.longitude, state, states)  # noqa: E731
                                  for r in frame.itertuples()]] if len(frame) else frame
    for frame in (stations[stations["nname"] == key],
                  stations[stations["nname"].str.contains(key, regex=False, na=False)]):
        frame = inside(frame.dropna(subset=["latitude", "longitude"]))
        if len(frame):
            return frame.iloc[0]
    for close in difflib.get_close_matches(key, stations["nname"].drop_duplicates().tolist(), n=5, cutoff=0.82):
        frame = inside(stations[stations["nname"] == close].dropna(subset=["latitude", "longitude"]))
        if len(frame):
            return frame.iloc[0]
    return None


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    stations = pd.read_csv(DATA_DIR / "rail" / "railway_stations.csv")
    stations["nname"] = stations["station_name"].map(norm)

    states = load_states()

    rows = []
    for zone, division, loc, state in GCT_ROWS:
        key = norm(loc.split("(")[0].split(" - ")[0])
        hit = match_station(key, state, stations, states)
        if hit is None:
            # final fallback: hub CSVs (some GCTs sit at ports, e.g. Krishnapatnam)
            for hub in ("ports.csv", "icds.csv"):
                hpath = DATA_DIR / "logistics_hubs" / hub
                if hpath.exists():
                    h = pd.read_csv(hpath)
                    m = h[h["name"].str.lower().map(norm).str.contains(
                        key.split()[0], regex=False, na=False)]
                    m = m[[in_state(r.latitude, r.longitude, state, states) for r in m.itertuples()]]
                    if len(m):
                        rows_hint = m.iloc[0]
                        rows.append({
                            "name": loc, "terminal_type": "gct", "zone": zone,
                            "division": division, "state": state, "city": loc,
                            "latitude": round(float(rows_hint.latitude), 5),
                            "longitude": round(float(rows_hint.longitude), 5),
                            "capacity_notes": "coordinate from hub dataset ("
                                              + hub + "); identified/proposed GCT (PIB 2023)",
                            "source_url": SOURCE_URL,
                            "matched_station_code": "",
                        })
                        break
            else:
                rows.append({
                    "name": loc, "terminal_type": "gct", "zone": zone,
                    "division": division, "state": state, "city": loc,
                    "latitude": "", "longitude": "",
                    "capacity_notes": "identified/proposed GCT (PIB 2023 annexure; tentative); no coordinate match",
                    "source_url": SOURCE_URL, "matched_station_code": "",
                })
            continue
        rows.append({
            "name": loc,
            "terminal_type": "gct",
            "zone": zone,
            "division": division,
            "state": state,
            "city": loc,
            "latitude": round(hit.latitude, 5) if hit is not None else "",
            "longitude": round(hit.longitude, 5) if hit is not None else "",
            "capacity_notes": "identified/proposed GCT (PIB 2023 annexure; tentative)",
            "source_url": SOURCE_URL,
            "matched_station_code": hit.station_code if hit is not None else "",
        })

    df = pd.DataFrame(rows)
    out = DATA_DIR / "rail" / "freight_terminals.csv"
    df.to_csv(out, index=False)
    n = (df.latitude != "").sum()
    print(f"Wrote {len(df)} GCT terminals -> {out}")
    print(f"  coordinates matched from stations table: {n}/{len(df)}")
    print(f"  unmatched: {df.loc[df.latitude == '', 'name'].tolist()}")


if __name__ == "__main__":
    main()
