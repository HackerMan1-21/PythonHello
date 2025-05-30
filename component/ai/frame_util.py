"""
frame_util.py
ffmpegによる動画→フレーム分解・フレーム→動画合成ユーティリティ。

主な機能:
- 動画ファイルからフレーム画像抽出
- フレーム画像から動画再合成

依存:
- ffmpeg, os, subprocess
"""

import os
import subprocess

def extract_frames(video_path, frames_dir):
    try:
        os.makedirs(frames_dir, exist_ok=True)
        frame_pattern = os.path.join(frames_dir, "frame_%06d.png")
        cmd = ["ffmpeg", "-y", "-i", video_path, "-qscale:v", "2", frame_pattern]
        subprocess.run(cmd, check=True)
        return frame_pattern
    except Exception as e:
        print(f"[extract_frames] フレーム分解失敗: {video_path}: {e}")
        raise

def combine_frames_to_video(frames_dir, output_path, framerate=30):
    try:
        frame_pattern = os.path.join(frames_dir, "frame_%06d.png")
        cmd = ["ffmpeg", "-y", "-framerate", str(framerate), "-i", frame_pattern, "-c:v", "libx264", "-pix_fmt", "yuv420p", output_path]
        subprocess.run(cmd, check=True)
        return output_path
    except Exception as e:
        print(f"[combine_frames_to_video] 動画合成失敗: {frames_dir} → {output_path}: {e}")
        raise
