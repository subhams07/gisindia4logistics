"""Slot definitions and the 4b plug-in points. 4a runs ONLY the synthetic stdlib workload.

M05 (real tiny W0 helper parity), M06 (real graph cache) and M07 (H1 to H3 transfer with real
producer helpers) need the scientific venv (D-9, step 4b). They plug in as payload sources: a payload
is a hash-bound source text executed by the stub inside the contained namespace, receiving
CONFIG (JSON) and the nonce-framed `mark()` function. 4b supplies real payload texts + validated
CONFIG for the six slots; the controller, ledger, containment and telemetry stay unchanged.
"""
from .errors import N2Stop

SLOT_REAL_COMPUTATION = {
    "H0": "graph_builder.load_highway_source + load_or_build_cached_graph (absent fresh scratch cache)",
    "H1_12": "nh_travel_matrix.build_travel_outputs, first 12 district rows",
    "H1_64": "nh_travel_matrix.build_travel_outputs, first 64 district rows",
    "H1_192": "nh_travel_matrix.build_travel_outputs, first 192 district rows (only this feeds H3)",
    "H2": "generate_isochrones.build_isochrones (major ports 4/8/12/24 h, MMLPs 4/8/12 h)",
    "H3": "compute_modal_costs + run_port_gravity_model on the H1(192) transfer root",
}

PENDING_4B = {
    "M05": "real tiny W0 helper parity (cross-prefix collision, same-state duplicate, uncoded/null population, "
           "non-default index, literal labels, lossy large-code round trip STOP, both population bindings)",
    "M06": "tiny real cold graph build and warm loads through both bindings; tamper/missing cache; latches",
    "M07": "both transfer domains, membership, exact real H3 arguments; tamper/missing/null/order drift refused",
}


class Pending4b(N2Stop):
    pass


def real_payload(slot):
    """Real producer payloads do not exist in 4a. Never fall back to a fake: STOP."""
    raise Pending4b("not_implemented_4b", "slot %s needs the scientific venv (D-9) and real producer helpers" % slot)


def synthetic_payload_only():
    return True
