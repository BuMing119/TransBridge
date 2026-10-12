"""Workbench write intents: all plugin/XML publication uses operation plans."""

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox, QPushButton

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle
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
            "写回插件/XML",
            "预检当前译文后安全写回 ESP 插件或 EET/XT XML 文件。",
            "写回",
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
        self.dsd_btn = QPushButton("导出 DSD", self)
        self.dsd_btn.setAccessibleName("独立导出 DSD JSON")
        ComponentStyle.apply_static(self.dsd_btn, ComponentKind.BUTTON)
        self.dsd_btn.clicked.connect(self.export_dsd)
        self.layout().itemAt(self.layout().count() - 1).layout().addWidget(self.dsd_btn)

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
        """DSD is a separate export, never a fallback from failed publication."""
        collection = self._ctx.collection
        if not collection:
            QMessageBox.warning(self, "无法导出", "当前没有可导出的翻译条目。")
            return
        self._write_dsd(collection)

    def _write_dsd(self, collection):
        """导出为 DSD 格式 JSON 文件。"""
        esp_path = getattr(self._ctx, "esp_path", None)
        if esp_path:
            default_name = Path(esp_path).stem + "_dsd.json"
            default_dir = str(Path(esp_path).parent)
        else:
            default_name = "export_dsd.json"
            default_dir = ""

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 DSD JSON",
            str(Path(default_dir) / default_name) if default_dir else default_name,
            "JSON 文件 (*.json);;所有文件 (*)",
        )
        if not save_path:
            return

        def _write():
            # 统计有译文的条目数
            count = sum(1 for e in collection if e.translation)
            collection.to_dsd_json_file(save_path)
            return count, Path(save_path)

        def _on_done(result):
            count, out_path = result
            if count == 0:
                QMessageBox.warning(
                    self,
                    "导出完成",
                    f"没有已翻译的条目，导出文件为空。\n\n保存至：{out_path}",
                )
            else:
                QMessageBox.information(
                    self,
                    "导出完成",
                    f"已导出 {count} 条译文到 DSD JSON。\n\n保存至：{out_path}",
                )

        self._run_worker(
            _write,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "DSD 导出失败", e),
            progress_total=0,
            progress_msg="正在导出 DSD JSON…",
        )
