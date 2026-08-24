"""Window-local selection for the legacy translation checkpoint."""

from __future__ import annotations


def check_translation_checkpoint(
    parent: object,
    esp_path: str | None,
    *,
    entries: tuple[object, ...] = (),
    overwrite: bool | None = None,
) -> str | None:
    if not esp_path:
        return None
    from PyQt6.QtWidgets import QMessageBox

    from transbridge.ai_translator.translator import ProgressCheckpoint

    checkpoint = ProgressCheckpoint.load(esp_path)
    if checkpoint is None or not checkpoint.run_id:
        return None
    if entries:
        expected = {str(getattr(entry, "id", getattr(entry, "key", ""))) for entry in entries}
        if set(checkpoint.target_entry_ids or ()) != expected or checkpoint.overwrite != overwrite:
            return None
    done = len(checkpoint.completed_fingerprints)
    reply = QMessageBox.question(
        parent,
        "检测到未完成的翻译任务",
        f"上次翻译未完成（已完成 {done} 批）。是否从断点继续？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    if reply == QMessageBox.StandardButton.No:
        checkpoint.delete(esp_path)
        return None
    return checkpoint.run_id


__all__ = ["check_translation_checkpoint"]
