"""Single-source import dialog; parsing and matching run in the owned worker."""

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState, configure_dialog

from ._parse_config_dialog import ParseConfig


@dataclass
class TranslationImportConfig(ParseConfig):
    import_source_path: str | None = None
    import_format_hint: str | None = None
    replace_existing: bool = False


class TranslationImportDialog(QDialog):
    """Select one translation source and the policy for the captured target."""

    def __init__(self, slot, parent=None):
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowTitle("导入译文")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        source = getattr(getattr(slot, "source_snapshot", None), "source", None)
        target = getattr(source, "display_name", None) or getattr(source, "uri", None) or getattr(slot, "label", None)
        self.target_label = QLabel(f"应用到当前文件：{Path(target).name if target else '当前集合'}")
        self.target_label.setTextFormat(Qt.TextFormat.PlainText)
        self.target_label.setWordWrap(True)
        self.target_label.setToolTip(str(target or "当前集合"))
        layout.addWidget(self.target_label)
        target_format = getattr(getattr(slot, "source_snapshot", None), "format_id", None) or getattr(
            slot, "format_id", None
        )
        names = {
            "binary.eet": "ESP-ESM Translator EET",
            "xml.eet": "ESP-ESM Translator XML",
            "xml.xt": "xTranslator XML",
            "sst.ssu8": "xTranslator SST · SSU8",
            "sst.ssu9": "xTranslator SST · SSU9",
            "json.dsd": "DSD JSON",
            "json.paratranz": "ParaTranz JSON",
            "plugin.sse": "Bethesda 插件",
        }
        if target_format in names:
            layout.addWidget(QLabel(names[target_format]))
        layout.addWidget(QLabel("译文文件"))
        row = QHBoxLayout()
        self.source_input = QLineEdit()
        self.source_input.setPlaceholderText("选择已有译文文件…")
        self.source_input.setAccessibleName("译文文件")
        row.addWidget(self.source_input, 1)
        self.browse_button = QPushButton("浏览…")
        self.browse_button.clicked.connect(self._browse)
        row.addWidget(self.browse_button)
        layout.addLayout(row)
        self.format_combo = QComboBox()
        self.format_combo.setAccessibleName("译文文件格式")
        for label, value in (
            ("自动识别文件内容", None),
            ("DSD JSON", "json.dsd"),
            ("ParaTranz JSON", "json.paratranz"),
            ("ESP-ESM Translator EET", "binary.eet"),
            ("ESP-ESM Translator XML", "xml.eet"),
            ("xTranslator XML", "xml.xt"),
            ("xTranslator SST · SSU8", "sst.ssu8"),
            ("xTranslator SST · SSU9", "sst.ssu9"),
            ("已翻译插件", "plugin.sse"),
            ("Strings 目录", "strings-directory"),
        ):
            self.format_combo.addItem(label, value)
        layout.addWidget(self.format_combo)
        self.strings_options = QWidget()
        strings_row = QHBoxLayout(self.strings_options)
        strings_row.setContentsMargins(0, 0, 0, 0)
        strings_row.addWidget(QLabel("Strings 语言"))
        self.strings_language = QComboBox()
        self.strings_language.addItems(["chinese", "english", "german", "french", "japanese", "russian"])
        self.strings_language.setEditable(True)
        strings_row.addWidget(self.strings_language, 1)
        self.strings_apply_all = QCheckBox("应用到全部来源")
        strings_row.addWidget(self.strings_apply_all)
        layout.addWidget(self.strings_options)
        self.strings_options.hide()
        layout.addWidget(QLabel("当前文件已经有译文时"))
        self.keep_existing = QRadioButton("保留，只填充空译文")
        self.keep_existing.setChecked(True)
        self.replace_existing = QRadioButton("用导入的译文替换")
        layout.addWidget(self.keep_existing)
        layout.addWidget(self.replace_existing)
        self.feedback = QLabel()
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        self.feedback.hide()
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.import_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.import_button.setText("导入译文")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.source_input.textChanged.connect(self._clear_feedback)
        self.format_combo.currentIndexChanged.connect(self._format_changed)
        for editor in (self.source_input, self.format_combo, self.strings_language):
            ComponentStyle.apply_static(editor, ComponentKind.INPUT)
        for button in self.findChildren(QPushButton):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        ComponentStyle.apply_state(self.import_button, SemanticState.PRIMARY)

    def _format_changed(self):
        self.strings_options.setVisible(self.format_combo.currentData() == "strings-directory")
        self._clear_feedback()

    def _clear_feedback(self):
        self.feedback.clear()
        self.feedback.hide()

    def _browse(self):
        if self.format_combo.currentData() == "strings-directory":
            path = QFileDialog.getExistingDirectory(self, "选择 Strings 目录", self.source_input.text())
        else:
            path, chosen_filter = QFileDialog.getOpenFileName(
                self,
                "选择译文文件",
                self.source_input.text(),
                "支持的译文文件 (*.xml *.eet *.sst *.json *.esp *.esm *.esl);;"
                "DSD JSON (*.json);;ParaTranz JSON (*.json);;ESP-ESM Translator EET (*.eet);;"
                "ESP-ESM Translator XML (*.xml);;xTranslator XML (*.xml);;xTranslator SST (*.sst);;"
                "已翻译插件 (*.esp *.esm *.esl);;所有文件 (*)",
            )
            if path:
                hint = {"DSD JSON (*.json)": "json.dsd", "ParaTranz JSON (*.json)": "json.paratranz"}.get(chosen_filter)
                if hint is not None:
                    self.format_combo.setCurrentIndex(self.format_combo.findData(hint))
        if path:
            self.source_input.setText(path)

    def _validate_and_accept(self):
        if not self.source_input.text().strip():
            self.feedback.setText("请选择要导入的译文文件。")
            ComponentStyle.apply_state(self.feedback, SemanticState.ERROR)
            self.feedback.show()
            self.source_input.setFocus()
            return
        self.accept()

    def get_config(self) -> TranslationImportConfig:
        return TranslationImportConfig(
            import_source_path=self.source_input.text().strip() or None,
            import_format_hint=self.format_combo.currentData(),
            replace_existing=self.replace_existing.isChecked(),
            strings_lang=self.strings_language.currentText(),
            strings_dir=self.source_input.text().strip()
            if self.format_combo.currentData() == "strings-directory"
            else None,
            strings_apply_all=self.strings_apply_all.isChecked()
            and self.format_combo.currentData() == "strings-directory",
        )

    def prefill_migration_source(self, path: str, kind: str, format_id: str | None = None) -> bool:
        if kind not in {"eet", "xt", "json", "sst", "plugin", "strings-directory"}:
            return False
        hint = "strings-directory" if kind == "strings-directory" else format_id
        index = self.format_combo.findData(hint)
        if index < 0 and hint != "json.transbridge":
            return False
        self.source_input.setText(path)
        self.format_combo.setCurrentIndex(max(index, 0))
        return True
