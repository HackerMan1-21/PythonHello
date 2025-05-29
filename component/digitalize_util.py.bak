# digitalize_util.py
# デジタル化・高画質化・AI修復などの動画処理ユーティリティ
import os
import subprocess
import cv2
import shutil
import tempfile
import json
from PyQt5.QtWidgets import QMessageBox, QFileDialog, QProgressDialog

def run_mp4_digital_repair(file_path=None, parent=None):
    """
    Real-ESRGAN+GFPGANによる高画質化＋顔復元
    file_path: 入力動画パス
    parent: 親ウィジェット（QWidget）
    """
    import shutil, glob
    try:
        from gfpgan import GFPGANer
        import face_recognition
    except ImportError:
        if parent:
            QMessageBox.warning(parent, "エラー", "GFPGAN, face_recognitionがインストールされていません")
        return
    if not file_path and parent:
        file_path, _ = QFileDialog.getOpenFileName(parent, "AI超解像＋顔復元したい動画ファイルを選択", "", "動画ファイル (*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.mpg *.mpeg *.3gp)")
        if not file_path:
            return
    save_path, _ = QFileDialog.getSaveFileName(parent, "高画質化後の保存先を指定", os.path.splitext(file_path)[0] + "_aiup_gfpgan" + os.path.splitext(file_path)[1], "動画ファイル (*.*)")
    if not save_path:
        return
    frames_dir = "frames_raw"
    esrgan_out_dir = "frames_esrgan"
    gfpgan_out_dir = "frames_final_output"
    audio_only_path = "input_audio_temp.aac"
    realesrgan_exe = os.path.abspath("realesrgan-ncnn-vulkan.exe")
    gfpgan_model_path = os.path.abspath("GFPGANv1.4.pth")
    temp_dirs = [frames_dir, esrgan_out_dir, gfpgan_out_dir]
    try:
        for d in temp_dirs:
            os.makedirs(d, exist_ok=True)
        # フレームレート取得
        def get_video_framerate(video_path):
            cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "json", video_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            info = json.loads(result.stdout)
            rate_str = info['streams'][0]['avg_frame_rate']
            num, den = map(int, rate_str.split('/'))
            return float(num) / den if den != 0 else 0
        video_framerate = get_video_framerate(file_path)
        # 音声抽出
        subprocess.run(["ffmpeg", "-y", "-i", file_path, "-vn", "-c:a", "copy", audio_only_path], check=True)
        # フレーム分解
        subprocess.run(["ffmpeg", "-y", "-i", file_path, "-qscale:v", "2", os.path.join(frames_dir, "frame_%06d.png")], check=True)
        # Real-ESRGAN
        subprocess.run([realesrgan_exe, "-i", frames_dir, "-o", esrgan_out_dir], check=True)
        # GFPGAN
        gfpgan_enhancer = GFPGANer(model_path=gfpgan_model_path, upscale=1, arch='clean', channel_multiplier=2, bg_upsampler=None)
        esrgan_frames = sorted([f for f in os.listdir(esrgan_out_dir) if f.endswith('.png')])
        for i, fname in enumerate(esrgan_frames):
            img_path = os.path.join(esrgan_out_dir, fname)
            img = cv2.imread(img_path)
            if img is None:
                continue
            rgb_frame = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(rgb_frame, model="hog")
            final_frame = img.copy()
            if face_locations:
                for (top, right, bottom, left) in face_locations:
                    padding = 50
                    p_top = max(0, top - padding)
                    p_bottom = min(img.shape[0], bottom + padding)
                    p_left = max(0, left - padding)
                    p_right = min(img.shape[1], right + padding)
                    cropped_face = img[p_top:p_bottom, p_left:p_right]
                    _, restored_face_image = gfpgan_enhancer.enhance(
                        cropped_face, has_aligned=False, only_center_face=False, paste_back=True)
                    final_frame[p_top:p_bottom, p_left:p_right] = restored_face_image
            cv2.imwrite(os.path.join(gfpgan_out_dir, fname), final_frame)
        # フレーム→動画再結合
        subprocess.run([
            "ffmpeg", "-y", "-framerate", str(video_framerate),
            "-i", os.path.join(gfpgan_out_dir, "frame_%06d.png"),
            "-i", audio_only_path,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-map", "0:v:0", "-map", "1:a:0?", "-shortest", save_path
        ], check=True)
        if parent:
            QMessageBox.information(parent, "完了", f"修復が完了しました:\n{save_path}")
    except Exception as e:
        if parent:
            QMessageBox.warning(parent, "エラー", f"修復処理中にエラー:\n{e}")
    finally:
        for d in temp_dirs:
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)
        if os.path.exists(audio_only_path):
            try:
                os.remove(audio_only_path)
            except Exception:
                pass
