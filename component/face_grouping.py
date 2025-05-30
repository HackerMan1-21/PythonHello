import os
import shutil
try:
    import face_recognition
except ImportError:
    face_recognition = None

def get_face_groups(file_list):
    if face_recognition is None:
        raise ImportError("face_recognitionライブラリが必要です")
    groups = []
    encodings = []
    for f in file_list:
        try:
            img = face_recognition.load_image_file(f)
            faces = face_recognition.face_encodings(img)
            if faces:
                encodings.append((f, faces[0]))
        except Exception:
            continue
    used = set()
    for i, (f1, enc1) in enumerate(encodings):
        if f1 in used:
            continue
        group = [f1]
        used.add(f1)
        for j, (f2, enc2) in enumerate(encodings):
            if i != j and f2 not in used:
                dist = face_recognition.face_distance([enc1], enc2)[0]
                if dist < 0.5:
                    group.append(f2)
                    used.add(f2)
        groups.append(group)
    return groups

def group_by_face_and_move(file_list, out_dir):
    groups = get_face_groups(file_list)
    for idx, group in enumerate(groups):
        group_dir = os.path.join(out_dir, f"face_group_{idx+1}")
        os.makedirs(group_dir, exist_ok=True)
        for f in group:
            try:
                shutil.move(f, group_dir)
            except Exception:
                continue
