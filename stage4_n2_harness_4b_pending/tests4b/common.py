"""Shared 4b in-process helpers (venv interpreter). Imports the REAL producer modules and asserts where they live."""
import os
import sys
from pathlib import Path

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

import scripts.analyze.generate_isochrones as iso
import scripts.analyze.intermodal_cost_engine as ice
import scripts.analyze.nh_travel_matrix as matrix
import scripts.analyze.port_hinterland_model as phm
import scripts.analyze.producer_io as pio
import scripts.clean.graph_builder as builder

from n2h4b import h0guard, w0
from tests import helpers as H
from tests4b import fixtures as F

REPO = H.REPO
PRODUCER_MODULES = (iso, ice, matrix, phm, pio, builder)


def assert_modules_in_repo(tc):
    rep = w0.module_origin_report(PRODUCER_MODULES, REPO)
    for name, v in rep.items():
        tc.assertTrue(v["inside_repo"], "%s resolves to %s (stale install?)" % (name, v["file"]))
    return rep


def make_data(scr, name="data", **kw):
    root = scr.path(name)
    os.makedirs(root, exist_ok=True)
    F.make_data_dir(root, **kw)
    return root


def warm_cache(data_dir, cache_dir):
    """Cold-build the cache with the REAL builder (as H0 would) and return the expected archive state."""
    nh, _ = builder.load_highway_source(Path(data_dir))
    builder.load_or_build_cached_graph(nh, cache_dir=Path(cache_dir))
    return h0guard.archive_state(cache_dir)


def witness_run(data_dir, cache_dir, sample, **wkw):
    """build_travel_outputs under the W0 witness (no cache guard). Returns (summary, matrix, prov, witness)."""
    dpath = os.path.join(data_dir, "administrative", "india_districts_lgd.geojson")
    wit = w0.W0Witness(matrix, pio, dpath, sample, **wkw)
    with wit:
        s, pm, prov = matrix.build_travel_outputs(data_dir=data_dir, cache_dir=cache_dir)
    return s, pm, prov, wit
