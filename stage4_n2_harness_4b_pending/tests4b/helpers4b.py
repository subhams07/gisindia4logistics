"""4b helpers: a uid-traversable private scratch base, mock worlds that run the VENV interpreter."""
import os
import shutil
import tempfile

from tests import helpers as H

VENV_PY = os.path.join(H.REPO, ".venv", "bin", "python")
UID = 65534


class UScratch(H.Scratch):
    """Private scratch root under /tmp with mode 0711: the scratchpad chain is 0700, so an unprivileged payload
    could not reach it. Created and removed by the tests; contains only synthetic data."""

    def __init__(self, tag):
        self.root = tempfile.mkdtemp(prefix="n2-4b-" + tag[:20] + "-", dir="/tmp")
        os.chmod(self.root, 0o711)
        self.leftover_layouts = []

    def cleanup(self):
        super().cleanup()
        shutil.rmtree(self.root, ignore_errors=True)


BIG_POLICY = {  # SYNTHETIC test values for real imports; NOT approved, NOT a proposal
    "attempt_memory_limit_bytes": 3 << 30, "attempt_pids_max": 256, "conservative_peak_max_bytes": 9 << 30,
    "per_slot_elapsed_cap_s": 300, "release_handshake_timeout_s": 60, "graceful_wait_s": 5,
}
