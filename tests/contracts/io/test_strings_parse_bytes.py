"""Localized strings snapshots can be parsed directly from archived resource bytes."""

from dataclasses import replace
import hashlib
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, LocalizedStringsAdapter, ParseRequest, SourceDescriptor
from transbridge.application.io.identity import SourceNamespace

FIXTURES = Path(__file__).with_name("fixtures") / "strings"
FORMATS = (FormatId.STRINGS, FormatId.DLSTRINGS, FormatId.ILSTRINGS)


def _request(format_id, **kwargs):
    filename = f"Example_English.{format_id.value.rsplit('.', 1)[-1]}"
    return ParseRequest(
        SourceDescriptor(f"bsa://Example.bsa!/Strings/{filename}", filename),
        RequestContext("strings-bytes-contract", run_id="bsa-resource"),
        format_id,
        **kwargs,
    )


def _table(format_id, records):
    directory, chunks = [], []
    offset = 0
    for string_id, payload in records:
        terminated = payload + b"\x00"
        chunk = terminated if format_id is FormatId.STRINGS else struct.pack("<I", len(terminated)) + terminated
        directory.append(struct.pack("<II", string_id, offset))
        chunks.append(chunk)
        offset += len(chunk)
    return struct.pack("<II", len(records), offset) + b"".join(directory) + b"".join(chunks)


def _forbid_local_path(*_args):
    pytest.fail("Archived bytes parsing must not resolve or read a physical source path")


@pytest.mark.parametrize("format_id", FORMATS)
def test_archived_real_fixture_matches_local_parse_without_reading_its_virtual_uri(format_id, monkeypatch):
    fixture = FIXTURES / f"integrity.{format_id.value.rsplit('.', 1)[-1]}"
    content = fixture.read_bytes()
    request = _request(format_id)
    local_request = replace(request, source=SourceDescriptor(str(fixture), fixture.name))
    adapter = LocalizedStringsAdapter(format_id)
    local = adapter.parse(local_request)
    assert local.outcome is OperationOutcome.COMPLETED
    monkeypatch.setattr("transbridge.application.io.strings_adapter._local_path", _forbid_local_path)

    archived = adapter.parse_bytes(request, content)

    assert archived == replace(
        local,
        source=request.source,
        source_snapshot=replace(local.source_snapshot, source=request.source),
    )
    assert archived.source_snapshot.content == content
    assert archived.source_snapshot.sha256 == hashlib.sha256(content).hexdigest()
    assert all(entry.translation == "" and entry.stage == 0 for entry in archived.entries)


@pytest.mark.parametrize("format_id", FORMATS)
def test_empty_archived_table_has_a_complete_byte_snapshot(format_id, monkeypatch):
    content = struct.pack("<II", 0, 0)
    request = _request(format_id)
    monkeypatch.setattr("transbridge.application.io.strings_adapter._local_path", _forbid_local_path)

    parsed = LocalizedStringsAdapter(format_id).parse_bytes(request, content)

    assert parsed.outcome is OperationOutcome.COMPLETED
    assert parsed.entries == ()
    assert parsed.stats.total == 0
    assert parsed.diagnostics == ()
    assert parsed.source_snapshot.source == request.source
    assert parsed.source_snapshot.content == content
    assert dict(parsed.source_snapshot.metadata)["strings.order"] == ()
    assert dict(parsed.source_snapshot.metadata)["strings.records"] == ()


@pytest.mark.parametrize("format_id", FORMATS)
@pytest.mark.parametrize(
    "content",
    [b"", struct.pack("<II", 1, 0), struct.pack("<IIII", 1, 2, 19, 99) + b"x\x00"],
    ids=["truncated-header", "truncated-directory", "invalid-offset"],
)
def test_invalid_bytes_return_the_same_failure_diagnostic_as_local_parse(format_id, content, tmp_path):
    adapter = LocalizedStringsAdapter(format_id)
    request = _request(format_id)
    source = tmp_path / request.source.display_name
    source.write_bytes(content)
    local = adapter.parse(replace(request, source=SourceDescriptor(str(source), source.name)))

    parsed = adapter.parse_bytes(request, content)

    assert parsed.outcome is OperationOutcome.FAILED
    assert parsed.source_snapshot is None
    assert parsed.entries == ()
    assert parsed.stats.failed == 1
    assert parsed.diagnostics == local.diagnostics
    assert parsed.diagnostics[0].code == "LOCALIZED_STRINGS_PARSE_FAILED"


@pytest.mark.parametrize("format_id", FORMATS)
def test_duplicate_archived_ids_fail_with_original_directory_indices(format_id):
    content = _table(format_id, ((19, b"first"), (4, b"other"), (19, b"duplicate")))

    parsed = LocalizedStringsAdapter(format_id).parse_bytes(_request(format_id), content)

    assert parsed.outcome is OperationOutcome.FAILED
    assert parsed.source_snapshot is None
    assert parsed.entries == ()
    assert parsed.stats.failed == 1
    assert parsed.diagnostics[0].code == "LOCALIZED_STRING_ID_DUPLICATE"
    assert dict(parsed.diagnostics[0].details) == {"string_id": 19, "record_indices": (0, 2)}


@pytest.mark.parametrize("format_id", FORMATS)
def test_bytes_preserve_bom_non_utf8_directory_order_and_unreferenced_ids(format_id):
    originals = ((19, b"\xef\xbb\xbfBOM text"), (4, b"Caf\xe9"), (800, b""))
    content = _table(format_id, originals)
    namespace = SourceNamespace("bsa:example-strings")

    parsed = LocalizedStringsAdapter(format_id).parse_bytes(_request(format_id, source_namespace=namespace), content)

    assert parsed.outcome is OperationOutcome.COMPLETED
    assert parsed.stats.parsed == 3
    assert [entry.string_id for entry in parsed.entries] == [19, 4, 800]
    assert [entry.original for entry in parsed.entries] == ["BOM text", "Café", ""]
    assert all(entry.identity.namespace == namespace for entry in parsed.entries)
    assert all(entry.stage == 0 and entry.translation == "" for entry in parsed.entries)
    metadata = dict(parsed.source_snapshot.metadata)
    assert metadata["strings.order"] == (19, 4, 800)
    records = metadata["strings.records"]
    assert tuple(record.raw_payload for record in records) == tuple(payload for _, payload in originals)
    assert [record.encoding for record in records] == ["utf-8-sig", "cp1252", "utf-8"]
    assert parsed.source_snapshot.encoding == "per-entry"
    assert parsed.source_snapshot.content == content


@pytest.mark.parametrize("format_id", FORMATS)
@pytest.mark.parametrize("method", ["parse", "parse_bytes"])
@pytest.mark.parametrize("callable_token", [False, True])
def test_cancelled_parse_never_reads_a_path_or_returns_publishable_content(
    format_id, method, callable_token, monkeypatch
):
    cancellation = SimpleNamespace(is_cancelled=(lambda: True) if callable_token else True)
    request = _request(format_id, cancellation=cancellation)
    adapter = LocalizedStringsAdapter(format_id)
    monkeypatch.setattr("transbridge.application.io.strings_adapter._local_path", _forbid_local_path)

    parsed = adapter.parse(request) if method == "parse" else adapter.parse_bytes(request, b"invalid")

    assert parsed.outcome is OperationOutcome.CANCELLED
    assert parsed.source_snapshot is None
    assert parsed.entries == ()
    assert parsed.stats.cancelled == 1
    assert parsed.diagnostics[0].code == "PARSE_CANCELLED"
