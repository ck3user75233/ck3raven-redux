---
name: Run Error Analysis
description: Sign and run the CK3 error analysis toolkit against the current or a specified error.log.
---

Run the CK3 error analysis toolkit (`analyze_error_files.py`) against the error log.

Steps:
1. If using a non-default log, write the host path to `root:ck3raven_data/wip/.log_path_override` first.
2. Sign `root:ck3raven_data/wip/error_analysis/analyze_error_files.py`.
3. Open a ROOT_CK3RAVEN_DATA contract with work_declaration covering `wip/error_file_analysis.txt` and `wip/script_error_report.txt`.
4. Execute: `python error_analysis/analyze_error_files.py` with working_dir `root:ck3raven_data/wip` and timeout 240.
5. Report the TOP 25 summary from stdout and note any files we own (Gambo + EC724).

If the user provides a log path, use the `--log` argument instead of the override file.
