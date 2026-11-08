from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from tests.source_format_fixtures import write_translation_source
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import (
    CapabilityLevel,
    FormatId,
    ParseRequest,
    ProbeRequest,
    ProbeStatus,
    SourceDescriptor,
    TranslationIoUseCase,
    WriteRequest,
    default_format_catalog,
)
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.io.json_source import parse_json_source

FORMATS = ("json.transbridge", "json.dsd", "sst.ssu8", "sst.ssu9")


def _request(path, kind):
    return ParseRequest(SourceDescriptor(str(path)), RequestContext("test"), FormatId(kind))


@pytest.mark.parametrize("kind", FORMATS)
def test_shared_import_preserves_text_snapshot_and_requires_supported_write_context(tmp_path, kind):
    path = write_translation_source(tmp_path, kind)
    original = path.read_bytes()
    request = _request(path, kind)
    catalog = default_format_catalog()
    probe = catalog.resolve(ProbeRequest(request.source, original))
    assert probe.status is ProbeStatus.EXACT
    assert probe.candidates == (FormatId(kind),)
    result = TranslationIoUseCase().parse(request)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert [(entry.original, entry.translation, entry.stage) for entry in result.entries] == [("Hello", "你好", 1)]
    assert result.source_snapshot.content == original
    assert result.entries[0].identity.namespace != SourceNamespace.legacy()
    assert dict(result.source_snapshot.metadata)["source_namespace"] == result.entries[0].identity.namespace.value
    is_sst = kind.startswith("sst.")
    assert result.capability.write is (CapabilityLevel.EXPERIMENTAL if is_sst else CapabilityLevel.UNAVAILABLE)
    target = tmp_path / ("output.sst" if is_sst else "output")
    target.write_bytes(b"do not replace")
    write = TranslationIoUseCase().write(
        WriteRequest(
            SourceDescriptor(str(target)), FormatId(kind), result.entries, 0, request.context, new_template=b""
        )
    )
    assert not write.is_success
    assert write.diagnostics[0].code == ("SOURCE_SNAPSHOT_REQUIRED" if is_sst else "FORMAT_WRITE_UNSUPPORTED")
    assert target.read_bytes() == b"do not replace"
    assert path.read_bytes() == original


@pytest.mark.parametrize("kind", FORMATS)
def test_import_cancellation_has_no_publishable_state(tmp_path, kind):
    request = replace(_request(tmp_path / "missing", kind), cancellation=SimpleNamespace(is_cancelled=True))
    result = TranslationIoUseCase().parse(request)
    assert result.outcome is OperationOutcome.CANCELLED
    assert result.source_snapshot is None and not result.entries


@pytest.mark.parametrize("kind", FORMATS)
def test_explicit_namespace_is_applied_without_changing_local_keys(tmp_path, kind):
    path = write_translation_source(tmp_path, kind)
    request = _request(path, kind)
    first = TranslationIoUseCase().parse(request)
    second = TranslationIoUseCase().parse(replace(request, source_namespace=SourceNamespace("explicit")))
    assert second.outcome is OperationOutcome.COMPLETED
    assert second.entries[0].identity.namespace == SourceNamespace("explicit")
    assert second.entries[0].key == first.entries[0].key


@pytest.mark.parametrize("kind", ["json.transbridge", "json.dsd"])
def test_shared_json_reader_matches_assistant_compatibility_entry(tmp_path, kind):
    from transbridge.smart_assistant.tools._json_import import parse_json_source as assistant_reader

    path = write_translation_source(tmp_path, kind)
    args = {"format": kind.removeprefix("json.")}
    shared = parse_json_source(str(path), args)
    assistant = assistant_reader(str(path), args)
    assert [entry.to_dict() for entry in shared.collection] == [entry.to_dict() for entry in assistant.collection]
    assert shared.snapshot == assistant.snapshot


@pytest.mark.parametrize("kind", ["json.transbridge", "json.dsd"])
def test_duplicate_json_identity_rejects_entire_source(tmp_path, kind):
    path = write_translation_source(tmp_path, kind)
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload["entries"] if isinstance(payload, dict) else payload
    records.append(dict(records[0]))
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = TranslationIoUseCase().parse(_request(path, kind))
    assert result.outcome is OperationOutcome.FAILED
    assert result.source_snapshot is None and not result.entries


@pytest.mark.parametrize("kind,content", [("sst.ssu8", b"SSU8"), ("sst.ssu9", b"SSU9"), ("json.dsd", b"[1]")])
def test_invalid_source_rejects_without_publishable_state(tmp_path, kind, content):
    path = tmp_path / "invalid"
    path.write_bytes(content)
    result = TranslationIoUseCase().parse(_request(path, kind))
    assert result.outcome is OperationOutcome.FAILED
    assert result.source_snapshot is None and not result.entries


def test_sst_version_hint_must_match_content(tmp_path):
    path = write_translation_source(tmp_path, "sst.ssu8")
    result = TranslationIoUseCase().parse(_request(path, "sst.ssu9"))
    assert result.outcome is OperationOutcome.FAILED


@pytest.mark.parametrize("kind", ["sst.ssu8", "sst.ssu9"])
@pytest.mark.parametrize("cut", [1, 8])
def test_truncated_sst_does_not_publish_a_successful_prefix(tmp_path, kind, cut):
    path = write_translation_source(tmp_path, kind)
    path.write_bytes(path.read_bytes()[:-cut])
    result = TranslationIoUseCase().parse(_request(path, kind))
    assert result.outcome is OperationOutcome.FAILED
    assert not result.entries and result.source_snapshot is None


@pytest.mark.parametrize("kind,header", [("sst.ssu8", b"SSU8" + b"\0" * 5), ("sst.ssu9", b"SSU9" + b"\0" * 9)])
def test_empty_sst_header_is_readable_without_parser_local_variable_errors(tmp_path, kind, header):
    path = tmp_path / "empty.sst"
    path.write_bytes(header)
    result = TranslationIoUseCase().parse(_request(path, kind))
    assert result.outcome is OperationOutcome.COMPLETED
    assert not result.entries


def test_v2_identity_without_optional_legacy_fields_is_unambiguous(tmp_path):
    path = tmp_path / "minimal.json"
    path.write_text(
        json.dumps([
            {
                "id": "old",
                "key": "a",
                "original": "Hello",
                "translation": "你好",
                "stage": 1,
                "entry_key": {"namespace": "saved-source", "local_key": "a"},
            }
        ]),
        encoding="utf-8",
    )
    content = path.read_bytes()
    probe = default_format_catalog().resolve(ProbeRequest(SourceDescriptor(str(path)), content))
    assert probe.candidates == (FormatId.JSON_TRANSBRIDGE,)
    result = TranslationIoUseCase().parse(_request(path, "json.transbridge"))
    assert result.outcome is OperationOutcome.COMPLETED
    assert result.entries[0].identity.namespace.value == "saved-source"
