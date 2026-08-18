"""FOMOD 翻译向导面板（QDialog）。

面向产出的使用端配置（过滤规则预设 + 自定义扩展名 / 打包格式 / 目标语言 / AI 开关 / 输出），
翻译机制（来源优先级、键对齐、词典兜底）黑盒，不暴露。
用法：panel = FomodPanel(ctx, parent); panel.exec()
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QTextEdit, QProgressBar, QMessageBox, QComboBox, QGroupBox, QCheckBox,
)

from transbridge.fileops import PRESETS, DEFAULT_PRESET


class _PipelineWorker(QThread):
    """后台执行 FomodPipeline.run。"""
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, new_archive, old_archive, output_archive, fmt, target_lang, ai_enabled, rules):
        super().__init__()
        self._new = new_archive
        self._old = old_archive
        self._out = output_archive
        self._fmt = fmt
        self._lang = target_lang
        self._ai = ai_enabled
        self._rules = rules

    def run(self):
        try:
            from transbridge.fomod import FomodPipeline
            from transbridge.config.llm import LLMConfig
            pipeline = FomodPipeline(rules=self._rules, llm_config=LLMConfig.load_from_file())
            result = pipeline.run(
                self._new, self._out,
                old_archive=self._old or None,
                fmt=self._fmt,
                target_lang=self._lang,
                ai_enabled=self._ai,
            )
            self.done.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class FomodPanel(QDialog):
    def __init__(self, ctx=None, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._worker = None
        self.setWindowTitle("FOMOD 安装包翻译")
        self.resize(600, 560)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # 第 1 步：选文件
        file_group = QGroupBox("第 1 步：选择文件")
        fg = QVBoxLayout(file_group)
        self._new_edit = QLineEdit()
        self._new_edit.setPlaceholderText("新版 FOMOD 归档（.7z/.zip/.rar）")
        new_row = QHBoxLayout()
        new_row.addWidget(self._new_edit)
        new_btn = QPushButton("选择新版")
        new_btn.clicked.connect(self._pick_new)
        new_row.addWidget(new_btn)
        fg.addLayout(new_row)

        self._old_edit = QLineEdit()
        self._old_edit.setPlaceholderText("旧版中文成品（可选，有则自动复用翻译）")
        old_row = QHBoxLayout()
        old_row.addWidget(self._old_edit)
        old_btn = QPushButton("选择旧版")
        old_btn.clicked.connect(self._pick_old)
        old_row.addWidget(old_btn)
        fg.addLayout(old_row)
        layout.addWidget(file_group)

        # 第 2 步：产出配置（使用端，机制黑盒）
        cfg_group = QGroupBox("第 2 步：产出配置")
        cg = QVBoxLayout(cfg_group)

        # 过滤规则预设
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("过滤预设:"))
        self._preset_combo = QComboBox()
        self._preset_combo.addItems(list(PRESETS.keys()))
        self._preset_combo.setCurrentText(DEFAULT_PRESET)
        filter_row.addWidget(self._preset_combo)
        cg.addLayout(filter_row)

        # 自定义扩展名（白名单/黑名单）
        ext_row = QHBoxLayout()
        ext_row.addWidget(QLabel("自定义保留:"))
        self._keep_ext_edit = QLineEdit()
        self._keep_ext_edit.setPlaceholderText("额外保留扩展名，逗号分隔，如 .dds")
        ext_row.addWidget(self._keep_ext_edit)
        cg.addLayout(ext_row)
        ext_row2 = QHBoxLayout()
        ext_row2.addWidget(QLabel("自定义剔除:"))
        self._strip_ext_edit = QLineEdit()
        self._strip_ext_edit.setPlaceholderText("额外剔除扩展名，逗号分隔")
        ext_row2.addWidget(self._strip_ext_edit)
        cg.addLayout(ext_row2)

        # 打包格式 + 目标语言
        opts_row = QHBoxLayout()
        opts_row.addWidget(QLabel("打包格式:"))
        self._fmt_combo = QComboBox()
        self._fmt_combo.addItem("zip", "zip")
        self._fmt_combo.addItem("7z", "7z")
        opts_row.addWidget(self._fmt_combo)
        opts_row.addSpacing(16)
        opts_row.addWidget(QLabel("目标语言:"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItem("中文", "zh_CN")
        self._lang_combo.addItem("繁体中文", "zh_TW")
        self._lang_combo.addItem("英文", "en")
        self._lang_combo.addItem("日文", "ja")
        opts_row.addWidget(self._lang_combo)
        cg.addLayout(opts_row)

        # AI 补翻译开关
        self._ai_check = QCheckBox("AI 补翻译新增词条")
        self._ai_check.setChecked(True)
        self._ai_check.setToolTip("关闭后仅自动复用旧翻译/词典，剩余词条保留待翻译")
        cg.addWidget(self._ai_check)

        # 输出路径
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("输出中文安装包路径")
        out_row = QHBoxLayout()
        out_row.addWidget(self._out_edit)
        out_btn = QPushButton("选择输出")
        out_btn.clicked.connect(self._pick_out)
        out_row.addWidget(out_btn)
        cg.addLayout(out_row)
        layout.addWidget(cfg_group)

        # 第 3 步：执行 + 进度
        run_group = QGroupBox("第 3 步：执行")
        rg = QVBoxLayout(run_group)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        rg.addWidget(self._progress)
        self._run_btn = QPushButton("开始翻译")
        self._run_btn.clicked.connect(self._run)
        rg.addWidget(self._run_btn)
        layout.addWidget(run_group)

        # 结果摘要
        result_group = QGroupBox("结果")
        resg = QVBoxLayout(result_group)
        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setPlaceholderText("结果摘要将显示在这里")
        resg.addWidget(self._result_text)
        layout.addWidget(result_group)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

    def _pick_new(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择新版 FOMOD", "", "归档 (*.7z *.zip *.rar)")
        if p:
            self._new_edit.setText(p)

    def _pick_old(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择旧版中文成品", "", "归档 (*.7z *.zip *.rar)")
        if p:
            self._old_edit.setText(p)

    def _pick_out(self):
        p, _ = QFileDialog.getSaveFileName(self, "选择输出", "", "归档 (*.zip *.7z)")
        if p:
            self._out_edit.setText(p)

    def _collect_rules(self):
        """从 GUI 收集 FilterRules：预设套 + 自定义扩展名。"""
        from transbridge.fileops import FilterRules
        rules = FilterRules.from_preset(self._preset_combo.currentText())
        # 自定义保留
        keep_txt = self._keep_ext_edit.text().strip()
        if keep_txt:
            for ext in keep_txt.split(","):
                ext = ext.strip().lower()
                if ext and not ext.startswith("."):
                    ext = "." + ext
                if ext:
                    rules.keep_exts.add(ext)
                    rules.strip_exts.discard(ext)
        # 自定义剔除
        strip_txt = self._strip_ext_edit.text().strip()
        if strip_txt:
            for ext in strip_txt.split(","):
                ext = ext.strip().lower()
                if ext and not ext.startswith("."):
                    ext = "." + ext
                if ext:
                    rules.strip_exts.add(ext)
                    rules.keep_exts.discard(ext)
        return rules

    def _run(self):
        new = self._new_edit.text().strip()
        out = self._out_edit.text().strip()
        old = self._old_edit.text().strip() or None
        if not new or not out:
            QMessageBox.warning(self, "提示", "请先选择新版归档和输出路径")
            return
        self._run_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._result_text.clear()
        self._result_text.append("正在执行...")
        fmt = self._fmt_combo.currentData()
        lang = self._lang_combo.currentData()
        ai = self._ai_check.isChecked()
        rules = self._collect_rules()
        self._worker = _PipelineWorker(new, old, out, fmt, lang, ai, rules)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_done(self, result):
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        d = result.to_dict() if hasattr(result, "to_dict") else result
        summary = (
            "翻译完成！\n\n"
            f"解包文件数: {d.get('extracted_count', 0)}\n"
            f"自动继承旧译: {d.get('inherited', 0)}\n"
            f"词典命中: {d.get('dict_applied', 0)}\n"
            f"AI 翻译: {d.get('ai_translated', 0)}\n"
            f"保留文件: {d.get('kept_count', 0)} / 剔除: {d.get('stripped_count', 0)}\n"
            f"输出: {d.get('archive_path', '')}\n"
        )
        self._result_text.setPlainText(summary)

    def _on_failed(self, msg):
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        self._result_text.setPlainText(f"执行失败: {msg}")