from __future__ import annotations

import ast
import json
from pathlib import Path

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import (
    CapabilityLevel,
    EntryKey,
    ExternalEntryRef,
    FormatId,
    ParatranzEntry,
    ParatranzJsonAdapter,
    ParseRequest,
    ProbeRequest,
    ProbeStatus,
    SourceDescriptor,
    SourceNamespace,
    WriteRequest,
)
from transbridge.application.tasks.controls import CancellationToken as TaskCancellationToken

FIXTURES = Path(__file__).with_name("fixtures")


def _parse(path: Path, *, options=(), namespace: SourceNamespace | None = None):
    return ParatranzJsonAdapter().parse(
        ParseRequest(
            SourceDescriptor(str(path), path.name, path.stat().st_size, "application/json"),
            RequestContext("contract-test", run_id="run-pt"),
            FormatId.JSON_PARATRANZ,
            namespace,
            options,
        )
    )


def _write(path: Path, entries: tuple[object, ...], *, options=()):
    return ParatranzJsonAdapter().write(
        WriteRequest(
            SourceDescriptor(str(path), path.name, media_type="application/json"),
            FormatId.JSON_PARATRANZ,
            entries,
            0,
            RequestContext("contract-test", run_id="run-pt"),
            new_template=b"",
            options=options,
        )
    )


def _semantic(entry: ParatranzEntry) -> tuple[object, ...]:
    return (
        entry.entry_key,
        entry.original,
        entry.translation,
        entry.stage,
        entry.context,
        tuple((ref.system, ref.scope, ref.opaque_id) for ref in entry.external_refs),
        entry.extensions,
    )


def test_golden_dual_id_parse_and_round_trip_preserves_identity(tmp_path: Path) -> None:
    namespace = SourceNamespace("fixture:paratranz")
    parsed = _parse(
        FIXTURES / "paratranz_dual_id.json",
        namespace=namespace,
        options=(("external_scope", "project:42"),),
    )

    assert parsed.outcome is OperationOutcome.COMPLETED
    assert [entry.entry_key.local_key for entry in parsed.entries] == [
        "NPC_:0001|1~NPC_:FULL",
        "BOOK:0002|1~BOOK:DESC",
        "INFO:0003|1~INFO:NAM1",
    ]
    assert [entry.external_refs[0].opaque_id for entry in parsed.entries[:2]] == [42, "remote-α"]
    assert parsed.entries[2].external_refs == ()

    target = tmp_path / "round-trip.json"
    written = _write(
        target,
        parsed.entries,
        options=(("sort_by_key", True), ("preserve_extensions", True)),
    )
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert written.outcome is OperationOutcome.COMPLETED
    assert next(item for item in payload if item["key"].startswith("NPC_"))["id"] == 42
    assert next(item for item in payload if item["key"].startswith("BOOK"))["id"] == "remote-α"
    assert "id" not in next(item for item in payload if item["key"].startswith("INFO"))
    assert next(item for item in payload if item["key"].startswith("BOOK"))["note"] == "保留扩展字段"

    reparsed = _parse(target, namespace=namespace, options=(("external_scope", "project:42"),))
    assert reparsed.outcome is OperationOutcome.COMPLETED
    assert sorted((_semantic(entry) for entry in reparsed.entries), key=lambda item: item[0].local_key) == sorted(
        (_semantic(entry) for entry in parsed.entries),
        key=lambda item: item[0].local_key,
    )


def test_empty_array_is_completed_and_writes_empty_array(tmp_path: Path) -> None:
    source = tmp_path / "empty.json"
    source.write_text("[]", encoding="utf-8")

    parsed = _parse(source)
    target = tmp_path / "empty-out.json"
    written = _write(target, parsed.entries)

    assert parsed.outcome is OperationOutcome.COMPLETED
    assert parsed.entries == ()
    assert written.outcome is OperationOutcome.COMPLETED
    assert json.loads(target.read_text(encoding="utf-8")) == []


def test_legacy_entries_object_envelope_remains_read_compatible(tmp_path: Path) -> None:
    source = tmp_path / "envelope.json"
    source.write_text(
        '{"entries":[{"id":false,"key":"wrapped","original":"x","stage":0}]}',
        encoding="utf-8",
    )

    result = _parse(source)

    assert result.outcome is OperationOutcome.COMPLETED
    assert result.entries[0].key == "wrapped"
    assert result.entries[0].external_refs[0].opaque_id is False


def test_mixed_invalid_stage_is_partial_with_locatable_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "partial.json"
    source.write_text(
        json.dumps([
            {"key": "valid", "original": "a", "translation": "甲", "stage": 1},
            {"id": 9, "key": "invalid", "original": "b", "translation": "乙", "stage": 4},
        ]),
        encoding="utf-8",
    )

    result = _parse(source)

    assert result.outcome is OperationOutcome.PARTIAL
    assert [entry.key for entry in result.entries] == ["valid"]
    assert result.stats.parsed == 1 and result.stats.failed == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == "PARATRANZ_STAGE_INVALID"
    assert dict(diagnostic.details)["stage"] == 4
    assert dict(diagnostic.details)["record_index"] == 1
    assert dict(diagnostic.details)["key"] == "invalid"
    assert dict(diagnostic.details)["id"] == 9


def test_invalid_only_and_fail_policy_are_explicitly_failed(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text('[{"key":"bad","original":"x","stage":true}]', encoding="utf-8")
    mixed = tmp_path / "mixed.json"
    mixed.write_text(
        '[{"key":"ok","original":"x","stage":0},{"key":"bad","original":"y","stage":99}]',
        encoding="utf-8",
    )

    invalid_result = _parse(invalid)
    fail_policy_result = _parse(mixed, options=(("invalid_record_policy", "failed"),))

    assert invalid_result.outcome is OperationOutcome.FAILED
    assert invalid_result.source_snapshot is None and invalid_result.entries == ()
    assert fail_policy_result.outcome is OperationOutcome.FAILED
    assert fail_policy_result.entries == ()
    assert fail_policy_result.stats.failed == 1
    assert fail_policy_result.stats.skipped == 1


def test_duplicate_key_and_remote_id_conflicts_never_choose_by_order(tmp_path: Path) -> None:
    source = tmp_path / "conflicts.json"
    source.write_text(
        json.dumps([
            {"id": 10, "key": "same", "original": "first", "stage": 0},
            {"id": 11, "key": "same", "original": "second", "stage": 0},
            {"id": "shared", "key": "left", "original": "left", "stage": 0},
            {"id": "shared", "key": "right", "original": "right", "stage": 0},
            {"key": "safe", "original": "safe", "stage": 0},
        ]),
        encoding="utf-8",
    )

    result = _parse(source)

    assert result.outcome is OperationOutcome.PARTIAL
    assert [entry.key for entry in result.entries] == ["safe"]
    assert result.stats.failed == 4
    assert {diagnostic.code for diagnostic in result.diagnostics} == {
        "PARATRANZ_KEY_DUPLICATE",
        "PARATRANZ_ID_CONFLICT",
    }
    assert all({"record_index", "key", "id"}.issubset(dict(item.details)) for item in result.diagnostics)


def test_record_order_does_not_change_default_entry_identity(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    records = [
        {"key": "alpha", "original": "a"},
        {"key": "beta", "original": "b"},
    ]
    first.write_text(json.dumps(records), encoding="utf-8")
    second.write_text(json.dumps(list(reversed(records))), encoding="utf-8")

    first_result = _parse(first)
    second_result = _parse(second)

    assert first_result.outcome is OperationOutcome.COMPLETED
    assert second_result.outcome is OperationOutcome.COMPLETED
    assert {entry.entry_key for entry in first_result.entries} == {entry.entry_key for entry in second_result.entries}
    assert [entry.key for entry in first_result.entries] == ["alpha", "beta"]
    assert [entry.key for entry in second_result.entries] == ["beta", "alpha"]


def test_duplicate_core_or_nested_extension_fields_are_locatable_conflicts(tmp_path: Path) -> None:
    source = tmp_path / "duplicate-fields.json"
    source.write_text(
        """[
          {"id": 4, "key": "bad", "key": "shadowed", "original": "x"},
          {"id": 5, "key": "nested", "original": "y", "meta": {"score": 1, "score": 2}},
          {"key": "safe", "original": "z"}
        ]""",
        encoding="utf-8",
    )

    result = _parse(source)

    assert result.outcome is OperationOutcome.PARTIAL
    assert [entry.key for entry in result.entries] == ["safe"]
    assert result.stats.failed == 2
    assert {item.code for item in result.diagnostics} == {"PARATRANZ_FIELD_CONFLICT"}
    first_details = dict(result.diagnostics[0].details)
    assert first_details["record_index"] == 0
    assert first_details["key"] == "shadowed"
    assert first_details["id"] == 4
    assert "$.key" in first_details["duplicate_fields"]
    assert "$.meta.score" in dict(result.diagnostics[1].details)["duplicate_fields"]


def test_writer_uses_entrykey_and_external_ref_without_id_synthesis(tmp_path: Path) -> None:
    namespace = SourceNamespace("fixture:writer")
    with_id = ParatranzEntry(
        EntryKey(namespace, "business-key"),
        "original",
        external_refs=(ExternalEntryRef("paratranz", "offline", "opaque-7"),),
    )
    without_id = ParatranzEntry(EntryKey(namespace, "no-remote-id"), "original")
    target = tmp_path / "write.json"

    result = _write(target, (with_id, without_id))
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert result.outcome is OperationOutcome.COMPLETED
    assert payload[0]["key"] == "business-key" and payload[0]["id"] == "opaque-7"
    assert payload[1]["key"] == "no-remote-id" and "id" not in payload[1]


def test_all_json_scalar_ids_round_trip_with_type_stable_identity(tmp_path: Path) -> None:
    source = tmp_path / "json-scalars.json"
    source.write_text(
        json.dumps([
            {"id": True, "key": "bool", "original": "a"},
            {"id": 1, "key": "int", "original": "b"},
            {"id": 1.0, "key": "float", "original": "c"},
            {"id": None, "key": "null", "original": "d"},
            {"key": "missing", "original": "e"},
        ]),
        encoding="utf-8",
    )

    parsed = _parse(source, options=(("external_scope", "scalar-test"),))
    target = tmp_path / "json-scalars-out.json"
    written = _write(target, parsed.entries)
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert parsed.outcome is OperationOutcome.COMPLETED
    assert written.outcome is OperationOutcome.COMPLETED
    references = [entry.external_refs for entry in parsed.entries]
    assert [(type(refs[0].opaque_id), refs[0].opaque_id) for refs in references[:4]] == [
        (bool, True),
        (int, 1),
        (float, 1.0),
        (type(None), None),
    ]
    assert len({refs[0].index_key for refs in references[:4]}) == 4
    assert references[4] == ()
    assert "id" in payload[3] and payload[3]["id"] is None
    assert "id" not in payload[4]


def test_non_finite_numbers_are_rejected_for_parse_extensions_and_ids(tmp_path: Path) -> None:
    source = tmp_path / "nan.json"
    overflow = tmp_path / "overflow.json"
    source.write_text('[{"id": NaN, "key": "bad", "original": "x"}]', encoding="utf-8")
    overflow.write_text('[{"id": 1e999, "key": "bad", "original": "x"}]', encoding="utf-8")
    namespace = SourceNamespace("fixture:finite")
    bad_extension = ParatranzEntry(
        EntryKey(namespace, "bad-extension"),
        "x",
        extensions=(("score", float("inf")),),
    )
    target = tmp_path / "nan-out.json"

    parsed = _parse(source)
    overflow_result = _parse(overflow)
    written = _write(target, (bad_extension,))

    assert parsed.outcome is OperationOutcome.FAILED
    assert parsed.diagnostics[0].code == "PARATRANZ_NUMBER_INVALID"
    assert overflow_result.outcome is OperationOutcome.FAILED
    assert overflow_result.diagnostics[0].code == "PARATRANZ_NUMBER_INVALID"
    assert written.outcome is OperationOutcome.FAILED
    assert not target.exists()


def test_empty_probe_remains_ambiguous_and_capability_does_not_preclaim_atomic_publish() -> None:
    adapter = ParatranzJsonAdapter()

    probe = adapter.probe(ProbeRequest(SourceDescriptor("memory:///empty.json", "empty.json"), b"[]"))

    assert probe.status is ProbeStatus.AMBIGUOUS
    assert set(probe.candidates) == {
        FormatId.JSON_DSD,
        FormatId.JSON_PARATRANZ,
        FormatId.JSON_TRANSBRIDGE,
    }
    assert adapter.capabilities().publish is CapabilityLevel.UNAVAILABLE


def test_real_task_cancellation_token_cancels_parse_and_write_before_artifact(tmp_path: Path) -> None:
    token = TaskCancellationToken()
    token._cancel("contract test")
    adapter = ParatranzJsonAdapter()
    source = tmp_path / "does-not-need-to-exist.json"
    parse_result = adapter.parse(
        ParseRequest(
            SourceDescriptor(str(source), source.name),
            RequestContext("contract-test"),
            FormatId.JSON_PARATRANZ,
            cancellation=token,
        )
    )
    target = tmp_path / "cancelled.json"
    write_result = adapter.write(
        WriteRequest(
            SourceDescriptor(str(target), target.name),
            FormatId.JSON_PARATRANZ,
            (),
            0,
            RequestContext("contract-test"),
            new_template=b"",
            cancellation=token,
        )
    )

    assert parse_result.outcome is OperationOutcome.CANCELLED
    assert write_result.outcome is OperationOutcome.CANCELLED
    assert not target.exists()


def test_writer_rejects_duplicate_key_and_id_before_creating_artifact(tmp_path: Path) -> None:
    namespace = SourceNamespace("fixture:writer-conflict")
    entries = (
        ParatranzEntry(
            EntryKey(namespace, "left"),
            "a",
            external_refs=(ExternalEntryRef("paratranz", "offline", 5),),
        ),
        ParatranzEntry(
            EntryKey(namespace, "right"),
            "b",
            external_refs=(ExternalEntryRef("paratranz", "offline", 5),),
        ),
    )
    target = tmp_path / "conflict.json"

    result = _write(target, entries)

    assert result.outcome is OperationOutcome.FAILED
    assert not target.exists()
    assert {diagnostic.code for diagnostic in result.diagnostics} == {"PARATRANZ_ID_CONFLICT"}


def test_probe_uses_schema_and_adapter_has_no_network_or_secret_imports() -> None:
    content = (FIXTURES / "paratranz_dual_id.json").read_bytes()
    adapter = ParatranzJsonAdapter()

    probe = adapter.probe(ProbeRequest(SourceDescriptor("memory:///pt.json", "pt.json"), content))

    assert probe.status is ProbeStatus.EXACT
    assert probe.candidates == (FormatId.JSON_PARATRANZ,)
    for module_path in (
        Path("src/transbridge/application/io/paratranz.py"),
        Path("src/transbridge/application/io/paratranz_mapping.py"),
    ):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported = {
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        assert not any(name.startswith("transbridge.paratranz") for name in imported)
        assert not any("config_manager" in name or "client" in name for name in imported)
