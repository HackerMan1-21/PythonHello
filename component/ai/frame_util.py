# frame_util.py
# ffmpegによる動画→フレーム分解・フレーム→動画合成ユーティリティ
import os
import subprocess

def extract_frames(video_path, frames_dir):
    os.makedirs(frames_dir, exist_ok=True)
    frame_pattern = os.path.join(frames_dir, "frame_%06d.png")
    cmd = ["ffmpeg", "-y", "-i", video_path, "-qscale:v", "2", frame_pattern]
    subprocess.run(cmd, check=True)
    return frame_pattern

def combine_frames_to_video(frames_dir, output_path, framerate=30):
    frame_pattern = os.path.join(frames_dir, "frame_%06d.png")
    cmd = ["ffmpeg", "-y", "-framerate", str(framerate), "-i", frame_pattern, "-c:v", "libx264", "-pix_fmt", "yuv420p", output_path]
    subprocess.run(cmd, check=True)
    return output_path
