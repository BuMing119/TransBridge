"""User-facing summaries and inspectable arguments for proposed operations."""

from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QPushButton, QToolButton, QVBoxLayout, QWidget

from transbridge.application.security import project_business_data
from transbridge.converter.translation_entry import STAGE_LABELS
from transbridge.smart_assistant.tool_registry import ToolRegistry

from .change_preview_view import ChangeReviewView


def _shorten(text):
    return text if len(text) <= 90 else text[:87] + "…"


def operation_name(step: dict) -> str:
    name = str(step.get("tool", "?"))
    spec = ToolRegistry.get(name)
    return spec.display_name if spec is not None else name


def operation_summary(step: dict, preview=None) -> str:
    """Describe only explicit targets; dynamic scopes must not imply a known count."""
    args = project_business_data(step.get("args", {}))
    if not isinstance(args, dict):
        return "参数无效，请检查操作详情"
    parts = []
    if preview is not None and preview.resolved:
        known = sum(row.before is not None for row in preview.entries)
        parts.append(f"已核对范围：{known} 条词条")
        if known != len(preview.entries):
            parts.append(f"另有 {len(preview.entries) - known} 条无法核验")
    elif isinstance(args.get("entry_ids"), list):
        parts.append(f"目标：{len(args['entry_ids'])} 条词条")
    elif args.get("entry_id"):
        parts.append(f"目标词条：{args['entry_id']}")
    elif args.get("scope"):
        scopes = {"all": "全部符合条件的词条", "passed": "已通过词条", "has_issues": "有问题的词条"}
        scope = args["scope"]
        parts.append(f"范围：{scopes.get(scope, scope) if isinstance(scope, str) else scope}（数量待执行时确定）")
    elif step.get("tool") in {"start_translation", "start_polish", "run_postprocess"}:
        parts.append("范围：请求保存的默认处理范围（数量待执行时确定）")
    elif args.get("task_id"):
        parts.append(f"目标任务：{args['task_id']}")
    elif args.get("all_tasks"):
        parts.append("范围：本请求范围内的全部任务")
    elif args.get("project_id"):
        parts.append(f"目标项目：{args['project_id']}")
    if "new_translation" in args:
        parts.append(f"译文改为：{args['new_translation']}")
    if step.get("tool") == "manage_entry_labels":
        label_actions = {"create": "创建标签", "assign": "添加标签", "batch_assign": "添加标签", "unassign": "移除标签"}
        parts.append(f"{label_actions.get(args.get('action'), '标签操作')}：{args.get('name', '未指定')}")
    for key in ("new_stage", "stage"):
        if key in args:
            value = args[key]
            label = STAGE_LABELS.get(value) if isinstance(value, int) else None
            parts.append(f"状态改为：{label}（{value}）" if label else f"状态改为：{value}")
    if not parts:
        spec = ToolRegistry.get(str(step.get("tool", "")))
        parts.append("读取信息" if spec is not None and spec.permission == "read" else "操作参数见详情")
    text = "；".join(parts)
    return text if len(text) <= 240 else text[:237] + "…"


class OperationPreview(QWidget):
    """Collapsed, scrollable plain-text preview keeps large batches readable."""

    def __init__(self, steps: list[dict], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._rows = ()
        self._dialog = None
        self._summary = QLabel("未读取原词条；原文和当前译文尚未核验。")
        self._summary.setTextFormat(Qt.TextFormat.PlainText)
        self._summary.setWordWrap(True)
        self._all = QPushButton("查看受影响词条")
        self._all.setVisible(False)
        self._all.clicked.connect(self._show_all)
        layout.addWidget(self._summary)
        layout.addWidget(self._all)
        self._toggle = QToolButton()
        self._toggle.setText("高级：操作参数")
        self._toggle.setCheckable(True)
        self._toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        self._details.setAccessibleName("待执行操作的完整参数")
        self._details.setMaximumHeight(180)
        blocks = []
        for index, step in enumerate(steps, 1):
            arguments = project_business_data(step.get("args", {}))
            blocks.append(
                f"{index}. {operation_name(step)} ({step.get('tool', '?')})\n"
                f"{operation_summary(step)}\n参数：\n{json.dumps(arguments, ensure_ascii=False, indent=2, default=str)}"
            )
        self._details.setPlainText("\n\n".join(blocks))
        self._details.hide()
        self._toggle.toggled.connect(self._set_expanded)
        layout.addWidget(self._toggle)
        layout.addWidget(self._details)

    def _set_expanded(self, expanded: bool) -> None:
        self._details.setVisible(expanded)
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self._toggle.setText("收起高级参数" if expanded else "高级：操作参数")

    def set_loading(self):
        self._summary.setText("正在读取原任务范围，准备变更对照…")

    def set_unavailable(self, reason):
        self._summary.setText(f"无法核验变更预览：{reason}。未知值不会用当前界面内容代替。")

    def set_previews(self, previews):
        self._rows = tuple(row for preview in previews for row in preview.entries)
        blocks = []
        for row in self._rows[:2]:
            before = "未知" if row.before is None else row.before or "（空译文）"
            after = "执行时生成，尚未确定" if row.after is None else row.after or "（空译文）"
            original = "原文未知" if row.original is None else row.original
            blocks.append(f"原文：{_shorten(original)}\n当前：{_shorten(before)}\n拟修改为：{_shorten(after)}")
            if row.detail:
                blocks[-1] += f"\n{row.detail}"
        notices = tuple(dict.fromkeys(preview.notice for preview in previews if preview.notice))
        self._summary.setText("\n\n".join((*blocks, *notices)))
        self._all.setText(f"查看全部 {len(self._rows)} 条词条的变更对照")
        self._all.setVisible(bool(self._rows))

    def _show_all(self):
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle("执行前变更预览")
        dialog.resize(900, 560)
        layout = QVBoxLayout(dialog)
        layout.addWidget(ChangeReviewView(self._rows))
        self._dialog = dialog
        dialog.show()
