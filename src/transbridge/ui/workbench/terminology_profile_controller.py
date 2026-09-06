"""Qt binding for Project/Variant-scoped terminology profile selection."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from .terminology_profile_bar import (
    TerminologyProfileBar,
    TerminologyProfileBarState,
    TerminologyProfileChoice,
)


class TerminologyProfileUiController(QObject):
    """Keep the selector and preview aligned with the persisted selection."""

    state_changed = pyqtSignal(object)

    def __init__(self, ctx, factory, bar: TerminologyProfileBar, preview, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._factory = factory
        self._bar = bar
        self._preview = preview
        self._service = None
        self._project_id: str | None = None
        self._manager = None
        self._state = TerminologyProfileBarState()
        bar.selection_requested.connect(self.select)
        bar.manage_requested.connect(self.open_manager)
        ctx.project_changed.connect(self.refresh)
        ctx.variant_changed.connect(lambda _variant_id: self.refresh())
        self.refresh()

    @property
    def state(self) -> TerminologyProfileBarState:
        return self._state

    @property
    def identity(self) -> tuple[str, str] | None:
        identity = getattr(self._ctx, "active_version_identity", None)
        if identity is None:
            return None
        return str(identity[0]), str(identity[1])

    def preview_source_import(self, source):
        """Build a Qt-free import preview for the current Project/Variant."""

        identity = getattr(self._ctx, "active_version_identity", None)
        if identity is None or self._service is None:
            raise RuntimeError("请先打开一个已保存的工程翻译版本。")
        project_id, variant_id = (str(identity[0]), str(identity[1]))
        read_base = getattr(self._factory, "base_terminology_snapshot", None)
        if not callable(read_base):
            raise RuntimeError("当前工程没有可用于创建方案的项目术语服务。")
        from transbridge.application.terminology_profiles import TerminologyProfileImportService

        base_snapshot = read_base(project_id, variant_id)
        if getattr(getattr(base_snapshot, "status", None), "value", None) != "ready":
            raise RuntimeError("当前翻译版本还没有已发布的项目术语，请先在术语工具中构建并发布。")
        if not any(getattr(decision, "is_effective", False) for decision in base_snapshot.decisions):
            raise RuntimeError("当前已发布的项目术语中没有可用于创建方案的有效术语。")
        if not getattr(source, "entries", ()):
            raise RuntimeError("该术语来源没有可保存的有效术语。")
        return TerminologyProfileImportService(self._service).preview(
            project_id,
            variant_id,
            base_snapshot,
            source,
        )

    def create_from_source_import(self, name: str, preview, *, select: bool = False):
        """Create and publish one imported profile, then refresh every bound view."""

        identity = getattr(self._ctx, "active_version_identity", None)
        if identity is None or self._service is None:
            raise RuntimeError("当前工程上下文已经变化，请重新读取术语来源。")
        project_id, variant_id = (str(identity[0]), str(identity[1]))
        read_base = getattr(self._factory, "base_terminology_snapshot", None)
        if not callable(read_base):
            raise RuntimeError("当前工程没有可用于创建方案的项目术语服务。")
        current_base = read_base(project_id, variant_id)
        if (
            current_base.version_id != preview.base_version_id
            or current_base.content_digest != preview.base_content_digest
        ):
            raise RuntimeError("项目术语在预览后发生了变化，请重新读取术语来源。")
        from transbridge.application.terminology_profiles import TerminologyProfileImportService

        result = TerminologyProfileImportService(self._service).create_and_publish(
            project_id,
            variant_id,
            name,
            preview,
            select=select,
        )
        self.refresh()
        return result

    def refresh(self) -> None:
        identity = getattr(self._ctx, "active_version_identity", None)
        if identity is None:
            self._service = None
            self._project_id = None
            self._preview.set_terminology_profile(None)
            self._render(TerminologyProfileBarState())
            return
        project_id, variant_id = (str(identity[0]), str(identity[1]))
        try:
            if self._service is None or self._project_id != project_id:
                self._service = self._factory.profile_service_for(project_id)
                self._project_id = project_id
            profiles = self._service.list_profiles(project_id)
            selected = self._service.selected_revision(project_id, variant_id)
        except Exception as exc:  # noqa: BLE001 - UI adapter boundary
            self._preview.set_terminology_profile(None)
            self._render(
                TerminologyProfileBarState(
                    enabled=False,
                    detail=f"译名方案不可用：{exc}",
                )
            )
            return

        choices = []
        for profile in profiles:
            revision = getattr(profile, "latest_published_revision", None)
            if revision is None:
                continue
            label = profile.name
            if selected is not None and profile.profile_id == selected.profile_id and selected.revision != revision:
                label = f"{profile.name}（有更新）"
            choices.append(TerminologyProfileChoice(profile.profile_id, label))
        selected_id = None if selected is None else selected.profile_id
        detail = (
            "当前显示项目译文，可直接编辑。"
            if selected is None
            else f"正在预览“{selected.name}”方案；只调整已登记译名，不会修改项目译文。"
        )
        self._preview.set_terminology_profile(selected)
        self._render(
            TerminologyProfileBarState(
                choices=tuple(choices),
                selected_profile_id=selected_id,
                enabled=True,
                can_manage=True,
                detail=detail,
            )
        )

    def select(self, profile_id: str | None) -> None:
        identity = getattr(self._ctx, "active_version_identity", None)
        if identity is None or self._service is None:
            self.refresh()
            return
        project_id, variant_id = (str(identity[0]), str(identity[1]))
        try:
            if profile_id is None:
                self._service.clear_selection(project_id, variant_id)
            else:
                self._service.select(project_id, variant_id, str(profile_id))
        except Exception as exc:  # noqa: BLE001 - UI adapter boundary
            self.refresh()
            state = TerminologyProfileBarState(
                choices=self._current_choices(),
                selected_profile_id=self._bar.combo.currentData(),
                enabled=True,
                can_manage=True,
                detail=f"切换失败，已保留原方案：{exc}",
            )
            self._render(state)
            return
        self.refresh()

    def open_manager(self) -> None:
        identity = getattr(self._ctx, "active_version_identity", None)
        if identity is None or self._service is None:
            self.refresh()
            return
        if self._manager is not None and self._manager.isVisible():
            self._manager.raise_()
            self._manager.activateWindow()
            return
        from transbridge.ui.tools.terminology_profiles import TerminologyProfileManagerDialog

        manager = TerminologyProfileManagerDialog(self._service, str(identity[0]), self._bar.window())
        manager.profiles_changed.connect(self._profiles_changed)
        manager.finished.connect(lambda _result, current=manager: self._clear_manager(current))
        self._manager = manager
        manager.open()

    def _clear_manager(self, manager) -> None:
        if self._manager is manager:
            self._manager = None

    def _profiles_changed(self) -> None:
        self.refresh()

    def _render(self, state: TerminologyProfileBarState) -> None:
        self._state = state
        self._bar.render(state)
        self.state_changed.emit(state)

    def _current_choices(self) -> tuple[TerminologyProfileChoice, ...]:
        return tuple(
            TerminologyProfileChoice(str(self._bar.combo.itemData(index)), self._bar.combo.itemText(index))
            for index in range(1, self._bar.combo.count())
        )


__all__ = ["TerminologyProfileUiController"]
