"""Nightly runner for duplicate detection.

Features:
- optional schedule start time (wait until night)
- checkpointing between batches (JSON)
- batch processing to limit memory/CPU
- ETA estimation via small sample

Usage:
  python -B tools\nightly_runner.py --folder "C:\path\to\data" --batch 200 --checkpoint .\dup_checkpoint.json --schedule 02:00 --eta-warn 3600
"""
import argparse
import datetime
import json
import os
import time
from typing import List

from component.utils.file_util import collect_files
from component.duplicate_finder import find_duplicates_streaming


def load_checkpoint(path: str):
    if not os.path.exists(path):
        return {"processed": [], "groups": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"processed": [], "groups": []}


def save_checkpoint(path: str, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def wait_until_time(time_str: str):
    # time_str like '02:00'
    now = datetime.datetime.now()
    hh, mm = [int(x) for x in time_str.split(":")]
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    wait = (target - now).total_seconds()
    print(f"[SCHEDULE] Waiting {int(wait)}s until {target}")
    time.sleep(wait)


def estimate_runtime(folder: str, sample_size: int = 50, batch_size: int = 200):
    files = collect_files(folder)
    sample = files[: min(len(files), sample_size)]
    if not sample:
        return 0.0
    print(f"[ESTIMATE] Sampling {len(sample)} files for time estimate...")
    t0 = time.perf_counter()
    # run a streaming pass on sample (fast path)
    try:
        find_duplicates_streaming(folder, sample, progress_callback=None, parallel=True)
    except Exception:
        pass
    t1 = time.perf_counter()
    elapsed = t1 - t0
    per_file = elapsed / max(1, len(sample))
    total_files = len(files)
    est_total = per_file * total_files
    print(f"[ESTIMATE] per_file={per_file:.3f}s total_files={total_files} est_total={est_total:.1f}s (~{est_total/3600:.2f}h)")
    return est_total


def run_batches(folder: str, checkpoint_path: str, batch_size: int, eta_warn: float):
    all_files = collect_files(folder)
    ck = load_checkpoint(checkpoint_path)
    processed = set(ck.get("processed", []))
    remaining = [p for p in all_files if p not in processed]
    total = len(all_files)
    print(f"[RUN] total files={total}, remaining={len(remaining)}")

    # quick ETA and warn
    est = estimate_runtime(folder, sample_size=50, batch_size=batch_size)
    if eta_warn and est > eta_warn:
        print(f"[WARNING] Estimated runtime {est:.0f}s exceeds warning threshold {eta_warn}s")

    start = time.perf_counter()
    processed_count = len(processed)
    groups_accum = ck.get("groups", []) or []

    for i in range(0, len(remaining), batch_size):
        batch = remaining[i : i + batch_size]
        print(f"[BATCH] Processing batch {i//batch_size + 1} ({len(batch)} files)...")
        t0 = time.perf_counter()
        try:
            batch_groups = find_duplicates_streaming(folder, batch, progress_callback=None, parallel=True)
        except Exception as e:
            print(f"[BATCH] Error processing batch: {e}")
            batch_groups = []
        t1 = time.perf_counter()
        dur = t1 - t0
        processed_count += len(batch)
        # naive merge: append (consumer can re-group later)
        if batch_groups:
            groups_accum.extend(batch_groups)

        # update checkpoint: mark files as processed
        processed.update(batch)
        ck = {"processed": list(processed), "groups": groups_accum}
        save_checkpoint(checkpoint_path, ck)
        elapsed = time.perf_counter() - start
        per_file = elapsed / max(1, processed_count)
        remaining_files = total - processed_count
        eta = remaining_files * per_file
        print(f"[BATCH] done dur={dur:.1f}s processed={processed_count}/{total} ETA={eta:.1f}s")

    print("[RUN] All batches complete")
    return groups_accum


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", required=True)
    parser.add_argument("--checkpoint", default="dup_checkpoint.json")
    parser.add_argument("--batch", type=int, default=200)
    parser.add_argument("--schedule", type=str, default=None, help="HH:MM to wait until")
    parser.add_argument("--eta-warn", type=float, default=3600.0, help="seconds to warn if estimated runtime exceeds")
    args = parser.parse_args()

    if args.schedule:
        wait_until_time(args.schedule)

    groups = run_batches(args.folder, args.checkpoint, args.batch, args.eta_warn)
    # final save
    save_checkpoint(args.checkpoint, {"processed": collect_files(args.folder), "groups": groups})
    print("[DONE] checkpoint saved")


if __name__ == "__main__":
    main()
