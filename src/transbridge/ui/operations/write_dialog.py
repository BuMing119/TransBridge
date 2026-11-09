"""Write-plan execution feedback, retained until the user closes the result."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from transbridge.application.preflight.sources import existing_reference
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState

from .execution_dialog import ExecutionPlanDialog
from .export_format_combo import ExportFormatComboBox
from .preflight_view import PreflightCheckStatus
from .write_json_export import FORMAT_NAMES


class WritePlanDialog(ExecutionPlanDialog):
    """Keep task results on the GUI thread and require fresh preflight for retries."""

    plan_input_changed = pyqtSignal()
    operation_label = "导出"
    show_transfer_counts = False
    choice_editor_type = ExportFormatComboBox

    def __init__(self, plan, _context=None, parent=None) -> None:
        self._rendering = False
        self._path_buttons = []
        self._overwrite = None
        self._save_original_button = None
        self._automatic_export = False
        source = getattr(getattr(_context, "active_slot", None), "source_snapshot", None)
        self._source_reference = source.source.uri if source is not None else ""
        self._source_format = str(getattr(source, "format_id", ""))
        super().__init__(plan, parent)
        self.setWindowTitle("导出文件")
        self.setMinimumWidth(580)
        self._fields_group.setTitle("导出选项")
        self._source_label = QLabel(self)
        self._source_label.setWordWrap(True)
        self._source_label.setTextFormat(Qt.TextFormat.PlainText)
        source_name = (
            source.source.display_name or Path(self._source_reference).name if source is not None else "当前文件"
        )
        self._source_label.setText(
            f"导出所选来源：{len(getattr(_context, 'slots', {}))} 个文件"
            if plan.is_batch
            else f"当前文件：{source_name}\n{FORMAT_NAMES.get(self._source_format, self._source_format)}"
        )
        self.layout().insertWidget(0, self._source_label)
        self._preflight_button.hide()
        self._edit_button.hide()
        self._confirm_button.setText("导出文件")
        self._confirm_button.setAccessibleName("导出文件")
        self._confirm_button.setEnabled(True)
        ComponentStyle.apply_state(self._confirm_button, SemanticState.PRIMARY)
        footer = self.layout().itemAt(self.layout().count() - 1).layout()
        footer.removeWidget(self._confirm_button)
        buttons = self.findChild(QDialogButtonBox)
        buttons.addButton(self._confirm_button, QDialogButtonBox.ButtonRole.ActionRole)
        self._summary_scroll.hide()
        self._checks_scroll.hide()
        self._transfer_summary = QLabel(self)
        self._transfer_summary.setWordWrap(True)
        self._transfer_summary.setTextFormat(Qt.TextFormat.PlainText)
        self._transfer_summary.setAccessibleName("套用匹配统计")
        ComponentStyle.apply_static(self._transfer_summary, ComponentKind.NOTIFICATION)
        ComponentStyle.apply_state(self._transfer_summary, SemanticState.INFO)
        self.layout().insertWidget(self.layout().indexOf(self._checks_scroll), self._transfer_summary)
        self._transfer_summary.hide()

    def render_plan(self, plan) -> None:
        self._rendering = True
        self._path_buttons = []
        self._overwrite = None
        self._save_original_button = None
        if hasattr(self, "_transfer_summary"):
            self._transfer_summary.hide()
        try:
            super().render_plan(plan)
            self._summary_scroll.hide()
            self._checks_scroll.hide()
            self._confirm_button.setEnabled(True)
            for field_id, editor in tuple(self._field_edits.items()):
                row, _role = self._fields.getWidgetPosition(editor)
                if field_id == "overwrite_confirmed":
                    self._overwrite = editor
                    self._fields.setRowVisible(editor, False)
                    editor.toggled.connect(self._options_changed)
                elif field_id == "output_format":
                    editor.currentIndexChanged.connect(self._output_format_changed)
                elif field_id == "pack_bsa":
                    editor.setText("将 STRINGS 打包为 BSA（默认不勾选）")
                    editor.setToolTip("仅打包本次本地化字符串资源；不勾选时生成散装文件。")
                    editor.toggled.connect(self._clear_overwrite)
                    editor.toggled.connect(self._options_changed)
                elif field_id in {"plugin_path", "target_path", "output_directory"}:
                    label = self._fields.labelForField(editor)
                    self._fields.takeRow(row)
                    container = QWidget(self)
                    layout = QHBoxLayout(container)
                    layout.setContentsMargins(0, 0, 0, 0)
                    layout.addWidget(editor, 1)
                    browse = QPushButton("浏览…" if field_id == "plugin_path" else "另存为…", container)
                    browse.setAccessibleName(
                        "选择要套用的插件" if field_id == "plugin_path" else "批量另存为" if plan.is_batch else "另存为"
                    )
                    ComponentStyle.apply_static(browse, ComponentKind.BUTTON)
                    browse.clicked.connect(
                        self._browse_plugin
                        if field_id == "plugin_path"
                        else self._browse_directory
                        if field_id == "output_directory"
                        else self._browse_output
                    )
                    layout.addWidget(browse)
                    self._path_buttons.append(browse)
                    if field_id == "plugin_path":
                        editor.setPlaceholderText("留空则导出当前文件；选择 ESP / ESM / ESL 套用当前译文")
                        clear = QPushButton("使用当前文件", container)
                        ComponentStyle.apply_static(clear, ComponentKind.BUTTON)
                        clear.clicked.connect(lambda: self._select_plugin(""))
                        layout.addWidget(clear)
                        self._path_buttons.append(clear)
                        editor.textEdited.connect(self._plugin_path_changed)
                    else:
                        restore = QPushButton("清空输出位置" if plan.is_batch else "保存回原文件", container)
                        restore.setAccessibleName(restore.text())
                        if not plan.is_batch:
                            self._save_original_button = restore
                        ComponentStyle.apply_static(restore, ComponentKind.BUTTON)
                        restore.clicked.connect(self._restore_output)
                        layout.addWidget(restore)
                        self._path_buttons.append(restore)
                        if field_id == "output_directory":
                            editor.setPlaceholderText("选择目录，将多个文件按原名另存到一起")
                        else:
                            editor.setPlaceholderText("请选择输出位置")
                        editor.textChanged.connect(self._clear_overwrite)
                    editor.textChanged.connect(self._options_changed)
                    self._fields.insertRow(row, label, container)
        finally:
            self._rendering = False
        self._sync_save_original()

    def _options_changed(self, *_args) -> None:
        if self._rendering or self._execution_running or self._preflight_running:
            return
        self._preflight = None
        self._automatic_export = False
        self._confirm_button.setEnabled(True)
        for editor in self._field_edits.values():
            editor.setEnabled(True)
        self._clear_details()
        self._progress.hide()
        self._checks_scroll.hide()
        self._transfer_summary.hide()
        self._sync_save_original()
        self.plan_input_changed.emit()

    def _plugin_path_changed(self, value: str) -> None:
        del value
        self._field_edits["target_path"].clear()
        if self._overwrite is not None:
            self._overwrite.setChecked(False)

    def _output_format_changed(self, *_args) -> None:
        if self._rendering:
            return
        for field in ("target_path", "output_directory"):
            if field in self._field_edits:
                self._field_edits[field].clear()
        plugin = self._field_edits.get("plugin_path")
        if plugin is not None:
            plugin.clear()
        self._clear_overwrite()
        self._options_changed()

    def _select_plugin(self, path: str) -> None:
        if self._execution_running or self._preflight_running:
            return
        self._field_edits["plugin_path"].setText(path)
        self._plugin_path_changed(path)
        self._options_changed()

    def _browse_plugin(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择要套用当前译文的插件",
            self._field_edits["plugin_path"].text(),
            "游戏插件 (*.esp *.esm *.esl);;所有文件 (*)",
        )
        if path:
            self._select_plugin(path)

    def _browse_output(self) -> None:
        if self._execution_running or self._preflight_running:
            return
        plugin = self._field_edits.get("plugin_path")
        reference = existing_reference(
            plugin.text().strip() if plugin is not None and plugin.text().strip() else self._source_reference
        )
        format_editor = self._field_edits.get("output_format")
        output_format = format_editor.currentData() if format_editor is not None else self._source_format
        json_output = output_format in {"json.paratranz", "json.dsd"}
        sst_output = (
            format_editor is not None and format_editor.currentData() == "sst.ssu9"
        ) or self._source_reference.lower().endswith(".sst")
        binary_eet_output = output_format == "binary.eet"
        suggested = (
            Path(reference).with_suffix(".json").name
            if json_output and reference
            else Path(reference).with_suffix(".sst").name
            if sst_output and reference
            else Path(reference).with_suffix(".eet").name
            if binary_eet_output and reference
            else Path(reference).name
            if reference
            else ""
        )
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "另存为",
            self._field_edits["target_path"].text() or suggested,
            f"{'DSD JSON' if output_format == 'json.dsd' else 'ParaTranz JSON'} (*.json);;所有文件 (*)"
            if json_output
            else "xTranslator SST (*.sst)"
            if sst_output
            else "ESP-ESM Translator EET (*.eet)"
            if binary_eet_output
            else "ESP-ESM Translator XML (*.xml);;所有文件 (*)"
            if output_format == "xml.eet"
            else "xTranslator XML (*.xml);;所有文件 (*)"
            if output_format == "xml.xt"
            else "所有文件 (*)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if path:
            self._field_edits["target_path"].setText(path)
            if self._overwrite is not None:
                self._overwrite.setChecked(False)

    def _browse_directory(self) -> None:
        if self._execution_running or self._preflight_running:
            return
        path = QFileDialog.getExistingDirectory(
            self, "批量另存为：选择输出目录", self._field_edits["output_directory"].text()
        )
        if path:
            self._field_edits["output_directory"].setText(path)

    def _restore_output(self) -> None:
        if self._execution_running or self._preflight_running:
            return
        if self._plan.is_batch:
            self._field_edits["output_directory"].clear()
        else:
            reference = self._original_output_path()
            if not reference:
                self._sync_save_original()
                return
            self._field_edits["target_path"].setText(reference)
        self._clear_overwrite()

    def _original_output_path(self) -> str:
        choice = self._field_edits.get("output_format")
        output_format = choice.currentData() if choice is not None else self._source_format
        # Even the two JSON schemas must not overwrite one another as "original".
        if output_format != self._source_format:
            return ""
        plugin = self._field_edits.get("plugin_path")
        reference = plugin.text().strip() if plugin is not None and plugin.text().strip() else self._source_reference
        return existing_reference(reference)

    def _sync_save_original(self) -> None:
        if self._save_original_button is not None:
            available = bool(self._original_output_path())
            self._save_original_button.setEnabled(available)
            self._save_original_button.setToolTip(
                "使用原文件路径；点击导出后确认覆盖，覆盖前自动备份。"
                if available
                else "原文件不可用或输出格式已改变，请另存为。"
            )

    def _clear_overwrite(self, *_args) -> None:
        if self._overwrite is not None:
            self._overwrite.setChecked(False)

    def set_preflight_running(self, running: bool) -> None:
        if running:
            self._transfer_summary.hide()
        super().set_preflight_running(running)
        if not running and self._automatic_export:
            # Worker completion releases the preflight lease. Submitting from
            # render_preflight would race that completion and its cleanup.
            QTimer.singleShot(0, self._finish_automatic_export)

    def render_preflight(self, result) -> None:
        super().render_preflight(result)
        self._transfer_summary.hide()
        if not result.ready:
            messages = tuple(
                dict.fromkeys(
                    item.reason or item.label
                    for item in result.checks
                    if item.status is PreflightCheckStatus.BLOCKED
                    and item.check_id not in {"TRANSFER_MATCH_SUMMARY", "LOCKED_HIDDEN"}
                )
            )
            self._checks.setText("\n".join(messages) or "无法导出，请检查所选文件和输出位置。")
        self._checks_scroll.setVisible(not result.ready)

    def _sync_controls(self):
        super()._sync_controls()
        self._confirm_button.setEnabled(not self._execution_running and not self._preflight_running)
        self._preflight_button.hide()
        self._edit_button.hide()

    def _confirm(self):
        if self._execution_running or self._preflight_running:
            return
        if self._preflight is not None and self._preflight.ready:
            # A still-current result may also be supplied by an existing host.
            # Every field edit invalidates it; submit rechecks target revisions.
            super()._confirm()
            return
        self._automatic_export = True
        self._request_preflight()

    def _finish_automatic_export(self):
        if not self._automatic_export or self._execution_running or self._preflight_running:
            return
        self._automatic_export = False
        result = self._preflight
        if result is None:
            return
        overwrite = any(
            "OVERWRITE" in item.check_id and item.status is not PreflightCheckStatus.PASSED for item in result.checks
        )
        if overwrite and self._overwrite is not None and not self._overwrite.isChecked():
            answer = QMessageBox.question(
                self,
                "文件已存在",
                "输出位置存在同名文件。是否覆盖？覆盖前会创建备份。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._overwrite.setChecked(True)
                self._confirm()
            return
        if result.ready:
            super()._confirm()
        else:
            for editor in self._field_edits.values():
                editor.setEnabled(True)

    def render_preflight_error(self, message):
        self._automatic_export = False
        super().render_preflight_error(message)
        self._checks_scroll.show()
        self._sync_controls()

    def _set_status(self, text, status, semantic):
        text = text.replace("如需重试，请重新运行预检", "如需重试，请再次点击导出文件")
        super()._set_status(text, status, semantic)
        self._checks_scroll.setVisible(status not in {"checking", "not-run"})

    def render_execution(self, activity, result) -> None:
        if self._execution_running and activity.is_terminal:
            self._transfer_summary.hide()
        super().render_execution(activity, result)
