"""Run-scoped request limits for batch AI translation."""

from __future__ import annotations

from PyQt6.QtWidgets import QFormLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class BatchRuntimePage(QWidget):
    def __init__(self, config: object, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        title = QLabel("运行参数", self)
        title.setProperty("tbTaskSectionTitle", True)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        note = QLabel("这些值只影响本次任务。插件按左侧列表从上到下处理。", self)
        note.setProperty("tbTaskHint", True)
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        self.concurrent = self._spin(1, 50, int(getattr(config, "max_concurrent", 3)))
        self.concurrent.setToolTip("整个批量任务共享的最大在途 LLM 请求数")
        form.addRow("最大并发请求", self.concurrent)
        self.retries = self._spin(0, 20, int(getattr(config, "llm_max_retries", 2)))
        form.addRow("失败重试次数", self.retries)
        self.input_tokens = self._spin(200, 32000, int(getattr(config, "max_tokens_per_batch", 2000)), 200)
        self.input_tokens.setToolTip("每个请求中业务内容的 Token 上限")
        form.addRow("输入 Token 上限", self.input_tokens)
        self.output_tokens = self._spin(0, 65536, int(getattr(config, "max_output_tokens", 0)), 256)
        self.output_tokens.setSpecialValueText("不限制（供应商支持时）")
        form.addRow("输出 Token 上限", self.output_tokens)
        layout.addLayout(form)
        layout.addStretch(1)

    def apply_to(self, config: object) -> None:
        config.max_concurrent = self.concurrent.value()
        config.llm_max_retries = self.retries.value()
        config.max_tokens_per_batch = self.input_tokens.value()
        config.max_output_tokens = self.output_tokens.value()

    def _spin(self, minimum: int, maximum: int, value: int, step: int = 1) -> QSpinBox:
        control = QSpinBox(self)
        ComponentStyle.apply_static(control, ComponentKind.INPUT)
        control.setRange(minimum, maximum)
        control.setSingleStep(step)
        control.setValue(max(minimum, min(maximum, value)))
        return control


__all__ = ["BatchRuntimePage"]
