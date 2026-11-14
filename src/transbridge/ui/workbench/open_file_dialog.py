# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Stable file-opening form; content detection and parsing belong to the worker."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.io.open_file_options import OpenFileOptions
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState, configure_dialog
from transbridge.ui.operations.export_format_combo import ExportFormatComboBox
from transbridge.ui.workbench.file_source_formats import (
    FILE_FILTER_HINTS as _FILTER_HINTS,
    FILE_SOURCE_FILTER as _FILE_FILTER,
    FILE_SOURCE_FORMATS as _FORMATS,
    TRANSLATION_FILE_FILTER,
    TRANSLATION_FILE_FORMATS,
)


class OpenFileDialog(QDialog):
    """Keep optional controls in place, enabling them without resizing the window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowTitle("打开翻译文件")
        self.setMinimumWidth(620)
        self._selected_paths: tuple[str, ...] = ()
        self._selection_text = ""
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel("要编辑的文件"))
        self.source_input = self._path_input("选择要编辑的文件（可多选）…", "要编辑的文件")
        self.source_browse = QPushButton("浏览…")
        self.source_browse.setAccessibleName("选择要编辑的文件")
        self.source_browse.clicked.connect(self._browse_sources)
        layout.addLayout(self._file_row(self.source_input, self.source_browse))
        self.source_hint = QLabel("按文件内容识别格式；多文件逐个打开。")
        self.source_hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.source_hint)
        self.format_combo = self._format_choice("文件格式")
        source_form = QFormLayout()
        source_form.addRow("文件格式", self.format_combo)
        layout.addLayout(source_form)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)
        self.with_translations = QCheckBox("同时导入已有译文（可选）")
        layout.addWidget(self.with_translations)
        self.translation_fields = QWidget()
        # Keep the optional form in place and visibly inactive under either theme.
        self._translation_opacity = QGraphicsOpacityEffect(self.translation_fields)
        self.translation_fields.setGraphicsEffect(self._translation_opacity)
        donor_layout = QVBoxLayout(self.translation_fields)
        donor_layout.setContentsMargins(24, 0, 0, 0)
        donor_layout.setSpacing(10)
        donor_layout.addWidget(QLabel("译文来源"))
        self.translation_input = self._path_input("选择已有译文文件…", "译文来源")
        self.translation_browse = QPushButton("浏览…")
        self.translation_browse.setAccessibleName("选择已有译文来源")
        self.translation_browse.clicked.connect(self._browse_translation)
        donor_layout.addLayout(self._file_row(self.translation_input, self.translation_browse))
        self.translation_format_combo = self._format_choice("译文来源格式", TRANSLATION_FILE_FORMATS)
        self.translation_format_combo.addItem("Strings 目录", "strings-directory")
        self.strings_language = ExportFormatComboBox()
        self.strings_language.setAccessibleName("Strings 语言")
        self.strings_language.addItems(["chinese", "english", "german", "french", "japanese", "russian"])
        self.strings_language.setEditable(True)
        donor_form = QFormLayout()
        donor_form.addRow("来源格式", self.translation_format_combo)
        self.strings_label = QLabel("Strings 语言")
        donor_form.addRow(self.strings_label, self.strings_language)
        donor_layout.addLayout(donor_form)
        donor_layout.addWidget(QLabel("文件中已经有译文时"))
        self.keep_existing = QRadioButton("保留，只填充空译文")
        self.replace_existing = QRadioButton("用导入的译文替换")
        self.keep_existing.setChecked(True)
        donor_layout.addWidget(self.keep_existing)
        donor_layout.addWidget(self.replace_existing)
        layout.addWidget(self.translation_fields)

        self.skip_empty = QCheckBox("跳过空原文")
        self.skip_empty.setChecked(True)
        layout.addWidget(self.skip_empty)
        self.feedback = QLabel()
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        self.feedback.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # Reserve two text lines from the start. Validation never moves the footer.
        self.feedback.setFixedHeight(self.fontMetrics().lineSpacing() * 2 + 8)
        self.feedback.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.feedback)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.open_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.open_button.setText("打开文件")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._validate_and_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.with_translations.toggled.connect(self._sync_options)
        self.format_combo.currentIndexChanged.connect(self._sync_options)
        self.translation_format_combo.currentIndexChanged.connect(self._sync_options)
        self.source_input.textChanged.connect(self._clear_feedback)
        self.source_input.textEdited.connect(self._source_edited)
        self.translation_input.textChanged.connect(self._clear_feedback)
        for editor in (
            self.source_input,
            self.format_combo,
            self.translation_input,
            self.translation_format_combo,
            self.strings_language,
        ):
            ComponentStyle.apply_static(editor, ComponentKind.INPUT)
        for button in self.findChildren(QPushButton):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        ComponentStyle.apply_state(self.open_button, SemanticState.PRIMARY)
        self._sync_options()

    @staticmethod
    def _path_input(placeholder, accessible_name):
        editor = QLineEdit()
        editor.setMinimumWidth(0)
        editor.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        editor.setPlaceholderText(placeholder)
        editor.setAccessibleName(accessible_name)
        editor.textChanged.connect(editor.setToolTip)
        return editor

    @staticmethod
    def _file_row(editor, button):
        row = QHBoxLayout()
        row.addWidget(editor, 1)
        row.addWidget(button)
        return row

    @staticmethod
    def _format_choice(name, formats=_FORMATS):
        choice = ExportFormatComboBox()
        choice.setAccessibleName(name)
        for label, value in formats:
            choice.addItem(label, value)
        return choice

    def _sync_options(self, *_args):
        importing = self.with_translations.isChecked()
        self.translation_fields.setEnabled(importing)
        self._translation_opacity.setOpacity(1.0 if importing else 0.45)
        strings = self.translation_format_combo.currentData() == "strings-directory"
        self.strings_language.setEnabled(strings)
        self.strings_label.setEnabled(strings)
        self.skip_empty.setEnabled(self.format_combo.currentData() in {None, "plugin.sse"})
        self._clear_feedback()

    def _source_edited(self, _text):
        self._selected_paths = ()
        self._selection_text = ""

    def _clear_feedback(self, *_args):
        self.feedback.clear()

    def _browse_sources(self):
        paths, selected_filter = QFileDialog.getOpenFileNames(
            self,
            "选择要编辑的文件",
            self.get_config().source_paths[0] if self.get_config().source_paths else "",
            _FILE_FILTER,
        )
        if not paths:
            return
        self._selected_paths = tuple(dict.fromkeys(paths))
        self._selection_text = paths[0] if len(self._selected_paths) == 1 else "；".join(self._selected_paths)
        self.source_input.setText(self._selection_text)
        self.source_input.setCursorPosition(0)
        self._apply_filter_hint(self.format_combo, selected_filter)

    def _browse_translation(self):
        if self.translation_format_combo.currentData() == "strings-directory":
            path = QFileDialog.getExistingDirectory(self, "选择 Strings 目录", self.translation_input.text())
        else:
            path, selected_filter = QFileDialog.getOpenFileName(
                self, "选择已有译文文件", self.translation_input.text(), TRANSLATION_FILE_FILTER
            )
            if path:
                self._apply_filter_hint(self.translation_format_combo, selected_filter)
        if path:
            self.translation_input.setText(path)
            self.translation_input.setCursorPosition(0)

    @staticmethod
    def _apply_filter_hint(combo, selected_filter):
        hint = _FILTER_HINTS.get(selected_filter)
        if hint is not None:
            combo.setCurrentIndex(combo.findData(hint))

    def get_config(self) -> OpenFileOptions:
        text = self.source_input.text().strip()
        paths = (
            self._selected_paths if text == self._selection_text and self._selected_paths else ((text,) if text else ())
        )
        importing = self.with_translations.isChecked()
        return OpenFileOptions(
            source_paths=paths,
            format_hint=self.format_combo.currentData(),
            translation_path=(self.translation_input.text().strip() or None) if importing else None,
            translation_format_hint=self.translation_format_combo.currentData() if importing else None,
            replace_existing=self.replace_existing.isChecked() if importing else False,
            strings_lang=self.strings_language.currentText().strip(),
            skip_empty=self.skip_empty.isChecked(),
        )

    def _validate_and_accept(self):
        config = self.get_config()
        if not config.source_paths:
            self._show_error("请选择要编辑的文件。", self.source_input)
        elif self.with_translations.isChecked() and not config.translation_path:
            self._show_error("请选择已有译文来源，或取消“同时导入已有译文”。", self.translation_input)
        elif config.translation_format_hint == "strings-directory" and not config.strings_lang:
            self._show_error("请选择或输入 Strings 语言。", self.strings_language)
        else:
            self.accept()

    def _show_error(self, message, editor):
        self.feedback.setText(message)
        ComponentStyle.apply_state(self.feedback, SemanticState.ERROR)
        editor.setFocus()
