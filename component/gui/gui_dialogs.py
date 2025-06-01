# gui_dialogs.py
# 各種ダイアログ（進捗・警告・詳細表示など）
import threading
from PyQt5.QtWidgets import QProgressDialog, QApplication
from PyQt5.QtCore import QTimer

def show_progress_dialog(parent, title, label, worker_func, min_val=0, max_val=100):
    dlg = QProgressDialog(label, "キャンセル", min_val, max_val, parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(True)
    dlg.setStyleSheet('''
        QProgressDialog {
            background: #232526;
            border: 2px solid #00ffe7;
            border-radius: 10px;
            font-size: 16px;
            color: #00ffe7;
        }
        QProgressDialog::bar {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00ffe7, stop:1 #00ff99);
            border-radius: 8px;
        }
    ''')
    dlg.setValue(min_val)
    cancelled = threading.Event()
    def progress_callback(value, total):
        if total > 0:
            dlg.setValue(int(value / total * 100))
        else:
            dlg.setValue(0)
        QApplication.processEvents()
    def run_worker():
        worker_func(progress_callback, cancelled)
        QTimer.singleShot(0, dlg.close)
    dlg.canceled.connect(cancelled.set)
    QTimer.singleShot(0, dlg.show)
    threading.Thread(target=run_worker, daemon=True).start()
    dlg.exec_()
