#!/usr/bin/env python3
"""Orchestrate high-density window extraction, FAISS rebuild and E2E detection.

Usage: python tools/full_rebuild_manager.py [--dry-run]
"""
import os
import time
import argparse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from component.utils import feature_extractor as fe
from component.utils import faiss_index as fi
from component.utils import db as dup_db
from component import duplicate_finder as df
try:
    from component.utils import clip_extractor as _ce
    _CLIP_AVAILABLE = True
except Exception:
    _ce = None  # type: ignore[assignment]
    _CLIP_AVAILABLE = False

LOG_DIR = ROOT / '.thumb_cache'
LOG_DIR.mkdir(parents=True, exist_ok=True)

# High-density parameters (Phase 11: frames 6→8 for higher precision)
WINDOW_SEC = 5.0
FRAMES_PER_WINDOW = 8
OVERLAP = 0.25

def log(msg: str):
    print(msg)

def run(dry_run=False, force_reextract=False, force_reclip=False):
    start = time.time()
    if force_reextract:
        log("--force-reextract: clearing all window features for re-extraction")
        if not dry_run:
            dup_db.clear_window_features_all()
    if force_reclip:
        log("--force-reclip: clearing all CLIP features for re-extraction")
        if not dry_run:
            dup_db.clear_clip_features_all()
    files = dup_db.all_files()
    total = len(files)
    log(f"Videos to process: {total}")

    processed = 0
    windows_stored = 0
    for idx, rec in enumerate(files, start=1):
        path = rec['path']
        fid = rec['file_id']
        w = dup_db.get_window_features_for_file(fid)
        if w and len(w) > 0 and not force_reextract:
            log(f"[{idx}/{total}] skip (already has windows): {Path(path).name}")
            continue
        log(f"[{idx}/{total}] extracting windows: {Path(path).name}")
        if dry_run:
            continue
        try:
            windows = fe.extract_window_vectors(path, window_sec=WINDOW_SEC, frames_per_window=FRAMES_PER_WINDOW, overlap=OVERLAP)
        except Exception as e:
            log(f"  extract error: {e}")
            continue
        rows = []
        for (widx, s_ms, e_ms, vec) in windows:
            try:
                rows.append((int(widx), vec.tobytes(), int(s_ms), int(e_ms)))
            except Exception:
                continue
        if rows:
            dup_db.upsert_window_features(fid, rows)
            windows_stored += len(rows)
            log(f"  stored windows: {len(rows)}")
        processed += 1

    # Rebuild FAISS from DB
    log("Rebuilding FAISS index from DB...")
    idx_obj, id_list = fi.build_index_from_db()
    entries = len(id_list) if id_list else 0
    elapsed = time.time() - start
    log(f"FAISS entries: {entries}")
    log(f"Window extraction + FAISS build elapsed: {elapsed}")

    # ------------------------------------------------------------------
    # CLIP feature extraction stage (GTX 1650: batch=8, FP16)
    # ------------------------------------------------------------------
    clip_extracted = 0
    clip_skipped = 0
    if _CLIP_AVAILABLE and _ce is not None and not dry_run:
        log("Extracting CLIP features (ViT-B/32, FP16, batch=8)...")
        clip_ver = "clip_vitb32_v1"
        clip_paths = [rec['path'] for rec in files]
        for idx, rec in enumerate(files, start=1):
            path = rec['path']
            fid = rec['file_id']
            # check cache
            cached = dup_db.get_clip_feature(fid)
            if cached and cached.get('vec') and not force_reclip:
                clip_skipped += 1
                continue
            log(f"[CLIP {idx}/{total}] {Path(path).name}")
            try:
                vec = _ce.extract_clip_features(path)
                if vec is not None:
                    dup_db.upsert_clip_feature(fid, vec.tobytes(), _ce.FRAMES_FOR_CLIP, clip_ver)
                    clip_extracted += 1
            except Exception as e:
                log(f"  CLIP extract error: {e}")
        # unload GPU model to free VRAM
        try:
            _ce.unload_model()
        except Exception:
            pass
        log(f"CLIP done: extracted={clip_extracted}, skipped(cached)={clip_skipped}")

        # Rebuild HNSW (CLIP) index
        log("Building HNSW (CLIP) index...")
        import numpy as np
        clip_mat = []
        clip_fids = []
        for rec in files:
            fid = rec['file_id']
            cf = dup_db.get_clip_feature(fid)
            if cf and cf.get('vec'):
                try:
                    arr = np.frombuffer(cf['vec'], dtype=np.float32).copy()
                    if arr.shape == (512,):
                        clip_mat.append(arr)
                        clip_fids.append(fid)
                except Exception:
                    pass
        if clip_mat:
            mat = np.vstack(clip_mat).astype(np.float32)
            hnsw_obj, hnsw_ids = fi.build_hnsw_index(mat, clip_fids)
            fi.save_hnsw_index(str(LOG_DIR / 'faiss_hnsw'), hnsw_obj, hnsw_ids)
            log(f"HNSW saved: {len(hnsw_ids)} vectors")
        else:
            log("No CLIP vectors available for HNSW build")
    elif not _CLIP_AVAILABLE:
        log("CLIP not available (open-clip-torch not installed) — skipping CLIP stage")

    # Run detection on repository media folder (use duplicate_finder wrapper)
    try:
        media_root = str(ROOT)
        log("Running find_duplicates_in_folder...")
        groups, stats = df.find_duplicates_in_folder(media_root, use_advanced=True)
        log(f"find_duplicates result groups= {len(groups)} elapsed= {stats.get('elapsed', None) if stats else None}")
    except Exception as e:
        log(f"detection run failed: {e}")

    # write run log
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    logf = LOG_DIR / f'daily_run_{ts}.log'
    with open(logf, 'w', encoding='utf8') as fh:
        fh.write(f"start: {datetime.utcnow().isoformat()}\n")
        fh.write(f"videos_total: {total}\n")
        fh.write(f"processed_files: {processed}\n")
        fh.write(f"windows_stored: {windows_stored}\n")
        fh.write(f"faiss_entries: {entries}\n")
        fh.write(f"elapsed_s: {elapsed}\n")
    log(f"Run log saved: {logf}")

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--force-reextract', action='store_true',
                   help='256dベクトルに遷移する際など、DBのウィンドウ特徴を全削除して再抽出する')
    p.add_argument('--force-reclip', action='store_true',
                   help='CLIPモデルバージョン変更時など、DBのCLIP特徴を全削除して再抽出する')
    args = p.parse_args()
    run(dry_run=args.dry_run, force_reextract=args.force_reextract, force_reclip=args.force_reclip)

if __name__ == '__main__':
    main()
