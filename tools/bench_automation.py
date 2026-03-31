#!/usr/bin/env python3
"""Bench automation utilities: run subset or full bench and collect basic metrics.

Usage: python tools/bench_automation.py --folder <path> [--limit N]
"""
import time
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from component.utils import db as dup_db

def run(folder: Path, limit: int = None):
    files = [f for f in folder.rglob('*') if f.is_file()]
    if limit:
        files = files[:limit]
    start = time.time()
    total_files = len(files)
    total_windows = 0
    for f in files:
        rec = dup_db.get_file_by_path(str(f))
        if not rec:
            continue
        w = dup_db.get_window_features_for_file(rec['file_id'])
        total_windows += len(w) if w else 0
    elapsed = time.time() - start
    print(f"bench target: {folder}")
    print(f"files_count: {total_files}")
    print(f"total_windows: {total_windows}")
    print(f"avg_windows_per_file: {total_windows / total_files if total_files else 0:.2f}")
    print(f"elapsed_s: {elapsed}")

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--folder', required=True)
    p.add_argument('--limit', type=int)
    args = p.parse_args()
    run(Path(args.folder), args.limit)
