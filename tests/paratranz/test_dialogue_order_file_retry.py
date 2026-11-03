"""Keep the complete plugin projection frozen through actual file publication."""

from dataclasses import replace
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path

import pytest
import requests

from tests.dialogue_order_support import dialogue_bytes
from tests.paratranz.test_file_publication import gateway
from transbridge.application.categorized_upload import UploadSource, categorize_sources
from transbridge.application.file_upload import execute_file_upload, wire_records
from transbridge.application.io.paratranz_context_order import parse_ordered_context
from transbridge.parser.plugin.dialogue_order_evidence import read_dialogue_order_evidence
from transbridge.parser.plugin_parser import PluginParser


@pytest.fixture
def dialogue_file():
    parser = PluginParser()
    entries = tuple(
        replace(entry, translation=f"译文:{entry.original}", stage=3)
        for entry in parser.parse_plugin(
            Path("fixture.esp"),
            captured_content=dialogue_bytes(),
            discover_sibling_strings=False,
            skip_empty=False,
            raise_on_error=True,
        )
    )
    evidence = read_dialogue_order_evidence(parser.get_plugin())
    files = categorize_sources((UploadSource("source", "fixture.esp", entries, evidence),))
    result = next(file for file in files if file.category_id == "dialogue:00000001")
    assert [entry.original for entry in result.entries] == [
        "First topic",
        "First reply",
        "Second reply",
        "Later topic",
        "Later reply",
    ]
    # QUST and BOOK occupy positions in the complete source before this file.
    assert [parse_ordered_context(row["context"])[1] for row in wire_records(result)] == [2, 3, 4, 5, 6]
    return result


def uploaded_rows(request):
    envelope = f"Content-Type: {request.headers['Content-Type']}\r\nMIME-Version: 1.0\r\n\r\n".encode() + request.body
    message = BytesParser(policy=policy.default).parsebytes(envelope)
    part = next(part for part in message.walk() if part.get_filename() is not None)
    return json.loads(part.get_payload(decode=True))


def test_413_partial_failure_and_receipt_retry_keep_complete_source_numbers(dialogue_file):
    expected = list(wire_records(dialogue_file))
    before = tuple(entry.to_dict() for entry in dialogue_file.entries)
    port, first_session = gateway(
        [], (413, {}, {}), {"file": {"id": 20}}, requests.ConnectionError("second part response lost")
    )
    first = execute_file_upload(7, (dialogue_file,), port)
    assert first.succeeded == first.failed == len(first.retry_files) == 1
    assert [item.status for item in first.files] == ["succeeded", "uncertain"]
    (pending,) = first.retry_files
    assert pending.entries == dialogue_file.entries[2:]
    assert uploaded_rows(first_session.calls[1]) == expected
    accepted = uploaded_rows(first_session.calls[2])
    failed = uploaded_rows(first_session.calls[3])
    assert accepted == expected[:2] and failed == expected[2:]

    retry_port, retry_session = gateway([{"id": 20, "name": first.files[0].path}], {"file": {"id": 21}})
    second = execute_file_upload(7, first.retry_files, retry_port)
    assert second.succeeded == 1 and second.failed == 0 and not second.retry_files
    assert len(retry_session.calls) == 2
    assert second.files[0].path == pending.path
    retried = uploaded_rows(retry_session.calls[1])
    assert retried == failed
    merged = accepted + retried
    assert merged == expected
    assert [(row["key"], row["original"], row["context"]) for row in merged] == [
        (row["key"], row["original"], row["context"]) for row in expected
    ]
    # The 413 boundary splits the two responses of the same INFO record.
    assert [row["original"] for row in merged if row["original"] in {"First reply", "Second reply"}] == [
        "First reply",
        "Second reply",
    ]
    assert tuple(entry.to_dict() for entry in dialogue_file.entries) == before


@pytest.mark.parametrize(("mode", "force"), [("trans_safe", False), ("trans_force", True)])
def test_translation_only_413_keeps_keys_and_never_sends_display_context(dialogue_file, mode, force):
    expected = [{name: row[name] for name in ("key", "translation", "stage")} for row in wire_records(dialogue_file)]
    before = tuple(entry.to_dict() for entry in dialogue_file.entries)
    port, session = gateway(
        [{"id": 20, "name": dialogue_file.path}],
        (413, {}, {}),
        {"file": {"id": 20}},
        {"file": {"id": 20}},
    )
    result = execute_file_upload(7, (dialogue_file,), port, mode=mode)
    assert result.succeeded == 1 and not result.retry_files
    payloads = [json.loads(request.body) for request in session.calls[1:]]
    assert [payload["items"] for payload in payloads] == [expected, expected[:2], expected[2:]]
    assert all(payload["force"] is force for payload in payloads)
    assert all(set(row) == {"key", "translation", "stage"} for payload in payloads for row in payload["items"])
    assert all(request.path_url.endswith("/files/20/translation") for request in session.calls[1:])
    assert tuple(entry.to_dict() for entry in dialogue_file.entries) == before
