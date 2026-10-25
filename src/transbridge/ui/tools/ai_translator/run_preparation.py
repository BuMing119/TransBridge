"""One explicit choice for optional retrieval failures before freezing a run."""

from __future__ import annotations

from collections.abc import Iterable
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from .run_spec import AiPreflightIssue, FrozenExecutionConfig

logger = logging.getLogger(__name__)


def confirm_optional_retrieval(parent, config: object, issues: Iterable[AiPreflightIssue]) -> object | None:
    """Return a detached fallback only after consent; keep the saved preference intact."""
    issues = tuple(dict.fromkeys(issues))
    if not issues:
        return config
    dialog = QMessageBox(parent)
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setWindowTitle("语义检索不可用")
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setText("语义检索暂不可用。忽略后，本次将仅使用精确和字面术语匹配继续翻译。")
    dialog.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ignore)
    dialog.button(QMessageBox.StandardButton.Cancel).setText("取消")
    dialog.button(QMessageBox.StandardButton.Ignore).setText("忽略并继续")
    dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
    dialog.setEscapeButton(QMessageBox.StandardButton.Cancel)
    if dialog.exec() != QMessageBox.StandardButton.Ignore:
        return None
    execution = FrozenExecutionConfig(config).copy()
    execution.enable_semantic_match = False
    embedding = getattr(execution, "embedding", None)
    if embedding is not None:
        embedding.mode = "disabled"
    logger.warning("本次 AI 任务已忽略语义检索问题：%s", "；".join(issue.message for issue in issues))
    return execution
