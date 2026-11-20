# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Qt interactions for authoritative project snapshots and portable archives."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox


class ProjectTransferCoordinator:
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

    def _is_closing(self) -> bool:
        return getattr(self._host, "close_pending", False)

    def _snapshot_scope(self) -> tuple[str, str] | None:
        view = self._host.context
        if view.active_project_id and view.active_variant_id:
            return view.active_project_id, view.active_variant_id
        return None

    def _check_snapshot_scope(self, scope: tuple[str, str] | None) -> bool:
        view = self._host.context
        if (
            scope is None
            or scope != self._snapshot_scope()
            or self._is_closing()
            or getattr(view, "project_opening", False)
            or getattr(view, "project_view_failed", False)
        ):
            self._host.show_message("当前工程或翻译版本不可用，请重新打开历史还原。")
            return False
        return True

    def save_snapshot(self, *, scope=None, on_finished=None) -> bool:
        scope = self._snapshot_scope() if scope is None else scope
        if not self._check_snapshot_scope(scope):
            return False
        context = self._context()
        name, ok = QInputDialog.getText(self._host, "创建历史还原点", "快照名称:")
        if not ok or not name.strip() or not self._check_snapshot_scope(scope):
            return False
        return self._host.start_foreground_task(
            lambda: self._host.project_commands.save_snapshot(name.strip(), context),
            message="正在保存快照…",
            on_result=lambda result: self._finish(result, "快照已保存"),
            on_finished=on_finished,
        )

    def list_snapshots(self, scope: tuple[str, str], *, on_result, on_error=None, on_finished=None) -> bool:
        if not self._check_snapshot_scope(scope):
            return False
        context = self._context()
        service = self._service("project_snapshots")
        return self._host.start_foreground_task(
            lambda: service.list(context),
            message="正在读取历史还原点…",
            on_result=on_result,
            on_error=on_error,
            on_finished=on_finished,
            disable_workbench=False,
        )

    def load_snapshot(self) -> None:
        self._choose_snapshot(self.restore_snapshot, "载入历史还原点")

    def delete_snapshot(self) -> None:
        self._choose_snapshot(self.remove_snapshot, "删除历史还原点")

    def _choose_snapshot(self, apply, title: str) -> None:
        scope = self._snapshot_scope()
        if scope is None:
            self._host.show_message("请先打开一个翻译版本。")
            return

        def choose(snapshots):
            if not self._check_snapshot_scope(scope):
                return
            if not snapshots:
                QMessageBox.information(self._host, "无历史还原点", "当前版本尚无历史还原点。")
                return
            items = [f"{item.name}（修订 {item.revision} · {item.identity[:8]}）" for item in snapshots]
            choice, ok = QInputDialog.getItem(self._host, title, "选择还原点:", items, 0, False)
            if ok:
                apply(snapshots[items.index(choice)], scope)

        self.list_snapshots(scope, on_result=choose)

    def restore_snapshot(self, snapshot, scope: tuple[str, str], *, on_finished=None) -> bool:
        if not self._check_snapshot_scope(scope):
            return False
        context = self._context()
        answer = QMessageBox.question(
            self._host,
            "确认恢复",
            f"恢复“{snapshot.name}”会替换当前版本内容。若还原点包含旧来源，工程版本会同步恢复来源修订。\n"
            "系统会保留恢复前的还原点。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes or not self._check_snapshot_scope(scope):
            return False
        return self._restore_snapshot(
            self._service("project_snapshots"), snapshot.identity, context, on_finished=on_finished
        )

    def _restore_snapshot(self, service, identity, context, *, on_finished=None) -> bool:
        view = self._host.context
        view_token = None

        def begin():
            nonlocal view_token
            view_token = view.begin_project_open()

        def release():
            if view_token is not None:
                view.finish_project_open(view_token)

        def failed(message):
            release()
            QMessageBox.warning(self._host, "快照操作失败", message)
            if on_finished is not None:
                on_finished()

        def finished(result):
            token = result.value.get("source_read_token") if result.is_success else None
            if token is None:
                release()
                self._finish(result, "快照已加载，请保存当前翻译版本")
                if on_finished is not None:
                    on_finished()
                return
            self._finish(result, "快照及来源修订已恢复")
            options = {} if on_finished is None else {"on_finished": lambda _success: on_finished()}
            self._host.project_coordinator.refresh_committed_sources(token, context, view_token=view_token, **options)

        return self._host.start_foreground_task(
            lambda: service.restore(identity, context, backup_name=f"恢复前-{context.run_id}"),
            message="正在加载快照…",
            before_start=begin,
            on_result=finished,
            on_error=failed,
        )

    def remove_snapshot(self, snapshot, scope: tuple[str, str], *, on_finished=None) -> bool:
        if not self._check_snapshot_scope(scope):
            return False
        context = self._context()
        service = self._service("project_snapshots")
        answer = QMessageBox.warning(
            self._host,
            "确认删除历史还原点",
            f"将永久删除“{snapshot.name}”。此操作不可撤销，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes or not self._check_snapshot_scope(scope):
            return False
        return self._host.start_foreground_task(
            lambda: service.delete(snapshot.identity, context),
            message="正在删除历史还原点…",
            on_result=lambda result: self._finish(result, "历史还原点已删除"),
            on_finished=on_finished,
        )

    def export_transbridge(self) -> None:
        if not self._host.context.active_project_id:
            QMessageBox.warning(self._host, "导出", "请先打开一个项目。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self._host,
            "导出工程包",
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
                self._host,
                "导入工程包",
                "",
                "TransBridge 项目 (*.transbridge)",
            )
        if not path:
            return
        context = replace(self._host.runtime_context, project_id=None, variant_id=None, run_id=uuid4().hex)

        def choose_strategy(inspection) -> None:
            if self._is_closing():
                return
            requested_name = None
            copy_on_conflict = False
            if inspection.identity_conflict:
                answer = QMessageBox.question(
                    self._host,
                    "工程已存在",
                    "这个归档来自当前数据目录中已经存在的工程。\n"
                    "可以生成新的工程和版本标识，作为独立副本导入；现有工程不会被覆盖。是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes or self._is_closing():
                    return
                copy_on_conflict = True
                requested_name = self._choose_import_name(f"{inspection.name} 副本")
                if requested_name is None:
                    return
            elif inspection.name_conflict:
                requested_name = self._choose_import_name(inspection.name)
                if requested_name is None:
                    return

            if self._is_closing():
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
            lambda: self._service("project_archive").inspect_import_conflicts(path, context),
            message="正在检查项目包…",
            on_result=choose_strategy,
        )

    def _choose_import_name(self, suggested: str) -> str | None:
        name, ok = QInputDialog.getText(self._host, "导入工程副本", "新工程名称:", text=suggested)
        if not ok or not name.strip():
            return None
        return name.strip()

    def _offer_open_imported(self, target: str) -> None:
        if self._is_closing():
            return
        answer = QMessageBox.question(
            self._host,
            "导入完成",
            "项目包已安全导入。是否现在打开？\n当前工程不会在未确认保存策略时被覆盖。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if self._is_closing():
            return
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
