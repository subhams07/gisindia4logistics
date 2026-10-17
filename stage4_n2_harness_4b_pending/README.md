# Stage 4 N2 harness — step 4b snapshot: NOT ACCEPTED

**Work in progress, preserved for restart only. Do not use for any run. Not production code. Never merge to `main`.**

- Harness hash `9f9348a44e036dbf6df13d2dc0b2b0bd66c8855b7cd62e7f3bd4b2f52a1928f4`; self-test receipt
  `3844e76e2062a8fa3e0b4a8af97c6d22bdb004817053093bef6907cf0451195a`; per-file hashes in `results/FILES.sha256`.
- Status: stopped at the operator's two-review cap. Final Opus review: ACCEPT_WITH_REQUIRED_CHANGES with **one open
  BLOCKER, B1-R1**: validation resolves only the configured read directory, so a symlink or hard link inside it can reach
  `<repo>/data`. Details, the proposed fix and the non-blocking list: `docs/claude_remote_handoff.md` section 0.
- The accepted harness remains `../stage4_n2_harness/` (step 4a, hash `eda94197…abec`).
- Reviewer-reported results: stdlib suite 213/213, venv suite 58/58 (with `data/` masked), ruff clean.
  `results/findings-*.json` and `results/compat-4b.json` were last rewritten by the reviewer's run (not in the manifest).
- To resume: copy this directory to a private scratch location, recreate the venv (handoff section 0), fix B1-R1, then
  get an Opus review before it may replace `../stage4_n2_harness/`.
