from dataclasses import replace
import json

import pytest

from transbridge.application.io import FormatId, Stage
from transbridge.application.io.identity import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.io.mutation import EntrySnapshot
from transbridge.application.projects.storage_contracts import ExtractedSource, StoredEntry
from transbridge.application.projects.working_copy import ProjectWorkingCopy
from transbridge.application.tasks.result_application import ResultApplication
from transbridge.application.tasks.storage_contracts import ApplicationState
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.project_store.catalog import ActiveProjectPreference, ProjectDeletionStore
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.records import WorkspaceProjects
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.task_store import TaskStore, TaskStoreError
from transbridge.persistence.v2 import OsPersistenceFilesystem
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantId
from transbridge.persistence.v2.variant import VariantEntryState

_KEY = EntryKey(SourceNamespace("source:task-test"), "NPC:Welcome")
_PROJECT_ID = ProjectId("task-target")
_VARIANT_ID = VariantId("main")
_RUN_ID = "download-one"
_TIME = "2026-11-09T00:00:00+08:00"
_OWNER = {"owner_id": "owner-a", "session_id": "session-a"}


def _task_record(run_id=_RUN_ID, **changes):
    return {
        "task_id": run_id,
        "project_id": _PROJECT_ID.value,
        "variant_id": _VARIANT_ID.value,
        "mode": "download",
        "config_digest": "config-v1",
        "state": "running",
        "created_at": _TIME,
        "sources": [],
        "result_kind": "download",
        "results_only": True,
        **changes,
    }


def _candidate(text="Downloaded translation"):
    return {
        "entry_key": _KEY.to_dict(),
        "source": "Example.esp",
        "action": "translation",
        "before": {"original": "Welcome", "translation": "", "stage": 0, "context": "NPC_:FULL", "revision": 0},
        "status": "succeeded",
        "text": text,
        "stage": Stage.TRANSLATED.value,
        "decision": "pending",
        "applied": False,
        "attempt": 1,
    }


@pytest.fixture
def stores(tmp_path):
    project = ProjectStore(project_path(tmp_path, _PROJECT_ID))
    entry = EntrySnapshot(_KEY, "welcome", "Welcome", "", 0, "NPC_:FULL", (), EntryRevision(), (), ())
    source = ExtractedSource(
        _KEY.namespace,
        "Example.esp",
        FormatId.PLUGIN_SSE,
        "a" * 64,
        (StoredEntry(entry, VariantEntryState(_KEY)),),
    )
    project.create(_PROJECT_ID, "Task target", _VARIANT_ID, "Main", (source,))
    return project, TaskStore(tmp_path / "task-store-v1")


def _application(recovery, project, *, staged=None):
    return ResultApplication(
        recovery.decisions,
        lambda _project_id, operation_id: project.receipt(operation_id),
        lambda project_id: project_id == _PROJECT_ID.value and project.path.is_file(),
        is_staged=None if staged is None else staged.__contains__,
    )


def test_project_deletion_does_not_end_run_or_remove_result_access(stores):
    project, task_store = stores
    recovery = task_store.recovery(**_OWNER)
    recovery.create(_task_record())
    recovery.save_results(_RUN_ID, [_candidate("Partial result")])
    initial = recovery.load_results(_RUN_ID)["rows"]
    requested = _application(recovery, project).prepare(_RUN_ID, _PROJECT_ID, _VARIANT_ID, initial, "prefer_incoming")
    recovery.acquire(_RUN_ID)
    try:
        workspace = task_store.root.parent
        deletion = ProjectDeletionStore(
            WorkspaceProjects(workspace),
            ActiveProjectPreference(str(workspace), OsPersistenceFilesystem()),
        )
        deletion.delete(ProjectRef(_PROJECT_ID))
        recovery.save_results(_RUN_ID, [{**_candidate(), "attempt": 2}])
        recovery.update_metadata(_RUN_ID, state="completed")
    finally:
        recovery.release()

    restarted = TaskStore(task_store.root)
    assert restarted.path.is_file()
    scoped = restarted.recovery(**_OWNER)
    (summary,) = scoped.list_summaries(project_id=_PROJECT_ID.value, states=("completed",), limit=1)
    assert summary["task_id"] == _RUN_ID
    (candidate,) = scoped.load_results(_RUN_ID)["rows"]
    assert candidate["text"] == "Downloaded translation"
    assert candidate["candidate_revision"] == 2
    application = _application(scoped, project)
    assert application.reconcile(requested.request.operation_id).state is ApplicationState.TARGET_UNAVAILABLE
    with pytest.raises(ValueError, match="原项目已删除"):
        application.prepare(_RUN_ID, _PROJECT_ID, _VARIANT_ID, [candidate], "prefer_incoming")
    assert not project.path.exists()
    assert scoped.load_results(_RUN_ID)["rows"] == [candidate]

    for other in (
        restarted.recovery(owner_id="owner-b", session_id="session-a"),
        restarted.recovery(owner_id="owner-a", session_id="session-b"),
    ):
        assert other.list_summaries() == ()
        with pytest.raises(TaskStoreError, match="scope"):
            other.load_results(_RUN_ID)


def test_task_delete_respects_active_lease_and_requires_unmerged_confirmation(stores):
    _project, task_store = stores
    worker = task_store.recovery(**_OWNER)
    worker.create(_task_record())
    worker.save_results(_RUN_ID, [_candidate()])
    manager = TaskStore(task_store.root).recovery(**_OWNER)
    worker.acquire(_RUN_ID)
    try:
        with pytest.raises(RuntimeError):
            manager.delete(_RUN_ID, discard_unmerged=True)
        assert manager.load_results(_RUN_ID)["rows"][0]["text"] == "Downloaded translation"
    finally:
        worker.release()

    with pytest.raises(TaskStoreError, match="confirmation"):
        manager.delete(_RUN_ID)
    manager.delete(_RUN_ID, discard_unmerged=True)
    assert not manager.list_summaries()
    with pytest.raises(TaskStoreError):
        worker.save_results(_RUN_ID, [_candidate("Late completion")])


def test_task_deletion_keeps_retryable_cleanup_and_never_removes_neighbor_or_output(tmp_path, monkeypatch):
    from transbridge.persistence.task_store import recovery as recovery_module

    store = TaskStore(tmp_path / "tasks")
    recovery = store.recovery(**_OWNER)
    recovery.create(_task_record(state="completed"))
    recovery.create(_task_record("neighbor", state="completed"))
    owned = store.run_directory(_RUN_ID) / "runtime-v2"
    adjacent = store.run_directory("neighbor") / "runtime-v2"
    user_output = tmp_path / "export.csv"
    for item in (owned, adjacent):
        item.mkdir(parents=True)
        (item / "checkpoint").write_bytes(b"checkpoint")
    user_output.write_bytes(b"exported result")

    def blocked_cleanup(path):
        assert path == owned.parent
        raise OSError("sharing violation")

    with monkeypatch.context() as patch:
        patch.setattr(recovery_module.shutil, "rmtree", blocked_cleanup)
        with pytest.raises(TaskStoreError, match="sharing violation"):
            recovery.delete(_RUN_ID)

    restarted = TaskStore(store.root).recovery(**_OWNER)
    assert [item["task_id"] for item in restarted.list_summaries()] == ["neighbor"]
    assert restarted.list_cleanup_pending() == (_RUN_ID,)
    assert store.recovery(owner_id="another", session_id="another").list_cleanup_pending() == ()
    assert owned.is_dir()
    restarted.delete(_RUN_ID)
    assert restarted.list_cleanup_pending() == ()
    assert not owned.parent.exists()
    assert (adjacent / "checkpoint").read_bytes() == b"checkpoint"
    assert user_output.read_bytes() == b"exported result"


def test_reconciles_after_task_acknowledgement_failure_and_deletion_keeps_project(stores, monkeypatch):
    project, task_store = stores
    recovery = task_store.recovery(**_OWNER)
    recovery.create(_task_record(state="completed"))
    recovery.save_results(_RUN_ID, [_candidate()])
    (candidate,) = recovery.load_results(_RUN_ID)["rows"]
    staged = set()
    service = _application(recovery, project, staged=staged)
    request = service.prepare(_RUN_ID, _PROJECT_ID, _VARIANT_ID, [candidate], "prefer_incoming")
    operation_id = request.request.operation_id
    assert request.state is ApplicationState.PREPARED
    working = ProjectWorkingCopy(project, _VARIANT_ID)
    working.edit(_KEY, translation=candidate["text"], stage=candidate["stage"])
    staged.add(operation_id)
    assert service.reconcile(operation_id).state is ApplicationState.STAGED_UNSAVED
    assert project.receipt(operation_id) is None
    assert project.read_entries(_VARIANT_ID, [_KEY])[0].state.translation == ""

    receipt = working.save(operation_id)
    assert receipt.project_revision == 1
    before = project.path.read_bytes()

    def fail_acknowledgement(*_):
        raise OSError("task store unavailable after project commit")

    with monkeypatch.context() as patch:
        patch.setattr(recovery.decisions, "acknowledge", fail_acknowledgement)
        assert service.reconcile(operation_id).state is ApplicationState.ACKNOWLEDGEMENT_PENDING
    assert project.path.read_bytes() == before
    assert recovery.load_results(_RUN_ID)["rows"] == [candidate]

    restarted = TaskStore(task_store.root).recovery(**_OWNER)
    service = _application(restarted, ProjectStore(project.path))
    result = service.reconcile(operation_id)
    assert result.state is ApplicationState.COMMITTED
    assert result.receipt["project_revision"] == 1
    assert result.receipt["entries"][0]["translation"] == "Downloaded translation"
    assert service.reconcile(operation_id) == result
    with pytest.raises(TaskStoreError, match="Operation ID"):
        restarted.decisions.prepare(replace(request.request, policy="prefer_current"))
    restarted.delete(_RUN_ID, discard_unmerged=True)

    assert not restarted.list_summaries()
    assert project.path.read_bytes() == before
    assert project.receipt(operation_id).entries == receipt.entries
    assert project.read_entries(_VARIANT_ID, [_KEY])[0].state.translation == "Downloaded translation"


def test_candidate_overwritten_before_save_is_not_acknowledged_as_persisted(stores):
    project, task_store = stores
    recovery = task_store.recovery(**_OWNER)
    recovery.create(_task_record(state="completed"))
    recovery.save_results(_RUN_ID, [_candidate()])
    (candidate,) = recovery.load_results(_RUN_ID)["rows"]
    service = _application(recovery, project)
    request = service.prepare(_RUN_ID, _PROJECT_ID, _VARIANT_ID, [candidate], "prefer_incoming")
    operation_id = request.request.operation_id
    working = ProjectWorkingCopy(project, _VARIANT_ID)
    working.edit(_KEY, translation=candidate["text"], stage=candidate["stage"])
    working.edit(_KEY, translation="Manual edit before save")
    working.save(operation_id)

    reconciled = service.reconcile(operation_id)

    assert reconciled.state is ApplicationState.DISCARDED
    assert not (reconciled.receipt or {}).get("entries")
    assert recovery.load_results(_RUN_ID)["rows"][0]["text"] == "Downloaded translation"
    assert project.read_entries(_VARIANT_ID, [_KEY])[0].state.translation == "Manual edit before save"


def test_task_scope_keeps_path_like_translation_but_drops_source_location_and_parser_metadata(tmp_path):
    store = TaskStore(tmp_path / "tasks")
    recovery = store.recovery(**_OWNER)
    original = "/path/quoted/in/dialogue"
    translation = "D:\\A\\Path\\In\\Translation"
    historical_path = "Z:\\private-plugin-427\\Example.esp"
    parse_tree_marker = "PRIVATE-PARSE-TREE-669"
    entry = TranslationEntry(
        "welcome",
        _KEY.local_key,
        original,
        translation,
        0,
        "NPC_:FULL",
        entry_key=_KEY,
        metadata=(("parser_tree", {"record": parse_tree_marker}), ("source_path", historical_path)),
    )
    record = _task_record(
        mode="translation",
        result_kind="translation",
        results_only=False,
        recovery_contract=2,
        overwrite=False,
        execution_runs=[_RUN_ID],
        sources=[
            {
                "key": "source:task-test",
                "label": "Example.esp",
                "esp_path": historical_path,
                "translate_keys": [_KEY.to_dict()],
                "polish_keys": [],
                "inputs": [
                    {
                        "entry_key": _KEY.to_dict(),
                        "original": original,
                        "translation": translation,
                        "stage": 0,
                        "context": "NPC_:FULL",
                        "revision": 0,
                        "entry": entry.to_dict(),
                    }
                ],
            }
        ],
    )

    recovery.create(record)

    (source,) = TaskStore(store.root).recovery(**_OWNER).load(_RUN_ID)["sources"]
    (saved,) = source["inputs"]
    assert (saved["original"], saved["translation"]) == (original, translation)
    assert "esp_path" not in source
    assert parse_tree_marker not in json.dumps(saved)
    assert b"private-plugin-427" not in store.path.read_bytes()
    assert parse_tree_marker.encode() not in store.path.read_bytes()


def test_candidate_storage_rejects_a_serialized_plugin_payload_without_erasing_the_prior_result(tmp_path):
    store = TaskStore(tmp_path / "tasks")
    recovery = store.recovery(**_OWNER)
    recovery.create(_task_record())
    recovery.save_results(_RUN_ID, [_candidate()])
    previous = recovery.load_results(_RUN_ID)
    forged = {
        **_candidate("Another result"),
        "report_details": {"extra": {"input_snapshot": {"opaque": "PRIVATE-PLUGIN-PAYLOAD-813"}}},
        "attempt": 2,
    }

    with pytest.raises(ValueError):
        recovery.save_results(_RUN_ID, [forged])

    assert recovery.load_results(_RUN_ID) == previous
    assert b"PRIVATE-PLUGIN-PAYLOAD-813" not in store.path.read_bytes()


def _report(run_id, candidate):
    return {
        "run_id": run_id,
        "project_id": _PROJECT_ID.value,
        "variant_id": _VARIANT_ID.value,
        "state": "completed",
        "created_at": _TIME,
        "sources": [
            {
                "key": "source:task-test",
                "label": "Example.esp",
                "snapshot": {"entries": [{"entry_key": _KEY.to_dict(), **candidate}]},
            }
        ],
    }


def test_report_resolves_immutable_candidate_generation_without_storing_another_copy(tmp_path):
    store = TaskStore(tmp_path / "tasks")
    recovery = store.recovery(**_OWNER)
    history = store.history(**_OWNER)
    old_text, new_text = "Unique candidate text 941", "Replacement candidate text 171"
    recovery.create(_task_record())
    recovery.save_results(_RUN_ID, [_candidate(old_text)])
    history.save(_report(_RUN_ID, {"candidate": old_text}))
    assert store.path.read_bytes().count(old_text.encode()) == 1

    recovery.save_results(_RUN_ID, [{**_candidate(new_text), "attempt": 2}])
    recovery.save_results(_RUN_ID, [{**_candidate(old_text), "candidate_revision": 1}])
    (latest,) = recovery.load_results(_RUN_ID)["rows"]

    assert (latest["candidate_revision"], latest["text"]) == (2, new_text)
    saved_report = TaskStore(store.root).history(**_OWNER).load(_RUN_ID)
    assert saved_report["sources"][0]["snapshot"]["entries"][0]["candidate"] == old_text


def test_scoped_report_rejects_caller_supplied_candidate_reference_to_another_owner(tmp_path):
    store = TaskStore(tmp_path / "tasks")
    store.recovery(**_OWNER).create(_task_record())
    store.recovery(**_OWNER).save_results(_RUN_ID, [_candidate("Other owner's candidate")])
    unrelated = store.history(owner_id="owner-b", session_id="session-b")

    with pytest.raises(ValueError):
        unrelated.save(_report("unrelated", {"result_reference": [_RUN_ID, 1, ["candidate"]]}))

    assert not unrelated.contains("unrelated")


def test_deleting_one_run_preserves_reports_that_refer_to_its_candidate(tmp_path):
    store = TaskStore(tmp_path / "tasks")
    recovery = store.recovery(**_OWNER)
    history = store.history(**_OWNER)
    recovery.create(_task_record(state="completed"))
    recovery.save_results(_RUN_ID, [_candidate("Referenced result")])
    report = _report("continued-run-report", {"candidate": "Referenced result"})
    history.save(report, result_run_id=_RUN_ID)
    assert history.load("continued-run-report") == report

    try:
        recovery.delete(_RUN_ID, discard_unmerged=True)
    except TaskStoreError as error:
        assert "report" in str(error).lower()
        assert recovery.load_results(_RUN_ID)["rows"][0]["text"] == "Referenced result"
    else:
        assert recovery.list_summaries() == ()
    assert history.load("continued-run-report") == report


def test_report_metadata_update_rejects_an_older_generation(tmp_path):
    store = TaskStore(tmp_path / "tasks")
    first, second = store.history(**_OWNER), store.history(**_OWNER)
    report_revision = first.save(_report(_RUN_ID, {"candidate": "Completed text"}))
    first.update_metadata(_RUN_ID, expected_record_file=report_revision, state="saved")

    with pytest.raises(TaskStoreError, match="generation"):
        second.update_metadata(_RUN_ID, expected_record_file=report_revision, state="failed")

    assert first.load(_RUN_ID)["state"] == "saved"
