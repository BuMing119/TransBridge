"""Qt interactions for authoritative project snapshots and portable archives."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox


class V2ProjectTransferCoordinator:
    def __init__(self, host) -> None:
        self._host = host

    def _context(self):
        return replace(
            self._host.runtime_context,
            project_id=self._host.context.active_project_id,
            variant_id=self._host.context.active_variant_id,
            run_id=f"project-transfer-{uuid4().hex}",
        )

    def _service(self, name):
        return self._host.app_runtime.use_cases.resolve(name)

    def save_snapshot(self) -> None:
        if not self._host.context.active_variant_id:
            QMessageBox.warning(self._host, "快照", "请先打开一个翻译版本。")
            return
        name, ok = QInputDialog.getText(self._host, "创建历史还原点", "快照名称:")
        if not ok or not name.strip():
            return
        context = self._context()
        self._host.start_foreground_task(
            lambda: self._host.project_commands.save_snapshot(name.strip(), context),
            message="正在保存快照…",
            on_result=lambda result: self._finish(result, "快照已保存"),
        )

    def load_snapshot(self) -> None:
        if not self._host.context.active_variant_id:
            QMessageBox.warning(self._host, "快照", "请先打开一个翻译版本。")
            return
        context = self._context()
        service = self._service("project_snapshots")

        def choose(snapshots):
            if not snapshots:
                QMessageBox.information(self._host, "无快照", "当前版本无可用快照。")
                return
            items = [f"{item.name}（修订 {item.revision} · {item.identity[:8]}）" for item in snapshots]
            choice, ok = QInputDialog.getItem(self._host, "载入历史还原点", "选择快照:", items, 0, False)
            if not ok:
                return
            selected = snapshots[items.index(choice)]
            answer = QMessageBox.question(
                self._host,
                "确认加载",
                "加载快照将替换当前版本内容，并成为未保存修改。\n加载前会自动为当前内容创建还原点。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

            def restore():
                backup = self._host.project_commands.save_snapshot(f"恢复前-{context.run_id}", context)
                if not backup.is_success:
                    return backup
                return service.restore(selected.identity, context)

            self._host.start_foreground_task(
                restore,
                message="正在加载快照…",
                on_result=lambda result: self._finish(result, "快照已加载，请保存当前翻译版本"),
            )

        self._host.start_foreground_task(
            lambda: service.list(context),
            message="正在读取快照列表…",
            on_result=choose,
        )

    def delete_snapshot(self) -> None:
        if not self._host.context.active_variant_id:
            QMessageBox.warning(self._host, "快照", "请先打开一个翻译版本。")
            return
        context = self._context()
        service = self._service("project_snapshots")

        def choose(snapshots):
            if not snapshots:
                QMessageBox.information(self._host, "无快照", "当前版本无可删除的历史还原点。")
                return
            items = [f"{item.name}（修订 {item.revision} · {item.identity[:8]}）" for item in snapshots]
            choice, ok = QInputDialog.getItem(self._host, "删除历史还原点", "选择快照:", items, 0, False)
            if not ok:
                return
            selected = snapshots[items.index(choice)]
            answer = QMessageBox.warning(
                self._host,
                "确认删除历史还原点",
                f"将永久删除“{selected.name}”。此操作不可撤销，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._host.start_foreground_task(
                lambda: service.delete(selected.identity, context),
                message="正在删除历史还原点…",
                on_result=lambda result: self._finish(result, "历史还原点已删除"),
            )

        self._host.start_foreground_task(
            lambda: service.list(context),
            message="正在读取快照列表…",
            on_result=choose,
        )

    def export_transbridge(self) -> None:
        if not self._host.context.active_project_id:
            QMessageBox.warning(self._host, "导出", "请先打开一个项目。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self._host,
            "导出 .transbridge",
            f"{self._host.context.project_name}.transbridge",
            "TransBridge 项目 (*.transbridge);;所有文件 (*)",
        )
        if not path:
            return
        context = self._context()

        def start(saved):
            if saved:
                self._host.start_foreground_task(
                    lambda: self._service("project_archive").export_project(path, context),
                    message="正在导出项目包…",
                    on_result=lambda target: QMessageBox.information(
                        self._host, "导出完成", f"项目已导出到:\n{target}"
                    ),
                )

        self._host.save_current_project_async(on_finished=start)

    def import_transbridge(self, path: str | None = None) -> None:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self._host, "导入 .transbridge", "", "TransBridge 项目 (*.transbridge);;所有文件 (*)"
            )
        if not path:
            return
        context = replace(self._host.runtime_context, project_id=None, variant_id=None, run_id=uuid4().hex)

        def choose_strategy(inspection) -> None:
            requested_name = None
            copy_on_conflict = False
            if inspection.identity_conflict:
                answer = QMessageBox.question(
                    self._host,
                    "工程已存在",
                    "这个归档来自当前数据目录中已经存在的工程。\n"
                    "可以生成新的工程和版本标识，作为独立副本导入；现有工程不会被覆盖。是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                copy_on_conflict = True
                requested_name = self._choose_import_name(f"{inspection.name} 副本")
                if requested_name is None:
                    return
            elif inspection.name_conflict:
                requested_name = self._choose_import_name(inspection.name)
                if requested_name is None:
                    return

            self._host.start_foreground_task(
                lambda: self._service("project_archive").import_project(
                    path,
                    context,
                    requested_name=requested_name,
                    copy_on_identity_conflict=copy_on_conflict,
                ),
                message="正在校验并导入项目包…",
                on_result=self._offer_open_imported,
            )

        self._host.start_foreground_task(
            lambda: self._service("project_archive").inspect_import(path, context),
            message="正在检查项目包…",
            on_result=choose_strategy,
        )

    def _choose_import_name(self, suggested: str) -> str | None:
        name, ok = QInputDialog.getText(self._host, "导入工程副本", "新工程名称:", text=suggested)
        if not ok or not name.strip():
            return None
        return name.strip()

    def _offer_open_imported(self, target: str) -> None:
        answer = QMessageBox.question(
            self._host,
            "导入完成",
            "项目包已安全导入。是否现在打开？\n当前工程不会在未确认保存策略时被覆盖。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._host.project_coordinator.open_project_path(str(target))
        else:
            self._host.show_message("项目包已导入，可稍后从本地工程列表打开。")

    def _finish(self, result, message):
        if result.is_success:
            self._host.show_message(message)
        else:
            detail = "\n".join(f"{item.code}: {item.message}" for item in result.diagnostics)
            QMessageBox.warning(self._host, "快照操作失败", detail)
