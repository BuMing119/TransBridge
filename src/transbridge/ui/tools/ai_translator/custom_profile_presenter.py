"""Presentation orchestration for named custom AI workflow profiles."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from transbridge.application.translation.custom_workflow_profile import (
    BaseMode,
    CustomWorkflowProfile,
    CustomWorkflowProfileDocument,
    WorkflowProfileValidationError,
)
from transbridge.config.ai_workflow_profiles import AiWorkflowProfileRepository


class CustomProfileConfigPort(Protocol):
    @property
    def active_custom_profile(self) -> CustomWorkflowProfile | None: ...

    def build(self) -> object: ...

    def save(self) -> object: ...

    def activate_custom(
        self,
        profile: CustomWorkflowProfile,
        persist: Callable[[CustomWorkflowProfile], None],
    ) -> object: ...

    def clear_custom(self) -> None: ...


class CustomProfileViewPort(Protocol):
    def render_profiles(self, document: CustomWorkflowProfileDocument) -> None: ...

    def render_profile_error(self, message: str) -> None: ...


class CustomProfilePresenter:
    """Own named-profile CRUD while delegating configuration capture/rendering."""

    def __init__(
        self,
        view: CustomProfileViewPort,
        config: CustomProfileConfigPort,
        repository: AiWorkflowProfileRepository | None = None,
    ) -> None:
        self._view = view
        self._config = config
        self._repository = repository or AiWorkflowProfileRepository()
        self._document = CustomWorkflowProfileDocument.empty()

    @property
    def document(self) -> CustomWorkflowProfileDocument:
        return self._document

    @property
    def selected_profile(self) -> CustomWorkflowProfile | None:
        return self._document.selected_profile

    @property
    def has_selection(self) -> bool:
        return self.selected_profile is not None

    def load(self) -> CustomWorkflowProfileDocument:
        try:
            document = self._repository.load()
        except WorkflowProfileValidationError as exc:
            self._document = CustomWorkflowProfileDocument.empty()
            self._view.render_profiles(self._document)
            self._view.render_profile_error(str(exc))
            return self._document
        if document.selected_profile is None and document.profiles:
            document = self._repository.select(document.profiles[0].id)
        self._document = document
        self._view.render_profiles(self._document)
        return self._document

    def activate_selected(self) -> CustomWorkflowProfile | None:
        profile = self.selected_profile
        if profile is None:
            self._config.clear_custom()
            return None
        self._config.activate_custom(profile, self._persist_active)
        return profile

    def select(self, profile_id: str) -> CustomWorkflowProfile:
        active = self._config.active_custom_profile
        if active is not None and active.id != profile_id:
            self._config.save()
        self._document = self._repository.select(profile_id)
        self._view.render_profiles(self._document)
        profile = self._document.selected_profile
        if profile is None:  # pragma: no cover - repository enforces the invariant
            raise ValueError(f"unknown custom workflow profile: {profile_id}")
        self._config.activate_custom(profile, self._persist_active)
        return profile

    def create(self, name: str, base_mode: BaseMode, *, description: str = "") -> CustomWorkflowProfile:
        if self._config.active_custom_profile is not None:
            self._config.save()
        profile = CustomWorkflowProfile.from_config(
            name,
            base_mode,
            self._config.build(),
            description=description,
        )
        self._document = self._repository.upsert(profile, select=True)
        self._view.render_profiles(self._document)
        self._config.activate_custom(profile, self._persist_active)
        return profile

    def rename_selected(self, name: str) -> CustomWorkflowProfile:
        profile = self._require_selected()
        self._config.save()
        self._document = self._repository.rename(profile.id, name)
        self._view.render_profiles(self._document)
        renamed = self._require_selected()
        self._config.activate_custom(renamed, self._persist_active)
        return renamed

    def delete_selected(self) -> CustomWorkflowProfile | None:
        profile = self._require_selected()
        document = self._repository.delete(profile.id)
        self._config.clear_custom()
        self._document = document
        self._view.render_profiles(self._document)
        selected = self._document.selected_profile
        if selected is not None:
            self._config.activate_custom(selected, self._persist_active)
        return selected

    def change_base_mode(self, base_mode: BaseMode) -> CustomWorkflowProfile:
        profile = self._require_selected()
        updated = CustomWorkflowProfile.from_config(
            profile.name,
            base_mode,
            self._config.build(),
            description=profile.description,
            profile_id=profile.id,
        )
        self._persist_active(updated)
        self._config.activate_custom(updated, self._persist_active)
        return updated

    def import_file(self, source: str | Path) -> CustomWorkflowProfileDocument:
        document = self._repository.import_file(source)
        self._config.clear_custom()
        self._document = document
        if self._document.selected_profile is None and self._document.profiles:
            self._document = self._repository.select(self._document.profiles[0].id)
        self._view.render_profiles(self._document)
        self.activate_selected()
        return self._document

    def export_selected(self, destination: str | Path) -> Path:
        if self._config.active_custom_profile is not None:
            self._config.save()
        return self._repository.export_file(destination, profile_id=self._require_selected().id)

    def _persist_active(self, profile: CustomWorkflowProfile) -> None:
        self._document = self._repository.upsert(profile, select=True)
        self._view.render_profiles(self._document)

    def _require_selected(self) -> CustomWorkflowProfile:
        profile = self.selected_profile
        if profile is None:
            raise ValueError("请先选择一个自定义工作流配置")
        return profile


__all__ = ["CustomProfilePresenter"]
