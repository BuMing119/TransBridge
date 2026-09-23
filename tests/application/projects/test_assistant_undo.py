"""An inverse must undo attributed values without rewinding unrelated work."""

from dataclasses import replace
import json

import pytest

from tests.application.projects.test_lifecycle import _active, _context, _harness, _refs
from transbridge.application.contracts import DomainError
from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, SourceNamespace
from transbridge.application.projects.assistant_undo import (
    VariantUndoReceipt,
    apply_variant_undo,
    build_variant_inverse,
    capture_variant_undo,
    combine_variant_undo,
)
from transbridge.persistence.v2.variant import SourceFingerprint, VariantAggregate, VariantEntryState, VariantSnapshot


def _case():
    project, ref = _refs("project", "variant")
    fingerprints = tuple(SourceFingerprint(SourceNamespace(name), name * 64) for name in ("a", "b"))
    first = VariantEntryState(
        EntryKey(SourceNamespace("a"), "shared-key"),
        "before",
        1,
        ("old",),
        revision=EntryRevision(2),
        external_refs=(ExternalEntryRef("paratranz", "project:1", 10),),
    )
    second = VariantEntryState(EntryKey(SourceNamespace("b"), "shared-key"), "unrelated")
    before = VariantSnapshot(ref, fingerprints, (first, second), 3, (("old", {"name": "old"}),))
    changed = replace(
        first,
        translation="assistant",
        stage=3,
        labels=("new",),
        revision=first.revision.next(),
        external_refs=(ExternalEntryRef("paratranz", "project:1", 20),),
    )
    after = replace(
        before,
        entries=(changed, second),
        revision=4,
        label_library=(("old", {"name": "updated"}), ("new", {"name": "new"})),
    )
    return project, before, after


def _service(snapshot, *, persisted_revision=4):
    project, _ = _refs("project", "variant")
    active = replace(
        _active(project, snapshot.ref),
        variant=VariantAggregate(snapshot),
        persisted_variant_revision=persisted_revision,
    )
    return _harness(active, {})


def test_sparse_receipt_json_roundtrip_and_inverse_preserve_unrelated_edits():
    _, before, after = _case()
    before = replace(before, label_library=after.label_library)  # entry-only command
    receipt = capture_variant_undo(before, after)
    wire = json.loads(json.dumps(receipt.to_dict()))
    assert len(wire["entries"]) == 1  # equal local keys in another namespace are independent
    receipt = VariantUndoReceipt.from_dict(wire)
    assert receipt == capture_variant_undo(before, after)
    later = replace(after.entries[1], translation="later user edit", revision=EntryRevision(1))
    current = replace(
        after,
        entries=(after.entries[0], later),
        revision=5,
        label_library=(*after.label_library, ("unrelated", {"name": "keep"})),
    )
    harness = _service(current)

    result = apply_variant_undo(harness.service, receipt, _context(run_id="undo"))

    assert result.is_success and result.value["revision"] == 6
    restored = harness.service.active.variant.snapshot()
    assert restored.entries[0] == replace(before.entries[0], revision=EntryRevision(4))
    assert restored.entries[1] == later
    assert restored.source_fingerprints == current.source_fingerprints
    assert restored.label_library == current.label_library
    assert harness.service.active.persisted_variant_revision == 4 and harness.service.active.dirty
    assert harness.store.staged == {}  # apply is explicitly a working-copy commit, not a durable save


@pytest.mark.parametrize("change", ["value", "revision", "missing", "library", "source", "identity"])
def test_any_conflict_rejects_entire_inverse_without_partial_mutation(change):
    _, before, after = _case()
    receipt = capture_variant_undo(before, after)
    current = after
    if change == "value":
        current = replace(after, entries=(replace(after.entries[0], translation="other"), after.entries[1]))
    elif change == "revision":
        current = replace(after, entries=(replace(after.entries[0], revision=EntryRevision(4)), after.entries[1]))
    elif change == "missing":
        current = replace(after, entries=(after.entries[1],))
    elif change == "library":
        current = replace(
            after,
            label_library=tuple(
                (key, {"name": "someone else"} if key == "old" else value) for key, value in after.label_library
            ),
        )
    elif change == "source":
        current = replace(
            after,
            source_fingerprints=(replace(after.source_fingerprints[0], sha256="c" * 64), after.source_fingerprints[1]),
        )
    else:
        _, ref = _refs("project", "other")
        current = replace(after, ref=ref)
    harness = _service(current)

    result = apply_variant_undo(harness.service, receipt, _context(run_id="undo"))

    assert not result.is_success
    assert result.diagnostics[0].code == "ASSISTANT_UNDO_CONFLICT"
    assert harness.service.active.variant.snapshot() == current


def test_new_unrelated_reference_prevents_deleting_label_definition():
    _, before, after = _case()
    current = replace(
        after,
        entries=(after.entries[0], replace(after.entries[1], labels=("new",), revision=EntryRevision(1))),
        revision=5,
    )
    with pytest.raises(DomainError):
        build_variant_inverse(current, capture_variant_undo(before, after), run_id="undo")


def test_lifecycle_cas_rejects_write_between_inverse_build_and_commit(monkeypatch):
    _, before, after = _case()
    harness = _service(after)
    original = harness.service.commit_active_variant
    concurrent = replace(after.entries[1], translation="concurrent", revision=EntryRevision(1))

    def racing_commit(change_set, context, **kwargs):
        # A real intervening aggregate commit invalidates the inverse's CAS.
        harness.service.active.variant.commit(replace(change_set, entries=(after.entries[0], concurrent)), context)
        return original(change_set, context, **kwargs)

    monkeypatch.setattr(harness.service, "commit_active_variant", racing_commit)
    result = apply_variant_undo(harness.service, capture_variant_undo(before, after), _context(run_id="undo"))
    assert not result.is_success and result.diagnostics[0].code == "ACTIVE_VARIANT_REVISION_CHANGED"
    assert harness.service.active.variant.snapshot().entries == (after.entries[0], concurrent)


@pytest.mark.parametrize("change", ["source", "entries", "tombstone", "inferred", "revision"])
def test_capture_explicitly_rejects_unsupported_or_uncommitted_changes(change):
    _, before, after = _case()
    if change == "source":
        after = replace(
            after,
            source_fingerprints=(replace(after.source_fingerprints[0], sha256="c" * 64), after.source_fingerprints[1]),
        )
    elif change == "entries":
        after = replace(after, entries=(after.entries[0],))
    elif change == "tombstone":
        after = replace(after, entries=(replace(after.entries[0], tombstone=True), after.entries[1]))
    elif change == "inferred":
        after = replace(
            after, entries=(replace(after.entries[0], inferred_fields=("external_refs",)), after.entries[1])
        )
    else:
        after = replace(
            after, entries=(replace(after.entries[0], revision=before.entries[0].revision), after.entries[1])
        )
    with pytest.raises(DomainError) as error:
        capture_variant_undo(before, after)
    assert error.value.code == "ASSISTANT_UNDO_UNSUPPORTED"


def test_library_only_inverse_can_restore_deleted_definition_and_preserve_unrelated_key():
    _, before, _ = _case()
    before = replace(before, entries=(), label_library=(("removed", {"value": [1, None]}), ("unrelated", None)))
    after = replace(before, revision=4, label_library=(("unrelated", None),))
    receipt = VariantUndoReceipt.from_dict(json.loads(json.dumps(capture_variant_undo(before, after).to_dict())))
    current = after
    inverse = build_variant_inverse(current, receipt, run_id="undo")
    snapshot = VariantSnapshot(inverse.ref, inverse.source_fingerprints, inverse.entries, 6, inverse.label_library)
    assert snapshot.to_dto().envelope.data["label_library"] == {"removed": {"value": [1, None]}, "unrelated": None}


def test_replaying_same_receipt_cannot_rewind_entry_revision():
    _, before, after = _case()
    receipt = capture_variant_undo(before, after)
    harness = _service(after)
    assert apply_variant_undo(harness.service, receipt, _context(run_id="undo")).is_success
    restored = harness.service.active.variant.snapshot()
    assert not apply_variant_undo(harness.service, receipt, _context(run_id="replay")).is_success
    assert harness.service.active.variant.snapshot() == restored


def test_combine_chained_entry_writes_skips_unrelated_user_edit_and_uses_one_commit():
    _, before, after = _case()
    before = replace(before, label_library=after.label_library)
    user_edit = replace(after.entries[1], translation="user edit", revision=EntryRevision(1))
    middle = replace(after, entries=(after.entries[0], user_edit), revision=5)
    final_entry = replace(after.entries[0], translation="assistant second pass", revision=EntryRevision(4))
    final = replace(middle, entries=(final_entry, user_edit), revision=6)
    combined = combine_variant_undo((capture_variant_undo(before, after), capture_variant_undo(middle, final)))
    harness = _service(final)
    result = apply_variant_undo(harness.service, combined, _context(run_id="undo"))
    assert result.is_success and result.value["revision"] == 7
    restored = harness.service.active.variant.snapshot()
    assert restored.entries == (replace(before.entries[0], revision=EntryRevision(5)), user_edit)


def test_combine_different_entries_restores_both_without_global_revision_continuity():
    _, before, after = _case()
    before = replace(before, label_library=after.label_library)
    middle = replace(after, revision=8, label_library=(*after.label_library, ("user-label", None)))
    second = replace(middle.entries[1], translation="assistant second entry", revision=EntryRevision(1))
    final = replace(middle, entries=(middle.entries[0], second), revision=9)
    combined = combine_variant_undo((capture_variant_undo(before, after), capture_variant_undo(middle, final)))
    inverse = build_variant_inverse(final, combined, run_id="undo")
    assert inverse.entries == (
        replace(before.entries[0], revision=EntryRevision(4)),
        replace(before.entries[1], revision=EntryRevision(2)),
    )
    assert "user-label" in dict(inverse.label_library)


def test_combine_rejects_intervening_same_entry_edit_even_if_value_returned_to_after_image():
    _, before, after = _case()
    before = replace(before, label_library=after.label_library)
    intervened = replace(after.entries[0], revision=EntryRevision(7))
    middle = replace(after, entries=(intervened, after.entries[1]), revision=8)
    final = replace(
        middle,
        entries=(replace(intervened, translation="next", revision=EntryRevision(8)), after.entries[1]),
        revision=9,
    )
    with pytest.raises(DomainError, match="breaks the undo chain"):
        combine_variant_undo((capture_variant_undo(before, after), capture_variant_undo(middle, final)))


@pytest.mark.parametrize("intervene", [False, True])
def test_combine_label_definition_chain_validates_before_value(intervene):
    _, before, after = _case()
    library = after.to_dto().envelope.data["label_library"]
    if intervene:
        library["new"] = {"name": "user definition"}
    middle = replace(after, label_library=tuple(library.items()), revision=4)
    library["new"] = {"name": "next assistant definition"}
    final = replace(middle, label_library=tuple(library.items()), revision=5)
    receipts = (capture_variant_undo(before, after), capture_variant_undo(middle, final))
    if intervene:
        with pytest.raises(DomainError, match="label definition edit"):
            combine_variant_undo(receipts)
    else:
        combined = combine_variant_undo(receipts)
        inverse = build_variant_inverse(final, combined, run_id="undo")
        assert "new" not in dict(inverse.label_library)


def test_conflict_in_second_of_two_touched_entries_rejects_entire_inverse():
    _, before, after = _case()
    before = replace(before, label_library=after.label_library)
    after = replace(
        after, entries=(after.entries[0], replace(after.entries[1], translation="assistant", revision=EntryRevision(1)))
    )
    receipt = capture_variant_undo(before, after)
    current = replace(
        after,
        entries=(after.entries[0], replace(after.entries[1], translation="user", revision=EntryRevision(2))),
        revision=5,
    )
    harness = _service(current)
    assert not apply_variant_undo(harness.service, receipt, _context(run_id="undo")).is_success
    assert harness.service.active.variant.snapshot() == current


def test_library_after_image_aba_cannot_bypass_global_revision_guard():
    _, before, after = _case()
    receipt = capture_variant_undo(before, after)
    current = replace(after, revision=6)  # external definition edit and restoration left the same values
    with pytest.raises(DomainError, match="no per-key revisions"):
        build_variant_inverse(current, receipt, run_id="undo")


@pytest.mark.parametrize("next_edits_library", [False, True])
def test_combine_rejects_library_gap_even_when_next_receipt_only_touches_entries(next_edits_library):
    _, before, after = _case()
    middle = replace(after, revision=6)  # unrecorded edits may have changed label definitions and back
    if next_edits_library:
        library = middle.to_dto().envelope.data["label_library"]
        library["new"] = {"name": "next"}
        final = replace(middle, revision=7, label_library=tuple(library.items()))
    else:
        final = replace(
            middle,
            revision=7,
            entries=(replace(middle.entries[0], translation="next", revision=EntryRevision(4)), middle.entries[1]),
        )
    with pytest.raises(DomainError, match="gap after label definition"):
        combine_variant_undo((capture_variant_undo(before, after), capture_variant_undo(middle, final)))


def test_active_variant_snapshot_captures_detached_variant_and_matching_project_revision():
    _, _, after = _case()
    harness = _service(after)
    snapshot, project_revision = harness.service.active_variant_snapshot()
    assert snapshot == after and project_revision == harness.service.active.project.envelope.revision
    assert _harness(None, {}).service.active_variant_snapshot() is None
