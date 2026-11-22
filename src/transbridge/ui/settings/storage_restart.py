# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Show the move after normal GUI shutdown, then reopen the selected storage."""

from pathlib import Path
from time import monotonic

from PyQt6.QtCore import QProcess, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QDialog, QLabel, QMessageBox, QProgressBar, QVBoxLayout

from transbridge.config.storage_migration import migrate_storage
from transbridge.ui.shell.project_window_launcher import project_window_command


class _MigrationWorker(QThread):
    progress = pyqtSignal(str)

    def __init__(self, destination, parent):
        super().__init__(parent)
        self.destination = destination
        self.failure = ""
        self.cleanup_warnings = ()

    def run(self):
        last_update = 0.0

        def show_progress(message):
            nonlocal last_update
            now = monotonic()
            if now - last_update >= 0.1:
                self.progress.emit(message)
                last_update = now

        try:
            self.cleanup_warnings = migrate_storage(self.destination, progress=show_progress)
        except Exception as exc:
            self.failure = str(exc) or type(exc).__name__


class _MigrationDialog(QDialog):
    def __init__(self, destination):
        super().__init__()
        self.setWindowTitle("正在迁移 TransBridge 数据")
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        status = QLabel("正在检查数据和目标文件夹…", self)
        status.setTextFormat(Qt.TextFormat.PlainText)
        status.setWordWrap(True)
        layout.addWidget(status)
        progress = QProgressBar(self)
        progress.setRange(0, 0)
        layout.addWidget(progress)
        self.worker = _MigrationWorker(destination, self)
        self.worker.progress.connect(status.setText)
        self.worker.finished.connect(self.accept)

    def reject(self):
        # A filesystem move must finish or roll back before the window can close.
        if self.worker.isFinished():
            super().reject()


def migrate_and_restart(destination: Path) -> int:
    dialog = _MigrationDialog(destination)
    QTimer.singleShot(0, dialog.worker.start)
    dialog.exec()
    dialog.worker.wait()
    if dialog.worker.failure:
        QMessageBox.warning(None, "数据迁移未完成", dialog.worker.failure)
    elif dialog.worker.cleanup_warnings:
        warnings = dialog.worker.cleanup_warnings
        QMessageBox.information(
            None, "已切换到新位置", f"旧位置有 {len(warnings)} 项清理提示。\n\n" + "\n".join(warnings[:5])
        )
    program, arguments = project_window_command()
    started, _pid = QProcess.startDetached(program, list(arguments))
    if not started:
        QMessageBox.warning(None, "无法重新打开 TransBridge", "请手动启动 TransBridge。")
        return 1
    return int(bool(dialog.worker.failure))
