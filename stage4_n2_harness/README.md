# Stage 4 N2 resource harness — step 4a snapshot

**Not production code. Not part of the 112-file accepted-code freeze. Never merge to `main` without explicit operator approval.**

Stdlib-only Linux containment/telemetry controller and synthetic self-tests implementing
[`docs/stage4_n2_linux_addendum.md`](../docs/stage4_n2_linux_addendum.md) r4
(SHA-256 `26d285927cf26d8317fd32206ae57b757045950d81259e885f395f724831969f`) and operator rulings D-1–D-11
recorded in [`docs/claude_remote_handoff.md`](../docs/claude_remote_handoff.md) section 0.

- Status: step 4a **accepted** after six review rounds; final independent Opus 5.5 (medium) re-check: ACCEPT.
  Reviewer run: 179 run / 0 fail / 0 error / 0 skip; `ruff check .` clean; cleanup clean; key fixes verified by revert.
  This replaces the earlier round-1 snapshot, which had a fail-open defect.
- Harness hash: `eda94197db6cae464244d8bf844dc909ceef6d8d954a0d90240a00518138abec`; self-test receipt
  `a0968f1b0e8b3cba53ca12c8babcbc05c36272b2557c1685032a79c6877bbf22`; per-file hashes in `results/FILES.sha256`
  (`results/*.json` files not listed there are run outputs).
- Scope: M01–M04, M08–M16. M05–M07 (step 4b) need the deferred venv (D-9). `run` mode cannot dispatch a real producer.
  The driver-only test hooks/knobs listed in `IMPLEMENTATION_REPORT.md` §12 must never be reachable from real dispatch.
- Running the self-tests creates and removes real `n2-*` cgroups and tiny owned processes. Run only as root on the reviewed
  Linux host type, with no other N2 user and no concurrent workloads: `/usr/bin/python3.11 -I -B -u run_selftests.py`.
- Details: `IMPLEMENTATION_REPORT.md` (sections 11–17: review fixes; section 12: 4b carry-forward), `MATRIX.md`.
