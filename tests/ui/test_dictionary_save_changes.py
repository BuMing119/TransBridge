from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.ui.shell.dictionary_save_changes import DictionarySaveChanges, source_keys_since


def receipt(previous, current, *keys, project="project"):
    return {
        "project_id": project,
        "persisted_project_revision": 42,
        "variant_id": "variant",
        "persisted_variant_revision": current,
        "entry_delta": {
            "project_id": project,
            "variant_id": "variant",
            "previous_variant_revision": previous,
            "variant_revision": current,
            "entry_keys": [key.to_dict() for key in keys],
        },
    }


def test_dictionary_deltas_cover_one_source_and_require_unbroken_history():
    first = EntryKey(SourceNamespace("source:a"), "greeting")
    other = EntryKey(SourceNamespace("source:b"), "greeting")
    changes = DictionarySaveChanges()
    initial = changes.record(receipt(2, 3, first, other))
    changes.record(receipt(3, 4, first))
    both = changes.record(receipt(4, 4))
    assert source_keys_since(both, 2, 4, "source:a") == (first,)
    assert source_keys_since(both, 2, 4, "source:b") == (other,)
    assert source_keys_since(both, 3, 4, "source:b") == ()
    assert source_keys_since(initial, 2, 3, "source:b") == (other,)
    gap = changes.record(receipt(6, 7, first))
    assert source_keys_since(gap, 4, 7, "source:a") is None
    switched = changes.record(receipt(7, 8, other, project="another"))
    assert source_keys_since(switched, 6, 8, "source:a") is None
