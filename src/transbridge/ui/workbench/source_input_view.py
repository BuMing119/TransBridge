"""Source-selection form for the first Workbench step."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True, slots=True)
class SourceInputCallbacks:
    select_slot: Callable[[int], None]
    new_slot: Callable[[], None]
    import_json: Callable[[], None]
    remove_slot: Callable[[], None]
    source_mode_changed: Callable[[], None]
    apply_migration: Callable[[], None]
    start_parse: Callable[[], None]


class SourceInputView(QWidget):
    """Own source form widgets and file-picker interactions."""

    def __init__(self, callbacks: SourceInputCallbacks, parent=None) -> None:
        super().__init__(parent)
        self._callbacks = callbacks
        self._build()

    def _build(self) -> None:
        box = QGroupBox("步骤1：源文件选择")
        outer = QVBoxLayout(self)
        outer.addWidget(box)
        outer.setContentsMargins(0, 0, 0, 0)
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        slot_row = QHBoxLayout()
        slot_row.addWidget(QLabel("当前集合："))
        self.slot_combo = QComboBox()
        self.slot_combo.setMinimumWidth(180)
        self.slot_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.slot_combo.currentIndexChanged.connect(self._callbacks.select_slot)
        slot_row.addWidget(self.slot_combo, stretch=1)
        self.new_button = self._button("＋ 新建", 64, self._callbacks.new_slot)
        slot_row.addWidget(self.new_button)
        self.import_json_button = self._button("导入 JSON", 80, self._callbacks.import_json)
        slot_row.addWidget(self.import_json_button)
        self.remove_button = self._button("✕ 移除", 64, self._callbacks.remove_slot)
        self.remove_button.setEnabled(False)
        slot_row.addWidget(self.remove_button)
        layout.addLayout(slot_row)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("解析来源："))
        self.source_group = QButtonGroup(self)
        self.esp_radio = QRadioButton("ESP 插件（标准）")
        self.eet_only_radio = QRadioButton("EET XML")
        self.esp_radio.setChecked(True)
        self.source_group.addButton(self.esp_radio, 0)
        self.source_group.addButton(self.eet_only_radio, 1)
        source_row.addWidget(self.esp_radio)
        source_row.addWidget(self.eet_only_radio)
        source_row.addStretch()
        layout.addLayout(source_row)
        self.esp_radio.toggled.connect(self._callbacks.source_mode_changed)

        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.esp_row_widget, self.esp_input, self.esp_browse_button, _ = self._file_row(
            "选择 .esp / .esm / .esl 文件…", self.browse_esp, clear=False
        )
        self.form.addRow("插件文件 *", self.esp_row_widget)
        self.eet_row_widget, self.eet_input, self.eet_browse_button, self.eet_clear_button = self._file_row(
            "可选，迁移旧 EET 译文", self.browse_eet
        )
        self.form.addRow("EET XML", self.eet_row_widget)
        self.xt_row_widget, self.xt_input, self.xt_browse_button, self.xt_clear_button = self._file_row(
            "可选，迁移旧 XT 译文", self.browse_xt
        )
        self.form.addRow("XT XML", self.xt_row_widget)
        (
            self.translated_plugin_row_widget,
            self.translated_plugin_input,
            self.translated_plugin_browse_button,
            self.translated_plugin_clear_button,
        ) = self._file_row("可选，从已汉化插件提取译文", self.browse_translated_plugin)
        self.form.addRow("已翻译插件", self.translated_plugin_row_widget)
        self.sst_row_widget, self.sst_input, self.sst_browse_button, self.sst_clear_button = self._file_row(
            "可选，从 SST 文件导入译文", self.browse_sst
        )
        self.form.addRow("SST 文件", self.sst_row_widget)

        self.strings_row_widget = QWidget()
        strings_row = QHBoxLayout(self.strings_row_widget)
        strings_row.setContentsMargins(0, 0, 0, 0)
        self.strings_input = self._read_only_input("可选，从 Strings 文件导入翻译（本地化插件）")
        self.strings_browse_button = self._button("浏览", 60, self.browse_strings_dir)
        self.strings_clear_button = self._clear_button(self.strings_input)
        strings_row.addWidget(self.strings_input)
        strings_row.addWidget(self.strings_browse_button)
        strings_row.addWidget(self.strings_clear_button)
        self.strings_language = QComboBox()
        self.strings_language.addItems([
            "chinese",
            "english",
            "german",
            "french",
            "spanish",
            "italian",
            "japanese",
            "polish",
            "russian",
        ])
        self.strings_language.setFixedWidth(100)
        strings_row.addWidget(self.strings_language)
        self.strings_apply_all = QCheckBox("全部")
        self.strings_apply_all.setToolTip("勾选后将Strings路径应用到所有已加载集合")
        strings_row.addWidget(self.strings_apply_all)
        self.form.addRow("Strings 目录", self.strings_row_widget)

        self.skip_empty = QComboBox()
        self.skip_empty.addItems(["是", "否"])
        self.form.addRow("跳过空串", self.skip_empty)
        layout.addLayout(self.form)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        action_row = QHBoxLayout()
        action_row.addStretch()
        self.apply_migration_button = QPushButton("应用迁移源")
        self.apply_migration_button.setFixedHeight(32)
        self.apply_migration_button.setVisible(False)
        self.apply_migration_button.clicked.connect(self._callbacks.apply_migration)
        action_row.addWidget(self.apply_migration_button)
        self.parse_button = QPushButton("▶ 解析插件")
        self.parse_button.setFixedHeight(32)
        self.parse_button.clicked.connect(self._callbacks.start_parse)
        action_row.addWidget(self.parse_button)
        layout.addLayout(action_row)

    @staticmethod
    def _button(text: str, width: int, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.setFixedWidth(width)
        button.clicked.connect(callback)
        return button

    @staticmethod
    def _read_only_input(placeholder: str) -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setReadOnly(True)
        return field

    @staticmethod
    def _clear_button(field: QLineEdit) -> QPushButton:
        button = QPushButton("✕")
        button.setFixedWidth(28)
        button.setToolTip("清除")
        button.clicked.connect(field.clear)
        return button

    def _file_row(
        self,
        placeholder: str,
        browse: Callable[[], None],
        *,
        clear: bool = True,
    ) -> tuple[QWidget, QLineEdit, QPushButton, QPushButton | None]:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        field = self._read_only_input(placeholder)
        browse_button = self._button("浏览", 60, browse)
        clear_button = self._clear_button(field) if clear else None
        row.addWidget(field)
        row.addWidget(browse_button)
        if clear_button is not None:
            row.addWidget(clear_button)
        return widget, field, browse_button, clear_button

    def browse_esp(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择插件文件（可多选）",
            "",
            "ESP/ESM/ESL 文件 (*.esp *.esm *.esl);;所有文件 (*)",
        )
        if not paths:
            return
        self.esp_paths = paths
        self.esp_input.setText(paths[0] if len(paths) == 1 else f"已选择 {len(paths)} 个文件")
        self.esp_input.setToolTip("\n".join(paths))

    def browse_eet(self) -> None:
        self._browse_file(self.eet_input, "选择 EET XML 文件", "XML 文件 (*.xml);;所有文件 (*)")

    def browse_xt(self) -> None:
        self._browse_file(self.xt_input, "选择 XT XML 文件", "XML 文件 (*.xml);;所有文件 (*)")

    def browse_translated_plugin(self) -> None:
        self._browse_file(
            self.translated_plugin_input,
            "选择已翻译插件文件",
            "ESP/ESM/ESL 文件 (*.esp *.esm *.esl);;所有文件 (*)",
        )

    def browse_sst(self) -> None:
        self._browse_file(self.sst_input, "选择 SST 文件", "SST 文件 (*.sst);;所有文件 (*)")

    def _browse_file(self, field: QLineEdit, title: str, file_filter: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, title, "", file_filter)
        if path:
            field.setText(path)

    def browse_strings_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择 Strings 目录", "")
        if directory:
            self.strings_input.setText(directory)
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Strings 文件",
            "",
            "Strings 文件 (*.strings *.dlstrings *.ilstrings);;所有文件 (*)",
        )
        if path:
            self.strings_input.setText(str(Path(path).parent))
