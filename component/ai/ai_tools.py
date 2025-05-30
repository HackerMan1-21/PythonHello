# ai_tools.py
# 修復: AI超解像・動画修復・変換など

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
    exe = os.path.abspath("realesrgan-ncnn-vulkan.exe")
    if not os.path.exists(exe):
        raise RuntimeError("realesrgan-ncnn-vulkan.exeが見つかりません")
    ext = os.path.splitext(input_path)[1].lower()
    if ext in ['.jpg', '.jpeg', '.png', '.bmp', '.webp']:
        # 画像の場合
        cmd = f'"{exe}" -i "{input_path}" -o "{output_path}" -n realesrgan-x4plus -s 2'
        code = os.system(cmd)
        if code != 0:
            raise RuntimeError("Real-ESRGAN実行に失敗しました")
    elif ext in ['.mp4', '.avi', '.mov', '.mkv', '.wmv']:
        # 動画の場合: フレーム分解→Real-ESRGAN→GFPGAN→再合成
        import tempfile
        import shutil
        import subprocess
        import glob
        from pathlib import Path
        try:
            from PIL import Image
            import cv2
        except ImportError:
            raise RuntimeError("Pillow, OpenCV(cv2)が必要です")
        # GFPGAN, face_recognitionはオプション
        try:
            from gfpgan import GFPGANer
            import face_recognition
            gfpgan_available = True
        except ImportError:
            gfpgan_available = False
        temp_dir = tempfile.mkdtemp()
        frames_dir = os.path.join(temp_dir, "frames")
        esrgan_dir = os.path.join(temp_dir, "esrgan")
        gfpgan_dir = os.path.join(temp_dir, "gfpgan")
        os.makedirs(frames_dir, exist_ok=True)
        os.makedirs(esrgan_dir, exist_ok=True)
        os.makedirs(gfpgan_dir, exist_ok=True)
        # 1. ffmpegでフレーム分解
        frame_pattern = os.path.join(frames_dir, "frame_%06d.png")
        cmd1 = ["ffmpeg", "-y", "-i", input_path, "-qscale:v", "2", frame_pattern]
        print("[digital_repair] ffmpegでフレーム分解...")
        subprocess.run(cmd1, check=True)
        # 2. Real-ESRGANで全フレーム高画質化
        frame_files = sorted(glob.glob(os.path.join(frames_dir, "*.png")))
        print(f"[digital_repair] Real-ESRGANで{len(frame_files)}フレーム高画質化...")
        exe_path = os.path.abspath("realesrgan-ncnn-vulkan.exe")
        for f in frame_files:
            out_f = os.path.join(esrgan_dir, os.path.basename(f))
            cmd2 = [exe_path, "-i", f, "-o", out_f, "-n", "realesrgan-x4plus", "-s", "2"]
            result = subprocess.run(cmd2, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"[digital_repair] Real-ESRGAN失敗: {f}\n{result.stderr}")
                shutil.rmtree(temp_dir)
                raise RuntimeError("Real-ESRGAN処理に失敗")
        # 3. GFPGANで顔復元（オプション）
        if gfpgan_available:
            print("[digital_repair] GFPGANで顔復元...")
            gfpganer = GFPGANer(model_path=None, upscale=2, arch='clean', channel_multiplier=2, bg_upsampler=None)
            esrgan_files = sorted(glob.glob(os.path.join(esrgan_dir, "*.png")))
            for f in esrgan_files:
                img = cv2.imread(f)
                faces = face_recognition.face_locations(img)
                if faces:
                    _, _, restored_img = gfpganer.enhance(img, has_aligned=False, only_center_face=False, paste_back=True)
                    out_f = os.path.join(gfpgan_dir, os.path.basename(f))
                    cv2.imwrite(out_f, restored_img)
                else:
                    shutil.copy2(f, os.path.join(gfpgan_dir, os.path.basename(f)))
            final_dir = gfpgan_dir
        else:
            print("[digital_repair] GFPGAN未インストールのため顔復元スキップ")
            final_dir = esrgan_dir
        # 4. ffmpegでフレームを動画に再合成
        out_pattern = os.path.join(final_dir, "frame_%06d.png")
        cmd3 = ["ffmpeg", "-y", "-framerate", "30", "-i", out_pattern, "-c:v", "libx264", "-pix_fmt", "yuv420p", output_path]
        print("[digital_repair] ffmpegで動画再合成...")
        subprocess.run(cmd3, check=True)
        shutil.rmtree(temp_dir)
        print(f"[digital_repair] 完了: {output_path}")
    else:
        raise RuntimeError("対応していないファイル形式です")
