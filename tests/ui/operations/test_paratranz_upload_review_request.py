"""Upload review decisions follow the current target and confirmed snapshots."""

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from tests.conftest import make_test_collection
from tests.ui.operations.ordinary_upload_support import patch_ordinary_gateway
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.paratranz.service import ParaTranzService
from transbridge.ui.operations.paratranz_sync import build_paratranz_sync_features
from transbridge.ui.operations.plan_view import OperationKind


@pytest.fixture
def upload_case(monkeypatch):
    collection = make_test_collection(1)
    entry = next(iter(collection))
    remote = [
        ParaTranzEntry(7, entry.key, entry.original, "甲", "上下文甲", 1),
        ParaTranzEntry(8, entry.key, entry.original, "乙", "上下文乙", 2),
    ]
    service = SimpleNamespace(
        list_projects=lambda **_kwargs: (SimpleNamespace(project_id=42, name="云端"),),
        list_entries=lambda *_args, **_kwargs: tuple(remote),
        close=lambda: None,
    )
    monkeypatch.setattr(ParaTranzService, "from_config", classmethod(lambda _cls, _config: service))
    patch_ordinary_gateway(monkeypatch, service)
    context = SimpleNamespace(
        collection=collection,
        esp_path="source.esp",
        active_version_identity=("project", "variant"),
        active_project_id="project",
        project_revision=0,
        variant_revision=0,
        paratranz_binding={
            "project_id": 42,
            "project_name": "云端",
            "endpoint": "https://paratranz.cn",
            "account_user_id": 5,
        },
        config=SimpleNamespace(token="configured", base_url="https://paratranz.cn", user_id=5, config_revision=1),
        current_user={"id": 5},
    )
    feature = next(
        feature for feature in build_paratranz_sync_features(SimpleNamespace()) if feature.kind is OperationKind.UPLOAD
    )
    return SimpleNamespace(context=context, remote=remote, feature=feature)


def test_upload_review_choice_changes_confirmation_and_only_has_upload_decision_field(upload_case):
    case = upload_case
    draft = case.feature.create_draft(case.context, False, {})
    check = case.feature.mapper.preflight(draft)
    row = check.sync_review[0]
    assert check.ready and dict(check.estimated_impact)["deferred"] == 1
    chosen = case.feature.edit_draft(
        draft, (("upload_decisions", json.dumps({row.row_id: row.candidates[1].candidate_id})),)
    )
    checked = case.feature.mapper.preflight(chosen)
    assert chosen.request_digest != draft.request_digest
    assert checked.ready and dict(checked.estimated_impact)["update_remote"] == 1
    assert checked.sync_review[0].decision == row.candidates[1].candidate_id
    fields = {field.field_id for field in chosen.editable_fields}
    assert "upload_decisions" in fields and "download_decisions" not in fields
    case.feature.discard_draft(draft)
    case.feature.discard_draft(chosen)


def test_upload_review_old_choice_is_cleared_after_remote_changes(upload_case):
    case = upload_case
    draft = case.feature.create_draft(case.context, False, {})
    row = case.feature.mapper.preflight(draft).sync_review[0]
    case.remote[1] = replace(case.remote[1], translation="候选已变更")
    edited = case.feature.edit_draft(
        draft, (("upload_decisions", json.dumps({row.row_id: row.candidates[1].candidate_id})),)
    )
    check = case.feature.mapper.preflight(edited)
    assert not check.ready
    assert any("选择已清除" in item.reason for item in check.checks)
    assert check.sync_review and all(not item.decision for item in check.sync_review)
    case.feature.discard_draft(draft)
    case.feature.discard_draft(edited)


def test_upload_choices_do_not_cross_local_project_boundaries(upload_case):
    case = upload_case
    draft = case.feature.create_draft(case.context, False, {})
    row = case.feature.mapper.preflight(draft).sync_review[0]
    case.context.active_version_identity = ("other-project", "variant")
    case.context.active_project_id = "other-project"
    edited = case.feature.edit_draft(draft, (("upload_decisions", json.dumps({row.row_id: "keep_remote"})),))
    assert edited.request.upload_decisions == ()
    assert next(field.value for field in edited.editable_fields if field.field_id == "upload_decisions") == "{}"
    case.feature.discard_draft(draft)
