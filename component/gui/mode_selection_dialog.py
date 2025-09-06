from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QButtonGroup
from PyQt5.QtCore import Qt

class ModeSelectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_mode = None
        self.init_ui()
    
    def init_ui(self):
        self.setWindowTitle("処理モード選択")
        self.setModal(True)
        self.resize(400, 200)
        
        layout = QVBoxLayout()
        
        # タイトル
        title = QLabel("処理モードを選択してください")
        title.setStyleSheet("font-size: 16px; font-weight: bold; margin: 10px;")
        layout.addWidget(title)
        
        # ボタン
        btn_layout = QVBoxLayout()
        
        self.duplicate_btn = QPushButton("重複チェックモード")
        self.duplicate_btn.setStyleSheet("font-size: 14px; padding: 10px; margin: 5px;")
        self.duplicate_btn.clicked.connect(lambda: self.select_mode("duplicate"))
        
        self.face_btn = QPushButton("顔グループ表示モード")
        self.face_btn.setStyleSheet("font-size: 14px; padding: 10px; margin: 5px;")
        self.face_btn.clicked.connect(lambda: self.select_mode("face"))
        
        btn_layout.addWidget(self.duplicate_btn)
        btn_layout.addWidget(self.face_btn)
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)
    
    def select_mode(self, mode):
        self.selected_mode = mode
        self.accept()