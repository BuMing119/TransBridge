# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Fixed project creation form with an independently scrolling content area."""

from pathlib import Path

from PyQt6.QtCore import QSignalBlocker, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.accessibility import update_accessible_state
from transbridge.ui.foundation.components import (
    ComponentKind,
    ComponentStyle,
    ElidedLabel,
    SemanticState,
    make_primary_button,
)
from transbridge.ui.foundation.tabler_icons import tabler_icon
from transbridge.ui.operations.export_format_combo import ExportFormatComboBox
from transbridge.ui.workbench.file_source_formats import FILE_SOURCE_FORMATS, TRANSLATION_FILE_FORMATS


class ProjectCreationForm(QWidget):
    """Render draft fields; all filesystem and application work stays in the controller."""

    add_sources_requested = pyqtSignal()
    sources_changed = pyqtSignal(object)
    source_format_changed = pyqtSignal(object)
    project_name_changed = pyqtSignal(str)
    variant_name_changed = pyqtSignal(str)
    skip_empty_changed = pyqtSignal(bool)
    choose_translation_requested = pyqtSignal()
    translation_options_changed = pyqtSignal(object)
    create_requested = pyqtSignal()
    back_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paths = ()
        self._translation_paths = ()
        self._rendering = False
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget(self)
        content.setMaximumWidth(1040)
        outer.addStretch()
        outer.addWidget(content, 1)
        outer.addStretch()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(10)
        header = QHBoxLayout()
        self.back = QPushButton(self)
        self.back.setIcon(tabler_icon(self.back, "arrow-left", 20))
        self.back.setIconSize(QSize(20, 20))
        self.back.setFixedSize(36, 36)
        self.back.setAccessibleName("返回开始中心")
        self.back.setToolTip("返回开始")
        self.back.clicked.connect(self.back_requested)
        title = QLabel("创建本地翻译工程", self)
        font = QFont(title.font())
        font.setPointSizeF(16)
        font.setWeight(QFont.Weight.DemiBold)
        title.setFont(font)
        header.addWidget(self.back)
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setAccessibleName("工程创建选项")
        body = QWidget()
        self.scroll.setWidget(body)
        fields = QVBoxLayout(body)
        fields.setContentsMargins(0, 0, 10, 0)
        fields.setSpacing(8)
        source_bar = QHBoxLayout()
        source_bar.addWidget(QLabel("来源文件"))
        self.source_label = QLabel()
        source_bar.addWidget(self.source_label)
        source_bar.addStretch()
        self.add_sources = QPushButton("添加文件…")
        self.add_sources.clicked.connect(self.add_sources_requested)
        self.remove_sources = QPushButton("移除选中")
        self.remove_sources.clicked.connect(self._remove_selected)
        source_bar.addWidget(self.add_sources)
        source_bar.addWidget(self.remove_sources)
        fields.addLayout(source_bar)
        self.source_list = QListWidget()
        self.source_list.setAccessibleName("已选择的来源文件")
        self.source_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.source_list.setFixedHeight(100)
        self.source_list.itemSelectionChanged.connect(self._sync_remove)
        fields.addWidget(self.source_list)

        form = QFormLayout()
        self.source_format = self._format_choice("来源文件格式")
        self.source_format.currentIndexChanged.connect(
            lambda: self.source_format_changed.emit(self.source_format.currentData()) if not self._rendering else None
        )
        self.name_edit = self._input("本地翻译工程名称")
        self.name_edit.textEdited.connect(self.project_name_changed)
        self.variant_edit = self._input("默认翻译版本名称")
        self.variant_edit.textEdited.connect(self.variant_name_changed)
        self.name_edit.returnPressed.connect(self._submit)
        self.variant_edit.returnPressed.connect(self._submit)
        form.addRow("文件格式", self.source_format)
        form.addRow("工程名称", self.name_edit)
        form.addRow("默认翻译版本", self.variant_edit)
        fields.addLayout(form)

        self.with_translations = QCheckBox("同时导入已有译文（可选）")
        self.with_translations.toggled.connect(self._translation_changed)
        fields.addWidget(self.with_translations)
        self.translation_fields = QWidget()
        self._translation_opacity = QGraphicsOpacityEffect(self.translation_fields)
        self.translation_fields.setGraphicsEffect(self._translation_opacity)
        donor = QFormLayout(self.translation_fields)
        donor.setContentsMargins(20, 0, 0, 0)
        self.translation_path = self._input("已有译文来源")
        self.translation_path.setPlaceholderText("选择已有译文文件（可多选）…")
        self.translation_path.setReadOnly(True)
        self.translation_browse = QPushButton("浏览…")
        self.translation_browse.setAccessibleName("选择已有译文来源")
        self.translation_browse.clicked.connect(self.choose_translation_requested)
        self.translation_clear = QPushButton("清空")
        self.translation_clear.setAccessibleName("清空已选译文来源")
        self.translation_clear.clicked.connect(self._clear_translations)
        path_row = QHBoxLayout()
        path_row.addWidget(self.translation_path, 1)
        path_row.addWidget(self.translation_browse)
        path_row.addWidget(self.translation_clear)
        donor.addRow("译文来源", path_row)
        self.translation_format = self._format_choice("译文来源格式", TRANSLATION_FILE_FORMATS)
        self.translation_format.addItem("Strings 目录", "strings-directory")
        self.translation_format.currentIndexChanged.connect(self._translation_changed)
        donor.addRow("来源格式", self.translation_format)
        self.strings_language = ExportFormatComboBox()
        self.strings_language.setAccessibleName("Strings 语言")
        self.strings_language.setEditable(True)
        self.strings_language.addItems(["chinese", "english", "german", "french", "japanese", "russian"])
        self.strings_language.currentTextChanged.connect(self._translation_changed)
        self.strings_label = QLabel("Strings 语言")
        donor.addRow(self.strings_label, self.strings_language)
        policy = QHBoxLayout()
        self.keep_existing = QRadioButton("保留，只填充空译文")
        self.replace_existing = QRadioButton("用导入的译文替换")
        self.keep_existing.setChecked(True)
        self.replace_existing.toggled.connect(self._translation_changed)
        policy.addWidget(self.keep_existing)
        policy.addWidget(self.replace_existing)
        policy.addStretch()
        donor.addRow("已有译文", policy)
        fields.addWidget(self.translation_fields)
        self.skip_empty = QCheckBox("跳过空原文")
        self.skip_empty.toggled.connect(self.skip_empty_changed)
        fields.addWidget(self.skip_empty)
        fields.addStretch()
        layout.addWidget(self.scroll, 1)

        self.summary = ElidedLabel("", self)
        self.summary.setAccessibleName("工程创建方案摘要")
        layout.addWidget(self.summary)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.setFixedHeight(self.fontMetrics().lineSpacing() * 2 + 8)
        self.error.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.error.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.error.setAccessibleName("工程创建错误")
        layout.addWidget(self.error)
        actions = QHBoxLayout()
        actions.addStretch()
        self.primary = make_primary_button("创建工程", self)
        self.primary.setAccessibleName("创建本地翻译工程并开始翻译")
        self.primary.clicked.connect(self.create_requested)
        actions.addWidget(self.primary)
        layout.addLayout(actions)
        for editor in (
            self.name_edit,
            self.variant_edit,
            self.source_format,
            self.translation_path,
            self.translation_format,
            self.strings_language,
        ):
            ComponentStyle.apply_static(editor, ComponentKind.INPUT)
        for button in (
            self.back,
            self.add_sources,
            self.remove_sources,
            self.translation_browse,
            self.translation_clear,
        ):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)

    @staticmethod
    def _input(name):
        editor = QLineEdit()
        editor.setAccessibleName(name)
        editor.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        editor.textChanged.connect(editor.setToolTip)
        return editor

    @staticmethod
    def _format_choice(name, formats=FILE_SOURCE_FORMATS):
        choice = ExportFormatComboBox()
        choice.setAccessibleName(name)
        for text, value in formats:
            choice.addItem(text, value)
        return choice

    def _remove_selected(self):
        removed = {self.source_list.row(item) for item in self.source_list.selectedItems()}
        self.sources_changed.emit(tuple(path for index, path in enumerate(self._paths) if index not in removed))

    def _sync_remove(self):
        self.remove_sources.setEnabled(self.source_list.isEnabled() and bool(self.source_list.selectedItems()))

    def translation_options(self):
        return dict(
            enabled=self.with_translations.isChecked(),
            paths=self._translation_paths,
            format_hint=self.translation_format.currentData(),
            replace_existing=self.replace_existing.isChecked(),
            strings_lang=self.strings_language.currentText().strip(),
        )

    def _translation_changed(self, *_args):
        if not self._rendering:
            self.translation_options_changed.emit(self.translation_options())

    def _clear_translations(self):
        options = self.translation_options()
        options["paths"] = ()
        self.translation_options_changed.emit(options)

    def _submit(self):
        if self.primary.isEnabled():
            self.primary.click()

    def render(self, state):
        self._rendering = True
        try:
            if state.source_paths != self._paths:
                self._paths = state.source_paths
                self.source_list.clear()
                for path in self._paths:
                    item = QListWidgetItem(Path(path).name, self.source_list)
                    item.setToolTip(path)
                    item.setData(Qt.ItemDataRole.AccessibleTextRole, path)
            self.source_label.setText(f"已选择 {len(self._paths)} 个文件" if self._paths else "无源文件（空工程）")
            for edit, value in (
                (self.name_edit, state.project_name),
                (self.variant_edit, state.default_variant_name),
            ):
                if edit.text() != value:
                    edit.setText(value)
            self._translation_paths = state.translation_paths
            count = len(self._translation_paths)
            display = (
                f"已选择 {count} 个译文文件"
                if count > 1
                else (self._translation_paths[0] if self._translation_paths else "")
            )
            self.translation_path.setText(display)
            self.translation_path.setToolTip("\n".join(self._translation_paths))
            self.translation_path.setAccessibleDescription("\n".join(self._translation_paths))
            self.translation_clear.setEnabled(bool(self._translation_paths))
            self.source_format.setCurrentIndex(self.source_format.findData(state.source_format_hint))
            self.translation_format.setCurrentIndex(self.translation_format.findData(state.translation_format_hint))
            self.with_translations.setChecked(state.import_translations)
            self.replace_existing.setChecked(state.replace_existing)
            self.keep_existing.setChecked(not state.replace_existing)
            self.strings_language.setCurrentText(state.strings_lang)
            with QSignalBlocker(self.skip_empty):
                self.skip_empty.setChecked(bool(dict(state.parse_options).get("skip_empty", False)))
            ready = not state.in_flight
            self.scroll.widget().setEnabled(ready)
            self.back.setEnabled(ready)
            self.translation_fields.setEnabled(state.import_translations)
            self._translation_opacity.setOpacity(1 if state.import_translations else 0.45)
            strings = state.translation_format_hint == "strings-directory"
            self.translation_path.setPlaceholderText("选择 Strings 目录…" if strings else "选择已有译文文件（可多选）…")
            self.strings_language.setEnabled(strings)
            self.strings_label.setEnabled(strings)
            self.skip_empty.setEnabled(state.source_format_hint in {None, "plugin.sse"})
            self._sync_remove()
            self.summary.set_full_text(state.summary)
            diagnostic = f"{state.diagnostic_code}: {state.diagnostic_message}" if state.diagnostic_code else ""
            self.error.setText(diagnostic)
            self.error.setToolTip(diagnostic)
            update_accessible_state(self.error, diagnostic or "当前没有工程创建错误")
            ComponentStyle.apply_state(self.error, SemanticState.ERROR if diagnostic else SemanticState.DEFAULT)
            self.primary.setEnabled(state.can_submit and ready)
        finally:
            self._rendering = False
