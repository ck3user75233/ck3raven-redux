---
name: WIP Toolkit Reference
description: "Use when running error analysis, normalizer, or any CK3 WIP Python script. Covers SDK usage, script locations, sign/contract/exec workflow, and log path override pattern."
---

# CK3 WIP Toolkit Reference

## SDK

**Location:** `C:\Users\nateb\.ck3raven\wip\sdk\ck3_sdk.py`  
**Canonical:** `root:ck3raven_data/wip/sdk/ck3_sdk.py`

Bootstrap pattern (used at the top of every WIP script):

```python
import importlib.util, sys
from pathlib import Path
sdk_path = Path.home() / ".ck3raven" / "wip" / "sdk" / "ck3_sdk.py"
spec = importlib.util.spec_from_file_location("ck3_sdk", sdk_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
sdk = module.CK3SDK()
```

Key SDK methods:
- `sdk.resolve("root:user_docs/mod/Gambo + EC724")` → host `Path`
- `sdk.to_canonical(host_path_str)` → canonical string or `None`
- `sdk.wip` → `Path` to `~/.ck3raven/wip/`
- `sdk.mods` → list with `.name`, `.load_order`, `.path`, `.is_indexed`
- `sdk.playset_name` → active playset name

---

## Error Analysis Toolkit

**Location:** `root:ck3raven_data/wip/error_analysis/`

| Script | Output | Purpose |
|--------|--------|---------|
| `parse_script_errors.py` | `wip/script_error_report.txt` | Cluster all script errors by type |
| `analyze_error_files.py` | `wip/error_file_analysis.txt` | Top-25 files with override chains + diffs |

`analyze_error_files.py` internally calls parse — **only sign and run `analyze_error_files.py`**.

### Log source

- **Default:** `root:user_docs/logs/error.log` (current session log)
- **Recovered/crash log:** write the host path to `root:ck3raven_data/wip/.log_path_override` before running (auto-deleted on first read)
- **`--log` arg:** `python analyze_error_files.py --log "C:\...\error.log"`

### Exec

```
working_dir: root:ck3raven_data/wip  |  timeout: 240
python error_analysis/analyze_error_files.py
```

---

## Normalizer

**Location:** `root:ck3raven_data/wip/normalizer/`

| Script | Purpose |
|--------|---------|
| `run_gambo_scan.py` | Scan Gambo + EC724 (read-only) |
| `run_gambo_apply.py` | Fix in-place — back up first |
| `run_early_bookmarks_scan.py` | Scan EB mod |

Target spec for mod files: UTF-8 with BOM, CRLF line endings.  
Report output: `root:ck3raven_data/wip/review/line_endings/`  
Backups: `root:ck3raven_data/wip/review/line_endings/backups/`

### Exec

```
working_dir: root:ck3raven_data/wip  |  timeout: 120
python normalizer/run_gambo_scan.py
python normalizer/run_gambo_apply.py
```

---

## Sign → Contract → Exec (all WIP scripts)

1. `ck3_contract(sign_script, script_path="root:ck3raven_data/wip/...")` → user clicks **Approve** CodeLens in VS Code
2. `ck3_contract(open, root_category="ROOT_CK3RAVEN_DATA", script_path=..., script_signature=<full sign response dict>, intent=..., work_declaration={work_summary, work_plan, edits})`
3. `ck3_exec("python <relpath>", contract_id=..., working_dir="root:ck3raven_data/wip", timeout=240)`

Scripts that write to mod files (ROOT_USER_DOCS) **still use ROOT_CK3RAVEN_DATA** — enforcement authorizes the process; OS-level writes to mod files are not intercepted by the gate.
