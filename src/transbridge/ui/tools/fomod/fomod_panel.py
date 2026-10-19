"""FOMOD 翻译向导面板（QDialog）。

面向产出的使用端配置（过滤规则预设 + 自定义扩展名 / 打包格式 / 目标语言 / AI 开关 / 输出），
翻译机制（来源优先级、键对齐、词典兜底）黑盒，不暴露。
用法：panel = FomodPanel(ctx, parent); panel.exec()
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from transbridge.config.language_profiles import discover_language_profiles
from transbridge.ui.foundation.accessibility import configure_accessible_widget, update_accessible_state
from transbridge.ui.foundation.components import (
    ComponentKind,
    ComponentStyle,
    SemanticState,
    configure_dialog,
    reserve_text_width,
)

from .form_view import build_archive_form


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
            from transbridge.config.llm import LLMConfig
            from transbridge.fomod import FomodPipeline

            pipeline = FomodPipeline(rules=self._rules, llm_config=LLMConfig.load_from_file())
            result = pipeline.run(
                self._new,
                self._out,
                old_archive=self._old or None,
                fmt=self._fmt,
                target_lang=self._lang,
                ai_enabled=self._ai,
            )
            self.done.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class FomodPanel(QDialog):
    def __init__(self, ctx=None, parent=None, *, operation_plan_facade=None):
        super().__init__(parent)
        configure_dialog(self)
        self._ctx = ctx
        self._worker = None
        self._operation_plan_facade = operation_plan_facade
        self._suggested_output = ""
        self._plan_dialog = None
        self._execution_status = "idle"
        self.setWindowTitle("FOMOD 安装包翻译")
        configure_accessible_widget(self, name="FOMOD 安装包翻译", description="配置归档输入、输出和翻译选项")
        self.resize(600, 560)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self._form_scroll = build_archive_form(self, discover_language_profiles())
        layout.addWidget(self._form_scroll, 1)

        self._result_group = QGroupBox("执行结果")
        results = QVBoxLayout(self._result_group)
        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMinimumHeight(64)
        self._result_text.setMaximumHeight(120)
        configure_accessible_widget(self._result_text, name="FOMOD 翻译结果", state_text="尚无结果")
        ComponentStyle.apply_static(self._result_text, ComponentKind.NOTIFICATION)
        results.addWidget(self._result_text)
        self._result_group.hide()
        layout.addWidget(self._result_group)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.hide()
        configure_accessible_widget(self._progress, name="FOMOD 翻译进度", state_text="尚未开始")
        ComponentStyle.apply_static(self._progress, ComponentKind.PROGRESS)
        layout.addWidget(self._progress)
        self._ready_hint = QLabel()
        self._ready_hint.setWordWrap(True)
        self._ready_hint.setAccessibleName("下一步操作")
        layout.addWidget(self._ready_hint)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self._close_btn = QPushButton("关闭")
        ComponentStyle.apply_static(self._close_btn, ComponentKind.BUTTON)
        self._close_btn.clicked.connect(self.close)
        actions.addWidget(self._close_btn)
        facade = self._operation_plan_facade or getattr(self._ctx, "operation_plan_facade", None)
        self._start_label = "检查并继续…" if hasattr(facade, "begin_fomod") else "开始翻译"
        self._run_btn = QPushButton(self._start_label)
        ComponentStyle.apply_static(self._run_btn, ComponentKind.BUTTON)
        ComponentStyle.apply_state(self._run_btn, SemanticState.PRIMARY)
        self._run_btn.setAccessibleName("检查 FOMOD 构建配置" if hasattr(facade, "begin_fomod") else "开始 FOMOD 翻译")
        reserve_text_width(self._run_btn, (self._start_label, "等待确认…", "正在构建…"))
        self._run_btn.clicked.connect(self._run)
        actions.addWidget(self._run_btn)
        layout.addLayout(actions)
        self._advanced_toggle.toggled.connect(self._set_advanced_expanded)
        self._new_edit.textChanged.connect(self._refresh_suggested_output)
        self._fmt_combo.currentIndexChanged.connect(self._refresh_suggested_output)
        for signal in (
            self._new_edit.textChanged,
            self._old_edit.textChanged,
            self._out_edit.textChanged,
            self._keep_ext_edit.textChanged,
            self._strip_ext_edit.textChanged,
            self._preset_combo.currentIndexChanged,
            self._fmt_combo.currentIndexChanged,
            self._lang_combo.currentIndexChanged,
            self._ai_check.toggled,
        ):
            signal.connect(self._inputs_changed)
        self._inputs_changed()

    def _set_advanced_expanded(self, expanded):
        self._advanced_body.setVisible(expanded)
        self._advanced_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self._advanced_toggle.setText("收起高级过滤规则" if expanded else "高级过滤规则")

    def _inputs_changed(self):
        if self._execution_status not in {"idle", "waiting", "running"} and self._plan_dialog is None:
            self._result_group.setTitle("上次执行结果")
            self._execution_status = "idle"
        keep_count, strip_count = (
            len({item.strip().lower().lstrip(".") for item in editor.text().split(",") if item.strip()})
            for editor in (self._keep_ext_edit, self._strip_ext_edit)
        )
        self._filter_summary.setText(
            f"预设：{self._preset_combo.currentText()} · 额外保留 {keep_count} 类 · 额外剔除 {strip_count} 类"
        )
        self._update_action_state()

    def _update_action_state(self):
        busy = self._plan_dialog is not None or self._execution_status in {"waiting", "running"}
        ready = bool(self._new_edit.text().strip() and self._out_edit.text().strip())
        self._form_body.setEnabled(not busy)
        self._run_btn.setEnabled(ready and not busy)
        self._run_btn.setText(
            {"waiting": "等待确认…", "running": "正在构建…"}.get(self._execution_status, self._start_label)
        )
        hint = {
            "waiting": "请在确认窗口检查输入与输出，再开始构建。",
            "running": "正在翻译并生成安装包，进度与结果会显示在这里。",
            "completed": "已完成，输出位置见执行结果。",
            "failed": "构建未完成，请查看原因，调整配置后重试。",
            "partial": "已部分完成，请先查看结果中的未完成原因。",
            "cancelled": "操作已取消，请查看结果后再决定是否重试。",
            "not-run": "已取消确认，尚未开始构建；可以调整配置后继续。",
        }.get(self._execution_status)
        if hint is None:
            hint = (
                "请选择新版安装包。"
                if not self._new_edit.text().strip()
                else "请指定输出位置。"
                if not ready
                else "已填写必需信息。"
            )
            if ready and self._start_label == "检查并继续…":
                hint += " 下一步检查输入与输出，确认后才会开始构建。"
        self._ready_hint.setText(hint)
        self._run_btn.setToolTip(hint)

    def prefill_new_archive(self, path: str) -> None:
        """Populate a reviewed safe-drop path without starting the pipeline."""

        self._new_edit.setText(path)
        self._refresh_suggested_output()

    def _pick_new(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择新版 FOMOD", "", "归档 (*.7z *.zip *.rar)")
        if p:
            self._new_edit.setText(p)
            self._refresh_suggested_output()

    def _pick_old(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择旧版中文成品", "", "归档 (*.7z *.zip *.rar)")
        if p:
            self._old_edit.setText(p)

    def _pick_out(self):
        p, _ = QFileDialog.getSaveFileName(self, "选择输出", "", "归档 (*.zip *.7z)")
        if p:
            self._suggested_output = ""
            self._out_edit.setText(p)

    def _refresh_suggested_output(self) -> None:
        """Suggest a safe sibling artifact so the common path needs no second picker."""

        current = self._out_edit.text().strip()
        if current and current != self._suggested_output:
            return
        source_text = self._new_edit.text().strip()
        if not source_text:
            self._suggested_output = ""
            self._out_edit.clear()
            return
        source = Path(source_text)
        if not source.name or source.name in {".", ".."}:
            self._suggested_output = ""
            self._out_edit.clear()
            return
        output_format = str(self._fmt_combo.currentData() or "zip")
        self._suggested_output = str(source.with_name(f"{source.stem}-translated.{output_format}"))
        self._out_edit.setText(self._suggested_output)

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
        if self._plan_dialog is not None:
            self._plan_dialog.raise_()
            return
        new = self._new_edit.text().strip()
        out = self._out_edit.text().strip()
        old = self._old_edit.text().strip() or None
        if not new or not out:
            QMessageBox.warning(self, "提示", "请先选择新版归档和输出路径")
            return
        fmt = self._fmt_combo.currentData()
        lang = self._lang_combo.currentData()
        ai = self._ai_check.isChecked()
        rules = self._collect_rules()
        facade = self._operation_plan_facade or getattr(self._ctx, "operation_plan_facade", None)
        if facade is not None and hasattr(facade, "begin_fomod"):
            from transbridge.application.fomod import FomodTaskDraft

            config_payload = {
                "target_locale": lang,
                "ai_enabled": ai,
                "preset": self._preset_combo.currentText(),
                "keep": sorted(rules.keep_exts),
                "strip": sorted(rules.strip_exts),
            }
            config_hash = hashlib.sha256(
                json.dumps(config_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            self._show_plan_status("waiting", "等待确认，尚未开始构建。")
            try:
                dialog = facade.begin_fomod(
                    self._ctx,
                    draft=FomodTaskDraft(
                        new,
                        out,
                        lang,
                        config_hash,
                        output_format=fmt,
                        old_archive=old,
                        ai_enabled=ai,
                    ),
                    rules=rules,
                    parent=self,
                )
            except (RuntimeError, TypeError, ValueError) as exc:
                self._on_failed(str(exc))
                return
            self._plan_dialog = dialog
            dialog.execution_status_changed.connect(self._show_plan_status)
            dialog.finished.connect(self._plan_closed)
            return
        self._show_plan_status("running", "正在执行…")
        self._worker = _PipelineWorker(new, old, out, fmt, lang, ai, rules)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _show_plan_status(self, status, text):
        self._execution_status = status
        self._result_group.setTitle("执行结果")
        self._result_group.show()
        self._progress.setVisible(status == "running")
        self._result_text.setPlainText(text)
        self._result_text.setProperty("tbStatusId", status)
        update_accessible_state(self._result_text, text)
        semantic = {
            "completed": SemanticState.SUCCESS,
            "failed": SemanticState.ERROR,
            "partial": SemanticState.WARNING,
            "cancelled": SemanticState.WARNING,
        }.get(status, SemanticState.INFO)
        ComponentStyle.apply_state(self._result_text, semantic)
        self._update_action_state()

    def _plan_closed(self, _result):
        self._plan_dialog = None
        if self._result_text.property("tbStatusId") == "waiting":
            self._show_plan_status("not-run", "已取消确认，尚未开始构建。")
        else:
            self._update_action_state()

    def done(self, result):
        if self._plan_dialog is not None:
            if self._plan_dialog._execution_running:
                return
            self._plan_dialog.close()
        super().done(result)

    def closeEvent(self, event):  # noqa: N802 - Qt override
        if self._plan_dialog is not None and self._plan_dialog._execution_running:
            event.ignore()
        else:
            super().closeEvent(event)

    def _on_done(self, result):
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
        self._show_plan_status("completed", summary)

    def _on_failed(self, msg):
        self._show_plan_status("failed", f"执行失败: {msg}")
