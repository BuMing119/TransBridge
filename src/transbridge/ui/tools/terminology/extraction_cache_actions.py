# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Explicit project extraction-cache maintenance, separate from draft actions."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .async_actions import AsyncActionRunner


class ExtractionCacheActions:
    def __init__(self, window):
        self.window = window
        self._busy = False
        self._task_busy = window._workflow.task_busy
        options = window.build_view.extraction_options
        self.button = QPushButton("清理当前项目提取缓存", options)
        self.button.setAutoDefault(False)
        self.button.setToolTip("清理本项目所有翻译版本共用的提取缓存；保留草稿、人工决定和已发布术语。")
        ComponentStyle.apply_static(self.button, ComponentKind.BUTTON)
        row = QHBoxLayout()
        row.addWidget(self.button)
        row.addStretch(1)
        options.layout().addLayout(row)
        self.status = QLabel(options)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.hide()
        options.layout().addWidget(self.status)
        self._runner = AsyncActionRunner(options, on_error=self._failed)
        self.button.clicked.connect(self.clear)
        window.task_changed.connect(self._task_changed)
        window.context_invalidated.connect(self.close)
        window.closed.connect(self.close)

    def clear(self):
        if self._busy or not self.window.context_is_current:
            return
        if self._task_busy or self.window._workflow.task_busy:
            self._message("请等待当前术语任务完成后再清理提取缓存。")
            return
        self._busy = True
        self.button.setEnabled(False)
        self._message("正在清理当前项目提取缓存…")
        self._runner.run(self.window.presenter.clear_extraction_cache, self._saved)

    def _saved(self, count):
        self._busy = False
        self.button.setEnabled(not self._task_busy and self.window.context_is_current)
        self._message(
            f"已清理 {count} 条提取缓存。草稿和已发布术语保持不变；下次 LLM 抽取不再复用这些缓存。"
            if count
            else "当前项目没有可清理的提取缓存。草稿和已发布术语保持不变。"
        )

    def _failed(self, error):
        self._busy = False
        self.button.setEnabled(not self._task_busy and self.window.context_is_current)
        self._message(f"提取缓存清理失败：{error}。可重试。")

    def _task_changed(self, state):
        self._task_busy = not state.is_terminal
        self.button.setEnabled(not self._busy and not self._task_busy and self.window.context_is_current)

    def _message(self, message):
        self.status.setText(message)
        self.status.show()

    def close(self):
        self.button.setEnabled(False)
        self._runner.close()
