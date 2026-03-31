import json
import shutil
import subprocess
import math
from typing import Dict, Optional, List, Tuple


def _run_ffprobe(path: str) -> Optional[Dict]:
    if shutil.which("ffprobe") is None:
        return None
    cmd = [
        "ffprobe", "-v", "error", "-show_entries",
        "format:stream:stream_tags", "-of", "json", path
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        return json.loads(out)
    except Exception:
        return None


def get_media_info(path: str) -> Dict:
    """
    return keys: width, height, duration (sec float), bitrate (bps int or None), codec (str), fps (float or None)
    """
    info = {"width": None, "height": None, "duration": None, "bitrate": None, "codec": None, "fps": None}
    probe = _run_ffprobe(path)
    if probe:
        streams = probe.get("streams", [])
        vstream = None
        for s in streams:
            if s.get("codec_type") == "video":
                vstream = s
                break
        fmt = probe.get("format", {})
        if vstream:
            info["width"] = int(vstream.get("width")) if vstream.get("width") else None
            info["height"] = int(vstream.get("height")) if vstream.get("height") else None
            r = vstream.get("r_frame_rate") or vstream.get("avg_frame_rate")
            try:
                if r and '/' in r:
                    num, den = r.split('/')
                    info["fps"] = float(num) / float(den) if float(den) != 0 else None
                else:
                    info["fps"] = float(r) if r else None
            except Exception:
                info["fps"] = None
            info["codec"] = vstream.get("codec_name") or info["codec"]
            b = vstream.get("bit_rate") or fmt.get("bit_rate")
            try:
                info["bitrate"] = int(b) if b is not None else None
            except Exception:
                info["bitrate"] = None
        d = fmt.get("duration")
        try:
            info["duration"] = float(d) if d else None
        except Exception:
            info["duration"] = None
        return info

    # fallback: OpenCV and filesize-based estimate
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or None
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            duration = (frames / fps) if fps and frames else None
            info.update({"width": w or None, "height": h or None, "fps": fps, "duration": duration})
        cap.release()
    except Exception:
        pass

    try:
        import os
        st = os.path.getsize(path)
        if info.get("duration"):
            info["bitrate"] = int((st * 8) / info["duration"])
    except Exception:
        pass
    return info


# codec preference ranking (higher is better)
_CODEC_PREF = {
    "av1": 4,
    "hevc": 3, "h265": 3, "h.265": 3,
    "vp9": 2,
    "h264": 1,
}


def codec_score(codec: Optional[str]) -> int:
    if not codec:
        return 0
    c = codec.lower()
    for k, v in _CODEC_PREF.items():
        if k in c:
            return v
    return 0


def quality_score(info: Dict) -> float:
    """
    Combine resolution, bitrate, codec, fps into single score.
    Weights are tunable.
    """
    w_res = 0.45
    w_br = 0.25
    w_codec = 0.15
    w_fps = 0.15

    res_score = 0.0
    if info.get("width") and info.get("height"):
        area = info["width"] * info["height"]
        res_score = math.log1p(area) / math.log1p(1920 * 1080)

    br_score = 0.0
    if info.get("bitrate"):
        br_score = math.log1p(info["bitrate"]) / math.log1p(8_000_000)

    codec_s = codec_score(info.get("codec"))
    codec_score_norm = codec_s / max(_CODEC_PREF.values()) if _CODEC_PREF else 0

    fps_score = 0.0
    if info.get("fps"):
        fps_score = min(info["fps"], 60.0) / 60.0

    total = w_res * res_score + w_br * br_score + w_codec * codec_score_norm + w_fps * fps_score
    return total


def sort_files_by_quality(paths: List[str]) -> List[Tuple[str, float]]:
    scored = []
    for p in paths:
        try:
            info = get_media_info(p)
            s = quality_score(info)
        except Exception:
            s = 0.0
        scored.append((p, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
