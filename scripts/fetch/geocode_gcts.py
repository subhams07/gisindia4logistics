"""Geocode coordinate-less GCT sites in freight_terminals.csv via Nominatim.

Run AFTER build_gct_terminals.py (which regenerates from the station join).
Only fills empty coordinates; existing ones untouched. Respects Nominatim's
1 req/s policy. Site-level precision; flagged in capacity_notes.

Usage: python scripts/fetch/geocode_gcts.py
"""
from __future__ import annotations

import pathlib
import sys
import time

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR, http_session  # noqa: E402

PATH = DATA_DIR / "rail" / "freight_terminals.csv"
INDIA_BBOX = (68.0, 6.0, 97.5, 37.5)


def geocode(sess, name: str, state: str) -> tuple | None:
    q = f"{name}, {state}, India"
    r = sess.get("https://nominatim.openstreetmap.org/search",
                 params={"q": q, "format": "json", "countrycodes": "in",
                         "limit": 3},
                 headers={"Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    results = r.json()
    for hit in results:
        lat, lon = float(hit["lat"]), float(hit["lon"])
        if INDIA_BBOX[0] <= lon <= INDIA_BBOX[2] and INDIA_BBOX[1] <= lat <= INDIA_BBOX[3]:
            return round(lat, 5), round(lon, 5), hit.get("display_name", "")[:120]
    return None


def main() -> None:
    df = pd.read_csv(PATH)
    need = df[df.latitude.isna() | (df.astype(str).latitude == "")]
    print(f"GCTs needing coordinates: {len(need)}")
    sess = http_session()
    sess.headers["User-Agent"] += " (nominatim geocode, one-shot batch)"
    filled = 0
    for idx, row in need.iterrows():
        hit = geocode(sess, row["name"], row["state"])
        if hit:
            df.loc[idx, "latitude"], df.loc[idx, "longitude"] = hit[0], hit[1]
            df.loc[idx, "capacity_notes"] = (
                str(row["capacity_notes"]) +
                " | geocoded via Nominatim (site-level precision)")
            df.loc[idx, "source_url"] = (
                "https://nominatim.openstreetmap.org (c) OpenStreetMap contributors | "
                + str(row["source_url"]))
            filled += 1
            print(f"  {row['name']}: {hit[0]}, {hit[1]}")
        else:
            print(f"  {row['name']}: NO MATCH")
        time.sleep(1.1)
    df.to_csv(PATH, index=False)
    print(f"Filled {filled}/{len(need)}; wrote {PATH}")


if __name__ == "__main__":
    main()
