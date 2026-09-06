from __future__ import annotations

from time import perf_counter

import pytest

from transbridge.application.history_search import (
    HistoryDiagnostic,
    HistoryEntryKind,
    HistoryQuery,
    HistorySearchScopeKind,
    HistorySourceRef,
    HistorySourceType,
    SourceRecord,
)
from transbridge.application.tasks import TaskCancelled
from transbridge.persistence.history_search import SqliteHistorySearchIndex


def _record(
    source_id: str,
    original: str,
    translation: str,
    *,
    kind: HistoryEntryKind = HistoryEntryKind.TRANSLATION,
    scope: str = "",
    project_id: str | None = None,
    project_name: str | None = None,
    dictionary_id: str | None = None,
) -> SourceRecord:
    if kind is HistoryEntryKind.TERM:
        source_type = HistorySourceType.TERMINOLOGY
    elif project_id:
        source_type = HistorySourceType.PROJECT_VARIANT
    else:
        source_type = HistorySourceType.DICTIONARY
    return SourceRecord(
        kind,
        original,
        translation,
        HistorySourceRef(
            source_type,
            source_id,
            source_id,
            project_id=project_id,
            project_name=project_name,
            dictionary_id=dictionary_id,
        ),
        source_locale="en",
        target_locale="zh-CN",
        scope_key=scope,
    )


def test_index_queries_both_sides_merges_sources_and_marks_alternatives(tmp_path) -> None:
    index = SqliteHistorySearchIndex(tmp_path / "history.sqlite3")
    index.replace(
        (
            _record("project", "Skyrim Coast", "天际海岸"),
            _record("dictionary", "ＳＫＹＲＩＭ Coast", "天际海岸"),
            _record("alternate", "Skyrim Coast", "天际沿岸"),
            _record("term", "Skyrim", "天际", kind=HistoryEntryKind.TERM, scope="project:p1:project"),
        ),
        (HistoryDiagnostic("SOURCE_WARN", "one warning"),),
        built_at="2026-09-06T00:00:00+00:00",
    )

    page = index.query(HistoryQuery("SKYRIM"))

    assert page.total == 3
    coast = next(item for item in page.items if item.translation == "天际海岸")
    assert {item.source_id for item in coast.sources} == {"project", "dictionary"}
    assert coast.has_alternatives
    assert next(item for item in page.items if item.translation == "天际沿岸").has_alternatives
    assert index.query(HistoryQuery("沿岸")).items[0].translation == "天际沿岸"
    assert index.status().diagnostics[0].code == "SOURCE_WARN"


def test_unknown_project_locale_merges_only_when_one_language_direction_is_proven(tmp_path) -> None:
    index = SqliteHistorySearchIndex(tmp_path / "history.sqlite3")
    project = _record("project", "Dragon", "龙")
    project = SourceRecord(
        project.kind,
        project.original,
        project.translation,
        project.source,
    )
    index.replace(
        (project, _record("dictionary", "Dragon", "龙")),
        (),
        built_at="now",
    )

    page = index.query(HistoryQuery("Dragon"))

    assert page.total == 1
    assert {source.source_id for source in page.items[0].sources} == {"project", "dictionary"}


def test_kind_scope_and_like_metacharacters_remain_isolated(tmp_path) -> None:
    index = SqliteHistorySearchIndex(tmp_path / "history.sqlite3")
    index.replace(
        (
            _record("translation", "100%_real", "译文"),
            _record("term-project", "100%_real", "术语", kind=HistoryEntryKind.TERM, scope="project:p1:project"),
            _record("term-plugin", "100%_real", "术语", kind=HistoryEntryKind.TERM, scope="project:p1:plugin:a"),
        ),
        (),
        built_at="now",
    )

    page = index.query(HistoryQuery("%_", kind=HistoryEntryKind.TERM))

    assert page.total == 2
    assert {item.scope_key for item in page.items} == {"project:p1:project", "project:p1:plugin:a"}


def test_empty_keyword_browses_all_and_scope_filters_projects_with_terms_or_one_dictionary(tmp_path) -> None:
    index = SqliteHistorySearchIndex(tmp_path / "history.sqlite3")
    index.replace(
        (
            _record("p1-translation", "Dragon", "巨龙", project_id="p1", project_name="Skyrim 汉化"),
            _record(
                "p1-term",
                "Dragonborn",
                "龙裔",
                kind=HistoryEntryKind.TERM,
                scope="project:p1:project",
                project_id="p1",
                project_name="Skyrim 汉化",
            ),
            _record("p2-translation", "Guard", "守卫", project_id="p2", project_name="另一项目"),
            _record("dict-entry", "Priest", "祭司", dictionary_id="Skyrim"),
        ),
        (),
        built_at="now",
    )

    scopes = index.scopes()

    assert {(scope.kind, scope.scope_id) for scope in scopes} == {
        (HistorySearchScopeKind.PROJECT, "p2"),
        (HistorySearchScopeKind.PROJECT, "p1"),
        (HistorySearchScopeKind.DICTIONARY, "Skyrim"),
    }
    project = next(scope for scope in scopes if scope.scope_id == "p1")
    dictionary = next(scope for scope in scopes if scope.kind is HistorySearchScopeKind.DICTIONARY)
    assert {item.translation for item in index.query(HistoryQuery("", scope=project)).items} == {"巨龙", "龙裔"}
    assert [item.translation for item in index.query(HistoryQuery("", scope=dictionary)).items] == ["祭司"]
    assert index.query(HistoryQuery("")).total == 4


def test_cancelled_rebuild_preserves_previous_complete_index(tmp_path) -> None:
    index = SqliteHistorySearchIndex(tmp_path / "history.sqlite3")
    index.replace((_record("old", "old", "旧"),), (), built_at="old")

    class CancelBeforeReplace:
        calls = 0

        def raise_if_cancelled(self):
            self.calls += 1
            if self.calls >= 2:
                raise TaskCancelled("stop")

    with pytest.raises(TaskCancelled):
        index.replace((_record("new", "new", "新"),), (), built_at="new", cancellation=CancelBeforeReplace())

    assert index.query(HistoryQuery("old")).total == 1
    assert index.query(HistoryQuery("new")).total == 0


def test_keyword_query_meets_first_screen_budget_on_ten_thousand_records(tmp_path) -> None:
    index = SqliteHistorySearchIndex(tmp_path / "history.sqlite3")
    records = tuple(_record(f"source-{i}", f"Original {i}", f"译文 {i}") for i in range(10_000))
    index.replace(records, (), built_at="now")

    started = perf_counter()
    page = index.query(HistoryQuery("Original 9999"))
    elapsed = perf_counter() - started

    assert page.total == 1
    assert page.items[0].translation == "译文 9999"
    assert elapsed < 0.5

    browse_started = perf_counter()
    browse_page = index.query(HistoryQuery(""))
    browse_elapsed = perf_counter() - browse_started

    assert len(browse_page.items) == 200
    assert browse_page.truncated
    assert browse_elapsed < 0.5
