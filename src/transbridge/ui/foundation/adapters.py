"""Narrow adapters for custom-painted and rich-text theme consumers."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
import weakref

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QBrush, QPen

from transbridge.infra.markdown_renderer import MarkdownRenderTheme

from .model import RgbaColor, StateStyle
from .qt_palette import qcolor
from .theme_service import ThemeService, ThemeSnapshot

ThemeCallback = Callable[[ThemeSnapshot], None]


class ThemeSubscription:
    """Idempotent signal connection handle owned by one QObject lifecycle."""

    def __init__(
        self,
        service: ThemeService,
        owner: QObject,
        callback: ThemeCallback,
        on_close: Callable[[ThemeSubscription], None],
        on_error: Callable[[str], None],
    ) -> None:
        self._service_ref = weakref.ref(service)
        self._owner_ref = weakref.ref(owner)
        self._on_close = on_close
        self._on_error = on_error
        self._closed = False
        bound_callback = getattr(callback, "__self__", None) is not None
        self._callback = None if bound_callback else callback
        self._callback_ref = weakref.WeakMethod(callback) if bound_callback else None

        def dispatch(_revision: int, snapshot: ThemeSnapshot) -> None:
            if self._closed or self._owner_ref() is None:
                self.close()
                return
            target = self._callback_ref() if self._callback_ref is not None else self._callback
            if target is None:
                self.close()
                return
            try:
                target(snapshot)
            except Exception:  # noqa: BLE001 - one subscriber must not stop the signal fanout
                self._on_error("theme_subscriber_failed")

        def owner_destroyed(_object=None) -> None:
            self.close()

        self._dispatch = dispatch
        self._owner_destroyed = owner_destroyed
        service.theme_changed.connect(dispatch)
        owner.destroyed.connect(owner_destroyed)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        service = self._service_ref()
        if service is not None:
            try:
                service.theme_changed.disconnect(self._dispatch)
            except (TypeError, RuntimeError):
                pass
        owner = self._owner_ref()
        if owner is not None:
            try:
                owner.destroyed.disconnect(self._owner_destroyed)
            except (TypeError, RuntimeError):
                pass
        self._callback = None
        self._on_close(self)


class ThemeView:
    """Read-only component-facing projection over the process ThemeService."""

    def __init__(
        self,
        service: ThemeService,
        *,
        domain_brush_cache: OrderedDict[str, DomainBrushes] | None = None,
    ) -> None:
        self._service = service
        self._subscriptions: set[ThemeSubscription] = set()
        self._owns_domain_brush_cache = domain_brush_cache is None
        self._domain_brush_cache = OrderedDict() if domain_brush_cache is None else domain_brush_cache
        self._diagnostics: set[str] = set()
        self._closed = False

    def snapshot(self) -> ThemeSnapshot:
        return self._service.snapshot()

    def subscribe(self, owner: QObject, callback: ThemeCallback) -> ThemeSubscription:
        if self._closed:
            raise RuntimeError("theme_view_closed")
        if not isinstance(owner, QObject):
            raise TypeError("theme subscription owner must be a QObject")
        if not callable(callback):
            raise TypeError("theme subscription callback must be callable")
        subscription = ThemeSubscription(self._service, owner, callback, self._forget, self._record_diagnostic)
        self._subscriptions.add(subscription)
        return subscription

    def domain_brushes(self, snapshot: ThemeSnapshot | None = None) -> DomainBrushes:
        """Return one bounded, revision-keyed domain brush compilation for this view tree."""

        current = self.snapshot() if snapshot is None else snapshot
        cached = self._domain_brush_cache.get(current.fingerprint)
        if cached is not None:
            self._domain_brush_cache.move_to_end(current.fingerprint)
            return cached
        compiled = DomainBrushes(current)
        self._domain_brush_cache[current.fingerprint] = compiled
        self._domain_brush_cache.move_to_end(current.fingerprint)
        while len(self._domain_brush_cache) > 4:
            self._domain_brush_cache.popitem(last=False)
        return compiled

    @property
    def active_subscription_count(self) -> int:
        return len(self._subscriptions)

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(sorted(self._diagnostics))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for subscription in tuple(self._subscriptions):
            subscription.close()
        if self._owns_domain_brush_cache:
            self._domain_brush_cache.clear()

    def _forget(self, subscription: ThemeSubscription) -> None:
        self._subscriptions.discard(subscription)

    def _record_diagnostic(self, code: str) -> None:
        self._diagnostics.add(code)


@dataclass(frozen=True, slots=True)
class DomainBrush:
    foreground: QBrush
    background: QBrush
    border: QBrush
    border_pen: QPen
    label_key: str
    icon_id: str | None


class DomainBrushes:
    """Compile domain colours once so paint/delegate lookups stay O(1)."""

    _CATEGORIES = ("stages", "labels", "diff", "translation", "task", "report")

    def __init__(self, snapshot: ThemeSnapshot) -> None:
        self.fingerprint = snapshot.fingerprint
        self._diagnostics: set[str] = set()
        self._values: dict[tuple[str, str], DomainBrush] = {}
        for category in self._CATEGORIES:
            for style in getattr(snapshot.tokens.domain, category):
                self._values[(category, style.key)] = self._compile(style)
        self._neutral = self._values.get(("labels", "neutral"))
        if self._neutral is None:
            semantic = snapshot.tokens.semantic
            self._neutral = self._compile(
                StateStyle(
                    key="neutral",
                    foreground=semantic.text_primary,
                    background=semantic.surface_alt,
                    border=semantic.border,
                    label_key="state.neutral",
                )
            )

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(sorted(self._diagnostics))

    def stage(self, key: str | int) -> DomainBrush:
        return self._get("stages", key)

    def label(self, key: str) -> DomainBrush:
        return self._get("labels", key)

    def diff(self, key: str) -> DomainBrush:
        return self._get("diff", key)

    def translation(self, key: str) -> DomainBrush:
        return self._get("translation", key)

    def task(self, key: str) -> DomainBrush:
        return self._get("task", key)

    def report(self, severity: str) -> DomainBrush:
        return self._get("report", severity)

    def _get(self, category: str, key: str | int) -> DomainBrush:
        normalized = str(key)
        value = self._values.get((category, normalized))
        if value is not None:
            return value
        self._diagnostics.add(f"theme_domain_state_unknown:{category}.{normalized}")
        return self._neutral

    @staticmethod
    def _compile(style: StateStyle) -> DomainBrush:
        foreground = _required_color(style.foreground)
        background = _required_color(style.background)
        border = _required_color(style.border)
        return DomainBrush(
            foreground=QBrush(qcolor(foreground)),
            background=QBrush(qcolor(background)),
            border=QBrush(qcolor(border)),
            border_pen=QPen(qcolor(border)),
            label_key=style.label_key,
            icon_id=style.icon_id,
        )


class RichTextThemeAdapter:
    """Compile immutable Markdown render themes once per theme fingerprint."""

    def __init__(self, *, max_fingerprints: int = 8) -> None:
        if max_fingerprints < 1:
            raise ValueError("max_fingerprints must be positive")
        self._max_fingerprints = max_fingerprints
        self._cache: OrderedDict[str, MarkdownRenderTheme] = OrderedDict()
        self._compile_count = 0

    @property
    def compile_count(self) -> int:
        return self._compile_count

    def theme(self, snapshot: ThemeSnapshot) -> MarkdownRenderTheme:
        cached = self._cache.get(snapshot.fingerprint)
        if cached is not None:
            self._cache.move_to_end(snapshot.fingerprint)
            return cached
        compiled = self._compile(snapshot)
        self._cache[snapshot.fingerprint] = compiled
        self._cache.move_to_end(snapshot.fingerprint)
        while len(self._cache) > self._max_fingerprints:
            self._cache.popitem(last=False)
        self._compile_count += 1
        return compiled

    def stylesheet(self, snapshot: ThemeSnapshot) -> str:
        return self.theme(snapshot).stylesheet

    @staticmethod
    def _compile(snapshot: ThemeSnapshot) -> MarkdownRenderTheme:
        semantic = snapshot.tokens.semantic
        foreground = _css_color(semantic.text_primary)
        surface = _css_color(semantic.surface)
        surface_alt = _css_color(semantic.surface_alt)
        border = _css_color(semantic.border)
        link = _css_color(semantic.link)
        stylesheet = (
            f"color: {foreground}; background-color: {surface}; "
            f"selection-color: {_css_color(semantic.selection_text)}; "
            f"selection-background-color: {_css_color(semantic.selection_background)};"
        )
        return MarkdownRenderTheme(
            fingerprint=snapshot.fingerprint,
            stylesheet=stylesheet,
            inline_code_style=(
                f"background-color:{surface_alt}; color:{foreground}; padding:1px 4px; "
                "border-radius:3px; font-family:Consolas,monospace; font-size:12px;"
            ),
            code_block_stylesheet=(
                "QTextEdit {"
                f"background-color: {surface_alt}; color: {foreground}; border: 1px solid {border};"
                "border-radius: 6px; padding: 8px;"
                "}"
            ),
            horizontal_rule_stylesheet=f"QFrame {{ border-color: {border}; margin: 8px 0; }}",
            link_color=link,
        )


def _required_color(value: object) -> RgbaColor:
    if not isinstance(value, RgbaColor):
        raise TypeError("ThemeSnapshot must contain normalized RgbaColor tokens")
    return value


def _css_color(value: object) -> str:
    return _required_color(value).canonical


__all__ = [
    "DomainBrush",
    "DomainBrushes",
    "RichTextThemeAdapter",
    "ThemeSubscription",
    "ThemeView",
]
