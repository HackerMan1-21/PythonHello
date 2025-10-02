@echo off
REM --- Pythonアプリを簡単起動するバッチファイル ---
cd /d %~dp0
movieEditor\Scripts\python.exe video_duplicate_finder.py
