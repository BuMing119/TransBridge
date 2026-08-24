from __future__ import annotations

from collections import OrderedDict
import inspect
from pathlib import Path
import re

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QTextEdit, QWidget
import pytest

from transbridge.infra.markdown_renderer import MarkdownRenderer
from transbridge.ui.foundation.adapters import DomainBrushes, RichTextThemeAdapter, ThemeView
from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.qt_palette import compile_palette
from transbridge.ui.foundation.theme_service import ThemeSnapshot


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _snapshot(scheme: ThemeScheme, revision: int = 1) -> ThemeSnapshot:
    definition = create_builtin_registry().resolve(DEFAULT_THEME_ID, scheme)
    return ThemeSnapshot(
        revision=revision,
        provider_id=definition.manifest.provider_id,
        theme_id=definition.manifest.theme_id,
        effective_scheme=scheme,
        fingerprint=definition.fingerprint,
        tokens=definition.tokens,
        palette=compile_palette(definition),
        cache_namespace=f"test:{definition.fingerprint[:16]}",
    )


class _FakeThemeService(QObject):
    theme_changed = pyqtSignal(int, object)

    def __init__(self, snapshot: ThemeSnapshot) -> None:
        super().__init__()
        self._snapshot = snapshot

    def snapshot(self) -> ThemeSnapshot:
        return self._snapshot

    def publish(self, snapshot: ThemeSnapshot) -> None:
        self._snapshot = snapshot
        self.theme_changed.emit(snapshot.revision, snapshot)


class _Owner(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.received: list[str] = []

    def update_theme(self, snapshot: ThemeSnapshot) -> None:
        self.received.append(snapshot.fingerprint)


def _delete(owner: QObject) -> None:
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()


def test_theme_view_subscription_is_idempotent_and_owner_bound(qapp) -> None:
    light = _snapshot(ThemeScheme.LIGHT)
    dark = _snapshot(ThemeScheme.DARK, revision=2)
    service = _FakeThemeService(light)
    view = ThemeView(service)  # type: ignore[arg-type]
    owner = _Owner()
    subscription = view.subscribe(owner, owner.update_theme)

    service.publish(dark)
    assert owner.received == [dark.fingerprint]
    assert view.active_subscription_count == 1

    subscription.close()
    subscription.close()
    service.publish(light)
    assert owner.received == [dark.fingerprint]
    assert view.active_subscription_count == 0
    _delete(owner)
    view.close()


def test_owner_destruction_releases_100_subscriptions(qapp) -> None:
    service = _FakeThemeService(_snapshot(ThemeScheme.LIGHT))
    view = ThemeView(service)  # type: ignore[arg-type]
    for _ in range(100):
        owner = _Owner()
        view.subscribe(owner, owner.update_theme)
        _delete(owner)
    assert view.active_subscription_count == 0
    view.close()


def test_bad_subscriber_does_not_stop_other_subscribers(qapp) -> None:
    service = _FakeThemeService(_snapshot(ThemeScheme.LIGHT))
    view = ThemeView(service)  # type: ignore[arg-type]
    broken_owner = QObject()
    healthy_owner = _Owner()
    view.subscribe(broken_owner, lambda _snapshot: (_ for _ in ()).throw(RuntimeError("boom")))
    view.subscribe(healthy_owner, healthy_owner.update_theme)

    service.publish(_snapshot(ThemeScheme.DARK, revision=2))

    assert len(healthy_owner.received) == 1
    assert view.diagnostics == ("theme_subscriber_failed",)
    _delete(broken_owner)
    _delete(healthy_owner)
    view.close()


def test_domain_brushes_compile_each_state_and_aggregate_unknown_diagnostics(qapp) -> None:
    light = DomainBrushes(_snapshot(ThemeScheme.LIGHT))
    dark = DomainBrushes(_snapshot(ThemeScheme.DARK))

    assert light.stage(5).label_key == "translation.stage.reviewed"
    assert light.task("failed").icon_id == "task.failed"
    assert light.report("warning").foreground.color() != dark.report("warning").foreground.color()
    fallback = light.diff("not-a-state")
    assert fallback.label_key == "label.neutral"
    light.diff("not-a-state")
    assert light.diagnostics == ("theme_domain_state_unknown:diff.not-a-state",)


def test_theme_view_shares_bounded_domain_brushes_by_fingerprint(qapp) -> None:
    service = _FakeThemeService(_snapshot(ThemeScheme.LIGHT))
    view = ThemeView(service)  # type: ignore[arg-type]

    first = view.domain_brushes()
    assert view.domain_brushes() is first

    dark = _snapshot(ThemeScheme.DARK, revision=2)
    assert view.domain_brushes(dark) is view.domain_brushes(dark)
    assert len(view._domain_brush_cache) == 2

    view.close()
    assert not view._domain_brush_cache


def test_theme_views_can_share_foundation_owned_domain_brush_cache(qapp) -> None:
    service = _FakeThemeService(_snapshot(ThemeScheme.LIGHT))
    shared = OrderedDict()
    first_view = ThemeView(service, domain_brush_cache=shared)  # type: ignore[arg-type]
    second_view = ThemeView(service, domain_brush_cache=shared)  # type: ignore[arg-type]

    assert first_view.domain_brushes() is second_view.domain_brushes()
    first_view.close()
    assert shared
    second_view.close()
    shared.clear()


def test_rich_text_theme_compiles_once_per_fingerprint_and_renderer_accepts_injection(qapp) -> None:
    adapter = RichTextThemeAdapter(max_fingerprints=2)
    light = _snapshot(ThemeScheme.LIGHT)
    dark = _snapshot(ThemeScheme.DARK)
    first = adapter.theme(light)
    repeated = adapter.theme(light)

    assert first is repeated
    assert adapter.compile_count == 1
    assert adapter.stylesheet(light) == first.stylesheet
    assert adapter.compile_count == 1
    assert adapter.theme(dark).fingerprint == dark.fingerprint
    assert adapter.compile_count == 2

    themed = MarkdownRenderer(first).render("`inline`\n\n```python\nprint('ok')\n```")
    code = themed.findChild(QTextEdit)
    assert code is not None
    assert first.code_block_stylesheet == code.styleSheet()
    assert first.stylesheet == themed.styleSheet()
    assert isinstance(MarkdownRenderer().render("兼容默认渲染"), QWidget)


def test_markdown_renderer_has_no_hard_coded_theme_colours() -> None:
    source = Path(inspect.getsourcefile(MarkdownRenderer) or "").read_text(encoding="utf-8")
    assert re.search(r"#[0-9a-fA-F]{3,8}", source) is None
