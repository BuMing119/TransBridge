from __future__ import annotations

import json

from transbridge.application.history_search import HistoryQuery
from transbridge.application.tasks import OwnerRef, RuntimeTaskBridge
from transbridge.bootstrap import build_runtime


def test_runtime_refreshes_dictionary_into_searchable_projection(tmp_path) -> None:
    dictionaries = tmp_path / "dictionaries"
    dictionaries.mkdir()
    (dictionaries / "Skyrim.tbdict").write_text(
        json.dumps({
            "schema_version": 2,
            "mod_file_id": "Skyrim",
            "scope": "global",
            "entries": {
                "entry": {
                    "original": "Dragonborn",
                    "translation": "龙裔",
                    "source_locale": "en",
                    "target_locale": "zh-CN",
                    "enabled": True,
                }
            },
        }),
        encoding="utf-8",
    )
    runtime = build_runtime({
        "persistence_v2_root": tmp_path / "data",
        "translation_memory_root": dictionaries,
    })
    try:
        owner = OwnerRef("integration", "history-search")
        deferred = runtime.use_cases.resolve("history_search_tasks").refresh(owner)

        outcome = RuntimeTaskBridge(runtime.tasks).wait_terminal(deferred.ref, owner, timeout=5)
        page = runtime.use_cases.resolve("history_search").query(HistoryQuery("龙裔"))

        assert outcome.outcome.value == "completed"
        assert [(item.original, item.translation) for item in page.items] == [("Dragonborn", "龙裔")]
    finally:
        runtime.close()
