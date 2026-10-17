"""Entry: python3.11 -I -S -B -u n2_harness.py selftest|prepare|run ... (stdlib only)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from n2h import cli

sys.exit(cli.main(sys.argv))
