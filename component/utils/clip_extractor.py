"""CLIP feature extractor -- GTX 1650 (4 GB VRAM) optimized.

Design:
  - Model   : ViT-B/32 via open-clip (laion2b_s34b_b79k weights)
              Fallback to openai/clip if open-clip is unavailable.
  - FP16    : enabled on CUDA to cut VRAM ~30 %
  - Batch   : 8 images  (safe for 4 GB VRAM)
  - Crops   : 2 per frame (full + center-75 %)
  - Frames  : 6 per video  => 6 x 2 = 12 images / video / inference call
  - Memory footprint at inference: ~1.4 GB VRAM (model 0.35 + buffer 0.15 + tensors)

Install:
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
  pip install open-clip-torch

API:
  extract_clip_features(path) -> np.ndarray(512,) | None
  extract_clip_features_batch(paths, ...) -> {path: ndarray}
  clip_cosine_similarity(v1, v2) -> float [0, 1]
  is_clip_available() -> bool
"""
from __future__ import annotations

import os
import gc
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# GTX 1650 (4 GB VRAM) tuned constants
# ---------------------------------------------------------------------------
CLIP_DIM: int = 512
FRAMES_FOR_CLIP: int = 6            # frames sampled per video
CLIP_BATCH_SIZE: int = 8            # images per GPU batch
_USE_FP16: bool = True              # halve VRAM usage on CUDA
_CROP_CENTER_ONLY: bool = False     # True => only full crop (fastest, less accurate)

# ---------------------------------------------------------------------------
# Lazy-loaded globals (initialised on first call to _ensure_loaded)
# ---------------------------------------------------------------------------
_model = None
_preprocess = None
_device: Optional[str] = None
_fp16_active: bool = False


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def is_clip_available() -> bool:
    """Return True when a CLIP backend (open-clip or clip) can be imported."""
    try:
        import open_clip  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import clip  # noqa: F401
        return True
    except ImportError:
        pass
    return False


def _ensure_loaded() -> bool:
    """Lazy-load the CLIP model. Returns False if unavailable."""
    global _model, _preprocess, _device, _fp16_active
    if _model is not None:
        return True

    import torch

    device = 'cuda' if torch.cuda.is_available() else 'cpu'

    try:
        import open_clip
        model, _, preprocess = open_clip.create_model_and_transforms(
            'ViT-B-32', pretrained='laion2b_s34b_b79k'
        )
    except Exception:
        try:
            import clip
            model, preprocess = clip.load('ViT-B/32', device='cpu')
        except Exception:
            return False

    model = model.to(device).eval()
    use_fp16 = _USE_FP16 and device == 'cuda'
    if use_fp16:
        model = model.half()

    _model = model
    _preprocess = preprocess
    _device = device
    _fp16_active = use_fp16
    return True


# ---------------------------------------------------------------------------
# Frame utilities
# ---------------------------------------------------------------------------

def _auto_crop_black_bars(frame_bgr: np.ndarray, threshold: int = 15) -> np.ndarray:
    """Remove black bars (rows/cols whose max brightness < threshold)."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    rows = np.where(gray.max(axis=1) > threshold)[0]
    cols = np.where(gray.max(axis=0) > threshold)[0]
    if rows.size == 0 or cols.size == 0:
        return frame_bgr
    return frame_bgr[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def _make_crops(frame_bgr: np.ndarray) -> List[np.ndarray]:
    """Return [full_frame, center_75pct].  Skips center if frame is too small."""
    crops: List[np.ndarray] = [frame_bgr]
    if not _CROP_CENTER_ONLY:
        h, w = frame_bgr.shape[:2]
        if h > 64 and w > 64:
            mh, mw = h // 8, w // 8
            center = frame_bgr[mh: h - mh, mw: w - mw]
            if center.size > 0:
                crops.append(center)
    return crops


def _sample_frames(path: str, num_frames: int) -> List[np.ndarray]:
    """Sample `num_frames` BGR frames uniformly from 5 %..95 % of the video."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return []
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        if total <= 0 or fps <= 0:
            return []

        start_f = max(0, int(total * 0.05))
        end_f = min(total - 1, int(total * 0.95))
        span = max(end_f - start_f, 1)
        step = span / max(num_frames - 1, 1)
        indices = [int(start_f + step * i) for i in range(num_frames)]

        frames: List[np.ndarray] = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if ret and frame is not None:
                frames.append(frame)
        return frames
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def extract_clip_features(
    path: str,
    num_frames: int = FRAMES_FOR_CLIP,
) -> Optional[np.ndarray]:
    """Extract a single CLIP 512-d feature vector for one video.

    Steps:
      1. Sample `num_frames` frames (black-bar-trimmed).
      2. Generate 2 crops per frame (full + center-75 %).
      3. Batch-infer CLIP in groups of CLIP_BATCH_SIZE.
      4. Average all crop features and L2-normalise.

    Returns float32 ndarray of shape (512,), or None on failure.
    """
    import torch

    if not _ensure_loaded():
        return None

    raw_frames = _sample_frames(path, num_frames)
    if not raw_frames:
        return None

    # Build PIL image list (crops)
    pil_images: List[Image.Image] = []
    for frame in raw_frames:
        frame = _auto_crop_black_bars(frame)
        for crop in _make_crops(frame):
            pil_images.append(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)))

    if not pil_images:
        return None

    assert _preprocess is not None
    assert _model is not None
    assert _device is not None

    all_features: List[torch.Tensor] = []
    try:
        for batch_start in range(0, len(pil_images), CLIP_BATCH_SIZE):
            batch_pil = pil_images[batch_start: batch_start + CLIP_BATCH_SIZE]
            tensors = torch.stack([_preprocess(img) for img in batch_pil])
            tensors = tensors.to(_device)
            if _fp16_active:
                tensors = tensors.half()

            with torch.no_grad():
                feats = _model.encode_image(tensors).float()
                feats = feats / feats.norm(dim=-1, keepdim=True)
            all_features.append(feats.cpu())

        combined = torch.cat(all_features, dim=0)  # (N_crops, 512)
        avg_feat = combined.mean(dim=0).numpy().astype(np.float32)  # (512,)
        norm = float(np.linalg.norm(avg_feat))
        if norm > 1e-7:
            avg_feat /= norm
        return avg_feat

    except Exception:
        return None
    finally:
        # Release GPU tensors explicitly to keep 4 GB VRAM free
        del all_features
        if _device == 'cuda':
            torch.cuda.empty_cache()


def extract_clip_features_batch(
    paths: List[str],
    num_frames: int = FRAMES_FOR_CLIP,
    progress_callback=None,
) -> Dict[str, np.ndarray]:
    """Batch version: returns {path: feature_vector} for successful extractions."""
    results: Dict[str, np.ndarray] = {}
    for i, path in enumerate(paths):
        vec = extract_clip_features(path, num_frames)
        if vec is not None:
            results[path] = vec
        if progress_callback and (i % 10 == 0 or i == len(paths) - 1):
            progress_callback(i + 1, len(paths))
        # Periodic GC to avoid memory fragmentation
        if i % 100 == 99:
            gc.collect()
    return results


def extract_clip_frame_features(
    path: str,
    num_frames: int = 30,
) -> Optional[np.ndarray]:
    """Extract per-frame CLIP vectors — NOT averaged.

    Unlike extract_clip_features() which returns a single averaged 512-d vector,
    this function returns an (N, 512) array with one row per frame.

    Used for temporal DTW comparison: the 1-min clip's frame sequence is
    matched against the 120-min video's frame sequence via subsequence DTW.

    Parameters
    ----------
    num_frames : int
        Number of frames to sample. Default 30 gives dense coverage of a
        1-min clip (one frame per 2 sec) while being reasonable for long videos.

    Returns
    -------
    np.ndarray of shape (N, 512) | None
    """
    import torch

    if not _ensure_loaded():
        return None

    raw_frames = _sample_frames(path, num_frames)
    if not raw_frames:
        return None

    assert _preprocess is not None
    assert _model is not None
    assert _device is not None

    frame_features: List[np.ndarray] = []
    try:
        for frame in raw_frames:
            frame = _auto_crop_black_bars(frame)
            pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            tensor = _preprocess(pil_img).unsqueeze(0).to(_device)
            if _fp16_active:
                tensor = tensor.half()
            with torch.no_grad():
                feat = _model.encode_image(tensor).float()
                feat = feat / feat.norm(dim=-1, keepdim=True)
            frame_features.append(feat.cpu().numpy().astype(np.float32)[0])  # (512,)
    except Exception:
        return None
    finally:
        if _device == 'cuda':
            torch.cuda.empty_cache()

    if not frame_features:
        return None
    return np.vstack(frame_features)  # (N, 512)


# ---------------------------------------------------------------------------
# Similarity helpers
# ---------------------------------------------------------------------------

def clip_cosine_similarity(
    vec1: Optional[np.ndarray],
    vec2: Optional[np.ndarray],
) -> float:
    """Cosine similarity mapped to [0, 1].

    Both vectors are assumed L2-normalised (unit sphere).
    dot product in [-1, 1]  =>  (dot + 1) / 2  =>  [0, 1]
    """
    if vec1 is None or vec2 is None:
        return 0.0
    try:
        dot = float(np.dot(vec1.ravel(), vec2.ravel()))
        return max(0.0, min(1.0, (dot + 1.0) / 2.0))
    except Exception:
        return 0.0


def unload_model() -> None:
    """Release the CLIP model from GPU memory (call between large batch jobs)."""
    global _model, _preprocess, _device, _fp16_active
    try:
        import torch
        if _device == 'cuda':
            torch.cuda.empty_cache()
    except Exception:
        pass
    _model = None
    _preprocess = None
    _device = None
    _fp16_active = False
    gc.collect()
