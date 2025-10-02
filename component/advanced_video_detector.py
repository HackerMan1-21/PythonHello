"""
advanced_video_detector.py
超高精度動画重複検出システム - 切り抜き・編集動画対応
"""

import cv2
import numpy as np
import imagehash
from PIL import Image
import os

class UltraVideoDetector:
    def __init__(self):
        self.keyframe_count = 10
        self.hash_size = 16
        
    def extract_keyframes(self, video_path):
        """キーフレーム抽出（シーン変化点ベース）"""
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        keyframes = []
        prev_frame = None
        threshold = 30.0
        
        # 動的サンプリング（シーン変化検出）
        for i in range(0, total_frames, max(1, total_frames // 50)):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ret, frame = cap.read()
            if not ret: continue
            
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (64, 64))
            
            if prev_frame is not None:
                diff = cv2.absdiff(prev_frame, gray)
                if np.mean(diff) > threshold:
                    pil_img = Image.fromarray(gray)
                    keyframes.append(imagehash.phash(pil_img, hash_size=self.hash_size))
            
            prev_frame = gray
        
        cap.release()
        return keyframes[:self.keyframe_count]
    
    def calculate_sequence_similarity(self, frames1, frames2):
        """シーケンス類似度計算（部分一致対応）"""
        if not frames1 or not frames2:
            return 0.0
        
        max_similarity = 0.0
        
        # 短い方を長い方の中で検索
        short_seq, long_seq = (frames1, frames2) if len(frames1) <= len(frames2) else (frames2, frames1)
        
        for start_pos in range(len(long_seq) - len(short_seq) + 1):
            similarities = []
            
            for i, short_frame in enumerate(short_seq):
                long_frame = long_seq[start_pos + i]
                diff = abs(short_frame - long_frame)
                similarity = max(0, 1.0 - diff / (self.hash_size * self.hash_size))
                similarities.append(similarity)
            
            avg_sim = np.mean(similarities) if similarities else 0.0
            max_similarity = max(max_similarity, avg_sim)
        
        return max_similarity
    
    def is_duplicate_advanced(self, video1_path, video2_path, threshold=0.75):
        """超高精度重複判定"""
        frames1 = self.extract_keyframes(video1_path)
        frames2 = self.extract_keyframes(video2_path)
        
        similarity = self.calculate_sequence_similarity(frames1, frames2)
        return similarity >= threshold, similarity

def find_ultra_duplicates(video_files, progress_callback=None):
    """切り抜き対応超高精度検出"""
    detector = UltraVideoDetector()
    groups = []
    processed = set()
    
    for i, video1 in enumerate(video_files):
        if video1 in processed:
            continue
        
        group = [video1]
        processed.add(video1)
        
        for video2 in video_files[i+1:]:
            if video2 in processed:
                continue
            
            is_dup, similarity = detector.is_duplicate_advanced(video1, video2)
            if is_dup:
                group.append(video2)
                processed.add(video2)
        
        if len(group) > 1:
            groups.append(group)
        
        if progress_callback:
            progress_callback(i + 1, len(video_files))
    
    return groups