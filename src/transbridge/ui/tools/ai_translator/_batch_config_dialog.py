"""
批量翻译配置对话框。

简化版的 LLM 配置编辑，仅包含核心选项。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox, QSpinBox, QPushButton,
    QMessageBox,
)
from PyQt6.QtCore import Qt

if TYPE_CHECKING:
    from src.transbridge.paratranz.config_manager import LLMConfig


class _BatchConfigDialog(QDialog):
    """批量翻译配置对话框。"""

    def __init__(self, config: "LLMConfig | None", parent=None):
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("批量翻译配置")
        self.setMinimumWidth(420)
        self.setModal(True)
        self._init_ui()
        self._load_config()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        def _row(label_text: str, widget, label_width: int = 90):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(label_width)
            row.addWidget(lbl)
            row.addWidget(widget)
            return row

        # 供应商
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(["OpenAI 兼容", "Anthropic"])
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        layout.addLayout(_row("供应商:", self._provider_combo))

        # 模型
        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("如 gpt-4o / deepseek-chat")
        layout.addLayout(_row("模型名:", self._model_edit))

        # API Key
        self._apikey_edit = QLineEdit()
        self._apikey_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._apikey_edit.setPlaceholderText("API Key")
        layout.addLayout(_row("API Key:", self._apikey_edit))

        # Base URL
        self._baseurl_edit = QLineEdit()
        self._baseurl_edit.setPlaceholderText("https://api.openai.com/v1")
        layout.addLayout(_row("Base URL:", self._baseurl_edit))

        # 并发数
        self._concurrent_spin = QSpinBox()
        self._concurrent_spin.setRange(1, 50)
        self._concurrent_spin.setValue(20)
        layout.addLayout(_row("并发数:", self._concurrent_spin))

        # 测试按钮
        test_btn = QPushButton("测试连接")
        test_btn.setFixedWidth(100)
        test_btn.clicked.connect(self._on_test_connection)
        test_row = QHBoxLayout()
        test_row.addStretch()
        test_row.addWidget(test_btn)
        layout.addLayout(test_row)

        # 按钮
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _load_config(self):
        """加载配置到控件。"""
        if not self._config:
            return
        self._provider_combo.setCurrentIndex(0 if self._config.provider != "anthropic" else 1)
        self._model_edit.setText(self._config.model or "")
        self._apikey_edit.setText(self._config.api_key or "")
        self._baseurl_edit.setText(self._config.base_url or "")
        self._concurrent_spin.setValue(self._config.max_concurrent)
        self._on_provider_changed()

    def _on_provider_changed(self):
        """供应商切换时禁用/启用 Base URL。"""
        is_openai = self._provider_combo.currentIndex() == 0
        self._baseurl_edit.setEnabled(is_openai)

    def _on_test_connection(self):
        """测试 LLM 连接。"""
        cfg = self._build_config()
        if not cfg.api_key:
            QMessageBox.warning(self, "测试连接", "请先填写 API Key。")
            return
        if not cfg.model:
            QMessageBox.warning(self, "测试连接", "请先填写模型名。")
            return
        try:
            from src.transbridge.ai_translator.llm_client import create_llm_client
            client = create_llm_client(cfg)
            reply = client.chat([{"role": "user", "content": "Say 'OK' in one word."}], max_tokens=10)
            QMessageBox.information(self, "测试连接", f"连接成功！模型回复：{reply}")
        except Exception as exc:
            QMessageBox.critical(self, "测试连接失败", str(exc))

    def _build_config(self) -> "LLMConfig":
        """从控件构建配置对象，保留原有其他字段。"""
        from src.transbridge.paratranz.config_manager import LLMConfig
        # 使用传入的配置作为基础，保留其他字段
        cfg = self._config or LLMConfig()
        cfg.provider = "anthropic" if self._provider_combo.currentIndex() == 1 else "openai_compatible"
        cfg.model = self._model_edit.text().strip()
        cfg.api_key = self._apikey_edit.text().strip()
        cfg.base_url = self._baseurl_edit.text().strip()
        cfg.max_concurrent = self._concurrent_spin.value()
        # 注意：保留其他字段如 max_tokens_per_batch, local_json_path 等
        return cfg

    def _on_accept(self):
        """保存配置并关闭。"""
        cfg = self._build_config()
        cfg.save_to_file()
        self._config = cfg
        self.accept()

    def get_config(self) -> "LLMConfig":
        """返回修改后的配置。"""
        return self._config
