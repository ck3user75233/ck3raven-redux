---
name: Run Normalizer
description: Sign and run the CK3 line-endings normalizer to scan or fix Gambo + EC724 mod files.
---

Run the CK3 line-endings normalizer on Gambo + EC724.

**Scan (read-only, default):**
1. Sign `root:ck3raven_data/wip/normalizer/run_gambo_scan.py`.
2. Open ROOT_CK3RAVEN_DATA contract, declare output `wip/review/line_endings/scan_gambo_plus_ec724.json`.
3. Execute: `python normalizer/run_gambo_scan.py` with working_dir `root:ck3raven_data/wip`.
4. Report the summary (clean / needs_change / errors counts).

**Apply (fixes in-place):**
1. Read and back up any `needs_change` files to `root:ck3raven_data/wip/review/line_endings/backups/` first.
2. Sign `root:ck3raven_data/wip/normalizer/run_gambo_apply.py`.
3. Open ROOT_CK3RAVEN_DATA contract, declare output `wip/review/line_endings/apply_gambo_plus_ec724.json`.
4. Execute: `python normalizer/run_gambo_apply.py` with working_dir `root:ck3raven_data/wip`.
5. Stage and commit any fixed files through your normal local Git workflow (the `ck3_git` tool was retired at commit `72b0607`; no MCP Git capability exists).
