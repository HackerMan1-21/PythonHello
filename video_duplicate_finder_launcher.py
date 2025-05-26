import sys
import os
import subprocess

# video_duplicate_finder.py の絶対パスを取得
script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'video_duplicate_finder.py')

# Python実行コマンド
cmd = [sys.executable, script_path]

# サブプロセスで起動（ウィンドウを閉じてもアプリが残るように）
subprocess.Popen(cmd)
