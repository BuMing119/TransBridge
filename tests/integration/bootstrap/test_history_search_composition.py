from __future__ import annotations

from contextlib import closing
import sqlite3

from transbridge.application.history_search import (
    HistoryEntryKind,
    HistoryQuery,
    HistorySourceRef,
    HistorySourceType,
    SourceRecord,
)
from transbridge.application.tasks import OwnerRef, RuntimeTaskBridge
from transbridge.bootstrap import build_runtime
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.history_search import SqliteHistorySearchIndex
from transbridge.translation_memory.library import DictionaryLibrary


def test_runtime_rebuilds_retired_dictionary_projection_from_sqlite_library(tmp_path) -> None:
    dictionaries = tmp_path / "dictionaries"
    library = DictionaryLibrary(dictionaries)
    library.save([TranslationEntry("entry", "entry", "Dragonborn", "龙裔", 1, "MESG:FULL")], "Skyrim")
    persistence_root = tmp_path / "data"
    old_index = SqliteHistorySearchIndex(persistence_root / "history-search" / "history.sqlite3")
    stale_record = SourceRecord(
        HistoryEntryKind.TRANSLATION,
        "Old dictionary entry",
        "旧词条",
        HistorySourceRef(HistorySourceType.DICTIONARY, "Skyrim:entry", "Skyrim", dictionary_id="Skyrim"),
    )
    old_index.replace((stale_record,), (), built_at="old")
    with closing(sqlite3.connect(old_index.path)) as db:
        db.execute("UPDATE metadata SET value = '2' WHERE key = 'schema_version'")
        db.commit()

    runtime = build_runtime({
        "persistence_v2_root": persistence_root,
        "translation_memory_root": dictionaries,
    })
    try:
        search = runtime.use_cases.resolve("history_search")
        assert not search.status().ready
        assert not search.scopes()
        assert not search.query(HistoryQuery("")).items

        owner = OwnerRef("integration", "history-search")
        deferred = runtime.use_cases.resolve("history_search_tasks").refresh(owner)

        outcome = RuntimeTaskBridge(runtime.tasks).wait_terminal(deferred.ref, owner, timeout=5)
        page = search.query(HistoryQuery("龙裔"))

        assert outcome.outcome.value == "completed"
        assert search.status().ready
        assert search.status().record_count == 1
        assert [(item.original, item.translation) for item in page.items] == [("Dragonborn", "龙裔")]
        assert [(source.label, source.plugin_id) for source in page.items[0].sources] == [("个人库", "Skyrim")]
    finally:
        runtime.close()
