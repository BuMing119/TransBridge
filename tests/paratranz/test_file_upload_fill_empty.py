"""File-level fill imports use a current server read and submit only matching blank rows."""

from dataclasses import replace
import json

import pytest
import requests

from tests.paratranz.test_file_publication import artifact, gateway, remote_rows
from transbridge.application.file_upload import execute_file_upload
from transbridge.paratranz.artifact_file_reads import ARTIFACT_FILE_THRESHOLD


@pytest.mark.parametrize("path", ["A.esp.json", "A.esp/人名.json"])
def test_only_matching_blank_rows_are_submitted_and_repeat_is_read_only(path):
    file = artifact(8, path=path)
    local = (" 译文 ", "二", "三", "替换人工", "替换机翻", "　\t", "仅本地", "相同")
    file = replace(file, entries=tuple(replace(e, translation=t) for e, t in zip(file.entries, local, strict=True)))
    rows = [row for row in remote_rows(file, legacy=True, stage=1) if row["key"] != "6"]
    for row, translation in zip(rows, ("", None, "\n　", "人工", "机翻", "", "相同"), strict=True):
        row["translation"] = translation
    rows[0]["key"] = "00000099|TB2|0"
    expected = [
        {"key": row["key"], "translation": translation, "stage": 3}
        for row, translation in zip(rows[:3], local[:3], strict=True)
    ]
    filled = [dict(row, **item) for row, item in zip(rows[:3], expected, strict=True)] + rows[3:]
    inventory = [{"id": 20, "name": path}]
    port, session = gateway(inventory, rows, {"file": {"id": 20}}, inventory, filled)

    first = execute_file_upload(7, (file,), port, mode="trans_fill_empty")
    second = execute_file_upload(7, (file,), port, mode="trans_fill_empty")

    assert first.files[0].status == "succeeded" and "已提交填空 3 条，云端已有译文跳过 3 条" in first.files[0].message
    assert second.files[0].status == "unchanged" and "已提交填空 0 条，云端已有译文跳过 6 条" in second.files[0].message
    assert [req.method for req in session.calls] == ["GET", "GET", "POST", "GET", "GET"]
    request = session.calls[2]
    assert request.path_url == "/api/projects/7/files/20/translation"
    payload = json.loads(request.body)
    assert payload["force"] is False and payload["items"] == expected


def test_retry_after_unknown_result_reads_which_rows_are_still_blank():
    file = artifact(2)
    inventory = [{"id": 20, "name": file.path}]
    rows = remote_rows(file, legacy=True, translation="")
    updated = [dict(rows[0], translation="协作者填写"), rows[1]]
    port, session = gateway(inventory, rows, requests.Timeout("lost reply"), inventory, updated, {"file": {"id": 20}})
    first = execute_file_upload(7, (file,), port, mode="trans_fill_empty")
    assert first.files[0].status == "uncertain" and first.retry_files == (file,)
    retry = execute_file_upload(7, first.retry_files, port, mode="trans_fill_empty")
    assert retry.succeeded == 1 and not retry.retry_files
    assert [req.method for req in session.calls] == ["GET", "GET", "POST", "GET", "GET", "POST"]
    assert json.loads(session.calls[-1].body)["items"] == [{"key": "1", "translation": "译文", "stage": 3}]


@pytest.mark.parametrize("fields", [{}, {"translation": 0}])
def test_unknown_remote_translation_is_never_treated_as_empty(fields):
    file = artifact()
    port, session = gateway([{"id": 20, "name": file.path}], [{"key": "0", **fields}])
    receipt = execute_file_upload(7, (file,), port, mode="trans_fill_empty")
    assert receipt.failed == 1 and "translation" in receipt.files[0].message
    assert [req.method for req in session.calls] == ["GET", "GET"]


def test_large_fill_batch_reads_each_selected_file_directly():
    files = tuple(artifact(path=f"A.esp/{i}.json") for i in range(ARTIFACT_FILE_THRESHOLD))
    inventory = [{"id": i, "name": file.path} for i, file in enumerate(files, 1)]
    port, session = gateway(inventory, *(remote_rows(file, file_id=i) for i, file in enumerate(files, 1)))
    receipt = execute_file_upload(7, files, port, mode="trans_fill_empty")
    assert receipt.succeeded == len(files)
    assert [(r.method, r.path_url) for r in session.calls[1:]] == [
        ("GET", f"/api/projects/7/files/{item['id']}/translation") for item in inventory
    ]
