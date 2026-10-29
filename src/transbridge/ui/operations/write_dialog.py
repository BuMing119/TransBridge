"""Write-plan execution feedback, retained until the user closes the result."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from transbridge.application.preflight.sources import existing_reference
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState

from .execution_dialog import ExecutionPlanDialog


class WritePlanDialog(ExecutionPlanDialog):
    """Keep task results on the GUI thread and require fresh preflight for retries."""

    plan_input_changed = pyqtSignal()

    def __init__(self, plan, _context=None, parent=None) -> None:
        self._rendering = False
        self._path_buttons = []
        self._overwrite = None
        source = getattr(getattr(_context, "active_slot", None), "source_snapshot", None)
        self._source_reference = source.source.uri if source is not None else ""
        super().__init__(plan, parent)
        self.setMinimumWidth(680)
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
        if hasattr(self, "_transfer_summary"):
            self._transfer_summary.hide()
        try:
            super().render_plan(plan)
            for field_id, editor in tuple(self._field_edits.items()):
                row, _role = self._fields.getWidgetPosition(editor)
                if field_id == "overwrite_confirmed":
                    self._overwrite = editor
                    editor.toggled.connect(self._options_changed)
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
                        restore = QPushButton("清空输出位置", container)
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

    def _options_changed(self, *_args) -> None:
        if self._rendering or self._execution_running or self._preflight_running:
            return
        self._preflight = None
        self._confirm_button.setEnabled(False)
        for editor in self._field_edits.values():
            editor.setEnabled(True)
        self._clear_details()
        self._progress.hide()
        self._set_status("选项已变化，请运行预检后再确认。", "not-run", SemanticState.INFO)
        self._transfer_summary.hide()
        self.plan_input_changed.emit()

    def _plugin_path_changed(self, value: str) -> None:
        del value
        self._field_edits["target_path"].clear()
        if self._overwrite is not None:
            self._overwrite.setChecked(False)

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
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "另存为",
            self._field_edits["target_path"].text() or (Path(reference).name if reference else ""),
            "所有文件 (*)",
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
            self._field_edits["target_path"].clear()
        self._clear_overwrite()

    def _clear_overwrite(self, *_args) -> None:
        if self._overwrite is not None:
            self._overwrite.setChecked(False)

    def set_preflight_running(self, running: bool) -> None:
        if running:
            self._transfer_summary.hide()
        super().set_preflight_running(running)

    def render_preflight(self, result) -> None:
        super().render_preflight(result)
        summary = next((item.reason for item in result.checks if item.check_id == "TRANSFER_MATCH_SUMMARY"), "")
        self._transfer_summary.setText(summary)
        self._transfer_summary.setVisible(bool(summary))

    def render_execution(self, activity, result) -> None:
        if self._execution_running and activity.is_terminal:
            self._transfer_summary.hide()
        super().render_execution(activity, result)
