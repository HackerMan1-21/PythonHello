# Final Duplicate Engine Spec (Working Draft)

This document records the operational runbook and references the implemented orchestration scripts.

Key artifacts in repo:
- `tools/full_rebuild_manager.py` — orchestrates high-density window extraction, FAISS rebuild, E2E detection, logs to `.thumb_cache/daily_run_*.log`.
- `tools/bench_automation.py` — collects bench metrics from DB for a target folder.
- `.thumb_cache/run_fast_rebuild.py` — existing fast low-density runner (kept).

Run instructions (example):
```
# dry-run
python tools/full_rebuild_manager.py --dry-run

# actual
python tools/full_rebuild_manager.py
```

Nightly scheduling: use `tools/schedule_nightly.ps1` or create a Windows Scheduled Task to run `full_rebuild_manager.py` at 22:00.

See repository README for details and operational checks.
