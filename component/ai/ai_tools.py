"""
ai_tools.py
AI超解像・動画修復・変換などの高レベルAI処理ラッパー。

主な機能:
- 画像/動画のAI超解像（Real-ESRGAN）
- 顔復元（GFPGAN）
- 動画のフレーム分解・再合成
- サムネイルキャッシュ連携

依存:
- component.ai.real_esrgan_util
- component.ai.gfpgan_util
- component.ai.frame_util
- component.thumbnail.thumbnail_util
"""

# 修復: AI超解像・動画修復・変換など

from component.ai.real_esrgan_util import real_esrgan_upscale
from component.ai.gfpgan_util import gfpgan_restore_faces
from component.ai.frame_util import extract_frames, combine_frames_to_video
from component.thumbnail.thumbnail_util import ThumbnailCache

def ai_upscale_image(image_path, output_path, model_path=None):
    """
    画像をAI超解像（例: Real-ESRGAN）で高画質化するサンプル関数
    """
    import subprocess
    cmd = [
        "realesrgan-ncnn-vulkan.exe",
        "-i", image_path,
        "-o", output_path
    ]
    if model_path:
        cmd += ["-m", model_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0

def digital_repair(input_path, output_path):
    """
    Real-ESRGANやGFPGAN等を使ったAI超解像・顔復元のラッパー関数。
    input_path: 入力画像/動画パス
    output_path: 出力画像/動画パス
    """
    import os
    ext = os.path.splitext(input_path)[1].lower()
    if ext in ['.jpg', '.jpeg', '.png', '.bmp', '.webp']:
        # 画像の場合
        cmd = f'"realesrgan-ncnn-vulkan.exe" -i "{input_path}" -o "{output_path}" -n realesrgan-x4plus -s 2'
        code = os.system(cmd)
        if code != 0:
            raise RuntimeError("Real-ESRGAN実行に失敗しました")
    elif ext in ['.mp4', '.avi', '.mov', '.mkv', '.wmv']:
        import tempfile
        import shutil
        import glob
        temp_dir = tempfile.mkdtemp()
        frames_dir = os.path.join(temp_dir, "frames")
        esrgan_dir = os.path.join(temp_dir, "esrgan")
        gfpgan_dir = os.path.join(temp_dir, "gfpgan")
        # 1. フレーム分解
        extract_frames(input_path, frames_dir)
        # 2. Real-ESRGANで全フレーム高画質化（キャッシュ利用）
        os.makedirs(esrgan_dir, exist_ok=True)
        frame_files = sorted(glob.glob(os.path.join(frames_dir, "*.png")))
        thumb_cache = ThumbnailCache(folder=frames_dir)  # サムネイルキャッシュを利用
        for f in frame_files:
            out_f = os.path.join(esrgan_dir, os.path.basename(f))
            real_esrgan_upscale(f, out_f)
            # サムネイルキャッシュに登録（型変換統一）
            thumb_cache.set((f, (120, 90)), None)  # 実際のサムネイル生成は別途
        # 3. GFPGANで顔復元（オプション）
        try:
            gfpgan_restore_faces(esrgan_dir, gfpgan_dir)
            final_dir = gfpgan_dir
        except Exception as e:
            print(f"[digital_repair] GFPGAN未使用/失敗: {e}")
            final_dir = esrgan_dir
        # 4. フレームを動画に再合成
        combine_frames_to_video(final_dir, output_path)
        shutil.rmtree(temp_dir)
        print(f"[digital_repair] 完了: {output_path}")
    else:
        raise RuntimeError("対応していないファイル形式です")
