from __future__ import annotations

import inspect
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ui.foundation.adapters import DomainBrushes
from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.qt_palette import compile_palette
from transbridge.ui.foundation.theme_service import ThemeSnapshot
from transbridge.ui.paratranz.string_navigation import NavItemDelegate
from transbridge.ui.paratranz.strings_tab import StringsTab
from transbridge.ui.paratranz.widget import ParaTranzWidget


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Context(QObject):
    paratranz_permissions_changed = pyqtSignal()
    project_selected = pyqtSignal(object)
    config_changed = pyqtSignal(object)
    project_list_changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.config = SimpleNamespace(token="", user_id=None)
        self.current_project = None
        self.current_user = None
        self.mine_project_ids = set()

    @staticmethod
    def is_admin() -> bool:
        return False

    @staticmethod
    def is_member() -> bool:
        return False


def _snapshot(scheme: ThemeScheme, revision: int) -> ThemeSnapshot:
    definition = create_builtin_registry().resolve(DEFAULT_THEME_ID, scheme)
    return ThemeSnapshot(
        revision=revision,
        provider_id=definition.manifest.provider_id,
        theme_id=definition.manifest.theme_id,
        effective_scheme=scheme,
        fingerprint=definition.fingerprint,
        tokens=definition.tokens,
        palette=compile_palette(definition),
        cache_namespace=f"test:{scheme.value}",
    )


class _Subscription:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _ThemeView:
    def __init__(self, snapshot: ThemeSnapshot) -> None:
        self._snapshot = snapshot
        self.callback = None
        self.subscription = _Subscription()

    def snapshot(self) -> ThemeSnapshot:
        return self._snapshot

    def domain_brushes(self, snapshot: ThemeSnapshot | None = None) -> DomainBrushes:
        return DomainBrushes(self._snapshot if snapshot is None else snapshot)

    def subscribe(self, _owner, callback):
        self.callback = callback
        return self.subscription

    def emit(self, snapshot: ThemeSnapshot) -> None:
        self._snapshot = snapshot
        self.callback(snapshot)


def test_stage_brush_refresh_preserves_rows_selection_and_payload_identity(qapp) -> None:
    light = DomainBrushes(_snapshot(ThemeScheme.LIGHT, 1))
    dark = DomainBrushes(_snapshot(ThemeScheme.DARK, 2))
    tab = StringsTab(_Context(), domain_brushes=light)
    records = [{"id": 1, "key": "one", "original": "Hello", "translation": "你好", "stage": 2}]
    tab._on_strings_loaded(records)
    tab._table.selectRow(0)
    item_ids = tuple(id(tab._table.item(0, column)) for column in range(tab._table.columnCount()))
    payload = records[0]
    before = tab._table.item(0, 3).foreground().color()

    tab.apply_domain_brushes(dark)

    assert tuple(id(tab._table.item(0, column)) for column in range(tab._table.columnCount())) == item_ids
    assert tab._strings[0] is payload is records[0]
    assert tab._table.item(0, 0).isSelected()
    assert tab._table.item(0, 3).foreground().color() != before
    assert "有疑问" in tab._table.item(0, 3).text()
    assert "状态：" in tab._table.item(0, 3).toolTip()
    assert "状态同时显示为文字" in tab._table.accessibleDescription()
    tab.close()


def test_navigation_delegate_compiles_brushes_outside_paint() -> None:
    delegate = NavItemDelegate(domain_brushes=DomainBrushes(_snapshot(ThemeScheme.LIGHT, 1)))
    first = delegate._normal_brush.color()

    delegate.apply_domain_brushes(DomainBrushes(_snapshot(ThemeScheme.DARK, 2)))
    paint_source = inspect.getsource(NavItemDelegate.paint)

    assert delegate._normal_brush.color() != first
    assert "QColor" not in paint_source
    assert "QBrush(" not in paint_source
    assert "QPen(" not in paint_source


def test_paratranz_root_refreshes_one_domain_cache_and_releases_subscription(qapp) -> None:
    theme_view = _ThemeView(_snapshot(ThemeScheme.LIGHT, 1))
    widget = ParaTranzWidget(_Context(), theme_view=theme_view)
    strings_identity = id(widget._strings)
    widget.show()

    theme_view.emit(_snapshot(ThemeScheme.DARK, 2))

    assert id(widget._strings) == strings_identity
    assert widget._domain_brushes.fingerprint == theme_view.snapshot().fingerprint
    assert widget.accessibleName() == "ParaTranz 管理"
    widget.close()
    qapp.processEvents()
    assert theme_view.subscription.closed
