"""Embedding connection and semantic-retrieval defaults page."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
)

from .page_common import SettingsPage, apply_if_present, password_editor


class EmbeddingSettingsPage(SettingsPage):
    def __init__(self, config: object, *, secret_read_only: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        embedding = config.embedding
        form = QFormLayout(self)
        note = QLabel("关闭后仍保留精确和字面术语匹配；本地模型路径由模型管理器维护。", self)
        note.setWordWrap(True)
        form.addRow(note)
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("关闭语义检索", "disabled")
        self.mode_combo.addItem("本地向量模型", "local")
        self.mode_combo.addItem("独立 API 服务", "api")
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(str(getattr(embedding, "mode", "disabled")))))
        self.mode_combo.currentIndexChanged.connect(self._sync_mode)
        form.addRow("使用方式", self.mode_combo)
        self.provider_combo = QComboBox(self)
        self.provider_combo.addItem("OpenAI", "openai")
        self.provider_combo.addItem("自定义 OpenAI 兼容", "custom")
        self.provider_combo.setCurrentIndex(
            max(0, self.provider_combo.findData(str(getattr(embedding, "provider", "openai"))))
        )
        form.addRow("API 服务商", self.provider_combo)
        self.model_edit = QLineEdit(str(getattr(embedding, "model", "") or ""), self)
        form.addRow("模型名", self.model_edit)
        self.api_key_edit = password_editor(
            bool(getattr(embedding, "api_key", "")), read_only=secret_read_only, parent=self
        )
        form.addRow("Embedding API Key", self.api_key_edit)
        self.base_url_edit = QLineEdit(str(getattr(embedding, "base_url", "") or ""), self)
        form.addRow("Embedding Base URL", self.base_url_edit)
        self.local_model_edit = QLineEdit(str(getattr(embedding, "local_model_id", "") or ""), self)
        self.local_model_edit.setReadOnly(True)
        self.local_model_edit.setPlaceholderText("由本地模型管理器选择")
        form.addRow("本地模型", self.local_model_edit)
        self.manage_models_button = QPushButton("管理本地模型…", self)
        form.addRow(self.manage_models_button)
        self.retrieval_check = QCheckBox("启用术语检索", self)
        self.retrieval_check.setChecked(bool(getattr(config, "retrieval_enabled", True)))
        form.addRow(self.retrieval_check)
        self.semantic_check = QCheckBox("启用语义匹配", self)
        self.semantic_check.setChecked(bool(getattr(config, "enable_semantic_match", True)))
        form.addRow(self.semantic_check)
        self.threshold_spin = QDoubleSpinBox(self)
        self.threshold_spin.setRange(0.0, 1.0)
        self.threshold_spin.setSingleStep(0.05)
        self.threshold_spin.setValue(float(getattr(config, "semantic_similarity_threshold", 0.7) or 0.7))
        form.addRow("相似度阈值", self.threshold_spin)
        self.top_k_spin = QSpinBox(self)
        self.top_k_spin.setRange(1, 100)
        self.top_k_spin.setValue(int(getattr(config, "semantic_top_k", 5) or 5))
        form.addRow("Top K", self.top_k_spin)
        self.bm25_spin = QDoubleSpinBox(self)
        self.bm25_spin.setRange(0.0, 1.0)
        self.bm25_spin.setSingleStep(0.05)
        self.bm25_spin.setValue(float(getattr(config, "bm25_weight", 0.5) or 0.5))
        form.addRow("BM25 权重", self.bm25_spin)
        self.test_button = QPushButton("测试 Embedding 连接", self)
        form.addRow(self.test_button)
        self.test_status = QLabel("", self)
        self.test_status.setWordWrap(True)
        form.addRow("连接结果", self.test_status)
        self._api_widgets = (self.provider_combo, self.model_edit, self.api_key_edit, self.base_url_edit)
        self._sync_mode()

    def _sync_mode(self) -> None:
        mode = str(self.mode_combo.currentData())
        for widget in self._api_widgets:
            widget.setEnabled(mode == "api")
        self.local_model_edit.setEnabled(mode == "local")

    def select_local_model(self, model_id: str) -> None:
        self.local_model_edit.setText(model_id)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData("local"))

    def apply_to_draft(self) -> None:
        cfg = self._config
        embedding = cfg.embedding
        mode = str(self.mode_combo.currentData())
        embedding.mode = mode
        embedding.provider = "local" if mode == "local" else str(self.provider_combo.currentData())
        embedding.model = self.model_edit.text().strip()
        if self.api_key_edit.isEnabled() and self.api_key_edit.text():
            embedding.api_key = self.api_key_edit.text().strip()
        embedding.base_url = self.base_url_edit.text().strip()
        embedding.local_model_id = self.local_model_edit.text().strip()
        embedding.local_model_path = ""
        apply_if_present(cfg, "retrieval_enabled", self.retrieval_check.isChecked())
        apply_if_present(cfg, "enable_semantic_match", self.semantic_check.isChecked())
        apply_if_present(cfg, "semantic_similarity_threshold", self.threshold_spin.value())
        apply_if_present(cfg, "semantic_top_k", self.top_k_spin.value())
        apply_if_present(cfg, "bm25_weight", self.bm25_spin.value())


__all__ = ["EmbeddingSettingsPage"]
