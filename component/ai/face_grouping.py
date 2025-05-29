# face_grouping.py
# 顔グループ化: 顔認識・顔特徴量抽出・グループ分け

import numpy as np
from PIL import Image
from component.duplicate_finder import get_features_with_cache
from component.utils.file_util import normalize_path

def get_face_encoding(filepath):
    filepath = normalize_path(filepath)
    def calc_func(path):
        try:
            import face_recognition
        except ImportError:
            return np.zeros(128)
        img = Image.open(path).convert("RGB")
        arr = np.array(img)
        faces = face_recognition.face_encodings(arr)
        return faces[0] if faces else np.zeros(128)
    return get_features_with_cache(filepath, calc_func)

def get_video_face_encoding(filepath, sample_frames=7):
    filepath = normalize_path(filepath)
    def calc_func(path):
        try:
            import face_recognition
        except ImportError:
            return None
        import cv2
        cap = cv2.VideoCapture(path)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if frame_count == 0:
            cap.release()
            return None
        indices = np.linspace(0, frame_count-1, sample_frames, dtype=int)
        encodings = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            faces = face_recognition.face_encodings(rgb)
            if faces:
                encodings.append(faces[0])
        cap.release()
        if encodings:
            return np.mean(encodings, axis=0)
        else:
            return None
    return get_features_with_cache(filepath, calc_func)

def group_by_face(encodings, paths, threshold=0.6):
    from sklearn.metrics.pairwise import cosine_distances
    groups = []
    used = set()
    for i, (f1, e1) in enumerate(zip(paths, encodings)):
        if f1 in used or e1 is None:
            continue
        group = [f1]
        for j, (f2, e2) in enumerate(zip(paths, encodings)):
            if i != j and f2 not in used and e2 is not None:
                dist = cosine_distances([e1], [e2])[0][0]
                if dist < threshold:
                    group.append(f2)
                    used.add(f2)
        used.add(f1)
        if len(group) > 1:
            groups.append(group)
    return groups
