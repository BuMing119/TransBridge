# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Compact scheme checklist and a separate output root in the export dialog."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .write_schemes import PREFIX


def configure_scheme_fields(dialog):
    choices = [editor for key, editor in dialog._field_edits.items() if key.startswith(PREFIX)]
    if not choices:
        return
    group = QGroupBox("术语方案（可多选）", dialog)
    group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    layout = QVBoxLayout(group)
    hint = QLabel("不勾选：使用当前方案。勾选后：每个方案导出到各自文件夹，原译文不变。", group)
    hint.setWordWrap(True)
    hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    layout.addWidget(hint)
    content = QWidget(group)
    checks = QVBoxLayout(content)
    checks.setContentsMargins(0, 0, 0, 0)
    checks.setAlignment(Qt.AlignmentFlag.AlignTop)
    for editor in choices:
        row = dialog._fields.takeRow(editor)
        if row.labelItem is not None:
            label = row.labelItem.widget()
            label.hide()
            label.deleteLater()
        checks.addWidget(editor)
    scroll = QScrollArea(group)
    scroll.setWidgetResizable(True)
    scroll.setWidget(content)
    scroll.setFixedHeight(min(150, max(65, len(choices) * (group.fontMetrics().height() + 12) + 12)))
    layout.addWidget(scroll)
    compare = QPushButton("查看译文差异…", group)
    compare.setToolTip("比较勾选方案的词条译文，并可导出 Excel 工作簿。")
    ComponentStyle.apply_static(compare, ComponentKind.BUTTON)
    compare.clicked.connect(lambda: dialog.scheme_comparison_requested.emit(dialog.edited_values()))
    layout.addWidget(compare)
    dialog._path_buttons.append(compare)
    dialog._fields.insertRow(0, group)

    root = dialog._field_edits["scheme_directory"]
    row_index, _ = dialog._fields.getWidgetPosition(root)
    row = dialog._fields.takeRow(root)
    container = QWidget(dialog)
    path_layout = QHBoxLayout(container)
    path_layout.setContentsMargins(0, 0, 0, 0)
    path_layout.addWidget(root)
    browse = QPushButton("选择目录…", container)
    ComponentStyle.apply_static(browse, ComponentKind.BUTTON)
    path_layout.addWidget(browse)
    dialog._path_buttons.append(browse)
    dialog._fields.insertRow(row_index, row.labelItem.widget(), container)
    root.setPlaceholderText("例如：D:/导出 → 方案 A/插件.esp、方案 B/插件.esp")

    def choose():
        if dialog._execution_running or dialog._preflight_running:
            return
        path = QFileDialog.getExistingDirectory(dialog, "选择多方案输出目录", root.text())
        if path:
            root.setText(path)

    def sync(*_args):
        multiple = any(editor.isChecked() for editor in choices)
        dialog._has_selected_schemes = multiple
        compare.setEnabled(sum(editor.isChecked() for editor in choices) >= 2)
        dialog._fields.setRowVisible(container, multiple)

    def changed(*_args):
        sync()
        dialog._clear_overwrite()
        dialog._options_changed()

    browse.clicked.connect(choose)
    root.textChanged.connect(changed)
    for editor in choices:
        editor.toggled.connect(changed)
    sync()
