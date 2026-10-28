"""Worker-backed access to saved download results and standalone exports."""

from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QPushButton


class DownloadRecoveryControls:
    def __init__(self, dialog, layout, *, list_results, restore, export, restore_previous=None):
        self.dialog, self.list_results, self.restore, self.export = dialog, list_results, restore, export
        self.reopen = QPushButton("重开下载成果", dialog)
        self.reopen.clicked.connect(self.open)
        self.save_as = QPushButton("另存下载成果", dialog)
        self.save_as.clicked.connect(self.save)
        self.previous = QPushButton("恢复覆盖前译文", dialog)
        self.previous.clicked.connect(lambda: dialog._start_worker("run", restore_previous))
        self.previous.setVisible(restore_previous is not None)
        for button in (self.reopen, self.save_as, self.previous):
            button.setAutoDefault(False)
            layout.addWidget(button)

    def open(self):
        self.dialog._start_worker("saved_results", lambda *_: self.list_results())

    def choose(self, records):
        diagnostics = getattr(records, "diagnostics", ())
        if diagnostics:
            QMessageBox.warning(self.dialog, "下载成果记录损坏", "\n".join(diagnostics))
        if not records:
            QMessageBox.information(self.dialog, "下载成果", "当前工程版本和云端目标尚无已保存的下载成果。")
            return
        labels = [f"{row['created_at']} · {len(row['sources'])} 个来源 · {row['result_id'][:8]}" for row in records]
        selected, accepted = QInputDialog.getItem(self.dialog, "重开下载成果", "选择已保存成果", labels, editable=False)
        if accepted:
            record = records[labels.index(selected)]
            self.dialog._start_worker(
                "restore", lambda progress, cancel: self.restore(record["result_id"], progress, cancel)
            )

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self.dialog, "另存下载成果", "ParaTranz下载成果.json", "JSON (*.json)")
        if path:
            self.dialog._start_worker("export_results", lambda *_: self.export(path))

    def set_enabled(self, enabled):
        self.reopen.setEnabled(enabled)
        self.save_as.setEnabled(enabled)
        self.previous.setEnabled(enabled)
