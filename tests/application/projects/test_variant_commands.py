from transbridge.application.io.identity import EntryKey, ExternalEntryRef, SourceNamespace
from transbridge.application.projects.variant_commands import (
    EntryStatePatch,
    patch_entry_records,
    replace_labels,
    update_entry_by_key,
)
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


def test_external_reference_can_be_shared_by_separate_sources() -> None:
    first = EntryKey(SourceNamespace("source:plugin:first"), "shared-key")
    second = EntryKey(SourceNamespace("source:plugin:second"), "shared-key")
    reference = ExternalEntryRef("paratranz", "project:7", 701)
    entries = (VariantEntryState(first), VariantEntryState(second, external_refs=(reference,)))

    updated = patch_entry_records(entries, {first: EntryStatePatch("changed", 1, (reference,))})

    assert updated[0].external_refs == updated[1].external_refs == (reference,)
    assert updated[1] is entries[1]
    again = patch_entry_records(updated, {first: EntryStatePatch("edited again", 1, (reference,))})
    assert again[0].translation == "edited again"
    assert again[1] is entries[1]


def test_reference_patch_does_not_validate_unrelated_source_collection() -> None:
    first = EntryKey(SourceNamespace("source:plugin:first"), "first")
    second = EntryKey(SourceNamespace("source:plugin:second"), "second")
    third = EntryKey(second.namespace, "third")
    old_reference = ExternalEntryRef("paratranz", "project:7", 701)
    new_reference = ExternalEntryRef("paratranz", "project:7", 702)
    entries = (
        VariantEntryState(first),
        VariantEntryState(second, external_refs=(old_reference,)),
        VariantEntryState(third, external_refs=(old_reference,)),
    )

    updated = patch_entry_records(entries, {first: EntryStatePatch("changed", 1, (new_reference,))})

    assert updated[0].external_refs == (new_reference,)
    assert updated[1] is entries[1]
    assert updated[2] is entries[2]
