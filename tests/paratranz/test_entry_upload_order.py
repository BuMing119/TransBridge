from __future__ import annotations

from types import SimpleNamespace

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.tools._entry_upload import upload_entries


def test_assistant_selected_upload_retains_full_collection_order_and_local_context():
    entries = tuple(TranslationEntry(key, key, key, "", 0, "BOOK:FULL") for key in ("a", "b", "c"))
    collection = TranslationEntryCollection(entries)
    ctx = SimpleNamespace(collection=collection)
    sent = []

    def upsert_entry(project_id, entry, **kwargs):
        sent.append(entry)
        return SimpleNamespace(remote_id=17, key=entry.key, original=entry.original)

    result = upload_entries({"entry_ids": ["c"]}, ctx, collection, SimpleNamespace(upsert_entry=upsert_entry), 7, None)

    assert result.success
    assert [entry.context for entry in sent] == ["00000002|BOOK:FULL"]
    assert collection.get("c").context == "BOOK:FULL"


def test_assistant_upload_preserves_sparse_plugin_order_and_free_text():
    entries = (
        TranslationEntry("a", "a", "a", "", 0, "INFO:NAM1|", metadata=(("plugin.source_order", 428),)),
        TranslationEntry("b", "b", "b", "", 0, "custom text", metadata=(("plugin.source_order", 512),)),
    )
    collection = TranslationEntryCollection(entries)
    sent = []

    def upsert_entry(project_id, entry, **kwargs):
        sent.append(entry)
        return SimpleNamespace(remote_id=len(sent), key=entry.key, original=entry.original)

    result = upload_entries(
        {}, SimpleNamespace(collection=collection), collection, SimpleNamespace(upsert_entry=upsert_entry), 7, None
    )

    assert result.success
    assert [entry.context for entry in sent] == ["00000428|INFO:NAM1|", "custom text"]
