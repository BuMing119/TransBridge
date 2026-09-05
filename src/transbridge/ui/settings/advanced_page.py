"""Guardrail and MCP settings kept outside the common task path."""

from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QSpinBox

from .page_common import SettingsPage, apply_if_present, password_editor


class AdvancedSettingsPage(SettingsPage):
    def __init__(self, config: object, *, secret_read_only: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        form = QFormLayout(self)
        note = QLabel("这些选项会影响自动化写操作和 MCP 暴露面。仅在了解其影响时修改。", self)
        note.setWordWrap(True)
        form.addRow(note)
        self.admin_confirm = _check("管理操作需要确认", getattr(config, "guardrails_enable_admin_confirm", True), self)
        form.addRow(self.admin_confirm)
        self.input_validation = _check(
            "启用输入验证", getattr(config, "guardrails_enable_input_validation", True), self
        )
        form.addRow(self.input_validation)
        self.output_validation = _check(
            "启用输出验证", getattr(config, "guardrails_enable_output_validation", True), self
        )
        form.addRow(self.output_validation)
        self.write_confirm = _check("写操作需要确认", getattr(config, "guardrails_write_require_confirm", False), self)
        form.addRow(self.write_confirm)
        self.max_input_spin = QSpinBox(self)
        self.max_input_spin.setRange(1, 100_000_000)
        self.max_input_spin.setValue(int(getattr(config, "guardrails_max_input_size", 102400) or 102400))
        form.addRow("最大输入大小", self.max_input_spin)
        self.mcp_enabled = _check("启用 MCP 服务", getattr(config, "mcp_enabled", False), self)
        form.addRow(self.mcp_enabled)
        self.transport_combo = QComboBox(self)
        self.transport_combo.addItem("stdio", "stdio")
        self.transport_combo.addItem("HTTP", "http")
        self.transport_combo.setCurrentIndex(
            max(0, self.transport_combo.findData(str(getattr(config, "mcp_transport", "stdio"))))
        )
        form.addRow("MCP 传输", self.transport_combo)
        self.whitelist_edit = QLineEdit(str(getattr(config, "mcp_admin_tool_whitelist", "") or ""), self)
        form.addRow("管理工具白名单", self.whitelist_edit)
        self.policy_combo = QComboBox(self)
        for label, value in (("拒绝", "deny"), ("需要确认", "confirm"), ("允许", "allow")):
            self.policy_combo.addItem(label, value)
        self.policy_combo.setCurrentIndex(
            max(0, self.policy_combo.findData(str(getattr(config, "mcp_write_tool_policy", "deny"))))
        )
        form.addRow("MCP 写入策略", self.policy_combo)
        self.auth_token_edit = password_editor(
            bool(getattr(config, "mcp_auth_token", "")), read_only=secret_read_only, parent=self
        )
        form.addRow("MCP 认证令牌", self.auth_token_edit)

    def apply_to_draft(self) -> None:
        cfg = self._config
        values = {
            "guardrails_enable_admin_confirm": self.admin_confirm.isChecked(),
            "guardrails_enable_input_validation": self.input_validation.isChecked(),
            "guardrails_enable_output_validation": self.output_validation.isChecked(),
            "guardrails_write_require_confirm": self.write_confirm.isChecked(),
            "guardrails_max_input_size": self.max_input_spin.value(),
            "mcp_enabled": self.mcp_enabled.isChecked(),
            "mcp_transport": str(self.transport_combo.currentData()),
            "mcp_admin_tool_whitelist": self.whitelist_edit.text().strip(),
            "mcp_write_tool_policy": str(self.policy_combo.currentData()),
        }
        if self.auth_token_edit.text():
            values["mcp_auth_token"] = self.auth_token_edit.text().strip()
        for attr, value in values.items():
            apply_if_present(cfg, attr, value)


def _check(label: str, checked: object, parent) -> QCheckBox:
    widget = QCheckBox(label, parent)
    widget.setChecked(bool(checked))
    return widget


__all__ = ["AdvancedSettingsPage"]
