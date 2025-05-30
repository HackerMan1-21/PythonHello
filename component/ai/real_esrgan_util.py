# real_esrgan_util.py
# Real-ESRGANによる画像・フレーム高画質化ユーティリティ
import os
import subprocess

def real_esrgan_upscale(input_path, output_path, model_name="realesrgan-x4plus", scale="2"):
    exe = os.path.abspath("realesrgan-ncnn-vulkan.exe")
    if not os.path.exists(exe):
        raise RuntimeError("realesrgan-ncnn-vulkan.exeが見つかりません")
    cmd = [exe, "-i", input_path, "-o", output_path, "-n", model_name, "-s", scale]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Real-ESRGAN失敗: {input_path}\n{result.stderr}")
    return True
