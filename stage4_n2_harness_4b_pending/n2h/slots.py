"""Slot definitions and the payload builder (4b). Stdlib only: the payload is TEXT, hash-bound in the attempt
record, executed by the stub inside the contained namespace with CONFIG (JSON), `mark()` and NONCE.

The real producers are imported only by the payload (venv interpreter), never by the controller process.
Real computation stays refused at the run gate: no approved policy/scope receipt exists.
"""
import os

from .errors import N2Stop

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def verified_repo_root(interpreter):
    """The repository is the parent of the project venv the payload interpreter belongs to (it is an IMPORT root only,
    never a data root). Derived, not configured: a CONFIG repo_root must equal this."""
    venv = os.path.dirname(os.path.dirname(os.path.abspath(interpreter)))
    repo = os.path.dirname(venv)
    if not os.path.isfile(os.path.join(venv, "pyvenv.cfg")) or not os.path.isdir(os.path.join(repo, ".git")):
        raise N2Stop("repo_root_not_derivable", "the payload interpreter is not the project venv of a repository checkout")
    return os.path.realpath(repo)


SLOT_REAL_COMPUTATION = {
    "H0": "graph_builder.load_highway_source + load_or_build_cached_graph (absent fresh scratch cache)",
    "H1_12": "nh_travel_matrix.build_travel_outputs, first 12 district rows",
    "H1_64": "nh_travel_matrix.build_travel_outputs, first 64 district rows",
    "H1_192": "nh_travel_matrix.build_travel_outputs, first 192 district rows (only this feeds H3)",
    "H2": "generate_isochrones.build_isochrones (major ports 4/8/12/24 h, MMLPs 4/8/12 h)",
    "H3": "compute_modal_costs + run_port_gravity_model on the H1(192) transfer root",
}

# payload parts, in order; every part is self-contained ASCII
PAYLOAD_PARTS = ("n2h4b/w0.py", "n2h4b/h0guard.py", "n2h/transfer.py", "n2h4b/slotmain.py")
FOOTER = "\nrun_slot(CONFIG, mark, NONCE)\n"

DECLARED_PHASES = {
    "H0": ("imports", "imports_done", "source_selection", "graph_build", "graph_done", "serialize", "done"),
    "H1": ("imports", "imports_done", "build_travel_outputs", "write_outputs", "readback", "serialize", "done"),
    "H2": ("imports", "imports_done", "build_isochrones", "serialize", "done"),
    "H3": ("imports", "imports_done", "transfer_validation", "serialize", "done"),
}


class SlotStop(N2Stop):
    pass


def phases_for(slot):
    return DECLARED_PHASES["H1" if slot.startswith("H1") else slot]


def build_payload_text(with_footer=True):
    text = "# N2 4b payload; parts: %s\n" % ", ".join(PAYLOAD_PARTS)
    for rel in PAYLOAD_PARTS:
        with open(os.path.join(HARNESS_DIR, rel), "rb") as f:  # bytes + utf-8: no lazily loaded codec module
            part = f.read().decode("utf-8")
        if not part.isascii():
            raise SlotStop("payload_part_not_ascii", rel)
        text += "\n" + part
    return text + (FOOTER if with_footer else "")


def real_payload(slot):
    """The hash-bound payload text for a real slot H0..H3 (same text for synthetic and real data)."""
    if slot not in SLOT_REAL_COMPUTATION:
        raise SlotStop("unknown_slot", slot)
    return build_payload_text()
