"""
scripts/fetch/build_industrial_nodes.py
Compiles National Industrial Corridor Development Corporation (NICDC) Smart Manufacturing Nodes
and PM Mega Integrated Textile Region and Apparel (PM MITRA) Parks across India.
"""

import argparse
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.clean.assign_node_districts import assign_file, load_districts
from scripts.clean.standardize import DATA_DIR

PIB_NICDP_2024 = "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2049312"
PIB_NICDP_2026 = "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2232996"
PIB_CBIC_2020 = "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1684631"
PIB_DMIC_NIMZ = "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=87421"

NICDC_NODES_DATA = [
    # DMIC (Delhi - Mumbai)
    {"name": "Dholera Special Investment Region (DSIR)", "corridor": "DMIC", "node_type": "Special Investment Region / Smart City", "state": "Gujarat", "district": "Ahmadabad", "city": "Dholera", "latitude": 22.2450, "longitude": 72.1950, "area_acres": 227000, "status": "Under Implementation / Operational Anchor", "operator": "Dholera Industrial City Development Ltd (DICDL)", "source_url": "https://dholera.gujarat.gov.in/"},
    {"name": "Shendra Bidkin Industrial Area (AURIC)", "corridor": "DMIC", "node_type": "Smart Industrial City", "state": "Maharashtra", "district": "Chhatrapati Sambhaji Nagar", "city": "Chhatrapati Sambhajinagar", "latitude": 19.8760, "longitude": 75.5240, "area_acres": 10000, "status": "Operational", "operator": "Aurangabad Industrial Township Ltd (AITL)", "source_url": "https://www.auric.city/"},
    {"name": "Vikram Udyogpuri", "corridor": "DMIC", "node_type": "Integrated Industrial Township", "state": "Madhya Pradesh", "district": "Ujjain", "city": "Ujjain", "latitude": 23.2180, "longitude": 75.8920, "area_acres": 1100, "status": "Operational", "operator": "MP Industrial Development Corporation (MPIDC)", "source_url": "https://www.vikramudyogpuriujjain.com/"},
    {"name": "Integrated Industrial Township Greater Noida (IITGN)", "corridor": "DMIC", "node_type": "Smart Industrial City", "state": "Uttar Pradesh", "district": "Gautam Buddha Nagar", "city": "Greater Noida", "latitude": 28.4820, "longitude": 77.5350, "area_acres": 747, "status": "Operational", "operator": "DMIC IITGNL", "source_url": "https://www.iitgnl.com/"},
    {"name": "Manesar Bawal Industrial Area (MBIR)", "corridor": "DMIC", "node_type": "Industrial Region", "state": "Haryana", "district": "Rewari", "city": "Bawal", "latitude": 28.0820, "longitude": 76.5820, "area_acres": 10000, "status": "Under Development", "operator": "HSIIDC", "source_url": PIB_DMIC_NIMZ},
    {"name": "Khushkhera Bhiwadi Neemrana (KBNIR)", "corridor": "DMIC", "node_type": "Investment Region", "state": "Rajasthan", "district": "Kotputli-Behror", "city": "Neemrana", "latitude": 27.9850, "longitude": 76.3850, "area_acres": 14000, "status": "Under Development", "operator": "RIICO", "source_url": PIB_NICDP_2026},
    {"name": "Jodhpur Pali Marwar Industrial Area (JPMIA)", "corridor": "DMIC", "node_type": "Industrial Area", "state": "Rajasthan", "district": "Pali", "city": "Rohat", "latitude": 25.9850, "longitude": 73.1250, "area_acres": 1578, "status": "Under Implementation (Phase 1)", "operator": "RIICO", "source_url": PIB_NICDP_2026},
    {"name": "Mandal Bechraji Special Investment Region (MBSIR)", "corridor": "DMIC", "node_type": "Auto & Manufacturing SIR", "state": "Gujarat", "district": "Mahesana", "city": "Becharaji", "latitude": 23.5120, "longitude": 72.0450, "area_acres": 25000, "status": "Operational (Maruti/Honda Cluster)", "operator": "GIDB / GIDC", "source_url": "https://gidb.gujarat.gov.in/"},
    {"name": "Dighi Port Industrial Area", "corridor": "DMIC", "node_type": "Port Industrial Area", "state": "Maharashtra", "district": "Raigad", "city": "Dighi / Roha", "latitude": 18.2850, "longitude": 73.0150, "area_acres": 6000, "status": "Planning / Land Acquisition", "operator": "MIDC", "source_url": PIB_NICDP_2024},

    # CBIC (Chennai - Bengaluru)
    {"name": "Tumakuru Industrial Node", "corridor": "CBIC", "node_type": "Integrated Industrial Township", "state": "Karnataka", "district": "Tumakuru", "city": "Vasanthanarasapura", "latitude": 13.4120, "longitude": 77.0150, "area_acres": 8500, "status": "Under Implementation / Operational Phase 1", "operator": "NICDIT / KIADB", "source_url": PIB_CBIC_2020},
    {"name": "Krishnapatnam Industrial Node", "corridor": "CBIC", "node_type": "Coastal Industrial Node", "state": "Andhra Pradesh", "district": "Sri Potti Sriramulu Nellore", "city": "Krishnapatnam", "latitude": 14.2850, "longitude": 80.0820, "area_acres": 12000, "status": "Under Implementation", "operator": "APIIC / NICDIT", "source_url": PIB_CBIC_2020},
    {"name": "Ponneri Industrial Node", "corridor": "CBIC", "node_type": "Heavy Engineering & Port Node", "state": "Tamil Nadu", "district": "Tiruvallur", "city": "Ponneri", "latitude": 13.3250, "longitude": 80.2150, "area_acres": 4000, "status": "Planning / Master Planning Complete", "operator": "TIDCO / NICDIT", "source_url": PIB_NICDP_2026},

    # AKIC (Amritsar - Kolkata)
    {"name": "Integrated Manufacturing Cluster (IMC) Rajpura", "corridor": "AKIC", "node_type": "Integrated Manufacturing Cluster", "state": "Punjab", "district": "Patiala", "city": "Rajpura", "latitude": 30.4850, "longitude": 76.5850, "area_acres": 1100, "status": "Approved / Land Identified", "operator": "PSIEC / NICDIT", "source_url": PIB_NICDP_2024},
    {"name": "Integrated Manufacturing Cluster (IMC) Prayagraj", "corridor": "AKIC", "node_type": "Integrated Manufacturing Cluster", "state": "Uttar Pradesh", "district": "Prayagraj", "city": "Naini / Prayagraj", "latitude": 25.37867, "longitude": 81.87312, "area_acres": 352, "status": "Approved / Under Implementation (coordinates approximate: Naini area)", "operator": "UPSIDA / NICDIT", "source_url": PIB_NICDP_2026},
    {"name": "Integrated Manufacturing Cluster (IMC) Gaya", "corridor": "AKIC", "node_type": "Integrated Manufacturing Cluster", "state": "Bihar", "district": "Gaya", "city": "Dobhi", "latitude": 24.5820, "longitude": 84.9520, "area_acres": 1670, "status": "Approved / Master Planning", "operator": "BIADA / NICDIT", "source_url": PIB_NICDP_2024},
    {"name": "Integrated Manufacturing Cluster (IMC) Raghunathpur", "corridor": "AKIC", "node_type": "Integrated Manufacturing Cluster", "state": "West Bengal", "district": "Puruliya", "city": "Raghunathpur", "latitude": 23.5420, "longitude": 86.6720, "area_acres": 2480, "status": "Under Planning", "operator": "WBIDC / NICDIT", "source_url": PIB_NICDP_2026},

    # VCIC (Vizag - Chennai)
    {"name": "Kopparthy Industrial Node", "corridor": "VCIC", "node_type": "Manufacturing Cluster / Mega Industrial Hub", "state": "Andhra Pradesh", "district": "Y.S.R.", "city": "Kopparthy", "latitude": 14.4750, "longitude": 78.7120, "area_acres": 2596, "status": "Under Implementation (Phase 1)", "operator": "APIIC / NICDIT", "source_url": PIB_NICDP_2026},

    # HBIC (Hyderabad - Bengaluru)
    {"name": "Orvakal Industrial Node", "corridor": "HBIC", "node_type": "Mega Industrial Park & Airport Node", "state": "Andhra Pradesh", "district": "Kurnool", "city": "Orvakal", "latitude": 15.6820, "longitude": 78.1850, "area_acres": 2621, "status": "Under Implementation (Phase 1)", "operator": "APIIC / NICDIT", "source_url": PIB_NICDP_2026},

    # BMIC (Bengaluru - Mumbai)
    {"name": "Dharwad Industrial Node", "corridor": "BMIC", "node_type": "Manufacturing Cluster", "state": "Karnataka", "district": "Dharwad", "city": "Dharwad", "latitude": 15.4850, "longitude": 75.0120, "area_acres": 6000, "status": "Under Implementation", "operator": "KIADB / NICDIT", "source_url": PIB_NICDP_2026},

    # HNIC (Hyderabad - Nagpur)
    {"name": "Zaheerabad NIMZ (National Investment & Manufacturing Zone)", "corridor": "HNIC", "node_type": "NIMZ / Heavy Industry Hub", "state": "Telangana", "district": "Sangareddy", "city": "Zaheerabad", "latitude": 17.6820, "longitude": 77.6120, "area_acres": 3245, "status": "Under Implementation (Phase 1)", "operator": "TGIIC / NICDIT", "source_url": PIB_NICDP_2026},
]

PM_MITRA_PARKS_DATA = [
    {"name": "PM MITRA Mega Textile Park Virudhunagar", "state": "Tamil Nadu", "district": "Virudhunagar", "city": "Virudhunagar", "latitude": 9.5850, "longitude": 77.9520, "area_acres": 1052, "estimated_investment_inr_crore": 10000, "status": "Approved / Groundbreaking Complete", "operator": "State Industries Promotion Corporation of Tamil Nadu (SIPCOT)", "source_url": "https://texmin.nic.in/pm-mitra-tamil-nadu"},
    {"name": "PM MITRA Mega Textile Park Warangal", "state": "Telangana", "district": "Warangal", "city": "Warangal", "latitude": 17.9620, "longitude": 79.5950, "area_acres": 1188, "estimated_investment_inr_crore": 10000, "status": "Approved / Operationalizing", "operator": "Telangana Industrial Infrastructure Corporation (TGIIC)", "source_url": "https://texmin.nic.in/pm-mitra-telangana"},
    {"name": "PM MITRA Mega Textile Park Navsari", "state": "Gujarat", "district": "Navsari", "city": "Vansi Borsi", "latitude": 20.9120, "longitude": 72.8850, "area_acres": 1142, "estimated_investment_inr_crore": 10000, "status": "Approved / Under Development", "operator": "Gujarat Industrial Development Corporation (GIDC)", "source_url": "https://texmin.nic.in/pm-mitra-gujarat"},
    {"name": "PM MITRA Mega Textile Park Kalaburagi", "state": "Karnataka", "district": "Kalaburagi", "city": "Firozabad / Kalaburagi", "latitude": 17.3250, "longitude": 76.8420, "area_acres": 1000, "estimated_investment_inr_crore": 10000, "status": "Approved / Land Handed Over", "operator": "Karnataka Industrial Areas Development Board (KIADB)", "source_url": "https://texmin.nic.in/pm-mitra-karnataka"},
    {"name": "PM MITRA Mega Textile Park Dhar (Badnawar)", "state": "Madhya Pradesh", "district": "Dhar", "city": "Badnawar", "latitude": 23.0150, "longitude": 75.2250, "area_acres": 1560, "estimated_investment_inr_crore": 10000, "status": "Approved / Under Development", "operator": "MP Industrial Development Corporation (MPIDC)", "source_url": "https://texmin.nic.in/pm-mitra-madhya-pradesh"},
    {"name": "PM MITRA Mega Textile Park Lucknow-Hardoi", "state": "Uttar Pradesh", "district": "Lucknow", "city": "Malihabad / Atrauli", "latitude": 26.9850, "longitude": 80.6850, "area_acres": 1000, "estimated_investment_inr_crore": 10000, "status": "Approved / Land Handed Over", "operator": "Uttar Pradesh State Industrial Development Authority (UPSIDA)", "source_url": "https://texmin.nic.in/pm-mitra-uttar-pradesh"},
    {"name": "PM MITRA Mega Textile Park Amravati", "state": "Maharashtra", "district": "Amravati", "city": "Nandgaon Peth", "latitude": 21.0250, "longitude": 77.8120, "area_acres": 1020, "estimated_investment_inr_crore": 10000, "status": "Approved / Under Development", "operator": "Maharashtra Industrial Development Corporation (MIDC)", "source_url": "https://texmin.nic.in/pm-mitra-maharashtra"},
]


def build_industrial_nodes(out_dir: Path | None = None, data_dir: Path = DATA_DIR):
    out_dir = out_dir or (data_dir / "logistics_hubs")
    out_dir.mkdir(parents=True, exist_ok=True)
    districts = load_districts(data_dir)

    # 1. NICDC Industrial Nodes
    df_nicdc = pd.DataFrame(NICDC_NODES_DATA)
    p_nicdc = out_dir / "industrial_nodes.csv"
    df_nicdc.to_csv(p_nicdc, index=False, lineterminator="\n")
    assign_file(p_nicdc, districts=districts)
    print(f"Wrote {len(df_nicdc)} NICDC Industrial Nodes -> {p_nicdc}")

    # 2. PM MITRA Textile Parks
    df_mitra = pd.DataFrame(PM_MITRA_PARKS_DATA)
    p_mitra = out_dir / "pm_mitra_parks.csv"
    df_mitra.to_csv(p_mitra, index=False, lineterminator="\n")
    assign_file(p_mitra, districts=districts)
    print(f"Wrote {len(df_mitra)} PM MITRA Mega Textile Parks -> {p_mitra}")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    build_industrial_nodes()
