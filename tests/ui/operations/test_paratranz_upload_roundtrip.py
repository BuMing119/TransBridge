"""Exercise download/edit/upload through real V2 commands and the typed service."""

from dataclasses import replace
from datetime import UTC, datetime
from itertools import count
import json
from types import SimpleNamespace

import pytest

from tests.conftest import make_test_collection
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io.identity import EntryKey
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError, ParaTranzEntry
from transbridge.application.tasks import CallbackThreadBackend, OwnerRef, TaskRuntime
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
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


class MemoryStrings:
    """Fake only HTTP endpoints, retaining the production service's wire payload."""

    def __init__(self):
        self.entries = {}
        self.calls = []
        self.fail_ids = set()
        self.ids = count(100)
        self.after_write = lambda: None

    def list_strings(self, project_id, **_kwargs):
        assert project_id == 42
        return [dict(entry.to_remote_payload(), id=entry.remote_id) for entry in self.entries.values()]

    def update_string(self, project_id, remote_id, payload, **_kwargs):
        assert project_id == 42
        self.calls.append(("update", remote_id, dict(payload)))
        if remote_id in self.fail_ids:
            raise ExternalServiceError(ExternalServiceCategory.UNAVAILABLE, "Controlled outage", status=503)
        existing = self.entries[remote_id]
        updated = replace(existing, **payload)
        self.entries[remote_id] = updated
        self.after_write()
        return dict(updated.to_remote_payload(), id=updated.remote_id)

    def create_string(self, project_id, payload, **_kwargs):
        assert project_id == 42
        remote_id = next(self.ids)
        self.calls.append(("create", remote_id, dict(payload)))
        assert all(entry.key != payload["key"] for entry in self.entries.values())
        created = ParaTranzEntry.from_mapping(dict(payload, id=remote_id))
        self.entries[remote_id] = created
        self.after_write()
        return dict(created.to_remote_payload(), id=remote_id)

    def close(self):
        pass


class RoundtripCase:
    def __init__(self, root, monkeypatch, *, size=2, qualified=False):
        self.root = root
        self.sequence = count()
        self.services = build_persistence_v2_services(
            root,
            id_factory=lambda: f"roundtrip-{next(self.sequence)}",
            timestamp_factory=lambda: "2026-10-20T14:00:00+08:00",
        )
        sources = tuple(make_test_collection(size))
        self.sources = tuple(
            replace(entry, entry_key=EntryKey(entry.identity.namespace, entry.key, entry.original))
            if qualified
            else entry
            for entry in sources
        )
        self.project = ProjectRef(ProjectId("project"))
        self.variant = VariantRef(VariantId("variant"), self.project.identity)
        fingerprint = SourceFingerprint(self.sources[0].identity.namespace, "a" * 64)
        states = tuple(VariantEntryState(entry.identity, entry.translation, entry.stage) for entry in self.sources)
        project = ProjectDto(
            SchemaEnvelope(
                2,
                self.project.kind,
                self.project.identity.value,
                0,
                {
                    "name": "Roundtrip",
                    "sources": [{"type": "esp", "path": "source.esp"}],
                    "variant_ids": [self.variant.identity.value],
                    "active_variant_id": self.variant.identity.value,
                },
            )
        )
        self.services.projects.save(self.project, project)
        self.services.variants.save(self.variant, VariantSnapshot(self.variant, (fingerprint,), states).to_dto())
        self.services.baselines.register(self.project, self.variant, (SourceBaseline(fingerprint, states),))
        self.request_context = RequestContext("gui", run_id="roundtrip")
        assert self.services.gui_project_commands.switch_v2(self.project, self.variant, self.request_context).is_success
        self.context = SimpleNamespace(
            collection=TranslationEntryCollection(self.sources),
            uses_authoritative_projection=True,
            project_commands=self.services.gui_project_commands,
            runtime_context=self.request_context,
            active_version_identity=("project", "variant"),
            active_project_id="project",
            project_revision=0,
            variant_revision=0,
            project_name="Roundtrip",
            active_variant="Main",
            paratranz_binding={
                "project_id": 42,
                "project_name": "Remote",
                "endpoint": "https://paratranz.cn",
                "account_user_id": 5,
            },
            config=SimpleNamespace(token="configured", base_url="https://paratranz.cn", user_id=5, config_revision=1),
            current_user={"id": 5},
        )
        self.strings = MemoryStrings()
        projects = SimpleNamespace(list_projects=lambda **_kwargs: [{"id": 42, "name": "Remote"}], close=lambda: None)
        unused_endpoint = SimpleNamespace(close=lambda: None)
        service = ParaTranzService(projects, self.strings, unused_endpoint, unused_endpoint)
        monkeypatch.setattr(ParaTranzService, "from_config", classmethod(lambda _cls, _config: service))
        self.receipts = {}
        self.features = {
            feature.kind: feature
            for feature in build_paratranz_sync_features(
                SimpleNamespace(), on_result=lambda run, result: self.receipts.update({run: result})
            )
        }
        self.queue = []
        self.tasks = TaskRuntime(
            id_generator=SimpleNamespace(new_id=lambda: f"roundtrip-run-{next(self.sequence)}"),
            clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
            backend=CallbackThreadBackend(lambda _run, callback: self.queue.append(callback)),
        )
        self.owner = OwnerRef("gui", "gui.operation-plan")
        self.drafts = []

    def refresh(self):
        """Mirror the GUI's authoritative Variant projection after each command."""
        active = self.services.project_lifecycle.active
        snapshot = active.variant.snapshot()
        self.context.project_revision = active.project.envelope.revision
        self.context.variant_revision = snapshot.revision
        states = {entry.entry_key: entry for entry in snapshot.entries}
        self.context.collection = TranslationEntryCollection(
            replace(
                source,
                translation=states[source.identity].translation,
                stage=states[source.identity].stage.value,
                revision=states[source.identity].revision,
                external_refs=states[source.identity].external_refs,
            )
            for source in self.sources
        )

    def check(self, operation=OperationKind.UPLOAD, choices=None):
        self.refresh()
        feature = self.features[operation]
        field = "upload_decisions" if operation is OperationKind.UPLOAD else "download_decisions"
        draft = feature.create_draft(self.context, False, {field: json.dumps(choices or {})})
        self.drafts.append((feature, draft))
        return feature, draft, feature.mapper.preflight(draft)

    def execute(self, checked):
        feature, draft, preflight = checked
        assert preflight.ready, preflight.checks
        ref = feature.submit(draft, preflight, self.owner, self.tasks)
        self.queue.pop(0)()
        self.refresh()
        return self.receipts[ref.run_id]

    def edit(self, source, translation, stage=3):
        result = self.services.gui_project_commands.update_entry(
            source.identity, self.request_context, translation=translation, stage=stage
        )
        assert result.is_success
        self.refresh()

    def persisted_states(self):
        assert self.services.gui_project_commands.save(self.request_context).is_success
        reopened = build_persistence_v2_services(
            self.root,
            id_factory=lambda: f"reopen-{next(self.sequence)}",
            timestamp_factory=lambda: "2026-10-20T14:01:00+08:00",
        )
        try:
            saved = VariantSnapshot.from_dto(reopened.variants.load(self.variant).value, self.variant)
            return {entry.entry_key: entry for entry in saved.entries}
        finally:
            reopened.close()

    def close(self):
        for feature, draft in self.drafts:
            feature.discard_draft(draft)
        self.services.close()


@pytest.fixture
def roundtrip_case(tmp_path, monkeypatch):
    cases = []

    def create(**kwargs):
        case = RoundtripCase(tmp_path / f"data-{len(cases)}", monkeypatch, **kwargs)
        cases.append(case)
        return case

    yield create
    for case in cases:
        case.close()


@pytest.mark.parametrize("qualified", [False, True], ids=["legacy-key", "original-qualified"])
@pytest.mark.parametrize("choose_download", [False, True], ids=["auto-download", "chosen-download"])
def test_download_edit_upload_uses_saved_id_after_remote_rename_and_survives_reopen(
    roundtrip_case, qualified, choose_download
):
    case = roundtrip_case(size=1, qualified=qualified)
    source = case.sources[0]
    case.strings.entries[7] = ParaTranzEntry(7, source.key, source.original, "下载译文", "远端上下文", 1)
    if choose_download:
        case.strings.entries[8] = ParaTranzEntry(8, source.key, source.original, "未选中的译文", "另一上下文", 1)
        row = case.check(OperationKind.DOWNLOAD)[2].sync_review[0]
        chosen = next(candidate for candidate in row.candidates if candidate.translation == "下载译文")
        download = case.check(OperationKind.DOWNLOAD, {row.row_id: chosen.candidate_id})
    else:
        download = case.check(OperationKind.DOWNLOAD)
    assert case.execute(download).is_success
    assert case.context.collection.get(source.identity).external_refs[0].opaque_id == 7
    case.edit(source, "本地修改后的译文")
    renamed = replace(case.strings.entries[7], key="renamed-in-cloud", original="云端更正的原文", context="新上下文")
    case.strings.entries[7] = renamed
    checked = case.check()
    assert checked[2].ready and not checked[2].sync_review
    receipt = case.execute(checked)
    assert receipt.is_success and receipt.counts.failed == 0
    remote = case.strings.entries[7]
    assert (remote.translation, remote.stage) == ("本地修改后的译文", 3)
    assert (remote.key, remote.original, remote.context) == (renamed.key, renamed.original, renamed.context)
    if choose_download:
        assert case.strings.entries[8].translation == "未选中的译文"
    assert case.strings.calls == [("update", 7, {"translation": "本地修改后的译文", "stage": 3})]
    local = case.context.collection.get(source.identity)
    assert (local.original, local.context, local.identity) == (source.original, source.context, source.identity)
    saved = case.persisted_states()[source.identity]
    assert (saved.translation, saved.stage.value, saved.external_refs[0].opaque_id) == ("本地修改后的译文", 3, 7)
    assert dict(case.check()[2].estimated_impact)["update_remote"] == 0


def test_safe_upload_then_manual_target_preserves_unselected_cloud_content_and_persists_links(roundtrip_case):
    case = roundtrip_case(qualified=True)
    safe, conflict = case.sources
    case.strings.entries.update({
        7: ParaTranzEntry(7, safe.key, safe.original, "云端旧译文", "首项云端上下文", 1),
        8: ParaTranzEntry(8, conflict.key, conflict.original, "候选甲", "云端甲", 1),
        9: ParaTranzEntry(9, conflict.key, conflict.original, "候选乙", "云端乙", 1),
    })
    case.edit(safe, "首项本地修改")
    case.edit(conflict, "冲突项本地修改")
    checked = case.check()
    assert checked[2].ready and len(checked[2].sync_review) == 1
    assert dict(checked[2].estimated_impact)["deferred"] == 1
    local_conflict = case.context.collection.get(conflict.identity)
    receipt = case.execute(checked)
    assert receipt.is_success and receipt.value["deferred_count"] == 1
    assert case.strings.entries[7].translation == "首项本地修改"
    assert (case.strings.entries[8].translation, case.strings.entries[9].translation) == ("候选甲", "候选乙")
    assert case.context.collection.get(conflict.identity) == local_conflict
    assert case.context.collection.get(safe.identity).external_refs[0].opaque_id == 7

    pending = case.check()
    row = pending[2].sync_review[0]
    chosen = next(candidate for candidate in row.candidates if candidate.translation == "候选乙")
    checked = case.check(choices={row.row_id: chosen.candidate_id})
    assert case.execute(checked).is_success
    assert case.strings.entries[8].translation == "候选甲"
    assert (case.strings.entries[9].translation, case.strings.entries[9].context) == ("冲突项本地修改", "云端乙")
    assert all(set(payload) == {"translation", "stage"} for _kind, _id, payload in case.strings.calls)
    saved = case.persisted_states()
    assert saved[safe.identity].external_refs[0].opaque_id == 7
    assert saved[conflict.identity].external_refs[0].opaque_id == 9
    unchanged = case.check()[2]
    assert not unchanged.sync_review
    assert dict(unchanged.estimated_impact)["update_remote"] == 0


def test_same_translation_manual_choice_persists_association_and_preserves_cloud_content(roundtrip_case):
    case = roundtrip_case(size=1, qualified=True)
    source = case.sources[0]
    case.edit(source, "相同译文", 1)
    case.strings.entries.update({
        8: ParaTranzEntry(8, source.key, source.original, "相同译文", "云端甲", 1),
        9: ParaTranzEntry(9, source.key, source.original, "相同译文", "云端乙", 1),
    })
    before = dict(case.strings.entries)
    row = case.check()[2].sync_review[0]
    chosen = next(candidate for candidate in row.candidates if "#9" in candidate.label)
    receipt = case.execute(case.check(choices={row.row_id: chosen.candidate_id}))
    assert receipt.is_success
    assert case.strings.entries == before
    assert all(set(payload) == {"translation", "stage"} for _kind, _id, payload in case.strings.calls)
    assert case.persisted_states()[source.identity].external_refs[0].opaque_id == 9
    unchanged = case.check()[2]
    assert not unchanged.sync_review
    assert dict(unchanged.estimated_impact)["update_remote"] == 0


def test_successful_create_keeps_its_id_when_another_remote_update_fails(roundtrip_case):
    case = roundtrip_case()
    created, failed = case.sources
    case.edit(created, "新增译文")
    case.edit(failed, "等待重试")
    case.strings.entries[9] = ParaTranzEntry(9, failed.key, failed.original, "云端原值", "云端上下文", 1)
    case.strings.fail_ids.add(9)
    receipt = case.execute(case.check())
    assert receipt.outcome is OperationOutcome.PARTIAL
    assert receipt.counts.succeeded == receipt.counts.failed == 1
    saved = case.persisted_states()
    assert saved[created.identity].external_refs[0].opaque_id == 100
    assert not saved[failed.identity].external_refs
    assert case.strings.entries[9].translation == "云端原值"
    case.strings.fail_ids.clear()
    assert case.execute(case.check()).is_success
    assert sum(kind == "create" for kind, _id, _payload in case.strings.calls) == 1
    saved = case.persisted_states()
    assert saved[created.identity].external_refs[0].opaque_id == 100
    assert saved[failed.identity].external_refs[0].opaque_id == 9


def test_remote_change_invalidates_upload_choice_without_writing(roundtrip_case):
    case = roundtrip_case(size=1)
    source = case.sources[0]
    case.strings.entries.update({
        8: ParaTranzEntry(8, source.key, source.original, "候选甲", "甲", 1),
        9: ParaTranzEntry(9, source.key, source.original, "候选乙", "乙", 1),
    })
    row = case.check()[2].sync_review[0]
    candidate = row.candidates[0]
    case.strings.entries[8] = replace(case.strings.entries[8], translation="别人在云端修改")
    checked = case.check(choices={row.row_id: candidate.candidate_id})
    assert not checked[2].ready
    assert checked[2].sync_review and all(not row.decision for row in checked[2].sync_review)
    assert case.strings.calls == []
    assert not case.persisted_states()[source.identity].external_refs


@pytest.mark.parametrize("changed_side", ["remote", "local"])
def test_change_after_confirmation_prevents_upload_write(roundtrip_case, changed_side):
    case = roundtrip_case(size=1)
    source = case.sources[0]
    case.edit(source, "准备上传")
    case.strings.entries[7] = ParaTranzEntry(7, source.key, source.original, "旧译文", "云端上下文", 1)
    feature, draft, checked = case.check()
    assert checked.ready
    ref = feature.submit(draft, checked, case.owner, case.tasks)
    if changed_side == "remote":
        case.strings.entries[7] = replace(case.strings.entries[7], translation="确认后云端变更")
    else:
        case.edit(source, "确认后本地变更")
    case.queue.pop(0)()
    assert case.tasks.get(ref, case.owner).state.value == "failed"
    assert case.strings.calls == []
    assert case.strings.entries[7].translation == ("确认后云端变更" if changed_side == "remote" else "旧译文")
    assert not case.persisted_states()[source.identity].external_refs


def test_failed_local_association_commit_keeps_remote_success_and_new_local_edit(roundtrip_case):
    case = roundtrip_case(size=1)
    source = case.sources[0]
    case.edit(source, "本次上传译文")
    case.strings.entries[7] = ParaTranzEntry(7, source.key, source.original, "旧译文", "云端上下文", 1)
    case.strings.after_write = lambda: case.edit(source, "上传期间的本地编辑")
    receipt = case.execute(case.check())
    assert receipt.outcome is OperationOutcome.PARTIAL
    assert receipt.counts.succeeded == 1
    assert receipt.value["association_pending"]
    assert any(item.code == "UPLOAD_ASSOCIATION_COMMIT_FAILED" for item in receipt.diagnostics)
    assert case.strings.entries[7].translation == "本次上传译文"
    saved = case.persisted_states()[source.identity]
    assert saved.translation == "上传期间的本地编辑"
    assert not saved.external_refs


def test_missing_confirmed_remote_id_requires_explicit_reassociation(roundtrip_case):
    case = roundtrip_case(size=1)
    source = case.sources[0]
    case.strings.entries[7] = ParaTranzEntry(7, source.key, source.original, "下载译文", "云端上下文", 1)
    assert case.execute(case.check(OperationKind.DOWNLOAD)).is_success
    case.edit(source, "待上传译文")
    del case.strings.entries[7]
    case.strings.entries[8] = ParaTranzEntry(8, source.key, source.original, "新云端条目", "新的上下文", 1)
    checked = case.check()
    impact = dict(checked[2].estimated_impact)
    assert (impact["deferred"], impact["update_remote"], impact["create_remote"]) == (1, 0, 0)
    assert case.strings.calls == []
    row = checked[2].sync_review[0]
    assert len(row.candidates) == 1
    receipt = case.execute(case.check(choices={row.row_id: row.candidates[0].candidate_id}))
    assert receipt.is_success
    assert case.strings.entries[8].translation == "待上传译文"
    assert case.strings.entries[8].context == "新的上下文"
    assert case.persisted_states()[source.identity].external_refs[0].opaque_id == 8
