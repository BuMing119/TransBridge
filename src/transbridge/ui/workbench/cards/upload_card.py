from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMessageBox,
)

from transbridge.converter.translation_entry_collection_export import (
    export_to_categorized_json_files,
    get_categorized_file_names,
)
from transbridge.paratranz.workflow.uploader import ParaTranzUploader
from transbridge.ui.foundation.adapters import ThemeView

from .base import OpCard
from .presenter import OperationCardPresenter
from .upload_views import (
    BatchConfirmDialog as _BatchConfirmDialog,
    BatchResultDialog as _BatchResultDialog,
    BatchUploadModeDialog as _BatchUploadModeDialog,
    ConflictResolveDialog as _ConflictResolveDialog,
    FileSelectionDialog as _FileSelectionDialog,
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
            "将集合上传到当前已选 ParaTranz 项目，可选分类或普通上传（需先在管理模式中选中项目）。",
            "上传",
            parent,
            theme_view=theme_view,
        )
        self._ctx = ctx
        self._presenter = OperationCardPresenter(ctx)
        self._run_worker = run_worker
        self.btn.clicked.connect(self.upload)
        self.batch_btn.clicked.connect(self.batch_upload)

    def update_batch_visibility(self):
        """更新批量按钮可见性（由 step3 调用）。"""
        self.set_batch_visible(self._presenter.batch_available)

    def batch_upload(self):
        """批量上传入口。"""
        if self._dispatch_planned("upload", self._ctx, batch=True):
            return
        slots = self._ctx.slots
        if len(slots) <= 1:
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
        slot_names = [s.label or Path(s.esp_path).stem for s in selected_slots]
        items = [f"• {name}.json" for name in slot_names]
        header = f"即将上传到项目「{project_name}」\n每个插件将作为单个 JSON 文件上传（不分类）。"

        dlg = _BatchConfirmDialog("确认批量上传", header, items, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        config = self._ctx.config

        def _batch_upload_factory(progress_cb):
            results = BatchUploadResult()
            uploader = ParaTranzUploader(config)
            total = len(selected_slots)

            for i, slot in enumerate(selected_slots):
                if progress_cb:
                    slot_name = slot.label or Path(slot.esp_path).stem
                    progress_cb(i, total, f"正在上传 {slot_name}…")

                filename = (Path(slot.esp_path).stem + ".json") if slot.esp_path else f"{slot.label}.json"
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

            # 检查是否有冲突警告
            has_conflicts = any(
                hasattr(r, "name_conflicts") and r.name_conflicts for r in getattr(result, "individual_results", [])
            )
            if has_conflicts:
                QMessageBox.warning(
                    self,
                    "文件名冲突警告",
                    "部分插件检测到文件名冲突。\n请在 ParaTranz 中检查文件是否被移动或存在重复。",
                )

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
        if self._dispatch_planned("upload", self._ctx):
            return
        collection = self._ctx.collection
        from transbridge.ui.paratranz.target_context import bound_paratranz_project

        project = bound_paratranz_project(self._ctx)
        if not collection or not project:
            return
        project_id = project.get("id")

        dlg = _UploadModeDialog(self._ctx.esp_path, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        mode = dlg.mode
        filename = dlg.filename
        backup_enabled = dlg.backup_enabled
        translation_mode = dlg.translation_mode
        config = self._ctx.config

        backup_dir = None
        if backup_enabled:
            backup_dir = QFileDialog.getExistingDirectory(self, "选择本地备份目录")
            if not backup_dir:
                return

        def _on_done(result):
            parts = [f"新建：{result.created} 个", f"更新原文：{result.updated} 个", f"跳过：{result.skipped} 个"]
            if translation_mode != "orig_only":
                parts.append(f"导入译文：{result.translation_updated} 个")
            QMessageBox.information(self, "上传完成", "\n".join(parts))

        if mode == "categorized":
            # 预计算文件列表，让用户选择
            file_infos = get_categorized_file_names(collection)
            file_filter = None
            if len(file_infos) > 1:
                sel_dlg = _FileSelectionDialog(file_infos, parent=self)
                if sel_dlg.exec() != QDialog.DialogCode.Accepted:
                    return
                file_filter = sel_dlg.selected_files

            local_names = file_filter if file_filter is not None else {name for name, _ in file_infos}

            # Phase 1: 预检冲突（后台查询 ParaTranz 文件列表）
            def _detect_factory(progress_cb):
                uploader = ParaTranzUploader(config)
                return uploader.detect_conflicts(project_id, local_names, progress_callback=progress_cb)

            def _on_conflicts_detected(result: tuple):
                conflicts, file_maps = result  # 解构：冲突列表 + 已获取的文件映射
                file_id_override: dict[str, int] = {}

                if conflicts:
                    dlg = _ConflictResolveDialog(conflicts, parent=self)
                    if dlg.exec() != QDialog.DialogCode.Accepted:
                        return  # 用户取消
                    file_id_override = dlg.resolved_path_mapping(conflicts)

                # Phase 2: 实际上传，传入 prefetched_maps 避免重复 API 调用
                def _upload_factory(progress_cb):
                    if backup_dir:
                        export_to_categorized_json_files(collection, backup_dir)
                    uploader = ParaTranzUploader(config)
                    return uploader.upload_collection(
                        collection,
                        project_id=project_id,
                        file_filter=file_filter,
                        translation_mode=translation_mode,
                        file_id_override=file_id_override or None,
                        prefetched_maps=file_maps,
                        progress_callback=progress_cb,
                    )

                # 延迟启动第二个 worker，确保第一个 worker 的 finished 信号先处理
                # 避免 _restore() 把第二个 worker 的进度条隐藏掉
                from PyQt6.QtCore import QTimer

                def _start_upload():
                    self._run_worker(
                        fn_factory=_upload_factory,
                        on_result=_on_done,
                        on_error=lambda e: QMessageBox.critical(self, "上传失败", str(e)),
                        progress_total=len(local_names),
                        progress_msg="正在上传到 ParaTranz…",
                    )

                QTimer.singleShot(0, _start_upload)

            self._run_worker(
                fn_factory=_detect_factory,
                on_result=_on_conflicts_detected,
                on_error=lambda e: QMessageBox.critical(self, "冲突检测失败", str(e)),
                progress_total=0,
                progress_msg="正在检测文件冲突…",
            )
        else:

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
