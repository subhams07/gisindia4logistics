"""
scripts/fetch/build_agri_logistics.py
Compiles Cold Chain Storage Facilities and APMC e-NAM Mandis across India.
"""

import sys
import pandas as pd

sys.path.insert(0, ".")
from scripts.clean.standardize import DATA_DIR

COLD_CHAIN_DATA = [
    {"name": "Agra Cold Storage Cluster (Khandari)", "facility_type": "Cold Storage / CA Store", "state": "Uttar Pradesh", "district": "Agra", "city": "Agra", "latitude": 27.2150, "longitude": 77.9850, "capacity_mt": 45000, "commodities": "Potato, Fruits, Vegetables", "operator": "UP Cold Storage Association", "source_url": "https://nccd.gov.in"},
    {"name": "Nashik Agro Cold Hub (Pimpalgaon)", "facility_type": "Integrated Packhouse & Cold Storage", "state": "Maharashtra", "district": "Nashik", "city": "Pimpalgaon Baswant", "latitude": 20.1750, "longitude": 73.9850, "capacity_mt": 35000, "commodities": "Grapes, Onion, Pomegranate", "operator": "Mahagrapes / APEDA Cluster", "source_url": "https://apeda.gov.in"},
    {"name": "Shimla Controlled Atmosphere Storage (Parwanoo)", "facility_type": "CA Store & Packhouse", "state": "Himachal Pradesh", "district": "Solan", "city": "Parwanoo", "latitude": 30.8350, "longitude": 76.9580, "capacity_mt": 25000, "commodities": "Apples, Stone Fruits", "operator": "HPMC (Himachal Pradesh Agro)", "source_url": "https://hpmc.in"},
    {"name": "Guntur Cold Chain Terminal", "facility_type": "Specialized Spice Cold Storage", "state": "Andhra Pradesh", "district": "Guntur", "city": "Guntur", "latitude": 16.3050, "longitude": 80.4420, "capacity_mt": 50000, "commodities": "Red Chilli, Turmeric, Spices", "operator": "AP State Warehousing Corporation", "source_url": "https://nccd.gov.in"},
    {"name": "Jalgaon Banana Packhouse & Ripening Hub", "facility_type": "Packhouse & Cold Store", "state": "Maharashtra", "district": "Jalgaon", "city": "Raver / Jalgaon", "latitude": 21.0120, "longitude": 75.5680, "capacity_mt": 30000, "commodities": "Banana, Citrus", "operator": "Jain Farm Fresh / APEDA", "source_url": "https://apeda.gov.in"},
    {"name": "Vashi Cold Storage Complex", "facility_type": "Multi-Commodity Terminal Cold Store", "state": "Maharashtra", "district": "Thane", "city": "Navi Mumbai", "latitude": 19.0750, "longitude": 73.0050, "capacity_mt": 60000, "commodities": "Fruits, Vegetables, Dairy, Meat", "operator": "APMC Vashi Cold Logistics", "source_url": "https://apeda.gov.in"},
    {"name": "Sonipat Cold Chain & Food Logistics Park", "facility_type": "Integrated Cold Chain Park", "state": "Haryana", "district": "Sonipat", "city": "Rai / Kundli", "latitude": 28.9250, "longitude": 77.0850, "capacity_mt": 40000, "commodities": "Multi-Commodity (NCR Supply)", "operator": "Dev Bhumi Cold Chain / MoFPI", "source_url": "https://mofpi.gov.in"},
    {"name": "Indore Agri Cold Hub", "facility_type": "Multi-Chamber Cold Storage", "state": "Madhya Pradesh", "district": "Indore", "city": "Sanwer / Indore", "latitude": 22.7850, "longitude": 75.8420, "capacity_mt": 35000, "commodities": "Potato, Garlic, Fruits", "operator": "MP Agro Industries", "source_url": "https://nccd.gov.in"},
    {"name": "Ludhiana Multi-Commodity Cold Store", "facility_type": "Grain & Perishable Cold Storage", "state": "Punjab", "district": "Ludhiana", "city": "Ludhiana", "latitude": 30.9050, "longitude": 75.8550, "capacity_mt": 30000, "commodities": "Seed Potato, Dairy, Poultry", "operator": "Punjab Agro", "source_url": "https://nccd.gov.in"},
    {"name": "Bengaluru Rural Cold Hub (Hosakote)", "facility_type": "Integrated Packhouse & Cold Storage", "state": "Karnataka", "district": "Bengaluru Rural", "city": "Hosakote", "latitude": 13.0720, "longitude": 77.7980, "capacity_mt": 25000, "commodities": "Horticulture, Exotic Fruits, Floriculture", "operator": "Karnataka State Spices Development", "source_url": "https://nccd.gov.in"},
    {"name": "Ratnagiri Mango Irradiation & Cold Hub", "facility_type": "Vapour Heat & Cold Treatment Facility", "state": "Maharashtra", "district": "Ratnagiri", "city": "Ratnagiri", "latitude": 16.9920, "longitude": 73.3120, "capacity_mt": 15000, "commodities": "Alphonso Mango, Cashew", "operator": "MSAMB / APEDA", "source_url": "https://msamb.com"},
    {"name": "Hooghly Cold Storage Belt (Tarakeswar)", "facility_type": "Large-Scale Potato Cold Store", "state": "West Bengal", "district": "Hugli", "city": "Tarakeswar", "latitude": 22.8850, "longitude": 88.0210, "capacity_mt": 65000, "commodities": "Potato", "operator": "West Bengal Cold Storage Association", "source_url": "https://nccd.gov.in"},
    {"name": "Ahmedabad Cold Hub (Bavla)", "facility_type": "Perishable Export Cold Chain", "state": "Gujarat", "district": "Ahmadabad", "city": "Bavla", "latitude": 22.8350, "longitude": 72.3650, "capacity_mt": 28000, "commodities": "Vegetables, Dairy, Cumin", "operator": "Gujarat Agro Industries Corp", "source_url": "https://nccd.gov.in"},
    {"name": "Kochi Seafood & Spice Cold Terminal", "facility_type": "Reefer & Deep Freeze Cold Store", "state": "Kerala", "district": "Ernakulam", "city": "Kochi / Willingdon Island", "latitude": 9.9450, "longitude": 76.2750, "capacity_mt": 20000, "commodities": "Seafood (Marine Exports), Spices", "operator": "MPEDA Cold Chain Logistics", "source_url": "https://mpeda.gov.in"},
    {"name": "Guwahati Perishable Cargo Cold Hub", "facility_type": "Air Cargo Cold Storage", "state": "Assam", "district": "Kamrup Metropolitan", "city": "Guwahati (LGBI Airport)", "latitude": 26.1050, "longitude": 91.5850, "capacity_mt": 10000, "commodities": "Ginger, Turmeric, King Chilli, Tea, Fresh Vegetables", "operator": "AAI Cargo Logistics (AAICLAS)", "source_url": "https://aaiclas.aero"},
]

ENAM_MANDIS_DATA = [
    {"name": "Azadpur APMC Mandi (Asia's Largest)", "state": "Delhi", "district": "North", "city": "Azadpur, New Delhi", "latitude": 28.7120, "longitude": 77.1750, "market_type": "Mega Terminal Market", "primary_commodities": "Fruits, Vegetables, Onion, Potato, Apple", "annual_volume_mt": 4500000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Vashi APMC Market (Navi Mumbai)", "state": "Maharashtra", "district": "Thane", "city": "Navi Mumbai", "latitude": 19.0750, "longitude": 73.0080, "market_type": "Terminal Market", "primary_commodities": "Foodgrains, Spices, Fruits, Onion, Potato", "annual_volume_mt": 3800000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Unjha APMC Mandi (Spices Capital)", "state": "Gujarat", "district": "Mahesana", "city": "Unjha", "latitude": 23.8050, "longitude": 72.3950, "market_type": "Specialized Commodity Market", "primary_commodities": "Cumin (Jeera), Fennel (Saunf), Isabgol, Mustard", "annual_volume_mt": 850000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Guntur Mirchi Yard (Asia's Largest Chilli Market)", "state": "Andhra Pradesh", "district": "Guntur", "city": "Guntur", "latitude": 16.2950, "longitude": 80.4350, "market_type": "Specialized Commodity Market", "primary_commodities": "Red Chilli, Cotton, Turmeric", "annual_volume_mt": 1200000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Khanna Grain Market (Asia's Largest Grain Market)", "state": "Punjab", "district": "Ludhiana", "city": "Khanna", "latitude": 30.7050, "longitude": 76.2150, "market_type": "Grain Terminal Market", "primary_commodities": "Wheat, Paddy (Basmati/Non-Basmati), Maize", "annual_volume_mt": 2500000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Lasalgaon APMC Mandi (Asia's Largest Onion Market)", "state": "Maharashtra", "district": "Nashik", "city": "Lasalgaon", "latitude": 20.1450, "longitude": 74.2250, "market_type": "Specialized Commodity Market", "primary_commodities": "Onion, Grapes, Pomegranate, Soyabean", "annual_volume_mt": 950000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Indore Devi Ahilya Bai Holkar APMC Mandi", "state": "Madhya Pradesh", "district": "Indore", "city": "Indore", "latitude": 22.7150, "longitude": 75.8250, "market_type": "General Agri Terminal", "primary_commodities": "Soyabean, Wheat, Gram, Garlic, Onion", "annual_volume_mt": 1800000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Karnal Grain & Basmati APMC Mandi", "state": "Haryana", "district": "Karnal", "city": "Karnal", "latitude": 29.6850, "longitude": 76.9850, "market_type": "Grain & Basmati Export Hub", "primary_commodities": "Basmati Rice, Wheat, Mustard", "annual_volume_mt": 1400000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Nizamabad APMC Turmeric Market", "state": "Telangana", "district": "Nizamabad", "city": "Nizamabad", "latitude": 18.6750, "longitude": 78.0950, "market_type": "Specialized Commodity Market", "primary_commodities": "Turmeric, Maize, Soyabean, Paddy", "annual_volume_mt": 750000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Gondal APMC Mandi", "state": "Gujarat", "district": "Rajkot", "city": "Gondal", "latitude": 21.9620, "longitude": 70.7950, "market_type": "General Agri Market", "primary_commodities": "Groundnut, Cotton, Sesame, Chilli, Onion", "annual_volume_mt": 1100000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Mandsaur APMC Mandi (Garlic Capital)", "state": "Madhya Pradesh", "district": "Mandsaur", "city": "Mandsaur", "latitude": 24.0720, "longitude": 75.0680, "market_type": "Specialized Commodity Market", "primary_commodities": "Garlic, Opium/Poppy Seed, Mustard, Soyabean", "annual_volume_mt": 680000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Kalaburagi APMC Red Gram Mandi", "state": "Karnataka", "district": "Kalaburagi", "city": "Kalaburagi (Gulbarga)", "latitude": 17.3320, "longitude": 76.8350, "market_type": "Pulse / Dal Hub", "primary_commodities": "Tur (Red Gram / Pigeon Pea), Green Gram, Sunflower", "annual_volume_mt": 820000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Bikaner APMC Mandi", "state": "Rajasthan", "district": "Bikaner", "city": "Bikaner", "latitude": 28.0180, "longitude": 73.3150, "market_type": "Arid Agri Hub", "primary_commodities": "Guar Seed, Groundnut, Mustard, Moth", "annual_volume_mt": 720000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Kalyani APMC Terminal Market", "state": "West Bengal", "district": "Nadia", "city": "Kalyani", "latitude": 22.9750, "longitude": 88.4350, "market_type": "Terminal Market", "primary_commodities": "Jute, Rice, Vegetables, Fish", "annual_volume_mt": 1150000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Davanagere APMC Maize Market", "state": "Karnataka", "district": "Davanagere", "city": "Davanagere", "latitude": 14.4650, "longitude": 75.9250, "market_type": "Grain & Cotton Hub", "primary_commodities": "Maize, Cotton, Arecanut, Ragi", "annual_volume_mt": 690000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
    {"name": "Erode APMC Turmeric Market (Yellow City)", "state": "Tamil Nadu", "district": "Erode", "city": "Erode", "latitude": 11.3420, "longitude": 77.7280, "market_type": "Specialized Commodity Market", "primary_commodities": "Turmeric, Coconut, Banana, Tapioca", "annual_volume_mt": 580000, "enam_integrated": True, "source_url": "https://enam.gov.in"},
]


def build_agri():
    out_dir = DATA_DIR / "logistics_hubs"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Cold Chain Storages
    df_cold = pd.DataFrame(COLD_CHAIN_DATA)
    p_cold = out_dir / "cold_chain_storages.csv"
    df_cold.to_csv(p_cold, index=False)
    print(f"Wrote {len(df_cold)} Cold Chain Storage Hubs -> {p_cold}")

    # 2. e-NAM Mandis
    df_mandis = pd.DataFrame(ENAM_MANDIS_DATA)
    p_mandis = out_dir / "enam_mandis.csv"
    df_mandis.to_csv(p_mandis, index=False)
    print(f"Wrote {len(df_mandis)} APMC e-NAM Mandi Hubs -> {p_mandis}")


if __name__ == "__main__":
    build_agri()
