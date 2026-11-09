# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Workbench export intent; all formats share the export dialog and publication boundary."""

from dataclasses import dataclass

from PyQt6.QtWidgets import QMessageBox

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.operations.errors import OperationCompositionError

from .base import OpCard
from .presenter import OperationCardPresenter


@dataclass(frozen=True, slots=True)
class _SelectedWriteContext:
    """Narrow the source scope while preserving the current project authority."""

    context: object
    slots: dict

    def __getattr__(self, name):
        return getattr(self.context, name)


class WriteCard(OpCard):
    def __init__(self, ctx, run_worker, parent=None, *, theme_view: ThemeView | None = None):
        super().__init__(
            "导出文件",
            "按原格式导出，或选择 ParaTranz JSON、DSD JSON 等受支持的格式。",
            "导出文件",
            parent,
            theme_view=theme_view,
        )
        self._ctx = ctx
        self._presenter = OperationCardPresenter(ctx)
        self._run_worker = run_worker
        self.btn.clicked.connect(self.write)
        self.batch_btn.clicked.connect(self.batch_write)
        self.eet_btn = self.btn
        self.xt_btn = self.btn

    def update_batch_visibility(self):
        self.set_batch_visible(self._presenter.batch_available)

    def write(self):
        self._begin_planned(self._ctx, batch=False)

    def batch_write(self):
        self._begin_planned(self._ctx, batch=True)

    def do_batch_write(self, selected_slots: list):
        """Retain the public selected-source entrypoint without bypassing preflight."""
        available = getattr(self._ctx, "slots", {}) or {}
        selected = {key: slot for key, slot in available.items() if any(slot is item for item in selected_slots)}
        if len(selected) != len({id(slot) for slot in selected_slots}) or len(selected) < 2:
            QMessageBox.warning(self, "批量写回不可用", "请选择至少两个属于当前工程的有效来源文件后重试。")
            return
        self._begin_planned(_SelectedWriteContext(self._ctx, selected), batch=True)

    def _begin_planned(self, context, *, batch: bool) -> None:
        try:
            if self._dispatch_planned("write", context, batch=batch):
                return
        except (OperationCompositionError, TypeError, ValueError) as exc:
            message = f"无法创建安全写回计划：{exc}"
        else:
            message = (
                "安全写回服务尚未接入，请重新打开主窗口后重试。"
                if self._operation_plan_facade is None
                else "所选来源缺少完整的来源快照或格式信息，请重新打开文件/工程后重试。"
            )
        QMessageBox.warning(self, "批量写回不可用" if batch else "写回不可用", message)

    def export_dsd(self):
        """Keep the legacy callable, routing it through the guarded export UI."""
        if self._operation_plan_facade is None:
            QMessageBox.warning(self, "无法导出", "导出服务尚未接入，请重新打开工程。")
            return
        self._operation_plan_facade.begin_write(self._ctx, batch=False, output_format="json.dsd")
