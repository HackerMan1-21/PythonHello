# ffmpeg_util.py
# ffmpegコマンド生成・実行ユーティリティ

import subprocess

def run_ffmpeg_cmd(cmd, msg=None, title=None, parent=None):
    """
    ffmpegコマンドを実行し、必要に応じて進捗ダイアログを表示
    cmd: コマンドリスト
    msg, title: 進捗ダイアログ用
    parent: 親ウィジェット
    """
    dlg_prog = None
    if parent and msg and title:
        from PyQt5.QtWidgets import QProgressDialog
        dlg_prog = QProgressDialog(msg, None, 0, 0, parent)
        dlg_prog.setWindowTitle(title)
        dlg_prog.setWindowModality(2)  # Qt.WindowModal
        dlg_prog.show()
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if dlg_prog:
        dlg_prog.close()
    return result

# ...ここにffmpegコマンド生成・実行関連の関数・クラスを実装...
