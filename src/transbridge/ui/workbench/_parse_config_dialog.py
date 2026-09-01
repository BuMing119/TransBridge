"""
解析配置对话框。
从 Step1 提取，用于「文件 → 解析插件…」和「文件 → 应用迁移源…」菜单项。
支持两种模式：parse（完整表单）和 migrate（仅迁移源部分）。
"""

from dataclasses import dataclass, field

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


@dataclass
class ParseConfig:
    esp_paths: list[str] = field(default_factory=list)
    eet_path: str | None = None
    xt_path: str | None = None
    tp_path: str | None = None
    json_path: str | None = None
    json_format_id: str | None = None
    sst_path: str | None = None
    sst_format_id: str | None = None
    strings_dir: str | None = None
    strings_lang: str = "chinese"
    strings_apply_all: bool = False
    skip_empty: bool = True
    source_mode: str = "esp"  # "esp" | "eet"


class ParseConfigDialog(QDialog):
    """解析配置对话框，包含原 Step1 所有表单控件。"""

    def __init__(self, mode: str = "parse", parent=None):
        """
        Args:
            mode: "parse" — 完整表单（解析新插件）
                  "migrate" — 仅迁移源（应用到已有集合）
        """
        super().__init__(parent)
        self._mode = mode
        self._esp_paths: list[str] = []
        self.setWindowTitle("解析插件" if mode == "parse" else "应用迁移源")
        self.setMinimumWidth(520)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # ── 来源模式切换（仅 parse 模式） ──
        if self._mode == "parse":
            source_row = QHBoxLayout()
            source_row.addWidget(QLabel("解析来源："))
            self._src_group = QButtonGroup(self)
            self._rb_esp = QRadioButton("ESP 插件（标准）")
            self._rb_eet_only = QRadioButton("EET XML")
            self._rb_esp.setChecked(True)
            self._src_group.addButton(self._rb_esp, 0)
            self._src_group.addButton(self._rb_eet_only, 1)
            source_row.addWidget(self._rb_esp)
            source_row.addWidget(self._rb_eet_only)
            source_row.addStretch()
            layout.addLayout(source_row)
            self._rb_esp.toggled.connect(self._on_source_mode_changed)

        # ── 插件文件（ESP 模式） ──
        if self._mode == "parse":
            self._esp_row = QWidget()
            esp_row = QHBoxLayout(self._esp_row)
            esp_row.setContentsMargins(0, 0, 0, 0)
            self._esp_input = QLineEdit()
            self._esp_input.setPlaceholderText("选择 .esp / .esm / .esl 文件（可多选）…")
            self._esp_input.setReadOnly(True)
            esp_browse = QPushButton("浏览")
            esp_browse.setFixedWidth(60)
            esp_browse.clicked.connect(self._browse_esp)
            esp_row.addWidget(self._esp_input)
            esp_row.addWidget(esp_browse)
            form.addRow("插件文件 *", self._esp_row)

        # ── EET XML ──
        self._eet_row = QWidget()
        eet_row = QHBoxLayout(self._eet_row)
        eet_row.setContentsMargins(0, 0, 0, 0)
        self._eet_input = QLineEdit()
        placeholder = "可选，迁移旧 EET 译文" if self._mode == "parse" else "选择 EET XML 文件…"
        self._eet_input.setPlaceholderText(placeholder)
        self._eet_input.setReadOnly(True)
        eet_browse = QPushButton("浏览")
        eet_browse.setFixedWidth(60)
        eet_browse.clicked.connect(self._browse_eet)
        eet_clear = QPushButton("✕")
        eet_clear.setFixedWidth(28)
        eet_clear.setToolTip("清除")
        eet_clear.clicked.connect(lambda: self._eet_input.clear())
        eet_row.addWidget(self._eet_input)
        eet_row.addWidget(eet_browse)
        eet_row.addWidget(eet_clear)
        form.addRow("EET XML", self._eet_row)

        # ── XT XML ──
        self._xt_row = QWidget()
        xt_row = QHBoxLayout(self._xt_row)
        xt_row.setContentsMargins(0, 0, 0, 0)
        self._xt_input = QLineEdit()
        self._xt_input.setPlaceholderText("可选，迁移旧 XT 译文")
        self._xt_input.setReadOnly(True)
        xt_browse = QPushButton("浏览")
        xt_browse.setFixedWidth(60)
        xt_browse.clicked.connect(self._browse_xt)
        xt_clear = QPushButton("✕")
        xt_clear.setFixedWidth(28)
        xt_clear.setToolTip("清除")
        xt_clear.clicked.connect(lambda: self._xt_input.clear())
        xt_row.addWidget(self._xt_input)
        xt_row.addWidget(xt_browse)
        xt_row.addWidget(xt_clear)
        form.addRow("XT XML", self._xt_row)

        # ── 已翻译插件 ──
        self._tp_row = QWidget()
        tp_row = QHBoxLayout(self._tp_row)
        tp_row.setContentsMargins(0, 0, 0, 0)
        self._tp_input = QLineEdit()
        self._tp_input.setPlaceholderText("可选，从已汉化插件提取译文")
        self._tp_input.setReadOnly(True)
        tp_browse = QPushButton("浏览")
        tp_browse.setFixedWidth(60)
        tp_browse.clicked.connect(self._browse_translated_plugin)
        tp_clear = QPushButton("✕")
        tp_clear.setFixedWidth(28)
        tp_clear.setToolTip("清除")
        tp_clear.clicked.connect(lambda: self._tp_input.clear())
        tp_row.addWidget(self._tp_input)
        tp_row.addWidget(tp_browse)
        tp_row.addWidget(tp_clear)
        form.addRow("已翻译插件", self._tp_row)

        # JSON / SST 是“导入已有译文”的迁移来源，不是新建来源。
        if self._mode == "migrate":
            self._json_row = QWidget()
            json_row = QHBoxLayout(self._json_row)
            json_row.setContentsMargins(0, 0, 0, 0)
            self._json_input = QLineEdit()
            self._json_input.setPlaceholderText("选择 ParaTranz / DSD / TransBridge JSON…")
            self._json_input.setReadOnly(True)
            json_browse = QPushButton("浏览")
            json_browse.setFixedWidth(60)
            json_browse.clicked.connect(self._browse_json)
            json_clear = QPushButton("✕")
            json_clear.setFixedWidth(28)
            json_clear.setToolTip("清除")
            json_clear.clicked.connect(self._clear_json)
            self._json_format = QComboBox()
            self._json_format.addItem("自动识别", None)
            self._json_format.addItem("ParaTranz", "json.paratranz")
            self._json_format.addItem("DSD", "json.dsd")
            self._json_format.addItem("TransBridge", "json.transbridge")
            self._json_format.setToolTip("空数组等歧义 JSON 需要明确选择格式")
            json_row.addWidget(self._json_input)
            json_row.addWidget(json_browse)
            json_row.addWidget(json_clear)
            json_row.addWidget(self._json_format)
            form.addRow("JSON 译文", self._json_row)

            self._sst_row = QWidget()
            sst_row = QHBoxLayout(self._sst_row)
            sst_row.setContentsMargins(0, 0, 0, 0)
            self._sst_input = QLineEdit()
            self._sst_input.setPlaceholderText("选择 SSU8 / SSU9 SST 文件…")
            self._sst_input.setReadOnly(True)
            sst_browse = QPushButton("浏览")
            sst_browse.setFixedWidth(60)
            sst_browse.clicked.connect(self._browse_sst)
            sst_clear = QPushButton("✕")
            sst_clear.setFixedWidth(28)
            sst_clear.setToolTip("清除")
            sst_clear.clicked.connect(self._clear_sst)
            self._sst_format = QComboBox()
            self._sst_format.addItem("自动识别", None)
            self._sst_format.addItem("SSU8", "sst.ssu8")
            self._sst_format.addItem("SSU9", "sst.ssu9")
            sst_row.addWidget(self._sst_input)
            sst_row.addWidget(sst_browse)
            sst_row.addWidget(sst_clear)
            sst_row.addWidget(self._sst_format)
            form.addRow("SST 译文", self._sst_row)

        # ── Strings 目录 ──
        self._strings_row = QWidget()
        strings_row = QHBoxLayout(self._strings_row)
        strings_row.setContentsMargins(0, 0, 0, 0)
        self._strings_input = QLineEdit()
        self._strings_input.setPlaceholderText("可选，从 Strings 文件导入翻译（本地化插件）")
        self._strings_input.setReadOnly(True)
        strings_browse = QPushButton("浏览")
        strings_browse.setFixedWidth(60)
        strings_browse.clicked.connect(self._browse_strings_dir)
        strings_clear = QPushButton("✕")
        strings_clear.setFixedWidth(28)
        strings_clear.setToolTip("清除")
        strings_clear.clicked.connect(lambda: self._strings_input.clear())
        strings_row.addWidget(self._strings_input)
        strings_row.addWidget(strings_browse)
        strings_row.addWidget(strings_clear)

        self._strings_lang = QComboBox()
        self._strings_lang.addItems([
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
        self._strings_lang.setFixedWidth(100)
        strings_row.addWidget(self._strings_lang)

        self._strings_apply_all = QCheckBox("全部")
        self._strings_apply_all.setToolTip("勾选后将 Strings 路径应用到所有已加载集合")
        strings_row.addWidget(self._strings_apply_all)

        form.addRow("Strings 目录", self._strings_row)

        # ── 跳过空串 ──
        self._skip_empty = QComboBox()
        self._skip_empty.addItems(["是", "否"])
        form.addRow("跳过空串", self._skip_empty)

        layout.addLayout(form)

        # ── 分隔线 ──
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        # ── 按钮 ──
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        label = "开始解析" if self._mode == "parse" else "应用迁移"
        self._ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText(label)
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self._validate_and_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        # 迁移模式下隐藏 EET 清除按钮（必填）
        if self._mode == "migrate":
            self._eet_input.setPlaceholderText("选择 EET XML 文件…")

    def _on_source_mode_changed(self):
        esp_mode = self._rb_esp.isChecked()
        if hasattr(self, "_esp_row"):
            self._esp_row.setVisible(esp_mode)

    # ── File browsers ──────────────────────────────────────────

    def _browse_esp(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择插件文件（可多选）", "", "ESP/ESM/ESL 文件 (*.esp *.esm *.esl);;所有文件 (*)"
        )
        if paths:
            self._esp_paths = paths
            if len(paths) == 1:
                self._esp_input.setText(paths[0])
            else:
                self._esp_input.setText(f"已选择 {len(paths)} 个文件")
            self._esp_input.setToolTip("\n".join(paths))

    def _browse_eet(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 EET XML 文件", "", "XML 文件 (*.xml);;所有文件 (*)")
        if path:
            self._eet_input.setText(path)

    def _browse_xt(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 XT XML 文件", "", "XML 文件 (*.xml);;所有文件 (*)")
        if path:
            self._xt_input.setText(path)

    def _browse_translated_plugin(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择已翻译插件文件", "", "ESP/ESM/ESL 文件 (*.esp *.esm *.esl);;所有文件 (*)"
        )
        if path:
            self._tp_input.setText(path)

    def _browse_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 JSON 译文文件", "", "JSON 文件 (*.json);;所有文件 (*)")
        if path:
            self._json_input.setText(path)

    def _browse_sst(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 SST 译文文件", "", "SST 文件 (*.sst);;所有文件 (*)")
        if path:
            self._sst_input.setText(path)

    def _clear_json(self):
        self._json_input.clear()
        self._json_format.setCurrentIndex(0)

    def _clear_sst(self):
        self._sst_input.clear()
        self._sst_format.setCurrentIndex(0)

    def _browse_strings_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择 Strings 目录", "")
        if dir_path:
            self._strings_input.setText(dir_path)

    # ── Validation ─────────────────────────────────────────────

    def _validate_and_accept(self):
        if self._mode == "parse":
            is_eet = hasattr(self, "_rb_eet_only") and self._rb_eet_only.isChecked()
            if is_eet:
                if not self._eet_input.text().strip():
                    return  # TODO: show error
            else:
                if not self._esp_paths and not self._esp_input.text().strip():
                    return
        self.accept()

    # ── Public API ─────────────────────────────────────────────

    def get_config(self) -> ParseConfig:
        config = ParseConfig()
        if self._mode == "parse":
            config.esp_paths = list(self._esp_paths) if self._esp_paths else []
            if not config.esp_paths and hasattr(self, "_esp_input"):
                txt = self._esp_input.text().strip()
                if txt and not txt.startswith("已选择"):
                    config.esp_paths = [txt]
            is_eet = hasattr(self, "_rb_eet_only") and self._rb_eet_only.isChecked()
            config.source_mode = "eet" if is_eet else "esp"
        config.eet_path = self._eet_input.text().strip() or None
        config.xt_path = self._xt_input.text().strip() or None
        config.tp_path = self._tp_input.text().strip() or None
        if hasattr(self, "_json_input"):
            config.json_path = self._json_input.text().strip() or None
            config.json_format_id = self._json_format.currentData() if config.json_path else None
        if hasattr(self, "_sst_input"):
            config.sst_path = self._sst_input.text().strip() or None
            config.sst_format_id = self._sst_format.currentData() if config.sst_path else None
        config.strings_dir = self._strings_input.text().strip() or None
        config.strings_lang = self._strings_lang.currentText()
        config.strings_apply_all = self._strings_apply_all.isChecked()
        config.skip_empty = self._skip_empty.currentText() == "是"
        return config

    def prefill_migration_source(self, path: str, kind: str, format_id: str | None = None) -> bool:
        """Prefill one reviewed drop without executing the migration."""

        if self._mode != "migrate":
            return False
        if kind == "eet":
            self._eet_input.setText(path)
        elif kind == "xt":
            self._xt_input.setText(path)
        elif kind == "plugin":
            self._tp_input.setText(path)
        elif kind == "json":
            self._json_input.setText(path)
            self._select_format(self._json_format, format_id)
        elif kind == "sst":
            self._sst_input.setText(path)
            self._select_format(self._sst_format, format_id)
        elif kind == "strings-directory":
            self._strings_input.setText(path)
        else:
            return False
        return True

    @staticmethod
    def _select_format(combo: QComboBox, format_id: str | None) -> None:
        if format_id is None:
            combo.setCurrentIndex(0)
            return
        index = combo.findData(format_id)
        combo.setCurrentIndex(index if index >= 0 else 0)
