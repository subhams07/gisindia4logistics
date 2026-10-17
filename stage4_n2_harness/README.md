# Stage 4 N2 resource harness — step 4a snapshot

**Not production code. Not part of the 112-file accepted-code freeze. Never merge to `main` without explicit operator approval.**

Stdlib-only Linux containment/telemetry controller and synthetic self-tests implementing
[`docs/stage4_n2_linux_addendum.md`](../docs/stage4_n2_linux_addendum.md) r4
(SHA-256 `26d285927cf26d8317fd32206ae57b757045950d81259e885f395f724831969f`) and operator rulings D-1–D-11
recorded in [`docs/claude_remote_handoff.md`](../docs/claude_remote_handoff.md) section 0.

- Status: step 4a **accepted** by independent Opus 5.5 (medium) implementation re-review after fix round 1.
  Reviewer run: 161 run / 0 fail / 0 error / 0 skip; `ruff check .` clean; cleanup clean.
- Harness hash: `5354abcf89b6bb029d4cd808e07924e5330185a0cad6e64c7ef766029553620c`; per-file hashes in `results/FILES.sha256`
  (`results/*.json` files not listed there are run outputs).
- Scope: M01–M04, M08–M16. M05–M07 (step 4b) need the deferred venv (D-9). `run` mode cannot dispatch a real producer.
- Running the self-tests creates and removes real `n2-*` cgroups and tiny owned processes. Run only as root on the reviewed
  Linux host type, with no other N2 user and no concurrent workloads: `/usr/bin/python3.11 -I -B -u run_selftests.py`.
- Details: `IMPLEMENTATION_REPORT.md` (sections 11–12: review fixes and 4b carry-forward), `MATRIX.md`.
