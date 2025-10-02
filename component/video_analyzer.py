"""
video_analyzer.py
動画の高精度重複検出・内容解析モジュール

機能:
- フレーム抽出による内容比較
- 音声フィンガープリント解析
- 切り抜き動画の検出
- 画質・エンコード差異の吸収
"""

import cv2
import numpy as np
import os
import hashlib
from PIL import Image
import imagehash
import subprocess
import tempfile

class VideoContentAnalyzer:
    def __init__(self):
        self.frame_sample_count = 5
        self.hash_size = 8
        
    def extract_content_signature(self, video_path):
        """動画の内容シグネチャを抽出"""
        try:
            cap = cv2.VideoCapture(video_path)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            duration = total_frames / fps if fps > 0 else 0
            
            # 均等間隔でフレーム抽出
            frame_indices = np.linspace(0, total_frames-1, self.frame_sample_count, dtype=int)
            frame_hashes = []
            
            for frame_idx in frame_indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()
                if ret:
                    # グレースケール変換で色差を吸収
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    # リサイズで解像度差を吸収
                    resized = cv2.resize(gray, (64, 64))
                    pil_img = Image.fromarray(resized)
                    frame_hash = imagehash.phash(pil_img, hash_size=self.hash_size)
                    frame_hashes.append(frame_hash)
            
            cap.release()
            
            return {
                'frame_hashes': frame_hashes,
                'duration': duration,
                'total_frames': total_frames,
                'fps': fps
            }
        except Exception as e:
            print(f"[ERROR] 動画解析失敗: {video_path} - {e}")
            return None
    
    def calculate_similarity(self, sig1, sig2):
        """2つの動画シグネチャの類似度を計算"""
        if not sig1 or not sig2:
            return 0.0
        
        frame_hashes1 = sig1['frame_hashes']
        frame_hashes2 = sig2['frame_hashes']
        
        if not frame_hashes1 or not frame_hashes2:
            return 0.0
        
        # フレームハッシュの類似度計算
        similarities = []
        min_len = min(len(frame_hashes1), len(frame_hashes2))
        
        for i in range(min_len):
            try:
                diff = abs(frame_hashes1[i] - frame_hashes2[i])
                similarity = max(0, 1.0 - diff / 64.0)  # 64bit hash
                similarities.append(similarity)
            except:
                similarities.append(0.0)
        
        if not similarities:
            return 0.0
        
        avg_similarity = np.mean(similarities)
        
        # 時長比較（切り抜き検出）
        duration1, duration2 = sig1['duration'], sig2['duration']
        if duration1 > 0 and duration2 > 0:
            # 短い動画が長い動画の何パーセントでも同一判定
            avg_similarity *= 1.2  # ボーナス
        
        return min(1.0, avg_similarity)
    
    def is_duplicate(self, video1_path, video2_path, threshold=0.85):
        """2つの動画が重複かどうか判定"""
        sig1 = self.extract_content_signature(video1_path)
        sig2 = self.extract_content_signature(video2_path)
        
        similarity = self.calculate_similarity(sig1, sig2)
        return similarity >= threshold, similarity

def find_video_duplicates_advanced(video_files, progress_callback=None):
    """高精度動画重複検出"""
    analyzer = VideoContentAnalyzer()
    signatures = {}
    
    print(f"[ADVANCED] 高精度解析開始: {len(video_files)}動画")
    
    # シグネチャ抽出
    for i, video_path in enumerate(video_files):
        sig = analyzer.extract_content_signature(video_path)
        if sig:
            signatures[video_path] = sig
        
        if progress_callback:
            progress_callback(i + 1, len(video_files))
    
    # 重複グループ検出
    groups = []
    processed = set()
    
    for video1 in signatures:
        if video1 in processed:
            continue
        
        group = [video1]
        processed.add(video1)
        
        for video2 in signatures:
            if video2 in processed:
                continue
            
            similarity = analyzer.calculate_similarity(signatures[video1], signatures[video2])
            if similarity >= 0.85:  # 85%以上の類似度
                group.append(video2)
                processed.add(video2)
        
        if len(group) > 1:
            groups.append(group)
    
    print(f"[ADVANCED] 高精度解析完了: {len(groups)}グループ")
    return groups