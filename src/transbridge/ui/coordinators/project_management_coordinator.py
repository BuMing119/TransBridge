"""Qt interaction boundary for authoritative Project management."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from PyQt6.QtWidgets import QInputDialog, QMessageBox


class ProjectManagementCoordinator:
    def __init__(self, host) -> None:
        self._host = host

    def rename_current(self, requested_name: str | None = None) -> None:
        project_id = self._host.context.active_project_id
        old_name = self._host.context.project_name
        if not project_id or not old_name:
            self._host.show_message("请先打开本地工程。")
            return
        name = requested_name
        if name is None:
            name, accepted = QInputDialog.getText(self._host, "重命名本地工程", "新名称:", text=old_name)
            if not accepted:
                return
        name = name.strip()
        if not name or name == old_name:
            return
        context = self._context(project_id)
        self._host.start_foreground_task(
            lambda: self._service().rename(name, context),
            message="正在重命名本地工程…",
            on_result=lambda result: self._finish_rename(result, old_name, name),
        )

    def delete_project(self, project_id: str | None = None, name: str | None = None) -> None:
        target_id = project_id or self._host.context.active_project_id
        target_name = name or self._host.context.project_name
        if not target_id or not target_name:
            self._host.show_message("没有可删除的本地工程。")
            return
        is_active = target_id == self._host.context.active_project_id
        dirty = is_active and self._host.context.dirty
        dirty_text = "\n当前未保存的修改也会丢失。" if dirty else ""
        answer = QMessageBox.warning(
            self._host,
            "确认删除本地工程",
            f"将永久删除本地工程“{target_name}”及其翻译版本、历史还原点和工程内资产。"
            f"{dirty_text}\n外部源文件不会被删除。此操作不可撤销，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        context = self._context(target_id, include_variant=is_active)
        self._host.start_foreground_task(
            lambda: self._service().delete(target_id, context, expected_name=target_name),
            message=f"正在删除本地工程“{target_name}”…",
            on_result=lambda result: self._finish_delete(result, target_name),
        )

    def _service(self):
        return self._host.app_runtime.use_cases.resolve("project_management")

    def _context(self, project_id: str, *, include_variant: bool = True):
        return replace(
            self._host.runtime_context,
            project_id=project_id,
            variant_id=self._host.context.active_variant_id if include_variant else None,
            run_id=f"project-management-{uuid4().hex}",
        )

    def _finish_rename(self, result, old_name: str, name: str) -> None:
        if not result.is_success:
            self._show_failure("重命名工程失败", result)
            return
        self._host.workbench.project_bar.refresh()
        self._host.show_message(f"本地工程已重命名：{old_name} → {name}")

    def _finish_delete(self, result, name: str) -> None:
        if not result.is_success:
            self._show_failure("删除工程失败", result)
            return
        active_removed = bool(result.value and result.value.get("active_removed"))
        if active_removed:
            for key in tuple(self._host.context.slots):
                self._host.context.remove_slot(key)
            self._host.start_center_controller.show_empty()
        else:
            self._host.start_center_controller.show(user_requested=bool(self._host.context.project_name))
        self._host.show_message(f"本地工程“{name}”已删除；外部源文件已保留。")

    def _show_failure(self, title: str, result) -> None:
        detail = "\n".join(f"{item.code}: {item.message}" for item in result.diagnostics)
        self._host.show_message(detail)
        QMessageBox.warning(self._host, title, detail)


__all__ = ["ProjectManagementCoordinator"]
