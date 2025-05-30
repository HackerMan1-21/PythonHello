# PythonHello 動画・画像重複検出/修復アプリ

## 概要
PyQt5ベースの大規模動画・画像重複検出/修復アプリです。AI超解像・顔復元（Real-ESRGAN/GFPGAN）や壊れファイル検出、サムネイルキャッシュ、非同期処理、UI一貫性などを重視した設計です。

## 主な機能
- 画像・動画の重複検出・グループ化
- 顔グループ化・自動振り分け
- 壊れた画像・動画ファイルの検出・修復
- AI超解像・顔復元（Real-ESRGAN, GFPGAN）
- サムネイル生成・キャッシュ・クリーンアップ
- 進捗表示・エラー通知・UI一貫性

## 使い方
1. `start_app.bat` で起動
2. GUI上でフォルダ選択・重複チェック・修復等を操作
3. サムネイルキャッシュ削除や再読み込みもGUIから可能

## 依存ライブラリ
- Python 3.8+
- PyQt5
- Pillow
- OpenCV
- Real-ESRGAN, GFPGAN（外部バイナリ/モデル必要）

## テスト
`tests/`配下にユニットテストあり。`pytest`等で実行可能。

## 注意事項
- AI超解像・顔復元は外部バイナリ・モデルが必要です。
- サムネイルキャッシュは容量制限・手動削除対応。
- 詳細は各ソース・関数のdocstring参照。

## 外部バイナリ・モデルのセットアップ

- Real-ESRGAN: `realesrgan-ncnn-vulkan.exe` をルートまたはパスの通った場所に配置。
    - モデルファイル（例: `models/Real-ESRGAN-General-x4v3.bin`, `.param`）も `models/` フォルダに配置。
    - [Real-ESRGAN公式リリース](https://github.com/xinntao/Real-ESRGAN/releases) からダウンロード。
- GFPGAN: `GFPGANv1.4.pth` などのモデルをルートまたは `models/` フォルダに配置。
    - [GFPGAN公式リリース](https://github.com/TencentARC/GFPGAN/releases) からダウンロード。
- ffmpeg/ffprobe: パスが通っている必要あり（動画分解・合成・音声抽出に使用）。
- 詳細は各AIツールの公式READMEも参照。

## 不要ファイル・一時ファイルの削除

- `.bak`, `.tmp`, `.log`, `__pycache__`, `.cache/`, `build/`, `dist/` などは `.gitignore` で除外済み。
- 残存する一時ファイル・バックアップは手動または `PowerShell` で削除してください。

## テスト自動化・カバレッジ

- `pytest` で `tests/` 配下のテストを自動実行可能。
- カバレッジ計測例: `pytest --cov=component tests/`

---

# 開発・保守
- コードは責務分離・型変換・例外処理・ユーザー通知を徹底
- ドキュメント・テストも随時整備
- 問題・要望はIssueまたはPRで
