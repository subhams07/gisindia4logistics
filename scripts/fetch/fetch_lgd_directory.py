"""fetch_lgd_directory.py — Snapshot the official LGD district directory.

Queries the Local Government Directory web service (Ministry of Panchayati
Raj, lgdirectory.gov.in) for the districts of every state/UT and writes one
row per district with its LGD code and its Census 2011 / 2001 codes.

This is the reference for `district_code` across the repo: the SoI ABDB
boundary layer numbers most districts by their Census 2011 code, and newer
ones by a scheme of its own, so scripts/clean/apply_lgd_codes.py maps them to
these codes.

Output:
  data/administrative/lgd_districts.csv

Usage: python scripts/fetch/fetch_lgd_directory.py
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

import pandas as pd

from scripts.clean.standardize import DATA_DIR, http_session

LGD_DISTRICTS_URL = "https://lgdirectory.gov.in/webservices/lgdws/districtList"
# LGD state codes; 25 (Daman & Diu) and 26 (Dadra & Nagar Haveli) were merged into 38 in 2020
STATE_CODES = [c for c in range(1, 39) if c not in (25, 26)]
OUT = DATA_DIR / "administrative" / "lgd_districts.csv"


def census_code(value) -> int | None:
    """LGD gives census codes as zero-padded strings; '000'/'' mean none."""
    value = str(value or "").strip()
    return int(value) if value.isdigit() and int(value) > 0 else None


def main() -> None:
    session = http_session()
    rows = []
    for state_code in STATE_CODES:
        resp = session.post(LGD_DISTRICTS_URL, data={"stateCode": state_code}, timeout=60)
        resp.raise_for_status()
        districts = resp.json()
        if not districts:
            raise SystemExit(f"LGD returned no districts for state code {state_code}")
        for d in districts:
            rows.append({
                "state_code": state_code,
                "district_code": int(d["districtCode"]),
                "district_name": d["districtNameEnglish"].strip(),
                "census2011_code": census_code(d.get("census2011Code")),
                "census2001_code": census_code(d.get("census2001Code")),
            })
        time.sleep(1)  # one request per second; the service is shared
    out = pd.DataFrame(rows).sort_values(["state_code", "district_code"])
    for col in ("census2011_code", "census2001_code"):
        out[col] = out[col].astype("Int64")
    if out.duplicated("district_code").any():
        raise SystemExit("LGD district codes are not unique; refusing to write")
    out["fetched_on"] = date.today().isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"Wrote {len(out)} LGD districts across {out.state_code.nunique()} states -> {OUT}")


if __name__ == "__main__":
    main()
