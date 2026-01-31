#!/usr/bin/env python3
"""
diagnose_phash.py
簡易診断スクリプト：画像/動画のpHashをサンプリングして、2ファイル間のハミング距離分布を表示します。
        使い方:
    python tools/diagnose_phash.py A.mp4 B.mp4 --frames 15

依存: Pillow, imagehash, opencv-python, numpy
"""
import argparse
import os
import sys
from PIL import Image
import imagehash
import cv2
import numpy as np
import math
from typing import Any, Dict

VIDEO_EXTS = ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.mpg', '.mpeg', '.3gp')


def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def sample_phashes(path: str, n_frames: int = 15, hash_size: int = 8):
    """画像は1つのpHash、動画は均等サンプリングで複数フレームのpHashを返す。
    戻り値: list of hex-string hashes (e.g. 'c5f6...')
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if not is_video(path):
        try:
            img = Image.open(path).convert('RGB')
            ph = imagehash.phash(img, hash_size=hash_size)
            return [str(ph)]
        except Exception as e:
            print(f"[ERROR] 画像サンプル失敗: {path} - {e}")
            return []

    # video
    try:
        cap = cv2.VideoCapture(path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        if total <= 0:
            # try to read sequential frames up to n_frames
            phashes = []
            i = 0
            while len(phashes) < n_frames:
                ret, frame = cap.read()
                if not ret:
                    break
                pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                phashes.append(str(imagehash.phash(pil, hash_size=hash_size)))
                i += 1
            cap.release()
            return phashes

        indices = np.linspace(0, max(0, total - 1), min(n_frames, total)).astype(int)
        phashes = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ret, frame = cap.read()
            if not ret:
                continue
            pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            phashes.append(str(imagehash.phash(pil, hash_size=hash_size)))
        cap.release()
        return phashes
    except Exception as e:
        print(f"[ERROR] 動画サンプリング失敗: {path} - {e}")
        return []


def hex_hamming(a: str, b: str) -> int:
    try:
        ai = int(a, 16)
        bi = int(b, 16)
        return (ai ^ bi).bit_count()
    except Exception:
        # fallback: compare binary strings
        a_bin = bin(int(a, 16))[2:]
        b_bin = bin(int(b, 16))[2:]
        # pad
        L = max(len(a_bin), len(b_bin))
        a_bin = a_bin.zfill(L)
        b_bin = b_bin.zfill(L)
        return sum(1 for x, y in zip(a_bin, b_bin) if x != y)


def summarize_distances(dists):
    if not dists:
        return None
    arr = np.array(dists, dtype=int)
    return {
        'min': int(arr.min()),
        'median': int(np.median(arr)),
        'mean': float(arr.mean()),
        'max': int(arr.max())
    }


def file_meta(path: str):
    st = os.stat(path)
    size = st.st_size
    info: Dict[str, Any] = {'size_bytes': size}
    if is_video(path):
        try:
            cap = cv2.VideoCapture(path)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS) or 0
            duration = (total / fps) if fps > 0 else None
            cap.release()
            # assign individually to avoid type-checker overload confusion
            info['frames'] = total
            info['fps'] = float(fps)
            info['duration_s'] = duration
        except Exception:
            pass
    return info


def compare_paths(a: str, b: str, n_frames: int = 15, hash_size: int = 8, verbose: bool = False):
    import os
    # 早期チェック: 指定ファイルが存在するか
    if not os.path.exists(a):
        print(f"[ERROR] 指定パスが存在しません: {a}")
        return 2
    if not os.path.exists(b):
        print(f"[ERROR] 指定パスが存在しません: {b}")
        return 2

    print(f"[INFO] 比較対象:\n  A: {a}\n  B: {b}\n  frames={n_frames}, hash_size={hash_size}")
    a_meta = file_meta(a)
    b_meta = file_meta(b)
    print(f"[META] A: size={a_meta.get('size_bytes')} bytes", end='')
    if 'duration_s' in a_meta:
        a_frames = a_meta.get('frames')
        a_fps = a_meta.get('fps')
        a_dur = a_meta.get('duration_s')
        fps_str = f"{a_fps:.2f}" if a_fps is not None else "N/A"
        dur_str = f"{a_dur:.2f}s" if a_dur is not None else "N/A"
        print(f", frames={a_frames}, fps={fps_str}, duration={dur_str}")
    else:
        print("")
    print(f"[META] B: size={b_meta.get('size_bytes')} bytes", end='')
    if 'duration_s' in b_meta:
        b_frames = b_meta.get('frames')
        b_fps = b_meta.get('fps')
        b_dur = b_meta.get('duration_s')
        fps_str = f"{b_fps:.2f}" if b_fps is not None else "N/A"
        dur_str = f"{b_dur:.2f}s" if b_dur is not None else "N/A"
        print(f", frames={b_frames}, fps={fps_str}, duration={dur_str}")
    else:
        print("")

    a_hashes = sample_phashes(a, n_frames=n_frames, hash_size=hash_size)
    b_hashes = sample_phashes(b, n_frames=n_frames, hash_size=hash_size)

    print(f"[HASH] A: {len(a_hashes)} hashes sampled")
    if verbose:
        for i, h in enumerate(a_hashes):
            print(f"  A[{i}]: {h}")
    print(f"[HASH] B: {len(b_hashes)} hashes sampled")
    if verbose:
        for i, h in enumerate(b_hashes):
            print(f"  B[{i}]: {h}")

    dists = []
    pairs = []
    for i, ha in enumerate(a_hashes):
        for j, hb in enumerate(b_hashes):
            d = hex_hamming(ha, hb)
            dists.append(d)
            pairs.append((i, j, d, ha, hb))

    if not dists:
        print("[WARN] ハッシュが得られませんでした（動画/画像の読み取り失敗など）")
        return 1

    stats = summarize_distances(dists)
    if stats is None:
        print("[DIST] statistics not available")
    else:
        print(f"[DIST] min={stats['min']}, median={stats['median']}, mean={stats['mean']:.2f}, max={stats['max']}")

    # show top matches (smallest distances)
    pairs_sorted = sorted(pairs, key=lambda x: x[2])
    print('\n[TOP MATCHES] 最小距離の上位10組:')
    for i, j, d, ha, hb in pairs_sorted[:10]:
        print(f" A[{i}] <-> B[{j}]  dist={d}")

    # histogram
    try:
        import collections
        cnt = collections.Counter(dists)
        print('\n[HIST] distance distribution:')
        for k in sorted(cnt.keys()):
            print(f"  {k}: {cnt[k]}")
    except Exception:
        pass

    return 0


def main():
    p = argparse.ArgumentParser(description='Diagnose pHash similarities between two files')
    p.add_argument('a', help='path to first file')
    p.add_argument('b', help='path to second file')
    p.add_argument('--frames', type=int, default=15, help='number of frames to sample for videos')
    p.add_argument('--hash-size', type=int, default=8, help='imagehash hash_size')
    p.add_argument('-v', '--verbose', action='store_true')
    args = p.parse_args()
    try:
        rc = compare_paths(args.a, args.b, n_frames=args.frames, hash_size=args.hash_size, verbose=args.verbose)
        # compare_paths returns int exit code (0 success, 2 for missing file)
        if isinstance(rc, int):
            return rc
        return 0
    except FileNotFoundError as e:
        print(f"[ERROR] ファイルが見つかりませんでした: {e}")
        return 2
    except Exception as e:
        print(f"[ERROR] 予期しないエラー: {e}")
        return 3


if __name__ == '__main__':
    sys.exit(main())
