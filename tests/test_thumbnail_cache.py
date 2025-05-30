import os
import tempfile
from component.thumbnail.thumbnail_util import ThumbnailCache
from PIL import Image

def test_thumbnail_cache_set_and_get():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = ThumbnailCache(folder=tmpdir)
        key = ("test.png", (120, 90))
        img = Image.new("RGB", (120, 90), color="red")
        cache.set(key, img)
        assert cache.get(key) is not None
        assert isinstance(cache.get(key), Image.Image)

def test_thumbnail_cache_clear():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = ThumbnailCache(folder=tmpdir)
        key = ("test.png", (120, 90))
        img = Image.new("RGB", (120, 90), color="red")
        cache.set(key, img)
        cache.clear()
        assert cache.get(key) is None

def test_thumbnail_cache_cleanup_if_needed():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = ThumbnailCache(folder=tmpdir, max_items=3)
        for i in range(5):
            key = (f"test_{i}.png", (120, 90))
            img = Image.new("RGB", (120, 90), color="red")
            cache.set(key, img)
        # 最大件数を超えたら古いものが消える
        count = sum(1 for v in cache.cache.values() if v is not None)
        assert count <= 3
