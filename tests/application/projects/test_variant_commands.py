from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.variant_commands import replace_labels, update_entry_by_key
from transbridge.persistence.v2.variant import VariantEntryState


def test_exact_entry_update_does_not_cross_equal_local_keys_between_sources() -> None:
    first = EntryKey(SourceNamespace("source:plugin:first"), "shared-key")
    second = EntryKey(SourceNamespace("source:plugin:second"), "shared-key")
    entries = (VariantEntryState(first), VariantEntryState(second))

    updated = update_entry_by_key(entries, second, translation="second only", stage=1)

    assert updated[0] == entries[0]
    assert updated[1].translation == "second only"
    assert updated[1].revision.value == 1


def test_exact_label_patch_preserves_other_source_with_equal_local_key() -> None:
    first = EntryKey(SourceNamespace("source:plugin:first"), "shared-key")
    second = EntryKey(SourceNamespace("source:plugin:second"), "shared-key")
    entries = (
        VariantEntryState(first, labels=("first-label",)),
        VariantEntryState(second, labels=("second-label",)),
    )

    updated = replace_labels(entries, {first: {"changed"}})

    assert updated[0].labels == ("changed",)
    assert updated[1] == entries[1]
