from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from transbridge.application.history_search import (
    HistoryEntryKind,
    HistorySearchHit,
    HistorySearchPage,
    HistorySearchScope,
    HistorySearchScopeKind,
    HistorySourceRef,
    HistorySourceType,
    IndexStatus,
)
from transbridge.application.tasks import OwnerRef
from transbridge.ui.tools.history_search import HistorySearchWindow, window as window_module


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


class _Subscription:
    closed = False

    def close(self):
        self.closed = True


class _Runtime:
    def __init__(self):
        self.subscription = _Subscription()

    def subscribe(self, *_args, **_kwargs):
        return self.subscription


class _Tasks:
    def __init__(self):
        self.runtime = _Runtime()


class _Index:
    def __init__(self):
        self.queries = []

    def status(self):
        return IndexStatus(True, 1, "now")

    def scopes(self):
        return (
            HistorySearchScope(HistorySearchScopeKind.PROJECT, "project-1", "Project"),
            HistorySearchScope(HistorySearchScopeKind.DICTIONARY, "Skyrim", "Skyrim"),
        )

    def query(self, request):
        self.queries.append(request)
        return HistorySearchPage((), 0)


def _hit(translation="天际"):
    return HistorySearchHit(
        HistoryEntryKind.TRANSLATION,
        "Skyrim",
        translation,
        "en",
        "zh-CN",
        "",
        "TRANSLATED",
        (
            HistorySourceRef(
                HistorySourceType.PROJECT_VARIANT,
                "source",
                "Project / Variant / Skyrim.esm",
                project_id="project-1",
                project_name="Project",
                plugin_id="Skyrim.esm",
            ),
        ),
    )


def test_window_renders_sources_copies_translation_and_discards_stale_result(qapp) -> None:
    tasks = _Tasks()
    window = HistorySearchWindow(_Index(), tasks, OwnerRef("owner", "history-search"))
    window._generation = 2

    window._accept_query(1, HistorySearchPage((_hit("旧结果"),), 1))
    assert window.results.rowCount() == 0

    window._accept_query(2, HistorySearchPage((_hit(),), 300))
    assert window.results.rowCount() == 1
    assert window.results.item(0, 0).text() == "Skyrim.esm"
    assert window.sources.count() == 1
    assert "当前显示前 1 条" in window.status_label.text()

    window.copy_translation()
    assert qapp.clipboard().text() == "天际"

    window.close()
    assert tasks.runtime.subscription.closed


def test_window_defaults_to_all_content_and_keeps_scope_per_window(qapp) -> None:
    class _InlinePool:
        @staticmethod
        def start(runnable):
            runnable.run()

    index = _Index()
    window = HistorySearchWindow(index, _Tasks(), OwnerRef("owner", "history-search"))
    window._pool = _InlinePool()

    assert window.search_edit.text() == ""
    assert window.scope_combo.currentData() is None
    assert window.kind_combo.currentData() is None
    assert [window.scope_combo.itemText(i) for i in range(window.scope_combo.count())] == [
        "全部来源",
        "项目｜Project",
        "词典｜Skyrim",
    ]

    window.scope_combo.setCurrentIndex(1)
    window._start_query()

    assert any(
        request.keyword == "" and request.scope is not None and request.scope.scope_id == "project-1"
        for request in index.queries
    )
    window.close()


def test_window_applies_and_clears_its_explicit_taskbar_identity(qapp, monkeypatch) -> None:
    applied = []
    cleared = []
    monkeypatch.setattr(
        window_module,
        "set_window_app_user_model_id",
        lambda window, app_id: applied.append((window, app_id)) or True,
    )
    monkeypatch.setattr(
        window_module,
        "clear_window_app_user_model_id",
        lambda window: cleared.append(window) or True,
    )

    window = HistorySearchWindow(
        _Index(),
        _Tasks(),
        OwnerRef("owner", "history-search"),
        taskbar_app_user_model_id="TransBridge.HistorySearch.7",
    )

    assert applied == [(window, "TransBridge.HistorySearch.7")]
    window.close()
    assert cleared == [window]
