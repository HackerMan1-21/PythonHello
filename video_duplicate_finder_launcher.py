import sys
import os
import subprocess

# video_duplicate_finder.py の絶対パスを取得
script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'video_duplicate_finder.py')

# Python実行コマンド
cmd = [sys.executable, script_path]

# サブプロセスで起動（ウィンドウを閉じてもアプリが残るように）
# subprocess.Popen(cmd)

# --- PyQt5アプリの直接起動のみ許可。サブプロセス起動部分を削除 ---
if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    from video_duplicate_finder import DuplicateFinderGUI
    app = QApplication(sys.argv)
    win = DuplicateFinderGUI()
    win.show()
    sys.exit(app.exec_())
# --- ここまで ---
