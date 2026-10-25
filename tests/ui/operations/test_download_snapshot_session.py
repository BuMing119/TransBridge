"""Download review commits exactly the content read once for this window."""

from dataclasses import replace
from datetime import UTC, datetime
import json
from types import SimpleNamespace

import pytest

from tests.conftest import make_test_collection
from transbridge.application.io.identity import ExternalEntryRef
from transbridge.application.io.publish.guards import TaskRuntimeCommitGuard
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.sync.reviewed_snapshot import ReviewedDownloadSnapshot
from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskRuntime
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz import path_snapshot
from transbridge.paratranz.service import ParaTranzService
from transbridge.ui.operations.paratranz_sync import build_paratranz_sync_features


@pytest.fixture
def session_case(monkeypatch):
    first, conflict = make_test_collection(2)
    first = replace(
        first,
        external_refs=(
            ExternalEntryRef("paratranz", "project:42", 901),
            ExternalEntryRef("paratranz", "project:42", 902),
            ExternalEntryRef("other", "another", "keep"),
        ),
    )
    context = SimpleNamespace(
        collection=TranslationEntryCollection([first, conflict]),
        esp_path="source.esp",
        paratranz_binding={
            "project_id": 42,
            "project_name": "Cloud",
            "endpoint": "https://paratranz.cn",
            "account_user_id": 5,
        },
        config=SimpleNamespace(token="test", base_url="https://paratranz.cn", user_id=5, config_revision=1),
        current_user={"id": 5},
        project_revision=1,
        variant_revision=2,
        active_project_id="project",
        active_version_identity=("project", "variant"),
    )
    remote = [
        ParaTranzEntry(7, first.key, first.original, "Cloud first", first.context, 1),
        ParaTranzEntry(8, conflict.key, conflict.original, "Choice A", conflict.context, 1),
        ParaTranzEntry(9, conflict.key, conflict.original, "Choice B", conflict.context, 2),
    ]
    calls = []

    def list_projects(**_kwargs):
        calls.append("membership")
        return (SimpleNamespace(project_id=42, name="Cloud"),)

    def forbidden(*_args, **_kwargs):
        pytest.fail("download must never read project strings or write cloud data")

    service = SimpleNamespace(
        list_projects=list_projects,
        list_entries=forbidden,
        update_entry=forbidden,
        create_entry=forbidden,
        delete_entry=forbidden,
        close=lambda: None,
    )

    class Gateway:
        def inventory(self, project_id):
            assert project_id == 42
            calls.append("inventory")
            return {"source.esp.json": 11, "unrelated.json": 12, "source.esp/任务xx冲突xxx.json": 13}

        def get_file_translation(self, _project, file_id):
            assert file_id == 11
            calls.append("file")
            return [dict(item.to_remote_payload(), id=item.remote_id, fileId=11) for item in remote]

        def close(self):
            pass

    monkeypatch.setattr(ParaTranzService, "from_config", classmethod(lambda _cls, _config: service))
    monkeypatch.setattr(path_snapshot, "FilePublicationGateway", lambda _config: Gateway())
    feature = build_paratranz_sync_features(SimpleNamespace())[1]
    queued = []
    tasks = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: "session-run"),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
        backend=CallbackThreadBackend(lambda _run, target: queued.append(target)),
    )
    return SimpleNamespace(
        context=context,
        first=first,
        conflict=conflict,
        remote=remote,
        calls=calls,
        feature=feature,
        queued=queued,
        tasks=tasks,
        owner=OwnerRef("gui", "gui.operation-plan"),
    )


def _edit(case, draft, **changes):
    fields = {field.field_id: field.value for field in draft.editable_fields}
    fields.update(changes)
    edited = case.feature.edit_draft(draft, tuple(fields.items()))
    case.feature.discard_draft(draft)
    return edited


def _execute(case, draft, checked):
    ref = case.feature.submit(draft, checked, case.owner, case.tasks)
    case.queued.pop(0)()
    return case.tasks.get(ref, case.owner)


def test_choices_and_policy_reuse_one_read_through_authorization_and_atomic_commit(session_case):
    case = session_case
    draft = case.feature.create_draft(case.context, False, {})
    assert draft.input_ready  # Duplicate historical IDs do not block the snapshot.
    checked = case.feature.mapper.preflight(draft)
    assert checked.ready and len(checked.sync_review) == 1
    row = checked.sync_review[0]
    chosen = next(item for item in row.candidates if item.translation == "Choice B")
    assert case.calls == ["membership", "inventory", "file"]
    case.remote[0] = replace(case.remote[0], translation="Changed after review")
    case.remote[2] = replace(case.remote[2], translation="Changed B after review")

    edited = _edit(
        case,
        draft,
        download_decisions=json.dumps({row.row_id: chosen.candidate_id}),
        conflict_policy="prefer_local",
    )
    assert edited.request.download_session is draft.request.download_session
    checked = case.feature.mapper.preflight(edited)
    assert checked.ready
    edited = _edit(case, edited, conflict_policy="prefer_remote")
    checked = case.feature.mapper.preflight(edited)
    assert _execute(case, edited, checked).state is JobState.COMPLETED
    assert case.calls == ["membership", "inventory", "file"]
    updated = case.context.collection.get(case.first.identity)
    selected = case.context.collection.get(case.conflict.identity)
    assert (updated.translation, selected.translation) == ("Cloud first", "Choice B")
    assert updated.external_refs == (ExternalEntryRef("other", "another", "keep"),)
    assert selected.external_refs == ()
    assert (updated.original, updated.context, updated.metadata) == (
        case.first.original,
        case.first.context,
        case.first.metadata,
    )

    # A new window has fresh data, including same-path replacement contents.
    fresh = case.feature.create_draft(case.context, False, {})
    fresh_check = case.feature.mapper.preflight(fresh)
    assert fresh.request.download_session is not edited.request.download_session
    assert case.calls == ["membership", "inventory", "file"] * 2
    assert {item.translation for item in fresh_check.sync_review[0].candidates} == {
        "Choice A",
        "Changed B after review",
    }
    case.feature.discard_draft(fresh)


@pytest.mark.parametrize("change", ["translation", "project", "variant", "account", "config", "path"])
def test_mutable_local_or_target_changes_cannot_commit_reviewed_content(session_case, change):
    case = session_case
    draft = case.feature.create_draft(case.context, False, {})
    checked = case.feature.mapper.preflight(draft)
    if change == "translation":
        local = replace(case.first, translation="Edited while reviewing", revision=case.first.revision.next())
        case.context.collection = TranslationEntryCollection([local, case.conflict])
    elif change == "project":
        case.context.project_revision += 1
    elif change == "variant":
        case.context.active_version_identity = ("project", "another")
    elif change == "account":
        case.context.current_user = {"id": 6}
    elif change == "config":
        case.context.config.config_revision += 1
    else:
        case.context.esp_path = "renamed.esp"
    before = tuple(case.context.collection)
    assert _execute(case, draft, checked).state is JobState.FAILED
    assert tuple(case.context.collection) == before
    assert case.calls == ["membership", "inventory", "file"]


def test_source_change_during_edit_starts_a_new_snapshot(session_case):
    case = session_case
    draft = case.feature.create_draft(case.context, False, {})
    case.feature.mapper.preflight(draft)
    local = replace(case.first, translation="Edited", revision=case.first.revision.next())
    case.context.collection = TranslationEntryCollection([local, case.conflict])
    edited = _edit(case, draft)
    assert edited.request.download_session is not draft.request.download_session
    assert case.feature.mapper.preflight(edited).ready
    assert case.calls == ["membership", "inventory", "file"] * 2
    case.feature.discard_draft(edited)


@pytest.mark.parametrize("change", ["account", "config", "path"])
def test_target_change_between_authorization_and_commit_keeps_local_content(session_case, monkeypatch, change):
    case = session_case
    draft = case.feature.create_draft(case.context, False, {})
    checked = case.feature.mapper.preflight(draft)
    original_commit = TaskRuntimeCommitGuard.commit

    def change_before_publish(guard, run_id, mutation):
        if change == "account":
            case.context.current_user = {"id": 6}
        elif change == "config":
            case.context.config.config_revision += 1
        else:
            case.context.esp_path = "renamed.esp"
        return original_commit(guard, run_id, mutation)

    monkeypatch.setattr(TaskRuntimeCommitGuard, "commit", change_before_publish)
    before = tuple(case.context.collection)
    assert _execute(case, draft, checked).state is JobState.FAILED
    assert tuple(case.context.collection) == before
    assert case.calls == ["membership", "inventory", "file"]


def test_failed_file_read_does_not_cache_a_partial_download(session_case, monkeypatch):
    case = session_case
    original = path_snapshot.FilePublicationGateway
    attempts = []

    def gateway(config):
        result = original(config)
        if not attempts:

            def failed(*_args):
                attempts.append("failed")
                raise OSError("interrupted complete file read")

            result.get_file_translation = failed
        return result

    monkeypatch.setattr(path_snapshot, "FilePublicationGateway", gateway)
    draft = case.feature.create_draft(case.context, False, {})
    assert not case.feature.mapper.preflight(draft).ready
    assert case.feature.mapper.preflight(draft).ready
    assert case.calls == ["membership", "inventory", "membership", "inventory", "file"]
    case.feature.discard_draft(draft)


def test_reviewed_snapshot_rejects_cross_scope_limit_and_cancellation(session_case):
    case = session_case
    draft = case.feature.create_draft(case.context, False, {})
    case.feature.mapper.preflight(draft)
    snapshot, _ = draft.request.download_session.load(SimpleNamespace(), draft.request)
    assert isinstance(snapshot, ReviewedDownloadSnapshot)
    with pytest.raises(ValueError, match="another target"):
        snapshot.fetch(999, draft.request.namespace, limit=100_000)
    with pytest.raises(ValueError, match="limit"):
        snapshot.fetch(42, draft.request.namespace, limit=1)

    class Cancelled:
        def raise_if_cancelled(self):
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        snapshot.fetch(42, draft.request.namespace, limit=100_000, cancellation=Cancelled())
    case.feature.discard_draft(draft)


@pytest.mark.parametrize("size", [1, 1_000, 10_000])
def test_complete_download_request_count_depends_on_files_not_entries(session_case, size):
    case = session_case
    entries = tuple(make_test_collection(size))
    case.context.collection = TranslationEntryCollection(entries)
    case.remote[:] = [
        ParaTranzEntry(index + 1, entry.key, entry.original, f"Downloaded {index}", entry.context, 1)
        for index, entry in enumerate(entries)
    ]
    draft = case.feature.create_draft(case.context, False, {})
    checked = case.feature.mapper.preflight(draft)
    assert checked.ready and dict(checked.estimated_impact)["update_local"] == size
    edited = _edit(case, draft, conflict_policy="prefer_local")
    assert case.feature.mapper.preflight(edited).ready
    edited = _edit(case, edited, conflict_policy="prefer_remote")
    checked = case.feature.mapper.preflight(edited)
    assert _execute(case, edited, checked).state is JobState.COMPLETED
    assert case.calls == ["membership", "inventory", "file"]
    assert all(
        (entry.translation, entry.external_refs) == (f"Downloaded {index}", ())
        for index, entry in enumerate(case.context.collection)
    )
