# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Choose the exact ZIP destination with the native save/overwrite dialog."""

from PyQt6.QtWidgets import QFileDialog


def choose_artifact_destination(parent, suggested_name="export.zip", *, confirm_overwrite=True):
    options = QFileDialog.Option(0) if confirm_overwrite else QFileDialog.Option.DontConfirmOverwrite
    path, _ = QFileDialog.getSaveFileName(parent, "保存导出包", suggested_name, "ZIP 文件 (*.zip)", "", options)
    return path
