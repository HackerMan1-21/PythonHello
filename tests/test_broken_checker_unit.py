import os
import tempfile
import shutil
import pytest
from component.broken_checker import is_broken_image, is_broken_video
from PIL import Image
import cv2
import numpy as np

def test_is_broken_image():
    with tempfile.TemporaryDirectory() as tmpdir:
        img_path = os.path.join(tmpdir, "ok.png")
        Image.new("RGB", (10, 10)).save(img_path)
        assert is_broken_image(img_path) is False
        broken_path = os.path.join(tmpdir, "broken.png")
        with open(broken_path, "wb") as f:
            f.write(b"notanimage")
        assert is_broken_image(broken_path) is True

def test_is_broken_video():
    with tempfile.TemporaryDirectory() as tmpdir:
        # 正常な動画
        video_path = os.path.join(tmpdir, "ok.avi")
        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        out = cv2.VideoWriter(video_path, fourcc, 1.0, (10, 10))
        for _ in range(3):
            out.write(np.zeros((10, 10, 3), np.uint8))
        out.release()
        assert is_broken_video(video_path) is False
        # 壊れ動画
        broken_path = os.path.join(tmpdir, "broken.avi")
        with open(broken_path, "wb") as f:
            f.write(b"notavideo")
        assert is_broken_video(broken_path) is True
