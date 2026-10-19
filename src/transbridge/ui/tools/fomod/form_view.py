"""Scrollable common archive inputs with optional file-filter configuration."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.fileops import DEFAULT_PRESET, PRESETS
from transbridge.ui.foundation.accessibility import configure_accessible_widget
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


def _form(group):
    form = QFormLayout(group)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return form


def _path_row(form, title, placeholder, name, pick):
    container = QWidget()
    row = QHBoxLayout(container)
    row.setContentsMargins(0, 0, 0, 0)
    editor = QLineEdit()
    editor.setPlaceholderText(placeholder)
    configure_accessible_widget(editor, name=name)
    button = QPushButton("浏览…")
    ComponentStyle.apply_static(button, ComponentKind.BUTTON)
    button.setAccessibleName(f"选择{name}")
    button.clicked.connect(pick)
    row.addWidget(editor, 1)
    row.addWidget(button)
    label = QLabel(title)
    label.setBuddy(editor)
    form.addRow(label, container)
    return editor


def build_archive_form(panel, profiles) -> QScrollArea:
    """Populate the panel's existing input controls without owning execution."""

    scroll = QScrollArea(panel)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setAccessibleName("安装包输入与输出配置")
    panel._form_body = QWidget(scroll)
    body = QVBoxLayout(panel._form_body)
    body.setContentsMargins(0, 0, 8, 0)

    inputs = QGroupBox("1  选择安装包")
    form = _form(inputs)
    panel._new_edit = _path_row(form, "新版安装包", ".7z / .zip / .rar", "新版 FOMOD 归档", panel._pick_new)
    panel._old_edit = _path_row(form, "旧版译包（可选）", "用于复用已有译文", "旧版中文 FOMOD 归档", panel._pick_old)
    body.addWidget(inputs)

    output = QGroupBox("2  设置翻译与输出")
    form = _form(output)
    panel._lang_combo = QComboBox()
    for profile in profiles:
        panel._lang_combo.addItem(f"{profile.display_name} ({profile.locale})", profile.locale)
    panel._lang_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    panel._lang_combo.setMinimumContentsLength(10)
    form.addRow("目标语言", panel._lang_combo)
    panel._ai_check = QCheckBox("使用 AI 补翻新增词条")
    panel._ai_check.setChecked(True)
    panel._ai_check.setToolTip("关闭后仅复用已有译文和词典，剩余词条保留待翻译。")
    form.addRow("", panel._ai_check)
    panel._fmt_combo = QComboBox()
    panel._fmt_combo.addItem("ZIP", "zip")
    panel._fmt_combo.addItem("7z", "7z")
    form.addRow("输出格式", panel._fmt_combo)
    panel._out_edit = _path_row(form, "输出位置", "选择新版安装包后自动建议新文件名", "FOMOD 输出归档", panel._pick_out)
    body.addWidget(output)

    panel._advanced_toggle = QToolButton()
    ComponentStyle.apply_static(panel._advanced_toggle, ComponentKind.BUTTON)
    panel._advanced_toggle.setText("高级过滤规则")
    panel._advanced_toggle.setCheckable(True)
    panel._advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
    panel._advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    panel._advanced_toggle.setAccessibleName("展开或收起高级过滤规则")
    body.addWidget(panel._advanced_toggle)
    panel._filter_summary = QLabel()
    panel._filter_summary.setWordWrap(True)
    panel._filter_summary.setTextFormat(Qt.TextFormat.PlainText)
    panel._filter_summary.setAccessibleName("当前打包过滤规则")
    body.addWidget(panel._filter_summary)

    panel._advanced_body = QGroupBox("决定哪些文件包含在输出安装包中")
    form = _form(panel._advanced_body)
    panel._preset_combo = QComboBox()
    panel._preset_combo.addItems(list(PRESETS))
    panel._preset_combo.setCurrentText(DEFAULT_PRESET)
    form.addRow("过滤预设", panel._preset_combo)
    panel._keep_ext_edit = QLineEdit()
    panel._keep_ext_edit.setPlaceholderText("逗号分隔，例如 .dds, .wav")
    form.addRow("额外保留", panel._keep_ext_edit)
    panel._strip_ext_edit = QLineEdit()
    panel._strip_ext_edit.setPlaceholderText("逗号分隔，例如 .tmp")
    form.addRow("额外剔除", panel._strip_ext_edit)
    precedence = QLabel("同一扩展名同时出现在保留和剔除列表时，以剔除为准。")
    precedence.setWordWrap(True)
    precedence.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    form.addRow(precedence)
    panel._advanced_body.hide()
    body.addWidget(panel._advanced_body)
    body.addStretch(1)
    scroll.setWidget(panel._form_body)

    for editor in (
        panel._new_edit,
        panel._old_edit,
        panel._out_edit,
        panel._lang_combo,
        panel._fmt_combo,
        panel._preset_combo,
        panel._keep_ext_edit,
        panel._strip_ext_edit,
    ):
        ComponentStyle.apply_static(editor, ComponentKind.INPUT)
    return scroll
