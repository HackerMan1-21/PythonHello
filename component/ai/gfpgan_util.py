# gfpgan_util.py
# GFPGANによる顔復元ユーティリティ
import os
import cv2
import shutil

def gfpgan_restore_faces(input_dir, output_dir):
    try:
        from gfpgan import GFPGANer
        import face_recognition
    except ImportError:
        raise RuntimeError("GFPGAN, face_recognitionが必要です")
    os.makedirs(output_dir, exist_ok=True)
    gfpganer = GFPGANer(model_path=None, upscale=2, arch='clean', channel_multiplier=2, bg_upsampler=None)
    for fname in sorted(os.listdir(input_dir)):
        if not fname.lower().endswith('.png'):
            continue
        f = os.path.join(input_dir, fname)
        img = cv2.imread(f)
        faces = face_recognition.face_locations(img)
        if faces:
            _, _, restored_img = gfpganer.enhance(img, has_aligned=False, only_center_face=False, paste_back=True)
            out_f = os.path.join(output_dir, fname)
            cv2.imwrite(out_f, restored_img)
        else:
            shutil.copy2(f, os.path.join(output_dir, fname))
