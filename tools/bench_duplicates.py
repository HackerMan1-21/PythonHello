"""Benchmark runner for duplicate detection pipeline.

Usage:
    python tools/bench_duplicates.py --folder <path> --n 1000

This script will sample `n` files from the given folder (or use all if fewer), copy them
into a temporary bench folder under `.thumb_cache/bench_{n}`, run `find_duplicates_in_folder`
and report timings and group counts. For large `n` ensure there is enough disk space.
"""
import argparse
import os
import shutil
import time
from component.duplicate_finder import find_duplicates_in_folder, get_image_and_video_files


def prepare_sample(folder, n, tmpdir):
    files = get_image_and_video_files(folder)
    if not files:
        raise RuntimeError('No media files found in folder')
    if len(files) <= n:
        # if fewer files than n, just use all (no copy)
        return None, files
    os.makedirs(tmpdir, exist_ok=True)
    chosen = files[:n]
    print(f'Copying {len(chosen)} files to {tmpdir} (this may take time)')
    for i, p in enumerate(chosen):
        fn = os.path.basename(p)
        dest = os.path.join(tmpdir, f'{i:06d}_{fn}')
        try:
            shutil.copy2(p, dest)
        except Exception:
            # skip files that fail to copy
            continue
    return tmpdir, os.listdir(tmpdir)


def run_bench(folder, n, use_advanced=True):
    tmpdir = os.path.join('.thumb_cache', f'bench_{n}')
    copy_dir, files_or_none = prepare_sample(folder, n, tmpdir)
    target_folder = copy_dir if copy_dir is not None else folder

    start = time.time()
    groups, _ = find_duplicates_in_folder(target_folder, use_advanced=use_advanced)
    elapsed = time.time() - start
    print(f'BENCH n={n}: elapsed={elapsed:.2f}s, groups={len(groups)}')
    return elapsed, len(groups)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--folder', required=True)
    p.add_argument('--n', type=int, default=100)
    p.add_argument('--advanced', action='store_true')
    args = p.parse_args()
    print('Bench start', args.folder, args.n)
    try:
        run_bench(args.folder, args.n, use_advanced=args.advanced)
    except Exception as e:
        print('Bench failed:', e)
