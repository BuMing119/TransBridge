from __future__ import annotations

from threading import Thread

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.icons import DEFAULT_ICON_CACHE_BYTES, IconProvider
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.qt_palette import compile_palette
from transbridge.ui.foundation.theme_service import ThemeSnapshot


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _snapshot(scheme: ThemeScheme) -> ThemeSnapshot:
    definition = create_builtin_registry().resolve(DEFAULT_THEME_ID, scheme)
    return ThemeSnapshot(
        revision=1,
        provider_id=definition.manifest.provider_id,
        theme_id=definition.manifest.theme_id,
        effective_scheme=scheme,
        fingerprint=definition.fingerprint,
        tokens=definition.tokens,
        palette=compile_palette(definition),
        cache_namespace=f"test:{definition.fingerprint[:16]}",
    )


def _renderer(calls: list[tuple[str, int, float, str]]):
    def render(icon_id: str, size: QSize, dpr: float, state: str, _snapshot: ThemeSnapshot) -> QPixmap:
        calls.append((icon_id, size.width(), dpr, state))
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        return pixmap

    return render


def test_default_budget_and_cache_key_include_fingerprint_size_dpr_and_state(qapp) -> None:
    calls: list[tuple[str, int, float, str]] = []
    provider = IconProvider(_renderer(calls))
    light = _snapshot(ThemeScheme.LIGHT)
    dark = _snapshot(ThemeScheme.DARK)

    first = provider.pixmap("status.running", 16, 1.0, "normal", light)
    repeated = provider.pixmap("status.running", 16, 1.0, "normal", light)
    provider.pixmap("status.running", 16, 2.0, "normal", light)
    provider.pixmap("status.running", 16, 1.0, "disabled", light)
    provider.pixmap("status.running", 16, 1.0, "normal", dark)

    assert first.cacheKey() == repeated.cacheKey()
    assert len(calls) == 4
    assert provider.stats.hits == 1
    assert provider.stats.misses == 4
    assert provider.stats.max_cost_bytes == DEFAULT_ICON_CACHE_BYTES


def test_cost_aware_lru_evicts_and_rejects_oversized_items(qapp) -> None:
    calls: list[tuple[str, int, float, str]] = []
    snapshot = _snapshot(ThemeScheme.LIGHT)
    provider = IconProvider(_renderer(calls), max_cost_bytes=2200)
    for icon_id in ("one", "two", "three"):
        provider.pixmap(icon_id, 16, 1.0, "normal", snapshot)

    assert provider.stats.entries == 2
    assert provider.stats.cost_bytes <= 2200
    assert provider.stats.evictions == 1
    provider.pixmap("one", 16, 1.0, "normal", snapshot)
    assert calls.count(("one", 16, 1.0, "normal")) == 2

    uncached = IconProvider(_renderer([]), max_cost_bytes=1024, max_item_cost_bytes=512)
    uncached.pixmap("large", 32, 1.0, "normal", snapshot)
    uncached.pixmap("large", 32, 1.0, "normal", snapshot)
    assert uncached.stats.entries == 0
    assert uncached.stats.uncached == 2


def test_100_theme_round_trips_remain_bounded_after_two_namespace_warmup(qapp) -> None:
    provider = IconProvider(_renderer([]), max_cost_bytes=4096)
    light = _snapshot(ThemeScheme.LIGHT)
    dark = _snapshot(ThemeScheme.DARK)
    provider.pixmap("status", 16, 1.0, "normal", light)
    provider.pixmap("status", 16, 1.0, "normal", dark)
    warmed = provider.stats

    for index in range(100):
        provider.pixmap("status", 16, 1.0, "normal", light if index % 2 == 0 else dark)

    assert provider.stats.entries == warmed.entries == 2
    assert provider.stats.cost_bytes == warmed.cost_bytes


def test_unknown_icon_uses_one_missing_icon_diagnostic(qapp) -> None:
    provider = IconProvider()
    snapshot = _snapshot(ThemeScheme.LIGHT)

    pixmap = provider.pixmap("missing", 16, 1.0, "normal", snapshot)
    provider.pixmap("missing", 16, 1.0, "normal", snapshot)

    assert not pixmap.isNull()
    assert provider.diagnostics == ("theme_icon_unknown:missing",)


def test_pixmap_creation_is_rejected_outside_the_gui_thread(qapp) -> None:
    provider = IconProvider(_renderer([]))
    snapshot = _snapshot(ThemeScheme.LIGHT)
    errors: list[Exception] = []

    def call_from_worker() -> None:
        try:
            provider.pixmap("worker", 16, 1.0, "normal", snapshot)
        except Exception as exc:  # noqa: BLE001 - assertion captures the stable boundary error
            errors.append(exc)

    worker = Thread(target=call_from_worker)
    worker.start()
    worker.join()

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert str(errors[0]) == "theme_wrong_thread"

    construction_errors: list[Exception] = []

    def construct_from_worker() -> None:
        try:
            IconProvider(_renderer([]))
        except Exception as exc:  # noqa: BLE001 - assertion captures the stable boundary error
            construction_errors.append(exc)

    constructor_worker = Thread(target=construct_from_worker)
    constructor_worker.start()
    constructor_worker.join()
    assert len(construction_errors) == 1
    assert str(construction_errors[0]) == "theme_wrong_thread"
