"""Global LLM service and request-default settings page."""

from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QPushButton, QSpinBox

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
        self.context_window_spin = _spin(0, 4_000_000, int(getattr(config, "assistant_context_window", 0)), self)
        self.context_window_spin.setSpecialValueText("自动匹配模型容量")
        self.context_window_spin.setToolTip("0 为自动；正数按服务商实际窗口设置。切换模型时保留手动值。")
        form.addRow("助手模型上下文窗口", self.context_window_spin)
        self.context_capacity_note = QLabel(self)
        self.context_capacity_note.setWordWrap(True)
        form.addRow(self.context_capacity_note)
        self.context_auto_button = QPushButton("使用自动容量", self)
        self.context_auto_button.clicked.connect(lambda: self.context_window_spin.setValue(0))
        form.addRow(self.context_auto_button)
        self.context_window_spin.valueChanged.connect(self._refresh_context_capacity)
        self.model_edit.textChanged.connect(self._refresh_context_capacity)
        self.base_url_edit.textChanged.connect(self._refresh_context_capacity)
        self.provider_combo.currentIndexChanged.connect(self._refresh_context_capacity)
        self._refresh_context_capacity()
        self.auto_compaction_check = QCheckBox("接近容量时自动生成分段摘要", self)
        self.auto_compaction_check.setChecked(bool(getattr(config, "assistant_auto_compaction", True)))
        form.addRow("助手自动摘要", self.auto_compaction_check)
        summary_note = QLabel("关闭后保留完整历史，不滚动裁剪旧消息；达到上下文容量限制时会暂停并提示处理。", self)
        summary_note.setWordWrap(True)
        form.addRow(summary_note)
        self.prompt_cache_check = QCheckBox("启用受支持服务的助手提示缓存", self)
        self.prompt_cache_check.setChecked(bool(getattr(config, "assistant_prompt_cache", True)))
        self.prompt_cache_check.setToolTip("可关闭以对比缓存诊断；实际是否命中取决于供应商与模型，并以返回的用量为准。")
        form.addRow("助手缓存诊断", self.prompt_cache_check)
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

    def _refresh_context_capacity(self) -> None:
        from transbridge.smart_assistant.context_capacity import resolve_context_capacity

        config = SimpleNamespace(
            provider=self.provider_combo.currentData(),
            base_url=self.base_url_edit.text().strip(),
            model=self.model_edit.text().strip(),
            assistant_context_window=self.context_window_spin.value(),
        )
        try:
            capacity = resolve_context_capacity(config)
            automatic = resolve_context_capacity(config, override=0)
        except ValueError:
            self.context_capacity_note.setText("服务地址格式无效，请检查后再设置容量。")
            return
        text = f"生效容量：{capacity.window:,} token；来源：{capacity.source}。"
        if config.assistant_context_window and automatic.window > capacity.window:
            text += f" 此官方模型自动容量为 {automatic.window:,}；可点击“使用自动容量”。"
        self.context_capacity_note.setText(text)

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
        apply_if_present(cfg, "assistant_context_window", self.context_window_spin.value())
        apply_if_present(cfg, "assistant_auto_compaction", self.auto_compaction_check.isChecked())
        apply_if_present(cfg, "assistant_prompt_cache", self.prompt_cache_check.isChecked())
        apply_if_present(cfg, "temperature", self.temperature_spin.value())


def _spin(minimum: int, maximum: int, value: int, parent) -> QSpinBox:
    widget = QSpinBox(parent)
    widget.setRange(minimum, maximum)
    widget.setValue(max(minimum, min(maximum, value)))
    return widget


__all__ = ["AiServicePage"]
