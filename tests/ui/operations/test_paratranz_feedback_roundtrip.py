"""Feedback regressions through production sync and persisted V2 projects."""

from dataclasses import replace
import json
import logging
from types import SimpleNamespace

import pytest

from tests.ui.operations.test_paratranz_upload_roundtrip import RoundtripCase
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.security.hitl import ConfirmationAuthority
from transbridge.application.sync import use_case
from transbridge.ui.operations import plan_presenter
from transbridge.ui.operations.plan_presenter import OperationPlanPresenter
from transbridge.ui.operations.plan_view import OperationKind


@pytest.fixture
def feedback_case(tmp_path, monkeypatch):
    cases = []

    def create(**kwargs):
        case = RoundtripCase(tmp_path / f"feedback-{len(cases)}", monkeypatch, **kwargs)
        cases.append(case)
        return case

    yield create
    for case in cases:
        case.close()


def test_shifted_quest_journal_download_edit_upload_keeps_cloud_source_without_download_bindings(feedback_case):
    quest = "DLC2dunKolbjornQST:0201810B"
    case = feedback_case(keys=(f"{quest}|88~QUST:CNAM", f"{quest}|89~QUST:CNAM"))
    first, second = case.sources
    case.strings.entries.update({
        7: ParaTranzEntry(7, f"{quest}|80~QUST:CNAM", first.original, "第一阶段下载译文", "云端第一阶段说明", 1),
        8: ParaTranzEntry(8, first.key, second.original, "第二阶段下载译文", "云端第二阶段说明", 5),
    })
    cloud_sources = {
        identity: (entry.key, entry.original, entry.context) for identity, entry in case.strings.entries.items()
    }
    checked = case.check(OperationKind.DOWNLOAD)
    assert not checked[2].sync_review
    assert dict(checked[2].estimated_impact)["update_local"] == 2
    assert case.execute(checked).is_success
    downloaded = case.persisted_states()
    assert not downloaded[first.identity].external_refs
    assert not downloaded[second.identity].external_refs
    for source in case.sources:
        local = case.context.collection.get(source.identity)
        assert (local.original, local.context, local.identity) == (source.original, source.context, source.identity)

    case.edit(first, "第一阶段本地修改", 3)
    case.edit(second, "第二阶段本地修改", 5)
    assert case.execute(case.check()).is_success
    assert case.strings.calls == [
        ("update", 7, {"translation": "第一阶段本地修改", "stage": 3}),
        ("update", 8, {"translation": "第二阶段本地修改", "stage": 5}),
    ]
    assert {
        identity: (entry.key, entry.original, entry.context) for identity, entry in case.strings.entries.items()
    } == cloud_sources
    saved = case.persisted_states()
    assert saved[first.identity].translation == "第一阶段本地修改"
    assert saved[second.identity].external_refs[0].opaque_id == 8
    assert dict(case.check()[2].estimated_impact)["update_remote"] == 0


def test_54_equivalent_downloads_do_not_invent_upload_target_and_explicit_target_survives_reopen(feedback_case):
    case = feedback_case(size=1)
    source = case.sources[0]
    case.strings.entries.update({
        identity: ParaTranzEntry(identity, source.key, source.original, "重复的共同译文", "共同云端说明", 1)
        for identity in range(1, 55)
    })
    checked = case.check(OperationKind.DOWNLOAD)
    assert not checked[2].sync_review
    assert dict(checked[2].estimated_impact)["update_local"] == 1
    assert case.execute(checked).is_success
    saved = case.persisted_states()[source.identity]
    assert saved.translation == "重复的共同译文"
    assert not saved.external_refs

    case.edit(source, "只应上传到指定目标", 3)
    checked = case.check()
    impact = dict(checked[2].estimated_impact)
    assert (impact["deferred"], impact["update_remote"], impact["create_remote"]) == (1, 0, 0)
    row = checked[2].sync_review[0]
    assert len(row.candidates) == 54
    assert not row.decision
    assert case.strings.calls == []
    selected = next(candidate for candidate in row.candidates if "#54" in candidate.label)
    assert case.execute(case.check(choices={row.row_id: selected.candidate_id})).is_success
    assert case.strings.calls == [("update", 54, {"translation": "只应上传到指定目标", "stage": 3})]
    assert all(
        entry.translation == "重复的共同译文" for identity, entry in case.strings.entries.items() if identity != 54
    )
    assert case.persisted_states()[source.identity].external_refs[0].opaque_id == 54
    assert not case.check()[2].sync_review
    assert dict(case.check()[2].estimated_impact)["update_remote"] == 0


def test_equivalent_candidates_changed_after_check_keep_reviewed_translation_for_this_download(feedback_case):
    case = feedback_case(size=1)
    source = case.sources[0]
    case.strings.entries.update({
        7: ParaTranzEntry(7, source.key, source.original, "共同译文", "共同说明", 1),
        8: ParaTranzEntry(8, source.key, source.original, "共同译文", "共同说明", 1),
    })
    feature, draft, preflight = case.check(OperationKind.DOWNLOAD)
    assert preflight.ready and not preflight.sync_review
    ref = feature.submit(draft, preflight, case.owner, case.tasks)
    case.strings.entries[8] = replace(case.strings.entries[8], translation="对照后另一位译者的修改")
    case.queue.pop(0)()
    case.refresh()
    assert case.tasks.get(ref, case.owner).state.value == "completed"
    assert case.context.collection.get(source.identity).translation == "共同译文"
    assert case.strings.calls == []
    assert not case.persisted_states()[source.identity].external_refs
    fresh = case.check(OperationKind.DOWNLOAD)
    assert fresh[2].sync_review
    assert "对照后另一位译者的修改" in {item.translation for item in fresh[2].sync_review[0].candidates}


def test_quest_journal_does_not_match_same_text_in_another_quest(feedback_case):
    case = feedback_case(size=1, keys=("FirstQuest:0201810B|88~QUST:CNAM",))
    source = case.sources[0]
    case.strings.entries.update({
        7: ParaTranzEntry(7, source.key, "当前任务中不同的阶段原文", "不应采用的译文", "", 1),
        8: ParaTranzEntry(8, "OtherQuest:0201810C|80~QUST:CNAM", source.original, "另一个任务的译文", "", 1),
    })
    checked = case.check(OperationKind.DOWNLOAD)
    assert dict(checked[2].estimated_impact)["deferred"] == 1
    assert all(candidate.translation != "另一个任务的译文" for candidate in checked[2].sync_review[0].candidates)
    assert case.strings.calls == []


def test_slow_cloud_reads_and_user_wait_keep_confirmation_valid_and_record_execution(
    feedback_case, monkeypatch, caplog
):
    elapsed = [0.0]

    def authority(**kwargs):
        return ConfirmationAuthority(clock=lambda: elapsed[0], **kwargs)

    monkeypatch.setattr(use_case, "ConfirmationAuthority", authority)
    monkeypatch.setattr(plan_presenter, "ConfirmationAuthority", authority)
    caplog.set_level(logging.INFO, logger="transbridge.application.sync.execution_diagnostics")
    case = feedback_case(size=1)
    source = case.sources[0]
    case.strings.entries[7] = ParaTranzEntry(7, source.key, source.original, "慢速读取的译文", "云端说明", 1)
    read = case.strings.list_strings

    def slow_read(*args, **kwargs):
        elapsed[0] += 118.147
        return read(*args, **kwargs)

    case.strings.list_strings = slow_read
    for operation in (OperationKind.DOWNLOAD, OperationKind.UPLOAD):
        if operation is OperationKind.UPLOAD:
            case.edit(source, "本地修改后上传", 3)
        feature, draft, _preflight = case.check(operation)
        submitter = SimpleNamespace(
            submit=lambda kind, request, checked, owner: feature.submit(request, checked, case.owner, case.tasks)
        )
        presenter = OperationPlanPresenter((feature.mapper,), submitter)
        opened = presenter.open(operation, draft, owner_id=case.owner.owner_id)
        checked = presenter.preflight(opened.session_id, owner_id=case.owner.owner_id)
        assert checked.confirmation_token.expires_at is None
        elapsed[0] += 3600
        ref = presenter.confirm(opened.session_id, checked.confirmation_token, owner_id=case.owner.owner_id)
        case.queue.pop(0)()
        case.refresh()
        assert case.receipts[ref.run_id].is_success
        events = [
            json.loads(record.getMessage())
            for record in caplog.records
            if record.name.endswith("execution_diagnostics")
        ]
        current = [event for event in events if event["run_id"] == ref.run_id]
        assert {event["operation"] for event in current} == {operation.value}
        assert len({event["plan_id"] for event in current}) == 1
        assert {"preparation", "commit_permission", "execution", "cleanup", "result"} <= {
            event["phase"] for event in current
        }
        assert next(event for event in current if event["phase"] == "result")["counts"]["succeeded"] == 1
    assert case.strings.calls == [("update", 7, {"translation": "本地修改后上传", "stage": 3})]
