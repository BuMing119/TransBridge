"""
批量翻译对话框。

支持：
- 可拖拽排序的插件列表
- 勾选要翻译的插件
- 覆盖已有译文选项
- 统计信息显示
- 配置显示与修改
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentStyle, ElidedLabel, SemanticState

from ._theme_support import AiThemeBinding, set_widget_brush

if TYPE_CHECKING:
    from transbridge.paratranz.config_manager import LLMConfig
    from transbridge.ui.context import AppContext, CollectionSlot

_ROLE_HAS_UNTRANSLATED = int(Qt.ItemDataRole.UserRole) + 1


class _BatchTranslationDialog(QDialog):
    """批量翻译对话框：插件排序 + 勾选 + 覆盖选项 + 配置显示。"""

    def __init__(self, ctx: AppContext, parent=None, *, theme_view: ThemeView | None = None):
        super().__init__(parent)
        self._ctx = ctx
        self.setWindowTitle("批量翻译")
        self.setMinimumWidth(500)
        self.setMinimumHeight(450)
        self.setMaximumHeight(620)

        self._slot_keys: list[str] = []  # 保持顺序
        self._llm_config: LLMConfig | None = None
        self._init_ui()
        self._load_config()
        self._populate_list()
        self._theme_binding = AiThemeBinding(self, theme_view, self._apply_theme)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── LLM 配置区 ────────────────────────────────────────────────────────
        cfg_box = QGroupBox("LLM 配置")
        cfg_layout = QVBoxLayout(cfg_box)
        cfg_layout.setSpacing(4)

        self._config_label = ElidedLabel()
        self._config_label.setAccessibleName("批量翻译 LLM 配置状态")
        cfg_layout.addWidget(self._config_label)

        cfg_btn_row = QHBoxLayout()
        self._config_btn = QPushButton("修改配置")
        self._config_btn.setFixedWidth(90)
        self._config_btn.clicked.connect(self._on_edit_config)
        cfg_btn_row.addStretch()
        cfg_btn_row.addWidget(self._config_btn)
        cfg_layout.addLayout(cfg_btn_row)

        layout.addWidget(cfg_box)

        # ── 插件列表区 ────────────────────────────────────────────────────────
        hint = QLabel("拖拽调整翻译顺序（从上到下依次翻译）：")
        layout.addWidget(hint)

        # 操作按钮
        btn_row = QHBoxLayout()
        self._btn_all = QPushButton("全选")
        self._btn_none = QPushButton("全不选")
        self._btn_untranslated = QPushButton("仅选未翻译")
        btn_row.addWidget(self._btn_all)
        btn_row.addWidget(self._btn_none)
        btn_row.addWidget(self._btn_untranslated)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 可拖拽列表
        self._list = QListWidget()
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self._list)

        # 覆盖选项
        self._overwrite_check = QCheckBox("覆盖已有译文（重新翻译）")
        layout.addWidget(self._overwrite_check)

        # 状态标签
        self._status_label = QLabel()
        self._status_label.setAccessibleName("批量翻译选择状态")
        layout.addWidget(self._status_label)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("开始翻译")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        # 连接信号
        self._btn_all.clicked.connect(self._select_all)
        self._btn_none.clicked.connect(self._select_none)
        self._btn_untranslated.clicked.connect(self._select_untranslated)
        self._list.itemChanged.connect(self._update_status)

    def _populate_list(self):
        """填充插件列表。"""
        self._list.clear()
        self._slot_keys.clear()

        for key, slot in self._ctx.slots.items():
            self._slot_keys.append(key)

            # 计算统计
            total = len(slot.collection) if slot.collection else 0
            untranslated = 0
            if slot.collection:
                untranslated = sum(1 for e in slot.collection if not e.translation or e.stage == 0)

            name = slot.label or Path(key).stem
            display_text = f"{name}    {total} 条（未翻 {untranslated} 条）"

            item = QListWidgetItem(display_text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # 默认勾选有未翻译条目的插件
            item.setCheckState(Qt.CheckState.Checked if untranslated > 0 else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, key)  # 存储 slot key
            item.setData(_ROLE_HAS_UNTRANSLATED, untranslated > 0)

            # 如果已全部翻译，显示灰色
            if untranslated == 0 and total > 0:
                item.setToolTip("该插件已全部翻译")

            self._list.addItem(item)

        self._update_status()

    def _select_all(self):
        """全选。"""
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setCheckState(Qt.CheckState.Checked)

    def _select_none(self):
        """全不选。"""
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setCheckState(Qt.CheckState.Unchecked)

    def _select_untranslated(self):
        """仅选未翻译。"""
        for i in range(self._list.count()):
            item = self._list.item(i)
            if not bool(item.data(_ROLE_HAS_UNTRANSLATED)):
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setCheckState(Qt.CheckState.Checked)

    def _update_status(self):
        """更新状态显示。"""
        checked = 0
        total_entries = 0
        total_untranslated = 0

        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                checked += 1
                key = item.data(Qt.ItemDataRole.UserRole)
                slot = self._ctx.slots.get(key)
                if slot and slot.collection:
                    total_entries += len(slot.collection)
                    total_untranslated += sum(1 for e in slot.collection if not e.translation or e.stage == 0)

        self._status_label.setText(f"已选 {checked} 个插件，共 {total_entries} 条（未翻 {total_untranslated} 条）")
        self._status_label.setAccessibleDescription(self._status_label.text())
        self._ok_btn.setEnabled(checked > 0)

    def get_selected_slots(self) -> list[CollectionSlot]:
        """返回按列表顺序排列的选中 slot 列表。"""
        result = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                key = item.data(Qt.ItemDataRole.UserRole)
                slot = self._ctx.slots.get(key)
                if slot:
                    result.append(slot)
        return result

    def is_overwrite(self) -> bool:
        """返回是否覆盖已有译文。"""
        return self._overwrite_check.isChecked()

    def _load_config(self):
        """加载 LLM 配置并更新显示。"""
        from transbridge.paratranz.config_manager import LLMConfig

        self._llm_config = LLMConfig.load_from_file()
        self._update_config_label()

    def _update_config_label(self):
        """更新配置显示标签。"""
        if self._llm_config:
            api_key = self._llm_config.api_key or ""
            masked_key = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "(未设置)"
            model = self._llm_config.model or "(未设置)"
            concurrent = self._llm_config.max_concurrent
            self._set_config_text(f"模型: {model}  |  API Key: {masked_key}  |  并发: {concurrent}")
            # 检查配置完整性
            if not self._llm_config.api_key or not self._llm_config.model:
                ComponentStyle.apply_state(self._config_label, SemanticState.ERROR)
                self._ok_btn.setToolTip("请先配置 API Key 和模型名")
            else:
                ComponentStyle.apply_state(self._config_label, SemanticState.SUCCESS)
                self._ok_btn.setToolTip("")
        else:
            self._set_config_text("未加载配置")
            ComponentStyle.apply_state(self._config_label, SemanticState.ERROR)

    def _set_config_text(self, text: str) -> None:
        self._config_label.set_full_text(text)
        self._config_label.setToolTip(text)
        self._config_label.setAccessibleDescription(text)

    def _apply_theme(self, binding: AiThemeBinding) -> None:
        valid = bool(self._llm_config and self._llm_config.api_key and self._llm_config.model)
        set_widget_brush(self._config_label, binding.report("success" if valid else "error"))

    @property
    def theme_revision(self) -> int:
        return self._theme_binding.revision

    def _on_edit_config(self):
        """打开配置编辑对话框。"""
        from transbridge.ui.tools.ai_translator._batch_config_dialog import _BatchConfigDialog

        dlg = _BatchConfigDialog(self._llm_config, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._llm_config = dlg.get_config()
            self._update_config_label()

    def get_llm_config(self) -> LLMConfig | None:
        """返回当前 LLM 配置。"""
        return self._llm_config
