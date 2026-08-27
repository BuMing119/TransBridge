"""Embedding configuration section for the AI translator advanced settings."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class EmbeddingConfigCallbacks(Protocol):
    def on_embed_provider_changed(self) -> None: ...

    def on_embedding_mode_activated(self) -> None: ...

    def on_embedding_api_provider_activated(self, index: int) -> None: ...

    def on_manage_embedding_models(self) -> None: ...

    def on_test_connection(self, target: str = "llm") -> None: ...


def build_embedding_config_section(
    owner: Any,
    callbacks: EmbeddingConfigCallbacks,
    row: Callable[[str, QWidget], QHBoxLayout],
) -> QGroupBox:
    """Build the local/API Embedding settings without adding more responsibility to the main view."""

    box = QGroupBox("语义检索服务（Embedding）")
    layout = QVBoxLayout(box)
    layout.setSpacing(4)

    owner._embed_provider_combo = QComboBox()  # type: ignore[attr-defined]
    owner._embed_provider_combo.addItem("关闭语义检索", "disabled")  # type: ignore[attr-defined]
    owner._embed_provider_combo.addItem("本地向量模型", "local")  # type: ignore[attr-defined]
    owner._embed_provider_combo.addItem("独立 API 服务", "api")  # type: ignore[attr-defined]
    owner._embed_provider_combo.setToolTip(  # type: ignore[attr-defined]
        "关闭后仍保留精确与字面术语匹配；本地模型由模型管理器下载；API 服务使用独立凭据。"
    )
    owner._embed_provider_combo.currentIndexChanged.connect(callbacks.on_embed_provider_changed)  # type: ignore[attr-defined]
    owner._embed_provider_combo.activated.connect(callbacks.on_embedding_mode_activated)  # type: ignore[attr-defined]
    layout.addLayout(row("使用方式:", owner._embed_provider_combo))  # type: ignore[attr-defined]

    owner._embed_local_model_edit = QLineEdit()  # type: ignore[attr-defined]
    owner._embed_local_model_edit.setVisible(False)  # type: ignore[attr-defined]
    owner._embed_local_model_id_edit = QLineEdit()  # type: ignore[attr-defined]
    owner._embed_local_model_id_edit.setVisible(False)  # type: ignore[attr-defined]
    owner._embed_local_status_label = QLabel("尚未选择本地模型")  # type: ignore[attr-defined]
    owner._embed_local_status_label.setWordWrap(True)  # type: ignore[attr-defined]
    owner._embed_manage_btn = QPushButton("管理本地模型…")  # type: ignore[attr-defined]
    owner._embed_manage_btn.clicked.connect(callbacks.on_manage_embedding_models)  # type: ignore[attr-defined]
    local_row = QHBoxLayout()
    local_label = QLabel("当前模型:")
    local_label.setFixedWidth(90)
    local_row.addWidget(local_label)
    local_row.addWidget(owner._embed_local_status_label, 1)  # type: ignore[attr-defined]
    local_row.addWidget(owner._embed_manage_btn)  # type: ignore[attr-defined]
    owner._embed_local_model_label = local_label  # type: ignore[attr-defined]
    layout.addLayout(local_row)

    owner._embed_api_provider_combo = QComboBox()  # type: ignore[attr-defined]
    owner._embed_api_provider_combo.addItem("OpenAI", "openai")  # type: ignore[attr-defined]
    owner._embed_api_provider_combo.addItem("自定义 OpenAI 兼容", "custom")  # type: ignore[attr-defined]
    owner._embed_api_provider_combo.activated.connect(  # type: ignore[attr-defined]
        callbacks.on_embedding_api_provider_activated
    )
    owner._embed_api_provider_label = QLabel("服务商:")  # type: ignore[attr-defined]
    owner._embed_api_provider_label.setFixedWidth(90)  # type: ignore[attr-defined]
    provider_row = QHBoxLayout()
    provider_row.addWidget(owner._embed_api_provider_label)  # type: ignore[attr-defined]
    provider_row.addWidget(owner._embed_api_provider_combo)  # type: ignore[attr-defined]
    layout.addLayout(provider_row)

    owner._embed_model_label = QLabel("模型名:")  # type: ignore[attr-defined]
    owner._embed_model_label.setFixedWidth(90)  # type: ignore[attr-defined]
    owner._embed_model_edit = QLineEdit()  # type: ignore[attr-defined]
    owner._embed_model_edit.setPlaceholderText("如 text-embedding-3-small")  # type: ignore[attr-defined]
    model_row = QHBoxLayout()
    model_row.addWidget(owner._embed_model_label)  # type: ignore[attr-defined]
    model_row.addWidget(owner._embed_model_edit)  # type: ignore[attr-defined]
    layout.addLayout(model_row)

    owner._embed_apikey_label = QLabel("API Key:")  # type: ignore[attr-defined]
    owner._embed_apikey_label.setFixedWidth(90)  # type: ignore[attr-defined]
    owner._embed_apikey_edit = QLineEdit()  # type: ignore[attr-defined]
    owner._embed_apikey_edit.setEchoMode(QLineEdit.EchoMode.Password)  # type: ignore[attr-defined]
    owner._embed_apikey_edit.setPlaceholderText("独立的 Embedding API Key")  # type: ignore[attr-defined]
    key_row = QHBoxLayout()
    key_row.addWidget(owner._embed_apikey_label)  # type: ignore[attr-defined]
    key_row.addWidget(owner._embed_apikey_edit)  # type: ignore[attr-defined]
    layout.addLayout(key_row)

    owner._embed_baseurl_label = QLabel("Base URL:")  # type: ignore[attr-defined]
    owner._embed_baseurl_label.setFixedWidth(90)  # type: ignore[attr-defined]
    owner._embed_baseurl_edit = QLineEdit()  # type: ignore[attr-defined]
    owner._embed_baseurl_edit.setPlaceholderText("https://api.openai.com/v1")  # type: ignore[attr-defined]
    url_row = QHBoxLayout()
    url_row.addWidget(owner._embed_baseurl_label)  # type: ignore[attr-defined]
    url_row.addWidget(owner._embed_baseurl_edit)  # type: ignore[attr-defined]
    layout.addLayout(url_row)

    owner._embed_test_btn = QPushButton("测试 Embedding 连接")  # type: ignore[attr-defined]
    owner._embed_test_btn.setToolTip("使用当前独立 API 配置在后台执行一次最小向量编码请求")  # type: ignore[attr-defined]
    owner._embed_test_btn.clicked.connect(lambda: callbacks.on_test_connection("embedding"))  # type: ignore[attr-defined]
    test_row = QHBoxLayout()
    test_row.addStretch()
    test_row.addWidget(owner._embed_test_btn)  # type: ignore[attr-defined]
    layout.addLayout(test_row)
    return box


__all__ = ["build_embedding_config_section"]
