"""ParaTranz connection settings without exposing an existing token."""

from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox, QFormLayout, QLabel, QLineEdit, QPushButton, QSpinBox

from .draft import ParaTranzSettingsDraft
from .page_common import SettingsPage, password_editor


class ParaTranzSettingsPage(SettingsPage):
    def __init__(
        self,
        draft: ParaTranzSettingsDraft,
        *,
        token_configured: bool,
        token_read_only: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._draft = draft
        form = QFormLayout(self)
        status = "已连接" if token_configured else "未连接"
        if token_read_only:
            status += "（凭据由环境变量提供）"
        self.status_label = QLabel(status, self)
        self.status_label.setProperty("tbStatusId", "success" if token_configured else "warning")
        form.addRow("连接状态", self.status_label)
        self.token_edit = password_editor(token_configured, read_only=token_read_only, parent=self)
        form.addRow("API Token", self.token_edit)
        self.base_url_edit = QLineEdit(draft.base_url, self)
        form.addRow("Base URL", self.base_url_edit)
        self.timeout_spin = QSpinBox(self)
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setValue(max(5, min(300, draft.timeout)))
        self.timeout_spin.setSuffix(" 秒")
        form.addRow("请求超时", self.timeout_spin)
        user_text = "未验证" if draft.user_id is None else str(draft.user_id)
        self.user_label = QLabel(user_text, self)
        form.addRow("当前用户 ID", self.user_label)
        self.disconnect_check = QCheckBox("解除 ParaTranz 连接", self)
        self.disconnect_check.setEnabled(token_configured and not token_read_only)
        self.disconnect_check.toggled.connect(self._sync_disconnect)
        form.addRow(self.disconnect_check)
        self.test_button = QPushButton("验证 ParaTranz 连接", self)
        form.addRow(self.test_button)
        self.test_status = QLabel("", self)
        self.test_status.setWordWrap(True)
        form.addRow("验证结果", self.test_status)

    def _sync_disconnect(self, checked: bool) -> None:
        self.token_edit.setEnabled(not checked and self.token_edit.property("environmentCredential") is not True)

    def apply_to_draft(self) -> None:
        self._draft.base_url = self.base_url_edit.text().strip()
        self._draft.timeout = self.timeout_spin.value()
        self._draft.replacement_token = self.token_edit.text().strip() if self.token_edit.isEnabled() else ""
        self._draft.disconnect_requested = self.disconnect_check.isChecked()


__all__ = ["ParaTranzSettingsPage"]
