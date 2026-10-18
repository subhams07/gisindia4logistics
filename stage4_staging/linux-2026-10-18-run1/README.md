# Staged national run linux-2026-10-18-run1 — NOT PUBLISHED

Staging output of the unchanged `scripts/analyze/stage4_generate.py`, run 2026-10-18 on the Linux cloud host with operator approval
("run stage4_generate in /home/user/n2_stage with a 12 GiB cap"). **Not a replacement for `data/analysis/`.** Drift explanation,
audits, consumer checks and an explicit publication decision come first.

- Command: `run.sh` (own memory cgroup capped at 12 GiB; threads = 1; Python 3.11.17 venv; source root `data/`).
- Result: rc 0; 1,045 s; cgroup peak 709,541,888 B; ru_maxrss 748,089,344 B; failcnt 0; oom_kill 0 (`run.log`, `mem.log`).
- Graph: 316,930 nodes / 932,722 edges from `roads/india_nh_network.parquet`.
- `analysis/`: the 7 artifacts + `producer_limitations.json` + `producer_manifest.json` (file modes normalised to 0644 for Git; bytes unchanged).
- `SHA256SUMS`: hashes of `analysis/*`.
