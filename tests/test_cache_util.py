import os
import tempfile
import shutil
import pytest
from component.cache_util import save_cache, load_cache

def test_save_and_load_cache():
    data = b"testdata123"
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_file = os.path.join(tmpdir, "testcache.bin")
        save_cache(cache_file, data)
        loaded = load_cache(cache_file)
        assert loaded == data

def test_load_cache_not_exist():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_file = os.path.join(tmpdir, "notfound.bin")
        loaded = load_cache(cache_file)
        assert loaded is None
