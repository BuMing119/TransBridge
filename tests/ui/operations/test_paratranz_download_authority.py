from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from types import SimpleNamespace

import pytest

from tests.conftest import make_test_collection
from transbridge.application.contracts import RequestContext
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskRuntime
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.paratranz.service import ParaTranzService
from transbridge.persistence.v2 import (
    ProjectDto,
    ProjectId,
    ProjectRef,
    SchemaEnvelope,
    SourceBaseline,
    SourceFingerprint,
    VariantEntryState,
    VariantId,
    VariantRef,
    VariantSnapshot,
)
from transbridge.ui.operations.paratranz_sync import build_paratranz_sync_features
from transbridge.ui.operations.plan_view import OperationKind


@pytest.mark.parametrize("remote_only", [False, True], ids=["source-text-differs", "extra-remote-entries"])
def test_v2_download_updates_matching_translations_and_survives_reopen(tmp_path, monkeypatch, remote_only):
    sequence = count()
    root = tmp_path / "data"
    services = build_persistence_v2_services(
        root,
        id_factory=lambda: f"sync-{next(sequence)}",
        timestamp_factory=lambda: "2026-08-31T14:00:00+08:00",
    )
    collection = make_test_collection(2)
    first, second = collection
    project_ref = ProjectRef(ProjectId("project"))
    variant_ref = VariantRef(VariantId("variant"), project_ref.identity)
    fingerprint = SourceFingerprint(first.identity.namespace, "a" * 64)
    entries = tuple(VariantEntryState(entry.identity, entry.translation, entry.stage) for entry in collection)
    project = ProjectDto(
        SchemaEnvelope(
            2,
            project_ref.kind,
            project_ref.identity.value,
            0,
            {
                "name": "Local plugin",
                "sources": [{"type": "esp", "path": "source.esp"}],
                "variant_ids": [variant_ref.identity.value],
                "active_variant_id": variant_ref.identity.value,
            },
        )
    )
    services.projects.save(project_ref, project)
    services.variants.save(variant_ref, VariantSnapshot(variant_ref, (fingerprint,), entries).to_dto())
    services.baselines.register(project_ref, variant_ref, (SourceBaseline(fingerprint, entries),))
    request_context = RequestContext("gui", run_id="download")
    assert services.gui_project_commands.switch_v2(project_ref, variant_ref, request_context).is_success
    context = SimpleNamespace(
        collection=collection,
        uses_authoritative_projection=True,
        project_commands=services.gui_project_commands,
        runtime_context=request_context,
        active_version_identity=("project", "variant"),
        active_project_id="project",
        project_revision=0,
        variant_revision=0,
        project_name="Local plugin",
        active_variant="Main",
        paratranz_binding={
            "project_id": 42,
            "project_name": "Remote translations",
            "endpoint": "https://paratranz.cn",
            "account_user_id": 5,
        },
        config=SimpleNamespace(token="configured", base_url="https://paratranz.cn", user_id=5, config_revision=1),
        current_user={"id": 5},
    )
    remote = [ParaTranzEntry(7, first.key, "Remote original differs", "云端译文", "Remote context", 1)]
    if remote_only:
        remote.append(ParaTranzEntry(8, "not-in-this-plugin", "Another plugin", "其他插件译文", "", 1))
    reads = []

    def list_entries(project_id, **_kwargs):
        reads.append(project_id)
        return tuple(remote)

    service = SimpleNamespace(
        list_projects=lambda **_kwargs: (SimpleNamespace(project_id=42, name="Remote translations"),),
        list_entries=list_entries,
        close=lambda: None,
    )
    monkeypatch.setattr(ParaTranzService, "from_config", classmethod(lambda _cls, _config: service))
    feature = next(
        item for item in build_paratranz_sync_features(SimpleNamespace()) if item.kind is OperationKind.DOWNLOAD
    )
    queued = []
    tasks = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: "download-run"),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
        backend=CallbackThreadBackend(lambda _run_id, target: queued.append(target)),
    )
    owner = OwnerRef("gui", "gui.operation-plan")
    try:
        draft = feature.create_draft(context, False, {})
        preflight = feature.mapper.preflight(draft)
        assert preflight.ready
        assert any("云端独有条目跳过" in check.reason for check in preflight.checks)
        fields = {field.field_id: field for field in draft.editable_fields}
        assert not fields["apply_remote_deletions"].enabled
        assert all("新增" not in label for _value, label in fields["conflict_policy"].options)
        ref = feature.submit(draft, preflight, owner, tasks)
        queued[0]()

        assert tasks.get(ref, owner).state is JobState.COMPLETED
        impact = dict(preflight.estimated_impact)
        assert impact["update_local"] == 1
        assert impact["create_local"] == impact["delete_local"] == 0
        assert impact["skip"] == 1 + int(remote_only)
        assert {entry.identity for entry in context.collection} == {first.identity, second.identity}
        downloaded = context.collection.get(first.id)
        assert (downloaded.translation, downloaded.stage) == ("云端译文", 1)
        assert (downloaded.original, downloaded.context) == (first.original, first.context)
        assert context.collection.get(second.id).translation == second.translation
        assert reads == [42, 42, 42]  # Existing preflight/authorization/execution reads only.
        assert services.gui_project_commands.save(request_context).is_success
    finally:
        services.close()

    reopened = build_persistence_v2_services(
        root,
        id_factory=lambda: f"reopen-{next(sequence)}",
        timestamp_factory=lambda: "2026-08-31T14:01:00+08:00",
    )
    try:
        saved = VariantSnapshot.from_dto(reopened.variants.load(variant_ref).value, variant_ref)
        states = {entry.entry_key: entry for entry in saved.entries}
        assert set(states) == {first.identity, second.identity}
        assert states[first.identity].translation == "云端译文"
        assert states[first.identity].external_refs[0].opaque_id == 7
        assert states[second.identity].translation == second.translation
    finally:
        reopened.close()
