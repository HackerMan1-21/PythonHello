#!/usr/bin/env python3
"""Run threshold-relaxation experiments for video partial-match parameters.

This script runs multiple profiles and writes a CSV summary to .thumb_cache/threshold_experiments_{ts}.csv

Usage:
  python tools/threshold_experiments.py --folder .thumb_cache/bench_sample_30
"""
import sys
from pathlib import Path
import time
import csv
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from component import duplicate_finder as df

DEFAULT_FOLDER = str(ROOT / '.thumb_cache' / 'bench_sample_30')

PROFILES = [
    {
        'name': 'baseline',
        'min_matches': df.VIDEO_PARTIAL_MIN_MATCHES,
        'overlap': df.VIDEO_PARTIAL_OVERLAP_RATIO_MIN,
        'hash_dist_max': df.VIDEO_PARTIAL_HASH_DISTANCE_MAX,
        'hash_dist_avg_max': df.VIDEO_PARTIAL_HASH_DISTANCE_AVG_MAX,
        'candidate_min_shared': df.VIDEO_PARTIAL_CANDIDATE_MIN_SHARED,
        'meta_penalty_weight': df.META_PENALTY_WEIGHT,
    },
    {
        'name': 'relaxed1',
        'min_matches': max(2, int(df.VIDEO_PARTIAL_MIN_MATCHES/2)),
        'overlap': max(0.15, df.VIDEO_PARTIAL_OVERLAP_RATIO_MIN * 0.6),
        'hash_dist_max': df.VIDEO_PARTIAL_HASH_DISTANCE_MAX + 4,
        'hash_dist_avg_max': df.VIDEO_PARTIAL_HASH_DISTANCE_AVG_MAX + 2,
        'candidate_min_shared': max(1, int(df.VIDEO_PARTIAL_CANDIDATE_MIN_SHARED/2)),
        'meta_penalty_weight': max(0.0, df.META_PENALTY_WEIGHT * 0.6),
    },
    {
        'name': 'relaxed2_more',
        'min_matches': max(1, int(df.VIDEO_PARTIAL_MIN_MATCHES/3)),
        'overlap': max(0.1, df.VIDEO_PARTIAL_OVERLAP_RATIO_MIN * 0.4),
        'hash_dist_max': df.VIDEO_PARTIAL_HASH_DISTANCE_MAX + 8,
        'hash_dist_avg_max': df.VIDEO_PARTIAL_HASH_DISTANCE_AVG_MAX + 4,
        'candidate_min_shared': 1,
        'meta_penalty_weight': max(0.0, df.META_PENALTY_WEIGHT * 0.4),
    },
    {
        'name': 'strict_meta_low',
        'min_matches': df.VIDEO_PARTIAL_MIN_MATCHES,
        'overlap': df.VIDEO_PARTIAL_OVERLAP_RATIO_MIN,
        'hash_dist_max': df.VIDEO_PARTIAL_HASH_DISTANCE_MAX,
        'hash_dist_avg_max': df.VIDEO_PARTIAL_HASH_DISTANCE_AVG_MAX,
        'candidate_min_shared': df.VIDEO_PARTIAL_CANDIDATE_MIN_SHARED,
        'meta_penalty_weight': df.META_PENALTY_WEIGHT * 1.5,
    }
]


def run_profile(folder: str, profile: dict):
    print(f"Running profile: {profile['name']}")
    # Temporarily set global meta weight
    orig_meta = df.META_PENALTY_WEIGHT
    df.META_PENALTY_WEIGHT = profile.get('meta_penalty_weight', orig_meta)

    start = time.time()
    groups, _ = df.find_duplicates_in_folder(
        folder,
        use_advanced=True,
        enable_video_partial_match=True,
        video_partial_sample_interval_sec=df.VIDEO_PARTIAL_SAMPLE_INTERVAL_SEC,
        video_partial_max_samples=df.VIDEO_PARTIAL_MAX_SAMPLES,
        video_partial_hash_distance_max=profile['hash_dist_max'],
        video_partial_hash_distance_avg_max=profile['hash_dist_avg_max'],
        video_partial_overlap_ratio_min=profile['overlap'],
        video_partial_min_matches=profile['min_matches'],
        video_partial_candidate_min_shared=profile['candidate_min_shared'],
        video_partial_require_order=df.VIDEO_PARTIAL_REQUIRE_ORDER,
        video_partial_avoid_dark_scenes=df.VIDEO_PARTIAL_AVOID_DARK_SCENES,
        video_partial_dark_trim_ratio=df.VIDEO_PARTIAL_DARK_TRIM_RATIO,
        video_partial_min_contrast_std=df.VIDEO_PARTIAL_MIN_CONTRAST_STD,
    )
    elapsed = time.time() - start

    # restore
    df.META_PENALTY_WEIGHT = orig_meta

    return {
        'profile': profile['name'],
        'groups_count': len(groups) if groups is not None else 0,
        'elapsed_s': elapsed,
        'min_matches': profile['min_matches'],
        'overlap': profile['overlap'],
        'hash_dist_max': profile['hash_dist_max'],
        'hash_dist_avg_max': profile['hash_dist_avg_max'],
        'candidate_min_shared': profile['candidate_min_shared'],
        'meta_penalty_weight': profile['meta_penalty_weight'],
    }


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--folder', default=DEFAULT_FOLDER)
    p.add_argument('--out', default=None)
    args = p.parse_args()

    folder = args.folder
    out = args.out
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    outf = Path('.thumb_cache') / f'threshold_experiments_{ts}.csv' if out is None else Path(out)
    rows = []
    for prof in PROFILES:
        try:
            r = run_profile(folder, prof)
            print(f"Profile {prof['name']}: groups={r['groups_count']} elapsed={r['elapsed_s']:.2f}s")
            rows.append(r)
        except Exception as e:
            print(f"Profile {prof['name']} failed: {e}")

    # write CSV
    with open(outf, 'w', newline='', encoding='utf8') as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ['profile'])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print(f"Results saved to {outf}")

if __name__ == '__main__':
    main()
