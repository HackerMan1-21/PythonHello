import os
import tempfile
from PIL import Image
import pytest
from component.thumbnail.thumbnail_util import get_image_thumbnail, get_video_thumbnail
import cv2
import numpy as np

def test_get_image_thumbnail():
    with tempfile.TemporaryDirectory() as tmpdir:
        img_path = os.path.join(tmpdir, "img.png")
        Image.new("RGB", (100, 100)).save(img_path)
        thumb = get_image_thumbnail(img_path, (32, 32))
        assert thumb is not None
        assert thumb.size == (32, 32) or thumb.size[0] <= 32 and thumb.size[1] <= 32

def test_get_video_thumbnail():
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "test.avi")
        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        out = cv2.VideoWriter(video_path, fourcc, 1.0, (32, 32))
        for _ in range(2):
            out.write(np.zeros((32, 32, 3), np.uint8))
        out.release()
        thumb = get_video_thumbnail(video_path, (16, 16))
        assert thumb is not None
        assert thumb.size == (16, 16) or thumb.size[0] <= 16 and thumb.size[1] <= 16
