"""Read-only presentation of the selection captured when an input was accepted."""

from dataclasses import dataclass
import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .theme_support import BUTTON_STRUCTURE_STYLE, QUIET_BUTTON_STYLE


def capture_display_context(context) -> dict:
    name = getattr(context, "project_name", "")
    path = getattr(context, "esp_path", "")
    if not name and isinstance(path, (str, Path)) and path:
        name = Path(path).name
    return {"project_name": str(name or "")}


@dataclass(frozen=True)
class SubmissionScope:
    summary: str
    details: str


def scope_from_input(entry: dict) -> SubmissionScope:
    selection = entry.get("selection") or {}
    context = entry.get("context") or {}
    display = selection.get("display_context") or {}
    project = display.get("project_name") or context.get("project_id") or "未记录项目"
    version = selection.get("active_version_identity") or context.get("variant_id")
    if isinstance(version, (tuple, list)):
        version = " / ".join(str(part) for part in version if part is not None)
    selected = selection.get("selected_entry_ids")
    count = "选区未记录" if selected is None else f"选中 {len(selected)} 条" if selected else "未选中词条"
    summary = f"提交时：{project} · {count}"
    if version:
        summary += f" · 版本 {version}"
    lines = [summary, "这是本次输入保存的范围；工作台之后的选择变化不会改写它。"]
    for key, caption in (("project_revision", "项目修订"), ("variant_revision", "版本修订")):
        if selection.get(key) is not None:
            lines.append(f"{caption}：{selection[key]}")
    if selected is not None:
        lines.append("提交时选中的词条标识：\n" + ("\n".join(map(str, selected)) or "无"))
    for key, caption in (("filter_state", "筛选条件"), ("translation_scope", "翻译范围")):
        if selection.get(key):
            lines.append(f"{caption}：\n{json.dumps(selection[key], ensure_ascii=False, indent=2)}")
    return SubmissionScope(summary, "\n\n".join(lines))


def saved_scopes(state: dict, requests) -> tuple[dict, dict]:
    inputs = state.get("ingress", ())
    messages = {entry["message_id"]: scope_from_input(entry) for entry in inputs}
    # Match apply_captured_scope: execution uses the first matching ingress,
    # not the latest follow-up or the current workbench selection.
    scopes = {}
    for request in requests:
        sources = getattr(request, "source_message_ids", ())
        first = next((entry["message_id"] for entry in inputs if entry["message_id"] in sources), None)
        if first is not None:
            scopes[request.request_id] = messages[first]
        elif hasattr(request, "request_id"):
            scopes[request.request_id] = scope_from_input({"context": dict(request.scope)})
    return scopes, messages


class SubmissionScopeView(QWidget):
    def __init__(self, theme, parent=None, *, compact=False):
        super().__init__(parent)
        self._theme = theme
        self._scope = None
        self._compact = compact
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        self.summary.setVisible(not compact)
        layout.addWidget(self.summary)
        self.details_button = QPushButton("提交范围" if compact else "查看提交范围")
        self.details_button.setAccessibleName("查看本条要求提交时的范围")
        self.details_button.setStyleSheet(QUIET_BUTTON_STYLE if compact else BUTTON_STRUCTURE_STYLE)
        self.details_button.clicked.connect(self._show_details)
        layout.addWidget(self.details_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.set_scope(None)
        self.apply_theme(theme)

    def set_scope(self, scope):
        self._scope = scope
        self.summary.setText(scope.summary if scope else "")
        self.details_button.setToolTip(scope.summary if scope else "")
        self.setVisible(scope is not None)

    def apply_theme(self, theme):
        self._theme = theme
        theme.apply_surface(self)
        theme.apply_semantic(self.summary, "muted")
        theme.apply_surface(self.details_button)

    def _show_details(self):
        if self._scope is None:
            return
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle("提交时的处理范围")
        dialog.resize(560, 380)
        layout = QVBoxLayout(dialog)
        content = QPlainTextEdit(self._scope.details)
        content.setReadOnly(True)
        layout.addWidget(content)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.close)
        layout.addWidget(close)
        dialog.show()
