from __future__ import annotations

from dataclasses import replace

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import (
    EntryKey,
    EntryRevision,
    ExternalEntryRef,
    Provenance,
    SourceNamespace,
)
from transbridge.application.io.stage_policy import Stage
from transbridge.persistence.v2 import (
    ProjectId,
    SourceBaseline,
    SourceFingerprint,
    VariantAggregate,
    VariantEntryState,
    VariantId,
    VariantMaterializer,
    VariantRef,
    VariantSnapshot,
)
from transbridge.persistence.v2.schema import validate_v2


def _source(token: str, digest: str) -> tuple[SourceNamespace, SourceFingerprint]:
    namespace = SourceNamespace.from_fingerprint(token, digest)
    return namespace, SourceFingerprint(namespace, digest)


def _state(
    namespace: SourceNamespace,
    key: str,
    translation: str = "",
    *,
    stage: Stage = Stage.UNTRANSLATED,
    labels: tuple[str, ...] = (),
    provenance: tuple[Provenance, ...] = (),
    revision: int = 0,
    tombstone: bool = False,
) -> VariantEntryState:
    return VariantEntryState(
        EntryKey(namespace, key),
        translation,
        stage,
        labels,
        provenance,
        EntryRevision(revision),
        tombstone,
    )


def _ref() -> VariantRef:
    return VariantRef(VariantId("variant-b"), ProjectId("project-a"))


def _context(ref: VariantRef) -> RequestContext:
    return RequestContext(
        owner_id="test",
        run_id="run-1",
        project_id=ref.project_id.value,
        variant_id=ref.identity.value,
    )


def test_snapshot_roundtrip_preserves_explicit_empty_stage_labels_and_provenance() -> None:
    namespace, fingerprint = _source("esp", "a" * 64)
    provenance = (Provenance("run-old", "editor", "manual", "2026-08-18T00:00:00Z"),)
    snapshot = VariantSnapshot(
        _ref(),
        (fingerprint,),
        (
            replace(
                _state(
                    namespace,
                    "same-key",
                    "",
                    stage=Stage.REVIEWED,
                    labels=(),
                    provenance=provenance,
                    revision=7,
                ),
                external_refs=(ExternalEntryRef("paratranz", "project:42", 7),),
            ),
        ),
        revision=11,
        label_library=(("review", {"rules": [["pair", 1]], "color": "blue"}),),
    )

    dto = snapshot.to_dto()
    validated = validate_v2(dto.envelope.to_dict(), snapshot.ref)
    restored = VariantSnapshot.from_dto(validated, snapshot.ref)

    assert restored == snapshot
    assert restored.entries[0].translation == ""
    assert restored.entries[0].labels == ()
    assert restored.entries[0].stage is Stage.REVIEWED
    assert restored.entries[0].revision == EntryRevision(7)
    assert restored.entries[0].provenance == provenance
    assert restored.entries[0].external_refs == (ExternalEntryRef("paratranz", "project:42", 7),)
    assert restored.to_dto().envelope.data["label_library"] == {"review": {"rules": [["pair", 1]], "color": "blue"}}


def test_pre_s02_v2_dto_is_loaded_as_unverified_legacy_state() -> None:
    ref = _ref()
    dto = VariantSnapshot(ref, (), ()).to_dto()
    dto.envelope.data.update(
        translations={"legacy-key": "old"},
        labels={"legacy-key": []},
    )
    dto.envelope.data.pop("source_fingerprints")
    dto.envelope.data.pop("entries")

    snapshot = VariantSnapshot.from_dto(dto, ref)

    assert snapshot.source_fingerprints == (SourceFingerprint(SourceNamespace.legacy(), None),)
    assert snapshot.entries[0].translation == "old"
    assert snapshot.entries[0].inferred_fields == ("provenance", "revision", "stage")


def test_replace_materialization_prevents_a_residue_and_old_resurrection() -> None:
    ref = _ref()
    namespace, fingerprint = _source("esp", "b" * 64)
    baseline_state = _state(namespace, "entry", "", stage=Stage.UNTRANSLATED)
    baseline = SourceBaseline(fingerprint, (baseline_state,))
    aggregate = VariantAggregate(
        VariantSnapshot(
            ref,
            (fingerprint,),
            (_state(namespace, "entry", "from-A", stage=Stage.TRANSLATED),),
        )
    )
    materializer = VariantMaterializer()

    empty = VariantSnapshot(ref, (fingerprint,), ())
    empty_result = materializer.materialize(empty, (baseline,), aggregate, _context(ref))
    assert empty_result.committed
    assert aggregate.snapshot().entries == (baseline_state,)

    variant_b = VariantSnapshot(
        ref,
        (fingerprint,),
        (_state(namespace, "entry", "from-B", stage=Stage.CHECKED),),
    )
    b_result = materializer.materialize(variant_b, (baseline,), aggregate, _context(ref))
    assert b_result.committed
    assert aggregate.snapshot().entries[0].translation == "from-B"

    cleared = replace(variant_b, entries=(_state(namespace, "entry", ""),))
    dto = cleared.to_dto()
    restarted = VariantSnapshot.from_dto(dto, ref)
    clear_result = materializer.materialize(restarted, (baseline,), aggregate, _context(ref))
    assert clear_result.committed
    assert aggregate.snapshot().entries[0].translation == ""


def test_tombstone_restores_baseline_and_empty_labels_are_not_truthy_filtered() -> None:
    ref = _ref()
    namespace, fingerprint = _source("json", "c" * 64)
    baseline_state = _state(
        namespace,
        "entry",
        "source-value",
        stage=Stage.LOCKED,
        labels=("source",),
    )
    snapshot = VariantSnapshot(
        ref,
        (fingerprint,),
        (_state(namespace, "entry", "ignored", labels=(), tombstone=True),),
    )
    aggregate = VariantAggregate(VariantSnapshot(ref, (fingerprint,), ()))

    result = VariantMaterializer().materialize(
        snapshot,
        (SourceBaseline(fingerprint, (baseline_state,)),),
        aggregate,
        _context(ref),
    )

    assert result.committed
    assert aggregate.snapshot().entries == (baseline_state,)


def test_same_local_key_is_isolated_by_source_namespace() -> None:
    ref = _ref()
    namespace_a, fingerprint_a = _source("esp", "d" * 64)
    namespace_b, fingerprint_b = _source("json", "e" * 64)
    baseline_a = _state(namespace_a, "shared", "")
    baseline_b = _state(namespace_b, "shared", "")
    snapshot = VariantSnapshot(
        ref,
        (fingerprint_a, fingerprint_b),
        (
            _state(namespace_a, "shared", "A", stage=Stage.TRANSLATED),
            _state(namespace_b, "shared", "B", stage=Stage.CHECKED),
        ),
    )
    aggregate = VariantAggregate(VariantSnapshot(ref, (fingerprint_a, fingerprint_b), ()))

    result = VariantMaterializer().materialize(
        snapshot,
        (
            SourceBaseline(fingerprint_a, (baseline_a,)),
            SourceBaseline(fingerprint_b, (baseline_b,)),
        ),
        aggregate,
        _context(ref),
    )

    assert result.committed
    assert {entry.entry_key: entry.translation for entry in aggregate.snapshot().entries} == {
        baseline_a.entry_key: "A",
        baseline_b.entry_key: "B",
    }


def test_fingerprint_mismatch_returns_plan_without_local_key_overwrite() -> None:
    ref = _ref()
    old_namespace, old_fingerprint = _source("esp", "f" * 64)
    new_fingerprint = SourceFingerprint(old_namespace, "0" * 64)
    old_state = _state(old_namespace, "same", "stored")
    aggregate = VariantAggregate(VariantSnapshot(ref, (old_fingerprint,), (old_state,), revision=4))
    before = aggregate.snapshot()

    result = VariantMaterializer().materialize(
        before,
        (SourceBaseline(new_fingerprint, (_state(old_namespace, "same", "new-source"),)),),
        aggregate,
        _context(ref),
    )

    assert not result.committed
    assert result.migration_plan is not None
    assert result.migration_plan.conflicts[0].stored_sha256 == "f" * 64
    assert aggregate.snapshot() == before


def test_changeset_failure_is_atomic() -> None:
    ref = _ref()
    namespace, fingerprint = _source("esp", "1" * 64)
    old_state = _state(namespace, "entry", "old")
    baseline = SourceBaseline(fingerprint, (_state(namespace, "entry", ""),))
    aggregate = VariantAggregate(VariantSnapshot(ref, (fingerprint,), (old_state,), revision=3))
    before = aggregate.snapshot()

    result = VariantMaterializer().materialize(
        VariantSnapshot(ref, (fingerprint,), (_state(namespace, "entry", "new"),)),
        (baseline,),
        aggregate,
        _context(ref),
        before_commit=lambda _: (_ for _ in ()).throw(OSError("disk failed")),
    )

    assert not result.committed
    assert [item.code for item in result.diagnostics] == ["VARIANT_CHANGESET_FAILED"]
    assert aggregate.snapshot() == before


def test_added_removed_and_unknown_entries_are_diagnostic() -> None:
    ref = _ref()
    removed_ns, removed_fp = _source("esp", "2" * 64)
    added_ns, added_fp = _source("json", "3" * 64)
    snapshot = VariantSnapshot(
        ref,
        (removed_fp, added_fp),
        (
            _state(removed_ns, "gone", "old"),
            _state(added_ns, "unknown", "old"),
        ),
    )
    aggregate = VariantAggregate(VariantSnapshot(ref, (), ()))

    result = VariantMaterializer().materialize(
        snapshot,
        (SourceBaseline(added_fp, (_state(added_ns, "current", ""),)),),
        aggregate,
        _context(ref),
    )

    assert result.committed
    assert {item.code for item in result.diagnostics} == {
        "VARIANT_ENTRY_REMOVED",
        "VARIANT_SOURCE_REMOVED",
    }
    assert [entry.entry_key.local_key for entry in aggregate.snapshot().entries] == ["current"]


def test_new_source_uses_baseline_and_is_diagnostic() -> None:
    ref = _ref()
    namespace, fingerprint = _source("json", "4" * 64)
    baseline_state = _state(namespace, "new", "source")
    aggregate = VariantAggregate(VariantSnapshot(ref, (), ()))

    result = VariantMaterializer().materialize(
        VariantSnapshot(ref, (), ()),
        (SourceBaseline(fingerprint, (baseline_state,)),),
        aggregate,
        _context(ref),
    )

    assert result.committed
    assert {item.code for item in result.diagnostics} == {
        "VARIANT_EMPTY_BASELINE_RESTORED",
        "VARIANT_SOURCE_ADDED",
    }
    assert aggregate.snapshot().entries == (baseline_state,)


def test_materialization_indexes_each_source_baseline_once() -> None:
    """Large Variants must not rebuild the full source index for every stored entry."""

    class CountingEntries:
        def __init__(self, values):
            self.values = tuple(values)
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            return iter(self.values)

    ref = _ref()
    namespace, fingerprint = _source("esp", "5" * 64)
    states = tuple(_state(namespace, f"entry-{index}") for index in range(200))
    counted = CountingEntries(states)
    baseline = SourceBaseline(fingerprint, counted)  # type: ignore[arg-type]
    counted.iterations = 0
    snapshot = VariantSnapshot(ref, (fingerprint,), states)
    aggregate = VariantAggregate(VariantSnapshot(ref, (fingerprint,), ()))

    result = VariantMaterializer().materialize(snapshot, (baseline,), aggregate, _context(ref))

    assert result.committed
    assert counted.iterations == 1
    assert len(aggregate.snapshot().entries) == len(states)
