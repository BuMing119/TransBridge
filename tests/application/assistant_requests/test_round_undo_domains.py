"""Cross-domain undo preflights, partial evidence, and later conversation facts."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from tests.application.assistant_requests.test_config_undo import _settle, _setup
from tests.application.assistant_requests.test_request_repository import _snapshot
from tests.application.assistant_requests.test_round_undo import _commit, _record
from tests.contracts.config.test_unified_repository import _repository
from transbridge.application.assistant_context.state_projection import decision_context
from transbridge.application.assistant_requests.config_undo_capture import capture_config_commit
from transbridge.application.assistant_requests.file_undo_capture import capture_file_commit
from transbridge.application.assistant_requests.models import RequestError, RequestStatus
from transbridge.application.io.file_undo import FileUndoAdapter

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def test_resume_without_new_input_cannot_reauthorize_undone_round(composed, tmp_path):
    case, _ = _mixed(composed, tmp_path)
    _, requests, context, _, _, _, execution, undo = case
    requests.update_request(
        context,
        execution.request_id,
        lambda request: replace(
            request,
            status=RequestStatus.OPEN,
            pause_reasons=("user_interrupted",),
        ),
    )
    undo.undo(context, execution.work_round_id)
    before = requests.state(context)
    with pytest.raises(RequestError, match="UNDO_ROUND_CLOSED"):
        requests.command(context, execution.request_id, "resume", execution.request_revision)
    assert requests.state(context) == before


def _mixed(composed, tmp_path):
    case = _setup(composed)
    _, requests, context, _, _, _, execution, undo = case
    undo.config_repository = _repository(tmp_path)
    undo.file_backup_root = tmp_path / "file-backups"
    undo.configuration().update_sections({"llm": {"temperature": "0.1"}})
    _commit(case)
    capture_config_commit(undo, context, execution, "effect", {"temperature": "0.8"})
    paths = (tmp_path / "first.txt", tmp_path / "second.txt")
    for path in paths:
        path.write_bytes(b"before")
        capture_file_commit(
            undo,
            context,
            execution,
            "effect",
            [path],
            lambda: path.write_bytes(b"after"),
            backup_root=undo.file_backup_root,
        )
    _settle(requests, context, execution)
    return case, paths


@pytest.mark.parametrize("conflict", ["variant", "config", "files"])
def test_each_domain_preflight_conflict_leaves_every_other_domain_untouched(composed, tmp_path, conflict):
    case, paths = _mixed(composed, tmp_path)
    _, _, context, harness, _, _, execution, undo = case
    if conflict == "config":
        undo.configuration().update_sections({"llm": {"temperature": "0.9"}})
    elif conflict == "files":
        paths[1].write_bytes(b"user edit")
    else:
        current = harness.service.active.variant.snapshot()
        changed = replace(current.entries[0], translation="user edit", revision=current.entries[0].revision.next())
        from transbridge.persistence.v2.variant import VariantChangeSet

        result = harness.service.commit_active_variant(
            VariantChangeSet(
                current.ref,
                current.revision,
                current.source_fingerprints,
                (changed, *current.entries[1:]),
                current.label_library,
                "user",
            ),
            replace(context, run_id="user"),
            expected_project_revision=harness.service.active.project.envelope.revision,
        )
        assert result.is_success
    project_before = harness.service.active.variant.snapshot()
    config_before = undo.configuration().load()
    files_before = [path.read_bytes() for path in paths]
    with pytest.raises(Exception):
        undo.undo(context, execution.work_round_id)
    assert harness.service.active.variant.snapshot() == project_before
    assert undo.configuration().load() == config_before
    assert [path.read_bytes() for path in paths] == files_before
    assert _record(case)["status"] == "recording"


def test_full_inverse_adds_later_fact_and_current_decision_material(composed, tmp_path):
    case, paths = _mixed(composed, tmp_path)
    services, requests, context, harness, before, _, execution, undo = case
    original = {"message_id": "historical-success", "role": "assistant", "content": "此前业务操作已完成"}
    requests.save_history(context, [original], request_id=execution.request_id)
    result = undo.undo(context, execution.work_round_id)
    assert not result["partial"]
    assert harness.service.active.variant.snapshot().entries[0].translation == before.entries[0].translation
    assert undo.configuration().load().value("llm", "temperature") == "0.1"
    assert [path.read_bytes() for path in paths] == [b"before", b"before"]
    messages = _snapshot(services, context).backend_messages()
    assert next(message for message in messages if message["message_id"] == original["message_id"]) == original
    assert messages.index(original) < len(messages) - 1
    fact = json.loads(messages[-1]["content"])
    assert fact["kind"] == "round_undo_receipt" and fact["status"] == "undone"
    assert fact["request_ids"] == [execution.request_id]
    state = requests.state(context)
    request = next(r for r in requests.requests(state) if r.request_id == execution.request_id)
    material = decision_context(request, SimpleNamespace(ready_item_ids=()), state)
    assert material["round_restoration"] == [
        {"round_id": execution.work_round_id, "status": "undone", "limitations": []}
    ]
    assert _record(case)["progress"] == {"variant": "applied", "config": "applied", "files": "applied"}
    assert _record(case)["undo_sequence"] == 1


def test_completed_undo_returns_persisted_result_without_reapplying_domains(composed, tmp_path, monkeypatch):
    case, _ = _mixed(composed, tmp_path)
    services, requests, context, _, _, _, execution, undo = case
    first = undo.undo(context, execution.work_round_id)
    snapshot = _snapshot(services, context)

    def forbidden(*args, **kwargs):
        pytest.fail("completed undo must not transact, preflight, or reapply")

    monkeypatch.setattr(requests, "transact", forbidden)
    monkeypatch.setattr(undo, "preview", forbidden)
    second = undo.undo(context, execution.work_round_id)
    assert second == first
    assert _snapshot(services, context).assistant_data() == snapshot.assistant_data()
    assert _snapshot(services, context).backend_messages() == snapshot.backend_messages()


def test_latest_restoration_uses_undo_sequence_and_ignores_dictionary_order(composed, tmp_path):
    case, _ = _mixed(composed, tmp_path)
    _, requests, context, _, _, _, execution, undo = case
    undo.undo(context, execution.work_round_id)
    state = requests.state(context)
    request = next(r for r in requests.requests(state) if r.request_id == execution.request_id)
    actual = state["undo_rounds"][execution.work_round_id]
    older = deepcopy(actual)
    older.update(undo_sequence=0, status="partially_undone", limitations=["older limitation"])
    foreign = deepcopy(actual)
    foreign.update(undo_sequence=100)
    for effect in foreign["effects"].values():
        effect["request_id"] = "another-request"
    for records in (
        {execution.work_round_id: actual, "older": older, "foreign": foreign},
        {"foreign": foreign, "older": older, execution.work_round_id: actual},
    ):
        state["undo_rounds"] = records
        assert decision_context(request, SimpleNamespace(ready_item_ids=()), state)["round_restoration"] == [
            {"round_id": execution.work_round_id, "status": "undone", "limitations": []}
        ]


def test_final_receipt_failure_keeps_all_domain_changes_and_forbids_replay(composed, tmp_path, monkeypatch):
    case, paths = _mixed(composed, tmp_path)
    services, requests, context, harness, before, _, execution, undo = case
    original = requests.transact

    def fail_finish(*args, **kwargs):
        cause = kwargs.get("cause")
        if cause is not None and cause.operation == "round.undone":
            raise OSError("final receipt failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(requests, "transact", fail_finish)
    with pytest.raises(OSError, match="final receipt"):
        undo.undo(context, execution.work_round_id)
    assert _record(case)["status"] == "undoing"
    assert _record(case)["progress"] == {"variant": "applied", "config": "applied", "files": "applied"}
    assert harness.service.active.variant.snapshot().entries[0].translation == before.entries[0].translation
    assert undo.configuration().load().value("llm", "temperature") == "0.1"
    assert [path.read_bytes() for path in paths] == [b"before", b"before"]
    state = requests.state(context)
    request = next(r for r in requests.requests(state) if r.request_id == execution.request_id)
    assert (
        decision_context(request, SimpleNamespace(ready_item_ids=()), state)["round_restoration"][0]["status"]
        == "undoing"
    )
    assert not any(
        "round_undo_receipt" in m.get("content", "") for m in _snapshot(services, context).backend_messages()
    )
    with pytest.raises(RequestError, match="UNDO_NOT_AVAILABLE"):
        undo.undo(context, execution.work_round_id)


def test_partial_file_restore_is_durable_and_does_not_claim_full_round_success(composed, tmp_path, monkeypatch):
    case, paths = _mixed(composed, tmp_path)
    services, _, context, harness, before, _, execution, undo = case
    original = FileUndoAdapter._restore

    def fail_second(self, path, row):
        if path == paths[1]:
            raise OSError("second output locked")
        return original(self, path, row)

    monkeypatch.setattr(FileUndoAdapter, "_restore", fail_second)
    with pytest.raises(RequestError, match="UNDO_FILES_INCOMPLETE"):
        undo.undo(context, execution.work_round_id)
    record = _snapshot(services, context).assistant_data()["undo_rounds"][execution.work_round_id]
    assert record["status"] == "undoing"
    assert record["progress"] == {"variant": "applied", "config": "applied", "files": "applying"}
    assert record["file_result"]["status"] == "partial"
    assert record["file_result"]["restored"] == [str(paths[0])]
    assert record["file_result"]["remaining"] == [str(paths[1])]
    assert harness.service.active.variant.snapshot().entries[0].translation == before.entries[0].translation
    assert undo.configuration().load().value("llm", "temperature") == "0.1"
    assert [path.read_bytes() for path in paths] == [b"before", b"after"]
    with pytest.raises(RequestError, match="UNDO_NOT_AVAILABLE"):
        undo.undo(context, execution.work_round_id)


def test_project_save_invalidating_file_after_preflight_is_detected_before_file_write(composed, tmp_path, monkeypatch):
    case, paths = _mixed(composed, tmp_path)
    _, _, context, harness, before, _, execution, undo = case
    original = harness.service.save_active

    def save(context):
        result = original(context)
        paths[1].write_bytes(b"changed during project save")
        return result

    monkeypatch.setattr(harness.service, "save_active", save)
    with pytest.raises(RequestError, match="UNDO_FILES_INCOMPLETE"):
        undo.undo(context, execution.work_round_id)
    assert harness.service.active.variant.snapshot().entries[0].translation == before.entries[0].translation
    assert undo.configuration().load().value("llm", "temperature") == "0.1"
    assert [path.read_bytes() for path in paths] == [b"after", b"changed during project save"]
    record = _record(case)
    assert record["status"] == "undoing"
    assert record["file_result"]["status"] == "conflict"
    assert record["file_result"]["restored"] == []
