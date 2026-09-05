"""Global LLM service and request-default settings page."""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QPushButton, QSpinBox

from .page_common import SettingsPage, apply_if_present, password_editor


class AiServicePage(SettingsPage):
    def __init__(self, config: object, *, secret_read_only: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        form = QFormLayout(self)
        note = QLabel("这些设置供所有 AI 任务共用。任务窗口只显示服务状态，不显示密钥。", self)
        note.setWordWrap(True)
        form.addRow(note)

        self.provider_combo = QComboBox(self)
        self.provider_combo.addItem("OpenAI 兼容", "openai_compatible")
        self.provider_combo.addItem("Anthropic", "anthropic")
        index = self.provider_combo.findData(str(getattr(config, "provider", "openai_compatible")))
        self.provider_combo.setCurrentIndex(max(0, index))
        form.addRow("供应商", self.provider_combo)

        self.model_edit = QLineEdit(str(getattr(config, "model", "") or ""), self)
        self.model_edit.setPlaceholderText("如 gpt-4o / deepseek-chat")
        form.addRow("默认模型", self.model_edit)

        self.api_key_edit = password_editor(
            bool(getattr(config, "api_key", "")), read_only=secret_read_only, parent=self
        )
        form.addRow("API Key", self.api_key_edit)

        self.base_url_edit = QLineEdit(str(getattr(config, "base_url", "") or ""), self)
        self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("Base URL", self.base_url_edit)

        self.concurrent_spin = _spin(1, 128, int(getattr(config, "max_concurrent", 3) or 3), self)
        form.addRow("默认最大并发", self.concurrent_spin)
        self.retries_spin = _spin(0, 20, int(getattr(config, "llm_max_retries", 2) or 0), self)
        form.addRow("失败重试次数", self.retries_spin)
        self.input_tokens_spin = _spin(1, 1_000_000, int(getattr(config, "max_tokens_per_batch", 2000) or 2000), self)
        form.addRow("每请求业务内容 Token 上限", self.input_tokens_spin)
        self.output_tokens_spin = _spin(0, 1_000_000, int(getattr(config, "max_output_tokens", 0) or 0), self)
        self.output_tokens_spin.setSpecialValueText("不限制")
        form.addRow("默认输出 Token 上限", self.output_tokens_spin)
        self.temperature_spin = QDoubleSpinBox(self)
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(float(getattr(config, "temperature", 0.0) or 0.0))
        form.addRow("Temperature", self.temperature_spin)
        self.test_button = QPushButton("测试 AI 连接", self)
        form.addRow(self.test_button)
        self.test_status = QLabel("", self)
        self.test_status.setWordWrap(True)
        form.addRow("连接结果", self.test_status)

    def apply_to_draft(self) -> None:
        cfg = self._config
        apply_if_present(cfg, "provider", str(self.provider_combo.currentData()))
        apply_if_present(cfg, "model", self.model_edit.text().strip())
        if self.api_key_edit.isEnabled() and self.api_key_edit.text():
            apply_if_present(cfg, "api_key", self.api_key_edit.text().strip())
        apply_if_present(cfg, "base_url", self.base_url_edit.text().strip())
        apply_if_present(cfg, "max_concurrent", self.concurrent_spin.value())
        apply_if_present(cfg, "llm_max_retries", self.retries_spin.value())
        apply_if_present(cfg, "max_tokens_per_batch", self.input_tokens_spin.value())
        apply_if_present(cfg, "max_output_tokens", self.output_tokens_spin.value())
        apply_if_present(cfg, "temperature", self.temperature_spin.value())


def _spin(minimum: int, maximum: int, value: int, parent) -> QSpinBox:
    widget = QSpinBox(parent)
    widget.setRange(minimum, maximum)
    widget.setValue(max(minimum, min(maximum, value)))
    return widget


__all__ = ["AiServicePage"]
