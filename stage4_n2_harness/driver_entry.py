"""Entry point: python3.11 -I -S -B -u driver_entry.py <spec.json> <result.json>"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from n2h import driver

sys.exit(driver.main(sys.argv))
