"""Retain exact unsuccessful file parts for an explicit later upload."""

import requests

from tests.paratranz.test_file_publication import artifact, gateway
from transbridge.application.file_upload import execute_file_upload


def test_retry_only_failed_413_part_with_original_rows_and_path():
    file = artifact(4, "A.esp.json")
    port, session = gateway([], (413, {}, {}), {"file": {"id": 20}}, requests.ConnectionError("response lost"))
    first = execute_file_upload(7, (file,), port)
    assert first.succeeded == first.failed == len(first.retry_files) == 1
    pending = first.retry_files[0]
    assert pending.path == "A.esp_2.json" and pending.entries == file.entries[2:]
    assert len(session.calls) == 4

    port, session = gateway([{"id": 20, "name": "A.esp_1.json"}], {"file": {"id": 21}})
    second = execute_file_upload(7, first.retry_files, port)
    assert second.succeeded == 1 and second.retry_files == ()
    assert len(session.calls) == 2
    assert b"A.esp_2.json" in session.calls[1].body
    assert b'"key": "2"' in session.calls[1].body and b'"key": "0"' not in session.calls[1].body


def test_permission_failure_retains_failed_and_unattempted_files():
    files = (artifact(), artifact(path="B.esp/人名.json"))
    port, _session = gateway([], (403, {}, {}))
    result = execute_file_upload(7, files, port)
    assert [item.status for item in result.files] == ["failed", "skipped"]
    assert result.retry_files == files


def test_cancel_before_network_retains_all_files():
    files = (artifact(), artifact(path="B.esp/人名.json"))
    port, session = gateway()
    result = execute_file_upload(7, files, port, cancelled=lambda: True)
    assert result.retry_files == files and session.calls == []


def test_local_preparation_failure_can_be_retried_without_including_success():
    invalid = artifact()
    invalid = type(invalid)(invalid.source_id, invalid.category_id, invalid.path, invalid.entries * 2)
    files = (invalid, artifact(path="B.esp/人名.json"))
    port, session = gateway([], {"file": {"id": 20}})
    result = execute_file_upload(7, files, port)
    assert result.succeeded == result.failed == 1
    assert result.retry_files == (invalid,) and len(session.calls) == 2
