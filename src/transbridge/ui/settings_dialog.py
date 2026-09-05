"""General UI settings dialog with isolated theme preview and safe recovery UX."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QMessageBox,
    QWidget,
)

from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode

from .foundation.accessibility import update_accessible_state
from .foundation.components import ComponentStyle, SemanticState, configure_dialog
from .foundation.model import ThemeDefinition
from .foundation.preview import ThemePreviewWidget
from .foundation.registry import ThemeRegistry
from .foundation.theme_service import (
    ThemeApplyResult,
    ThemeApplyStatus,
    ThemePreference,
    ThemeService,
    ThemeSnapshot,
)
from .settings.center_layout import build_settings_center
from .settings.draft import SettingsConfigDraft
from .settings.sections import SettingsSection


class PersistFailureChoice(StrEnum):
    KEEP_SESSION = "keep_session"
    RESTORE_PERSISTED = "restore_persisted"


@dataclass(frozen=True, slots=True)
class ThemeOption:
    theme_id: str
    provider_id: str
    display_name: str
    version: str


@dataclass(frozen=True, slots=True)
class UiSettingsDraft:
    persisted_preference: ThemePreference
    active_preference: ThemePreference
    draft_preference: ThemePreference
    preview_snapshot: ThemeSnapshot | None = None

    @property
    def dirty(self) -> bool:
        return self.draft_preference != self.persisted_preference


PersistFailureResolver = Callable[[ThemeApplyResult], PersistFailureChoice]


class SettingsDialog(QDialog):
    """Own the appearance draft; shell composition owns its menu intent."""

    service_settings_requested = pyqtSignal()

    def __init__(
        self,
        theme_service: ThemeService,
        preferences,
        parent: QWidget | None = None,
        *,
        registry: ThemeRegistry | None = None,
        persist_failure_resolver: PersistFailureResolver | None = None,
        locale_service: object | None = None,
        initial_section: SettingsSection | str = SettingsSection.APPEARANCE,
        llm_config: object | None = None,
        paratranz_config: object | None = None,
        reload_llm: Callable[[], object] | None = None,
        on_paratranz_saved: Callable[[object], None] | None = None,
    ) -> None:
        super().__init__(parent)
        configure_dialog(self)
        self._theme_service = theme_service
        self._preferences = preferences
        self._registry = registry
        candidate = getattr(locale_service, "gettext", None)
        self._gettext: Callable[[str], str] = candidate if callable(candidate) else lambda value: value
        self._persist_failure_resolver = persist_failure_resolver or self._ask_persist_failure
        self._initial_section = SettingsSection.parse(initial_section)
        self._paratranz_config = paratranz_config
        self._config_draft = SettingsConfigDraft(
            llm_config,
            paratranz_config,
            reload_llm=reload_llm,
            on_paratranz_saved=on_paratranz_saved,
        )
        self._disposed = False
        self._last_notice = ""
        self._theme_options = self._collect_theme_options()
        self._state = self._initial_state()
        self.setWindowTitle(self._tr("通用设置"))
        self.setMinimumSize(720, 480)
        self.resize(940, 680)
        self.setAccessibleName(self._tr("通用设置"))
        self.setAccessibleDescription(self._tr("管理外观、AI 服务、术语和安全设置"))
        self._build_ui()
        self._sync_controls()
        self._refresh_preview()
        self._theme_service.theme_changed.connect(self._on_active_theme_changed)

    @property
    def state(self) -> UiSettingsDraft:
        return self._state

    @property
    def preview(self) -> ThemePreviewWidget:
        return self._preview

    @property
    def last_notice(self) -> str:
        return self._last_notice

    @property
    def disposed(self) -> bool:
        return self._disposed

    @property
    def current_section(self) -> SettingsSection:
        item = self._section_list.currentItem()
        return SettingsSection.parse(None if item is None else item.data(Qt.ItemDataRole.UserRole))

    def select_section(self, section: SettingsSection | str) -> None:
        target = SettingsSection.parse(section).value
        for index in range(self._section_list.count()):
            if self._section_list.item(index).data(Qt.ItemDataRole.UserRole) == target:
                self._section_list.setCurrentRow(index)
                return

    def set_draft(self, preference: ThemePreference) -> None:
        if not isinstance(preference, ThemePreference):
            raise TypeError("preference must be a ThemePreference")
        self._state = replace(self._state, draft_preference=preference)
        self._sync_controls()
        self._refresh_preview()

    def restore_default(self) -> None:
        self.set_draft(ThemePreference(ThemeMode.SYSTEM, DEFAULT_THEME_ID))

    def apply_draft(self) -> bool:
        preference = self._state.draft_preference
        candidate = self._preview_candidate(preference)
        if candidate is None or candidate.theme_id != preference.theme_id:
            self._show_notice(self._tr("所选主题当前不可用，请选择其他主题。"), error=True)
            return False

        for page in self._settings_pages.values():
            apply_to_draft = getattr(page, "apply_to_draft", None)
            if callable(apply_to_draft):
                apply_to_draft()
        service_result = self._config_draft.save()
        if not service_result.saved:
            self._show_notice(self._tr(service_result.message or "无法保存服务设置，请检查后重试。"), error=True)
            return False

        previous_active = self._active_preference()
        result = self._theme_service.set_preference(preference, persist=True)
        if result.status is ThemeApplyStatus.FAILED:
            message = _safe_apply_message(result, self._gettext)
            if self._config_draft.has_llm or self._config_draft.has_paratranz:
                message = self._tr("服务设置已保存，但主题无法应用；当前主题保持不变。")
            self._show_notice(message, error=True)
            return False
        if result.status is ThemeApplyStatus.FALLBACK and (
            result.snapshot is None or result.snapshot.theme_id != preference.theme_id
        ):
            self._compensate_fallback(previous_active)
            message = self._tr("所选主题已不可用，当前设置保持不变。")
            if self._config_draft.has_llm or self._config_draft.has_paratranz:
                message = self._tr("服务设置已保存，但所选主题已不可用；当前主题保持不变。")
            self._show_notice(message, error=True)
            return False

        if result.persisted:
            self._state = UiSettingsDraft(preference, self._active_preference(), preference, result.snapshot)
            self.done(QDialog.DialogCode.Accepted)
            return True

        self._show_notice(self._tr("主题已应用到本次会话，但无法保存供下次启动使用。"), error=True)
        choice = self._persist_failure_resolver(result)
        if choice is PersistFailureChoice.KEEP_SESSION:
            self._state = replace(self._state, active_preference=self._active_preference())
            self.done(QDialog.DialogCode.Accepted)
            return True

        restored = self._theme_service.set_preference(self._state.persisted_preference, persist=False)
        if restored.status is ThemeApplyStatus.FAILED:
            self._show_notice(self._tr("无法恢复已保存主题；当前会话继续使用刚才的主题。"), error=True)
            return False
        self._state = replace(
            self._state,
            active_preference=self._active_preference(),
            draft_preference=self._state.persisted_preference,
            preview_snapshot=restored.snapshot,
        )
        self.done(QDialog.DialogCode.Accepted)
        return True

    def reject(self) -> None:
        self.done(QDialog.DialogCode.Rejected)

    def done(self, result: int) -> None:
        self._dispose()
        super().done(result)

    def _initial_state(self) -> UiSettingsDraft:
        active = self._active_preference()
        persisted = active
        try:
            snapshot = self._preferences.load()
            persisted = ThemePreference(snapshot.theme_mode, snapshot.theme_id)
        except Exception:
            self._last_notice = self._tr("无法读取已保存设置；当前会话主题仍可正常使用。")
        draft = active if not self._theme_available(persisted.theme_id) else persisted
        if draft != persisted and not self._last_notice:
            self._last_notice = self._tr("已保存的主题当前不可用，正在使用安全回退主题。")
        return UiSettingsDraft(persisted, active, draft)

    def _build_ui(self) -> None:
        build_settings_center(self, self._initial_section)

    def _sync_controls(self) -> None:
        preference = self._state.draft_preference
        self._set_combo_data(self._mode_combo, preference.mode)
        self._set_combo_data(self._theme_combo, preference.theme_id)
        option = next((item for item in self._theme_options if item.theme_id == preference.theme_id), None)
        provider_text = (
            self._tr("不可用") if option is None else f"{option.provider_id} · v{option.version} · {option.theme_id}"
        )
        self._provider_metadata.setText(provider_text)
        update_accessible_state(self._provider_metadata, provider_text)
        self._apply_button.setEnabled(
            self._state.dirty
            or preference != self._state.active_preference
            or self._config_draft.has_llm
            or self._config_draft.has_paratranz
        )

    def _refresh_preview(self) -> None:
        snapshot = self._preview_candidate(self._state.draft_preference)
        if snapshot is None:
            return
        self._state = replace(self._state, preview_snapshot=snapshot)
        self._preview.show_snapshot(snapshot)
        mode_text = self._tr("深色") if snapshot.effective_scheme.value == "dark" else self._tr("浅色")
        suffix = self._tr("（系统）") if self._state.draft_preference.mode is ThemeMode.SYSTEM else ""
        self._effective_scheme.setText(f"{mode_text}{suffix}")
        update_accessible_state(self._effective_scheme, f"{mode_text}{suffix}")

    def _preview_candidate(self, preference: ThemePreference) -> ThemeSnapshot | None:
        try:
            snapshot = self._theme_service.preview(preference)
        except Exception:
            self._show_notice(self._tr("无法生成所选主题的预览。"), error=True)
            return None
        if snapshot.theme_id != preference.theme_id:
            self._show_notice(self._tr("所选主题当前不可用，预览已使用安全回退主题。"), error=True)
        return snapshot

    def _on_mode_changed(self, _index: int) -> None:
        mode = self._mode_combo.currentData()
        if isinstance(mode, ThemeMode):
            self._state = replace(
                self._state,
                draft_preference=ThemePreference(mode, self._state.draft_preference.theme_id),
            )
            self._refresh_preview()
            self._sync_controls()

    def _on_theme_changed(self, _index: int) -> None:
        theme_id = self._theme_combo.currentData()
        if isinstance(theme_id, str) and theme_id:
            self._state = replace(
                self._state,
                draft_preference=ThemePreference(self._state.draft_preference.mode, theme_id),
            )
            self._refresh_preview()
            self._sync_controls()

    def _on_active_theme_changed(self, _revision: int, _snapshot: ThemeSnapshot) -> None:
        if self._disposed:
            return
        self._state = replace(self._state, active_preference=self._active_preference())
        if self._state.draft_preference.mode is ThemeMode.SYSTEM:
            self._refresh_preview()
        self._sync_controls()

    def _collect_theme_options(self) -> tuple[ThemeOption, ...]:
        definitions: tuple[ThemeDefinition, ...] = () if self._registry is None else self._registry.themes
        unique: dict[str, ThemeOption] = {}
        for definition in definitions:
            manifest = definition.manifest
            if manifest.compatibility.fallback_only:
                continue
            unique.setdefault(
                manifest.theme_id,
                ThemeOption(manifest.theme_id, manifest.provider_id, manifest.display_name, manifest.version),
            )
        if not unique:
            snapshot = self._theme_service.snapshot()
            unique[snapshot.theme_id] = ThemeOption(
                snapshot.theme_id,
                snapshot.provider_id,
                snapshot.theme_id,
                "unknown",
            )
        return tuple(unique[key] for key in sorted(unique))

    def _theme_available(self, theme_id: str) -> bool:
        return any(option.theme_id == theme_id for option in self._theme_options)

    def _active_preference(self) -> ThemePreference:
        snapshot = self._theme_service.snapshot()
        return ThemePreference(self._theme_service.preference.mode, snapshot.theme_id)

    def _compensate_fallback(self, previous_active: ThemePreference) -> None:
        persisted = self._state.persisted_preference
        restored = self._theme_service.set_preference(previous_active, persist=False)
        if restored.status is not ThemeApplyStatus.FAILED:
            try:
                self._preferences.save_theme_preference(persisted.mode, persisted.theme_id)
            except Exception:
                pass

    def _ask_persist_failure(self, _result: ThemeApplyResult) -> PersistFailureChoice:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self._tr("主题未保存"))
        box.setText(self._tr("主题已应用到本次会话，但无法保存供下次启动使用。"))
        keep = box.addButton(self._tr("保留本次会话"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self._tr("恢复已保存主题"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return (
            PersistFailureChoice.KEEP_SESSION if box.clickedButton() is keep else PersistFailureChoice.RESTORE_PERSISTED
        )

    def _show_notice(self, message: str, *, error: bool = False) -> None:
        self._last_notice = message
        if hasattr(self, "_feedback"):
            self._feedback.setText(message)
            self._feedback.setProperty("tbStatusId", "error" if error else "info")
            update_accessible_state(self._feedback, message)
            ComponentStyle.apply_state(self._feedback, SemanticState.ERROR if error else SemanticState.INFO)

    def _dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        for controller in getattr(self, "_connection_controllers", ()):
            controller.close()
        try:
            self._theme_service.theme_changed.disconnect(self._on_active_theme_changed)
        except (TypeError, RuntimeError):
            pass
        self._preview.dispose()

    def _tr(self, msgid: str) -> str:
        return self._gettext(msgid)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0 and combo.currentIndex() != index:
            previous = combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(previous)


def _safe_apply_message(result: ThemeApplyResult, gettext: Callable[[str], str] | None = None) -> str:
    translate = gettext or (lambda value: value)
    diagnostics = set(result.diagnostics)
    if "theme_service_closed" in diagnostics or "theme_service_not_started" in diagnostics:
        return translate("主题服务当前不可用，请重新打开设置后重试。")
    if "theme_wrong_thread" in diagnostics:
        return translate("主题只能从主窗口应用，请稍后重试。")
    return translate("无法应用所选主题；当前主题保持不变。")


__all__ = [
    "PersistFailureChoice",
    "SettingsDialog",
    "ThemeOption",
    "UiSettingsDraft",
]
