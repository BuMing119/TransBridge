from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QMessageBox,
)

from transbridge.paratranz.workflow.uploader import ParaTranzUploader
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.operations.ordinary_upload_scope import ordinary_upload_path
from transbridge.ui.operations.upload_mode_dialog import UploadModeDialog

from .base import OpCard
from .presenter import OperationCardPresenter
from .upload_views import (
    BatchConfirmDialog as _BatchConfirmDialog,
    BatchResultDialog as _BatchResultDialog,
    BatchUploadModeDialog as _BatchUploadModeDialog,
    SlotSelectDialog as _SlotSelectDialog,
    UploadModeDialog as _UploadModeDialog,
)


@dataclass
class BatchUploadResult:
    """批量上传结果汇总。"""

    success_count: int = 0
    failed_count: int = 0
    details: list[str] = field(default_factory=list)


class UploadCard(OpCard):
    def __init__(self, ctx, run_worker, parent=None, *, theme_view: ThemeView | None = None):
        super().__init__(
            "上传到 ParaTranz",
            "单独或批量上传到 ParaTranz，均可选择普通上传或分类上传。",
            "上传",
            parent,
            theme_view=theme_view,
        )
        self._ctx = ctx
        self._presenter = OperationCardPresenter(ctx)
        self._run_worker = run_worker
        self.btn.clicked.connect(self.upload)
        self.batch_btn.clicked.connect(self.batch_upload)

    def _choose_upload_mode(self, *, batch: bool = False) -> bool:
        """Return whether the caller should continue with ordinary upload."""
        from transbridge.ui.operations.categorized_upload_bridge import open_categorized_upload

        dialog = UploadModeDialog(batch=batch, parent=self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            if dialog.mode == "plain":
                return True
            open_categorized_upload(self._ctx, parent=self, batch=batch)
            return False
        finally:
            dialog.deleteLater()

    def update_batch_visibility(self):
        """更新批量按钮可见性（由 step3 调用）。"""
        self.set_batch_visible(self._presenter.batch_available)

    def batch_upload(self):
        """批量上传入口。"""
        slots = self._ctx.slots
        if len(slots) <= 1:
            return
        if not self._choose_upload_mode(batch=True):
            return
        if self._dispatch_planned("upload", self._ctx, batch=True):
            return

        from transbridge.ui.paratranz.target_context import bound_paratranz_project

        project = bound_paratranz_project(self._ctx)
        if not project:
            QMessageBox.warning(self, "未绑定项目", "请先为当前本地工程选择 ParaTranz 同步目标。")
            return

        # 弹出插件选择对话框
        dlg = _SlotSelectDialog("批量上传 - 选择插件", slots, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected_keys = dlg.selected_slots()
        if not selected_keys:
            QMessageBox.warning(self, "未选择插件", "请至少选择一个插件进行批量上传。")
            return

        selected_slots = [slots[k] for k in selected_keys]

        # 弹出上传模式选择对话框
        mode_dlg = _BatchUploadModeDialog(parent=self)
        if mode_dlg.exec() != QDialog.DialogCode.Accepted:
            return

        self.do_batch_upload(selected_slots, project, mode_dlg.translation_mode)

    def do_batch_upload(self, selected_slots: list, project: dict, translation_mode: str = "orig_only"):
        """执行批量上传。"""
        project_id = project.get("id")
        project_name = project.get("name", "?")

        # 确认对话框
        try:
            filenames = [ordinary_upload_path(self._ctx, slot.collection) for slot in selected_slots]
        except ValueError as exc:
            QMessageBox.warning(self, "无法确定上传路径", str(exc))
            return
        items = [f"• {name}" for name in filenames]
        header = f"即将上传到项目「{project_name}」\n每个插件将作为单个 JSON 文件上传（不分类）。"

        dlg = _BatchConfirmDialog("确认批量上传", header, items, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        config = self._ctx.config

        def _batch_upload_factory(progress_cb):
            results = BatchUploadResult()
            uploader = ParaTranzUploader(config)
            total = len(selected_slots)

            for i, (slot, filename) in enumerate(zip(selected_slots, filenames, strict=True)):
                if progress_cb:
                    slot_name = slot.label or Path(slot.esp_path).stem
                    progress_cb(i, total, f"正在上传 {slot_name}…")

                try:
                    result = uploader.upload_collection_as_single(
                        slot.collection,
                        project_id=project_id,
                        filename=filename,
                        translation_mode=translation_mode,
                    )
                    results.success_count += 1
                    msg = f"✓ {filename}: 新建 {result.created}, 更新 {result.updated}"
                    if translation_mode != "orig_only" and result.translation_updated > 0:
                        msg += f", 导入译文 {result.translation_updated}"
                    results.details.append(msg)
                except Exception as e:
                    results.failed_count += 1
                    results.details.append(f"✗ {filename}: {e}")

            if progress_cb:
                progress_cb(total, total, "上传完成")
            return results

        def _on_done(result: BatchUploadResult):
            header = f"成功：{result.success_count} 个\n失败：{result.failed_count} 个"

            dlg = _BatchResultDialog("批量上传完成", header, result.details, parent=self)
            dlg.exec()

        self._run_worker(
            fn_factory=_batch_upload_factory,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "批量上传失败", e),
            progress_total=len(selected_slots),
            progress_msg="正在批量上传…",
        )

    def upload(self):
        if not self._ctx.collection or not self._choose_upload_mode():
            return
        if self._dispatch_planned("upload", self._ctx):
            return
        collection = self._ctx.collection
        from transbridge.ui.paratranz.target_context import bound_paratranz_project

        project = bound_paratranz_project(self._ctx)
        if not collection or not project:
            return
        project_id = project.get("id")

        try:
            filename = ordinary_upload_path(self._ctx)
        except ValueError as exc:
            QMessageBox.warning(self, "无法确定上传路径", str(exc))
            return
        dlg = _UploadModeDialog(self._ctx.esp_path, parent=self, filename=filename)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        translation_mode = dlg.translation_mode
        config = self._ctx.config

        def _on_done(result):
            parts = [f"新建：{result.created} 个", f"更新原文：{result.updated} 个", f"跳过：{result.skipped} 个"]
            if translation_mode != "orig_only":
                parts.append(f"导入译文：{result.translation_updated} 个")
            QMessageBox.information(self, "上传完成", "\n".join(parts))

        def _upload_factory(progress_cb):
            uploader = ParaTranzUploader(config)
            return uploader.upload_collection_as_single(
                collection,
                project_id=project_id,
                filename=filename,
                translation_mode=translation_mode,
                progress_callback=progress_cb,
            )

        self._run_worker(
            fn_factory=_upload_factory,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "上传失败", str(e)),
            progress_total=0,
            progress_msg="正在上传到 ParaTranz…",
        )
