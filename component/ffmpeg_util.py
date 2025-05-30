"""
ffmpeg_util.py
PyQt5ダイアログ連携の動画修復・変換ユーティリティ。

主な機能:
- MP4修復・変換ダイアログ表示
- ffmpegによる動画修復・変換処理
- 進捗UI・エラー通知

依存:
- PyQt5, ffmpeg, subprocess, os
"""

import os
import subprocess
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QFileDialog, QMessageBox, QProgressDialog
from PyQt5.QtCore import Qt

def show_mp4_tool_dialog(parent=None):
    dlg = QDialog(parent)
    dlg.setWindowTitle("MP4修復/変換ツール")
    vbox = QVBoxLayout()
    label = QLabel("MP4ファイルの修復または変換を選択してください。\n(処理後は保存先を指定できます)")
    label.setStyleSheet("font-size:15px;color:#ffb300;padding:8px;")
    vbox.addWidget(label)
    btn_repair = QPushButton("MP4修復")
    btn_repair.setStyleSheet("font-size:16px;color:#00ffe7;border:2px solid #00ffe7;border-radius:8px;padding:8px;")
    btn_convert = QPushButton("MP4変換")
    btn_convert.setStyleSheet("font-size:16px;color:#00ff99;border:2px solid #00ff99;border-radius:8px;padding:8px;")
    vbox.addWidget(btn_repair)
    vbox.addWidget(btn_convert)
    btn_repair.clicked.connect(lambda: (dlg.accept(), repair_mp4(parent)))
    btn_convert.clicked.connect(lambda: (dlg.accept(), convert_mp4(parent)))
    dlg.setLayout(vbox)
    dlg.exec_()

def repair_mp4(parent=None):
    file_path, _ = QFileDialog.getOpenFileName(parent, "修復したい動画ファイルを選択", "", "動画ファイル (*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.mpg *.mpeg *.3gp)")
    if not file_path:
        return
    save_path, _ = QFileDialog.getSaveFileName(parent, "修復後の保存先を指定", os.path.splitext(file_path)[0] + "_repaired" + os.path.splitext(file_path)[1], "動画ファイル (*.*)")
    if not save_path:
        return
    try:
        cmd = ["ffmpeg", "-y", "-i", file_path, "-c", "copy", save_path]
        dlg = QProgressDialog("動画修復中...", None, 0, 0, parent)
        dlg.setWindowTitle("動画修復")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.show()
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        dlg.close()
        if result.returncode == 0:
            QMessageBox.information(parent, "完了", f"修復が完了しました:\n{save_path}")
        else:
            QMessageBox.warning(parent, "エラー", f"修復に失敗しました:\n{result.stderr}")
    except Exception as e:
        QMessageBox.warning(parent, "エラー", f"修復処理中にエラー:\n{e}")

def convert_mp4(parent=None):
    file_path, _ = QFileDialog.getOpenFileName(parent, "変換したい動画ファイルを選択", "", "動画ファイル (*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.mpg *.mpeg *.3gp)")
    if not file_path:
        return
    orig_ext = os.path.splitext(file_path)[1].lower()
    ext_map = {
        ".mp4": "MP4ファイル (*.mp4);;MKVファイル (*.mkv);;MOVファイル (*.mov);;AVIファイル (*.avi)",
        ".mkv": "MKVファイル (*.mkv);;MP4ファイル (*.mp4);;MOVファイル (*.mov);;AVIファイル (*.avi)",
        ".mov": "MOVファイル (*.mov);;MP4ファイル (*.mp4);;MKVファイル (*.mkv);;AVIファイル (*.avi)",
        ".avi": "AVIファイル (*.avi);;MP4ファイル (*.mp4);;MKVファイル (*.mkv);;MOVファイル (*.mov)",
    }
    filter_str = ext_map.get(orig_ext, 'MP4ファイル (*.mp4);;MKVファイル (*.mkv);;MOVファイル (*.mov);;AVIファイル (*.avi);;WMVファイル (*.wmv);;FLVファイル (*.flv);;WEBMファイル (*.webm);;MPGファイル (*.mpg);;MPEGファイル (*.mpeg);;3GPファイル (*.3gp)')
    save_path, _ = QFileDialog.getSaveFileName(parent, "変換後の保存先を指定", os.path.splitext(file_path)[0] + "_converted" + orig_ext, filter_str)
    if not save_path:
        return
    try:
        cmd = ["ffmpeg", "-y", "-i", file_path, "-c", "copy", save_path]
        dlg = QProgressDialog("動画変換中...", None, 0, 0, parent)
        dlg.setWindowTitle("動画変換")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.show()
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        dlg.close()
        if result.returncode == 0:
            QMessageBox.information(parent, "完了", f"変換が完了しました:\n{save_path}")
        else:
            QMessageBox.warning(parent, "エラー", f"変換に失敗しました:\n{result.stderr}")
    except Exception as e:
        QMessageBox.warning(parent, "エラー", f"変換処理中にエラー:\n{e}")
