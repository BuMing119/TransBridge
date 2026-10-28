"""Conflict defaults with a detached draft and explicit persistence errors."""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QFormLayout, QLabel, QWidget

from transbridge.application.translation.result_merge import ResultConflictPolicy
from transbridge.config.task_results import TaskResultPreferences


class TaskResultsSettingsPage(QWidget):
    def __init__(self, repository, parent=None) -> None:
        super().__init__(parent)
        self._repository = repository
        self._loaded = None
        layout = QFormLayout(self)
        note = QLabel(
            "仅在同一个词条有不同修改时使用。未冲突的结果正常合入；原文变化或词条删除时保留结果待处理。"
            "设置影响之后开始的任务。",
            self,
        )
        note.setWordWrap(True)
        layout.addRow(note)
        self.ai_policy = self._combo("AI 翻译冲突默认处理")
        self.download_policy = self._combo("下载冲突默认处理")
        layout.addRow("AI 翻译", self.ai_policy)
        layout.addRow("下载", self.download_policy)
        try:
            self._loaded = repository.load()
        except Exception as exc:
            error = QLabel(f"无法读取冲突设置：{exc}。重新打开设置后再试。", self)
            error.setWordWrap(True)
            layout.addRow(error)
            self.ai_policy.setEnabled(False)
            self.download_policy.setEnabled(False)
        if self._loaded is not None:
            self.ai_policy.setCurrentIndex(self.ai_policy.findData(self._loaded.ai_policy))
            self.download_policy.setCurrentIndex(self.download_policy.findData(self._loaded.download_policy))

    def _combo(self, label: str) -> QComboBox:
        combo = QComboBox(self)
        combo.setAccessibleName(label)
        for text, policy in (
            ("保留工作台内容", ResultConflictPolicy.KEEP_CURRENT),
            ("采用本次任务结果", ResultConflictPolicy.PREFER_INCOMING),
            ("暂不处理，稍后选择", ResultConflictPolicy.DEFER),
        ):
            combo.addItem(text, policy)
        return combo

    def save(self) -> None:
        if self._loaded is None:
            return
        preferences = TaskResultPreferences(self.ai_policy.currentData(), self.download_policy.currentData())
        if preferences != self._loaded:
            self._repository.save(preferences)
            self._loaded = preferences
