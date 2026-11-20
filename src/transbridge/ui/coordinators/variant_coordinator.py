# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
from dataclasses import replace

from PyQt6.QtWidgets import QMessageBox


class VariantCoordinator:
    """Own one application-shell interaction slice."""

    def __init__(self, host) -> None:
        self._host = host

    def new_variant(self):
        """创建空白新版本。"""
        self._create_variant(copy_active=False)

    def copy_variant(self):
        """从当前版本复制创建新版本。"""
        self._create_variant(copy_active=True)

    def _active_identity(self) -> tuple[str | None, str | None]:
        context = self._host.context
        return context.active_project_id, context.active_variant_id

    def _check_active(self, identity) -> bool:
        context = self._host.context
        if (
            identity == self._active_identity()
            and not getattr(context, "project_opening", False)
            and not getattr(context, "project_view_failed", False)
        ):
            return True
        self._host.show_message("工程或翻译版本已变化，请重新执行版本操作。")
        return False

    def _create_variant(self, *, copy_active: bool):
        from PyQt6.QtWidgets import QInputDialog

        identity = self._active_identity()
        if not all(identity) or self._host.runtime_context is None or self._host.project_commands is None:
            self._host.show_message("请先打开一个可用的工程版本。")
            return
        title = "复制版本" if copy_active else "新建版本"
        name, ok = QInputDialog.getText(self._host, title, "新版本名称:" if copy_active else "版本名称:")
        if not ok or not name.strip() or not self._check_active(identity):
            return
        display_name = name.strip()
        request = replace(self._host.runtime_context, project_id=identity[0], variant_id=identity[1])
        verb = "复制" if copy_active else "创建"

        def start() -> None:
            if not self._check_active(identity):
                return
            self._host.start_foreground_task(
                lambda: self._host.project_commands.create_variant(
                    display_name,
                    request,
                    copy_active=copy_active,
                ),
                message=f"正在{verb}版本「{display_name}」…",
                on_result=lambda result: self._finish_v2_variant_operation(
                    result,
                    success_message=f"版本「{display_name}」已{verb}",
                    reload_source=True,
                ),
            )

        if self._host.context.dirty:
            self._host.save_current_project_async(on_finished=lambda saved: saved and start())
        else:
            start()

    def switch_variant(self, name: str):
        """从 ProjectBar 下拉切换版本。"""
        if self._host.project_commands is None or self._host.runtime_context is None:
            self._host.show_message("V2 项目版本服务不可用。")
            return
        from transbridge.application.projects import DirtyDecision
        from transbridge.persistence.v2 import ProjectId, ProjectRef, VariantId, VariantRef

        identity = self._active_identity()
        project_id, _variant_id = identity
        if project_id is None:
            self._host.show_message("没有活动项目。")
            return
        display_name = next(
            (str(item["name"]) for item in self._host.context.project_variants if item["id"] == name),
            name,
        )
        decision = None
        if self._host.context.dirty:
            answer = QMessageBox.question(
                self._host,
                "保存确认",
                "当前版本有未保存修改。切换前是否保存？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                self._host.workbench.project_bar.refresh()
                return
            if answer == QMessageBox.StandardButton.Yes:
                if not self._check_active(identity):
                    return
                self._host.save_current_project_async(
                    on_finished=lambda saved: (
                        self.switch_variant(name)
                        if saved and self._check_active(identity)
                        else self._host.workbench.project_bar.refresh()
                    )
                )
                return
            decision = DirtyDecision.DISCARD
        if not self._check_active(identity):
            return
        opener = getattr(self._host, "current_project_opener", None)
        if getattr(opener, "supports_paged_workbench", False):
            if not self._host.project_coordinator.open_project_variant(
                name, decision=decision, display_name=display_name
            ):
                self._host.workbench.project_bar.refresh()
            return
        project_ref = ProjectRef(ProjectId(project_id))
        variant_ref = VariantRef(VariantId(name), project_ref.identity)
        started = self._host.start_foreground_task(
            lambda: self._host.project_commands.switch_v2(
                project_ref,
                variant_ref,
                self._host.runtime_context,
                dirty_decision=decision,
            ),
            message=f"正在加载版本「{display_name}」…",
            on_result=lambda result: self._finish_v2_variant_operation(
                result,
                success_message=f"已切换到版本「{display_name}」",
                reload_source=True,
            ),
        )
        if not started:
            self._host.workbench.project_bar.refresh()
        return

    def manual_save(self):
        """保存当前工程版本。"""
        from .profile_variant_save import ProfileVariantSaveCoordinator

        if ProfileVariantSaveCoordinator(self._host).request():
            return
        self._host.save_current_project_async()

    def delete_variant(self, name: str):
        """删除指定版本（至少保留一个）。"""
        display_name = next(
            (str(item["name"]) for item in self._host.context.project_variants if item["id"] == name),
            name,
        )
        if len(self._host.context.project_variants) <= 1:
            QMessageBox.warning(self._host, "无法删除", "至少保留一个版本。")
            return
        answer = QMessageBox.question(
            self._host,
            "确认删除",
            f"确定要删除版本「{display_name}」吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        deleting_active = self._host.context.active_variant_id == name

        def _delete() -> None:
            self._host.start_foreground_task(
                lambda: self._host.project_commands.delete_variant(name, self._host.runtime_context),
                message=f"正在删除版本「{display_name}」…",
                on_result=lambda result: self._finish_v2_variant_operation(
                    result,
                    success_message=f"版本「{display_name}」已删除",
                    reload_source=deleting_active,
                ),
            )

        if self._host.context.dirty:
            self._host.save_current_project_async(on_finished=lambda saved: saved and _delete())
        else:
            _delete()
        return

    def _finish_v2_variant_operation(
        self,
        result,
        *,
        success_message: str,
        reload_source: bool,
    ) -> None:
        if not result.is_success:
            diagnostic = result.diagnostics[0]
            self._host.show_message(f"{diagnostic.code}: {diagnostic.message}")
            QMessageBox.warning(self._host, "版本操作失败", diagnostic.message)
            self._host.workbench.project_bar.refresh()
            return
        self._host.show_message(success_message)
        if result.diagnostics:
            self._host.show_message(f"{success_message}；{result.diagnostics[0].message}")
        if reload_source:
            self._host.project_coordinator.restore_active_sources()
