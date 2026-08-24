"""Single GUI-process owner for resolving and applying theme snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import get_ident

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QStyleFactory

from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode, UiPreferenceRepository

from .model import ThemeError, ThemeScheme, ThemeTokens
from .qt_palette import compile_palette
from .registry import ThemeRegistry
from .visual_style import compile_application_stylesheet


class ThemeApplyStatus(StrEnum):
    APPLIED = "applied"
    UNCHANGED = "unchanged"
    FALLBACK = "fallback"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ThemePreference:
    mode: ThemeMode = ThemeMode.SYSTEM
    theme_id: str = DEFAULT_THEME_ID


@dataclass(frozen=True, slots=True)
class ThemeSnapshot:
    revision: int
    provider_id: str
    theme_id: str
    effective_scheme: ThemeScheme
    fingerprint: str
    tokens: ThemeTokens
    palette: QPalette
    cache_namespace: str


@dataclass(frozen=True, slots=True)
class ThemeApplyResult:
    status: ThemeApplyStatus
    persisted: bool
    snapshot: ThemeSnapshot | None
    diagnostics: tuple[str, ...] = ()


class ThemeService(QObject):
    """Resolve one immutable snapshot and apply one application palette revision."""

    theme_changed = pyqtSignal(int, object)

    def __init__(
        self,
        application: QApplication,
        registry: ThemeRegistry,
        preferences: UiPreferenceRepository,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if application is None:
            raise RuntimeError("theme_application_missing")
        self._application = application
        self._registry = registry
        self._preferences = preferences
        self._owner_thread_id = get_ident()
        self._owner_qt_thread = application.thread()
        self._require_owner_thread()
        self._preference = ThemePreference()
        self._snapshot: ThemeSnapshot | None = None
        self._last_good_palette = QPalette(application.palette())
        self._initial_stylesheet = application.styleSheet()
        self._last_good_stylesheet = self._initial_stylesheet
        self._palette_cache: dict[str, QPalette] = {}
        self._stylesheet_cache: dict[str, str] = {}
        self._started = False
        self._closed = False
        self._style_hints_connected = False

    def start(self) -> ThemeApplyResult:
        self._require_owner_thread()
        if self._closed:
            return ThemeApplyResult(ThemeApplyStatus.FAILED, False, self._snapshot, ("theme_service_closed",))
        if self._started:
            status = ThemeApplyStatus.UNCHANGED if self._snapshot is not None else ThemeApplyStatus.FAILED
            diagnostics = () if self._snapshot is not None else ("theme_service_start_failed",)
            return ThemeApplyResult(status, self._snapshot is not None, self._snapshot, diagnostics)
        try:
            configured = self._preferences.load()
            self._preference = ThemePreference(configured.theme_mode, configured.theme_id)
            preference_diagnostics = configured.diagnostics
        except Exception:
            self._preference = ThemePreference()
            preference_diagnostics = ("theme_preference_load_failed",)
        style_diagnostics = self._set_fusion_style()
        self._sync_system_signal(self._preference.mode)
        self._started = True
        return self._apply(
            self._preference,
            persist=False,
            inherited_diagnostics=preference_diagnostics + style_diagnostics,
        )

    def set_preference(self, preference: ThemePreference, *, persist: bool = True) -> ThemeApplyResult:
        self._require_owner_thread()
        if self._closed:
            return ThemeApplyResult(ThemeApplyStatus.FAILED, False, self._snapshot, ("theme_service_closed",))
        if not self._started:
            return ThemeApplyResult(ThemeApplyStatus.FAILED, False, self._snapshot, ("theme_service_not_started",))
        if not isinstance(preference, ThemePreference):
            raise TypeError("preference must be a ThemePreference")
        result = self._apply(preference, persist=persist)
        self._sync_system_signal(self._preference.mode)
        return result

    def preview(self, preference: ThemePreference) -> ThemeSnapshot:
        self._require_owner_thread()
        definition, _diagnostics = self._resolve(preference)
        palette = self._compiled_palette(definition.fingerprint, definition)
        revision = 0 if self._snapshot is None else self._snapshot.revision
        return self._make_snapshot(revision, definition, palette)

    def snapshot(self) -> ThemeSnapshot:
        if self._snapshot is None:
            raise RuntimeError("theme_service_not_started")
        return self._snapshot

    @property
    def preference(self) -> ThemePreference:
        return self._preference

    def close(self) -> None:
        if self._closed:
            return
        self._require_owner_thread()
        if self._style_hints_connected:
            signal = getattr(self._application.styleHints(), "colorSchemeChanged", None)
            if signal is not None:
                try:
                    signal.disconnect(self._on_system_scheme_changed)
                except (TypeError, RuntimeError):
                    pass
        self._style_hints_connected = False
        self._palette_cache.clear()
        self._stylesheet_cache.clear()
        if self._application.styleSheet() == self._last_good_stylesheet:
            self._application.setStyleSheet(self._initial_stylesheet)
        self._closed = True

    def _apply(
        self,
        preference: ThemePreference,
        *,
        persist: bool,
        inherited_diagnostics: tuple[str, ...] = (),
        system_scheme: Qt.ColorScheme | None = None,
    ) -> ThemeApplyResult:
        try:
            definition, resolve_diagnostics = self._resolve(preference, system_scheme=system_scheme)
            palette = self._compiled_palette(definition.fingerprint, definition)
            stylesheet = self._compiled_stylesheet(definition.fingerprint, definition, palette)
        except Exception as exc:
            code = getattr(getattr(exc, "code", None), "value", None) or getattr(exc, "code", None)
            return ThemeApplyResult(
                ThemeApplyStatus.FAILED,
                False,
                self._snapshot,
                inherited_diagnostics + (str(code or "theme_resolve_failed"),),
            )

        preference_changed = preference != self._preference
        diagnostics = inherited_diagnostics + resolve_diagnostics
        fallback_applied = bool(diagnostics)
        if self._snapshot is not None and definition.fingerprint == self._snapshot.fingerprint:
            persisted = True
            if persist and preference_changed:
                persisted, diagnostics = self._persist(preference, diagnostics)
            self._preference = preference
            return ThemeApplyResult(ThemeApplyStatus.UNCHANGED, persisted, self._snapshot, diagnostics)

        previous_palette = QPalette(self._application.palette())
        previous_stylesheet = self._application.styleSheet()
        stylesheet_changed = previous_stylesheet != stylesheet
        try:
            self._application.setPalette(palette)
            if stylesheet_changed:
                self._application.setStyleSheet(stylesheet)
        except Exception:
            try:
                self._application.setPalette(
                    previous_palette if self._snapshot is not None else self._last_good_palette
                )
                if stylesheet_changed:
                    self._application.setStyleSheet(
                        previous_stylesheet if self._snapshot is not None else self._last_good_stylesheet
                    )
            except Exception:
                pass
            return ThemeApplyResult(
                ThemeApplyStatus.FAILED,
                False,
                self._snapshot,
                diagnostics + ("theme_apply_failed",),
            )

        revision = 1 if self._snapshot is None else self._snapshot.revision + 1
        candidate = self._make_snapshot(revision, definition, palette)
        self._snapshot = candidate
        self._last_good_palette = QPalette(palette)
        self._last_good_stylesheet = stylesheet
        persisted = True
        if persist and preference_changed:
            persisted, diagnostics = self._persist(preference, diagnostics)
        self._preference = preference
        self.theme_changed.emit(candidate.revision, candidate)
        status = ThemeApplyStatus.FALLBACK if fallback_applied else ThemeApplyStatus.APPLIED
        return ThemeApplyResult(status, persisted, candidate, diagnostics)

    def _resolve(self, preference: ThemePreference, *, system_scheme: Qt.ColorScheme | None = None):
        scheme = self._effective_scheme(preference.mode, system_scheme=system_scheme)
        try:
            return self._registry.resolve(preference.theme_id, scheme), ()
        except ThemeError as exc:
            if preference.theme_id == DEFAULT_THEME_ID:
                raise
            fallback = self._registry.resolve(DEFAULT_THEME_ID, scheme)
            return fallback, (getattr(exc.code, "value", str(exc.code)), "theme_fallback_builtin")

    def _effective_scheme(
        self,
        mode: ThemeMode,
        *,
        system_scheme: Qt.ColorScheme | None = None,
    ) -> ThemeScheme:
        if mode is ThemeMode.LIGHT:
            return ThemeScheme.LIGHT
        if mode is ThemeMode.DARK:
            return ThemeScheme.DARK
        color_scheme = (
            system_scheme
            if system_scheme is not None
            else getattr(self._application.styleHints(), "colorScheme", lambda: None)()
        )
        dark = getattr(Qt.ColorScheme, "Dark", None)
        return ThemeScheme.DARK if color_scheme == dark else ThemeScheme.LIGHT

    def _compiled_palette(self, fingerprint: str, definition) -> QPalette:
        cached = self._palette_cache.get(fingerprint)
        if cached is None:
            cached = compile_palette(definition)
            self._palette_cache[fingerprint] = cached
        return QPalette(cached)

    def _compiled_stylesheet(self, fingerprint: str, definition, palette: QPalette) -> str:
        cached = self._stylesheet_cache.get(fingerprint)
        if cached is None:
            snapshot = self._make_snapshot(0, definition, palette)
            cached = compile_application_stylesheet(snapshot)
            self._stylesheet_cache[fingerprint] = cached
        return cached

    @staticmethod
    def _make_snapshot(revision: int, definition, palette: QPalette) -> ThemeSnapshot:
        return ThemeSnapshot(
            revision=revision,
            provider_id=definition.manifest.provider_id,
            theme_id=definition.manifest.theme_id,
            effective_scheme=definition.scheme,
            fingerprint=definition.fingerprint,
            tokens=definition.tokens,
            palette=QPalette(palette),
            cache_namespace=f"{definition.manifest.theme_id}:{definition.fingerprint[:16]}",
        )

    def _persist(self, preference: ThemePreference, diagnostics: tuple[str, ...]) -> tuple[bool, tuple[str, ...]]:
        try:
            result = self._preferences.save_theme_preference(preference.mode, preference.theme_id)
        except Exception:
            return False, diagnostics + ("ui_theme_preference_write_failed",)
        if result.saved:
            return True, diagnostics
        return False, diagnostics + (result.diagnostic_code or "ui_theme_preference_write_failed",)

    def _set_fusion_style(self) -> tuple[str, ...]:
        try:
            if QStyleFactory.create("Fusion") is None:
                return ("theme_fusion_style_unavailable",)
            self._application.setStyle("Fusion")
        except Exception:
            return ("theme_fusion_style_unavailable",)
        return ()

    def _sync_system_signal(self, mode: ThemeMode) -> None:
        if mode is ThemeMode.SYSTEM:
            self._connect_system_signal()
        else:
            self._disconnect_system_signal()

    def _connect_system_signal(self) -> None:
        if self._style_hints_connected:
            return
        signal = getattr(self._application.styleHints(), "colorSchemeChanged", None)
        if signal is not None:
            signal.connect(self._on_system_scheme_changed)
            self._style_hints_connected = True

    def _disconnect_system_signal(self) -> None:
        if not self._style_hints_connected:
            return
        signal = getattr(self._application.styleHints(), "colorSchemeChanged", None)
        if signal is not None:
            try:
                signal.disconnect(self._on_system_scheme_changed)
            except (TypeError, RuntimeError):
                pass
        self._style_hints_connected = False

    def _on_system_scheme_changed(self, scheme: Qt.ColorScheme) -> None:
        if self._closed or self._preference.mode is not ThemeMode.SYSTEM:
            return
        self._require_owner_thread()
        self._apply(self._preference, persist=False, system_scheme=scheme)

    def _require_owner_thread(self) -> None:
        if get_ident() != self._owner_thread_id or QThread.currentThread() != self._owner_qt_thread:
            raise RuntimeError("theme_wrong_thread")


__all__ = [
    "ThemeApplyResult",
    "ThemeApplyStatus",
    "ThemePreference",
    "ThemeService",
    "ThemeSnapshot",
]
