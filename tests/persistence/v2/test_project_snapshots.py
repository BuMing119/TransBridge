from dataclasses import replace
import json
from uuid import uuid4

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects import ProjectProvisioningRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.v2 import SourceBaseline, SourceFingerprint, VariantChangeSet, VariantEntryState


def test_snapshot_restores_complete_variant_and_survives_save_and_reopen(tmp_path):
    services = build_persistence_v2_services(tmp_path, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now")
    context = RequestContext("gui", run_id="snapshot")
    commands = services.gui_project_commands
    assert commands.create_project(ProjectProvisioningRequest("快照工程"), context).is_success
    active = services.project_lifecycle.active
    namespace = SourceNamespace("test:source")
    fingerprint = SourceFingerprint(namespace, "a" * 64)
    first = VariantEntryState(EntryKey(namespace, "first"), "原始译文", 3, ("review",))
    second = VariantEntryState(EntryKey(namespace, "second"), "", 0)
    content = VariantChangeSet(
        active.formal_variant_ref,
        active.variant.revision,
        (fingerprint,),
        (first, second),
        (("review", {"name": "已审核", "color": "#fff"}),),
        context.run_id,
    )
    assert services.project_lifecycle.commit_active_variant(
        content, context, expected_project_revision=active.project.envelope.revision
    ).is_success
    assert commands.save_snapshot("修改前", context).is_success
    snapshots = services.project_snapshots.list(context)
    assert len(snapshots) == 1
    frozen = next((tmp_path / "snapshots").glob("*.json")).read_bytes()
    assert commands.replace_entry_states(
        {first.entry_key: ("改写", 1), second.entry_key: ("新增", 1)}, context
    ).is_success
    assert commands.save(context).is_success
    previous_revision = active.variant.revision
    pointer = (tmp_path / "active-project.json").read_bytes()

    result = services.project_snapshots.restore(snapshots[0].identity, context)

    assert result.is_success
    restored = services.project_lifecycle.active.variant.snapshot()
    assert restored.revision > previous_revision
    assert restored.entries[0].translation == "原始译文"
    assert restored.entries[0].labels == ("review",)
    assert restored.entries[0].stage.value == 3
    assert restored.entries[1].translation == ""
    assert restored.to_dto().envelope.data["label_library"]["review"]["name"] == "已审核"
    assert services.project_lifecycle.active.dirty
    assert (tmp_path / "active-project.json").read_bytes() == pointer
    assert next((tmp_path / "snapshots").glob("*.json")).read_bytes() == frozen
    assert commands.save(context).is_success
    assert not services.project_lifecycle.active.dirty
    persisted = services.variants.read_snapshot(active.formal_variant_ref)
    assert persisted.envelope.data["entries"][0]["translation"] == "原始译文"
    assert persisted.envelope.data["label_library"]["review"]["name"] == "已审核"
    restarted = build_persistence_v2_services(tmp_path, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now")
    restarted.baselines.register(
        active.project_ref, active.formal_variant_ref, (SourceBaseline(fingerprint, (first, second)),)
    )
    assert restarted.gui_project_commands.switch_v2(active.project_ref, active.formal_variant_ref, context).is_success
    assert restarted.project_lifecycle.active.variant.snapshot() == restored


def test_snapshot_mismatch_or_corruption_never_changes_current_content(tmp_path):
    services = build_persistence_v2_services(tmp_path, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now")
    context = RequestContext("gui", run_id="snapshot")
    assert services.gui_project_commands.create_project(ProjectProvisioningRequest("工程"), context).is_success
    assert services.gui_project_commands.save_snapshot("空版本", context).is_success
    identity = services.project_snapshots.list(context)[0].identity
    active = services.project_lifecycle.active
    before = active.variant.snapshot()
    wrong_context = replace(context, variant_id="different")
    assert not services.gui_project_commands.save_snapshot("错误版本", wrong_context).is_success
    assert not services.gui_project_commands.save_snapshot(
        "错误工程", replace(context, project_id="different")
    ).is_success
    assert len(services.project_snapshots.list(context)) == 1
    assert not services.project_snapshots.restore(identity, wrong_context).is_success
    assert not services.project_snapshots.restore("../active-project", context).is_success
    path = tmp_path / "snapshots" / f"{identity}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["variant"]["data"]["source_fingerprints"] = [{"namespace": "other", "sha256": "b" * 64}]
    path.write_text(json.dumps(document), encoding="utf-8")
    assert not services.project_snapshots.restore(identity, context).is_success
    assert active.variant.snapshot() == before


def test_unrelated_corrupt_snapshot_does_not_block_current_list_and_is_retained(tmp_path, caplog):
    services = build_persistence_v2_services(tmp_path, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now")
    context = RequestContext("gui", run_id="snapshot")
    assert services.gui_project_commands.create_project(ProjectProvisioningRequest("工程"), context).is_success
    assert services.gui_project_commands.save_snapshot("可用", context).is_success
    corrupt = tmp_path / "snapshots" / ("a" * 64 + ".json")
    corrupt.write_bytes(b"{invalid")
    snapshots = services.project_snapshots.list(context)
    assert [item.name for item in snapshots] == ["可用"]
    assert corrupt.read_bytes() == b"{invalid"
    assert "保留原文件" in caplog.text


def test_snapshot_delete_requires_current_owner_and_removes_only_selected_record(tmp_path):
    services = build_persistence_v2_services(tmp_path, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now")
    context = RequestContext("gui", run_id="snapshot-delete")
    assert services.gui_project_commands.create_project(ProjectProvisioningRequest("工程"), context).is_success
    assert services.gui_project_commands.save_snapshot("保留", context).is_success
    assert services.gui_project_commands.save_snapshot("删除", context).is_success
    snapshots = services.project_snapshots.list(context)
    selected = next(item for item in snapshots if item.name == "删除")
    retained = next(item for item in snapshots if item.name == "保留")

    wrong = services.project_snapshots.delete(selected.identity, replace(context, variant_id="different"))
    assert not wrong.is_success
    assert (tmp_path / "snapshots" / f"{selected.identity}.json").exists()

    result = services.project_snapshots.delete(selected.identity, context)
    assert result.is_success, result.diagnostics
    assert not (tmp_path / "snapshots" / f"{selected.identity}.json").exists()
    assert (tmp_path / "snapshots" / f"{retained.identity}.json").exists()
