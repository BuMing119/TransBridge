from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, SourceNamespace
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError, ParaTranzEntry
from transbridge.application.sync import (
    AuthorizedSyncPlan,
    CallbackLocalSyncUnitOfWork,
    ConflictPolicy,
    ExecuteSyncRequest,
    LocalEntrySnapshot,
    ParaTranzSyncExecutor,
    SyncOperation,
    SyncPlanner,
)
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.service import ParaTranzService
from transbridge.paratranz.sync_snapshot import ParaTranzRemoteSnapshotAdapter


class StatefulStrings:
    def __init__(self, size: int, *, allow_list: bool = False) -> None:
        self.allow_list = allow_list
        self.records = {
            remote_id: {
                "id": remote_id,
                "key": f"key-{remote_id}",
                "original": f"original-{remote_id}",
                "translation": f"old-{remote_id}",
                "context": "",
                "stage": 1,
            }
            for remote_id in range(1, size + 1)
        }
        self.pages: list[tuple[int, int]] = []

    def list_strings(self, project_id, *, page, page_size, cancellation=None):
        if not self.allow_list:
            pytest.fail("upsert must not scan remote entries")
        self.pages.append((page, page_size))
        rows = list(self.records.values())
        return {"results": deepcopy(rows[(page - 1) * page_size : page * page_size])}

    def create_string(self, project_id, payload, *, cancellation=None):
        if any(record["key"] == payload["key"] for record in self.records.values()):
            raise ExternalServiceError(ExternalServiceCategory.CONFLICT, "duplicate key", status=409)
        remote_id = max(self.records, default=0) + 1
        self.records[remote_id] = {"id": remote_id, **payload}
        return deepcopy(self.records[remote_id])

    def update_string(self, project_id, remote_id, payload, *, cancellation=None):
        self.records[remote_id].update(payload)
        return deepcopy(self.records[remote_id])


def _service(strings: StatefulStrings) -> ParaTranzService:
    return ParaTranzService(MagicMock(), strings, MagicMock(), MagicMock())


def _entry(remote_id: int, *, known_identity: bool = False) -> ParaTranzEntry:
    return ParaTranzEntry(
        remote_id if known_identity else None,
        f"key-{remote_id}",
        f"original-{remote_id}",
        f"new-{remote_id}",
        "",
        1,
    )


def test_confirmed_identity_updates_directly_without_scanning_remote_entries() -> None:
    strings = StatefulStrings(1601)
    before = deepcopy(strings.records)

    result = _service(strings).upsert_entry(7, _entry(1601, known_identity=True), force_overwrite=True)

    assert result.remote_id == 1601
    before[1601]["translation"] = "new-1601"
    assert strings.records == before
    assert strings.pages == []


@pytest.mark.parametrize("known_identity", [False, True])
def test_non_forced_upsert_reports_server_conflict_without_a_preflight_scan(known_identity: bool) -> None:
    strings = StatefulStrings(801)
    before = deepcopy(strings.records)

    with pytest.raises(ExternalServiceError, match="duplicate key") as captured:
        _service(strings).upsert_entry(7, _entry(801, known_identity=known_identity))

    assert captured.value.category is ExternalServiceCategory.CONFLICT
    assert strings.records == before
    assert strings.pages == []


def test_confirmed_identity_does_not_scan_for_other_entries_with_the_same_key() -> None:
    strings = StatefulStrings(801)
    strings.records[801]["key"] = "key-1"
    before = deepcopy(strings.records)

    result = _service(strings).upsert_entry(7, _entry(1, known_identity=True), force_overwrite=True)

    assert result.remote_id == 1
    before[1]["translation"] = "new-1"
    assert strings.records == before
    assert strings.pages == []


@pytest.mark.parametrize("force", [False, True])
def test_missing_identity_creates_directly_without_scanning(force: bool) -> None:
    strings = StatefulStrings(801)
    before = deepcopy(strings.records)

    result = _service(strings).upsert_entry(7, _entry(802), force_overwrite=force)

    assert result.remote_id == 802
    assert strings.records == {**before, 802: {"id": 802, **_entry(802).to_remote_payload()}}
    assert strings.pages == []


def test_force_without_identity_does_not_resolve_conflicting_key_by_scanning() -> None:
    strings = StatefulStrings(801)
    before = deepcopy(strings.records)

    with pytest.raises(ExternalServiceError, match="duplicate key"):
        _service(strings).upsert_entry(7, _entry(801), force_overwrite=True)

    assert strings.records == before
    assert strings.pages == []


@pytest.mark.parametrize("reference_project", [7, 8])
def test_assistant_upload_reuses_only_the_target_projects_known_identity(monkeypatch, reference_project: int) -> None:
    from transbridge.smart_assistant.tools import tool_paratranz

    strings = StatefulStrings(801)
    before = deepcopy(strings.records)
    service = _service(strings)
    monkeypatch.setattr(tool_paratranz, "_get_paratranz_client", lambda *_args: (service, 7, None))
    entry = TranslationEntry(
        "local",
        "key-801",
        "original-801",
        "new-801",
        1,
        "",
        external_refs=(ExternalEntryRef("paratranz", f"project:{reference_project}", 801),),
    )
    context = SimpleNamespace(collection=TranslationEntryCollection([entry]))

    result = tool_paratranz._tool_upload_entries({"project_id": 7, "force_overwrite": True}, context)

    assert result.success
    if reference_project == 7:
        assert result.data["uploaded"] == 1
        before[801]["translation"] = "new-801"
    else:
        assert result.data["uploaded"] == 0
        assert "duplicate key" in result.data["failed_items"][0]["error"]
    assert strings.records == before
    assert strings.pages == []


def test_bounded_entry_read_keeps_pagination_offsets_stable() -> None:
    strings = StatefulStrings(1601, allow_list=True)

    entries = _service(strings).list_entries(7, limit=801)

    assert [entry.remote_id for entry in entries] == list(range(1, 802))
    assert strings.pages == [(1, 800), (2, 800)]


def test_sync_updates_planned_remote_ids_without_rescanning_per_entry() -> None:
    strings = StatefulStrings(1601, allow_list=True)
    before = deepcopy(strings.records)
    service = _service(strings)
    snapshots = ParaTranzRemoteSnapshotAdapter(service)
    namespace = SourceNamespace("test:upsert")
    changed_ids = (1, 801, 1601)
    local = tuple(
        LocalEntrySnapshot(
            EntryKey(namespace, f"key-{remote_id}"),
            EntryRevision(),
            f"original-{remote_id}",
            f"new-{remote_id}",
            stage=1,
        )
        for remote_id in changed_ids
    )
    plan = SyncPlanner().plan(
        local,
        snapshots.fetch(7, namespace, limit=100_000),
        operation=SyncOperation.UPLOAD,
        conflict_policy=ConflictPolicy.PREFER_LOCAL,
        scope=f"paratranz:project:7:source:{namespace.value}",
    )
    strings.pages.clear()
    executor = ParaTranzSyncExecutor(service, snapshots, CallbackLocalSyncUnitOfWork(lambda: local, lambda _: None))

    result = executor.execute(
        ExecuteSyncRequest(
            AuthorizedSyncPlan(plan, "owner", "CONFIRMED"),
            7,
            namespace,
            local,
            "run",
            ImmediateCommitGuard("run"),
        )
    )

    assert result.is_success
    assert result.counts.succeeded == 3
    for remote_id in changed_ids:
        before[remote_id]["translation"] = f"new-{remote_id}"
    assert strings.records == before
    # The executor still refreshes the complete snapshot once before dispatch.
    assert strings.pages == [(1, 800), (2, 800), (3, 800)]
