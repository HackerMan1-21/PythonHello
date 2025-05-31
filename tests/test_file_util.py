import os
import tempfile
import shutil
import pytest
from component.utils.file_util import move_to_trash, normalize_path, get_folder_state

def test_normalize_path():
    assert os.path.isabs(normalize_path("."))

def test_move_to_trash_and_get_folder_state(tmp_path):
    # ファイル作成
    f = tmp_path / "testfile.txt"
    f.write_text("abc")
    # ゴミ箱移動（send2trash未導入でも例外にならない）
    move_to_trash(str(f))
    # フォルダ状態取得
    state = get_folder_state(str(tmp_path))
    assert all(isinstance(x, tuple) and len(x) == 3 for x in state)
