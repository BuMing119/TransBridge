"""Production adapters must execute safe work and retain actionable conflicts."""

from dataclasses import replace
from datetime import UTC, datetime
from itertools import count
import json
from types import SimpleNamespace

import pytest

from tests.conftest import make_test_collection
from tests.ui.operations.current_download_support import patch_current_download_gateway
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskRuntime
from transbridge.paratranz.service import ParaTranzService
from transbridge.ui.operations.paratranz_sync import build_paratranz_sync_features
from transbridge.ui.operations.plan_view import OperationKind
from transbridge.ui.operations.sync_review_view import KEEP_LOCAL


@pytest.fixture
def review_case(monkeypatch):
    collection = make_test_collection(3)
    first, conflict, untouched = collection
    remote = [
        ParaTranzEntry(7, first.key, first.original, "已确定的更新", first.context, 1),
        ParaTranzEntry(8, conflict.key, conflict.original, "候选甲", conflict.context, 1),
        ParaTranzEntry(9, conflict.key, conflict.original, "候选乙", conflict.context, 2),
    ]
    context = SimpleNamespace(
        collection=collection,
        esp_path="source.esp",
        uses_authoritative_projection=False,
        active_version_identity=("project", "variant"),
        active_project_id="project",
        project_revision=0,
        variant_revision=0,
        project_name="本地工程",
        active_variant="当前版本",
        paratranz_binding={
            "project_id": 42,
            "project_name": "云端工程",
            "endpoint": "https://paratranz.cn",
            "account_user_id": 5,
        },
        config=SimpleNamespace(token="configured", base_url="https://paratranz.cn", user_id=5, config_revision=1),
        current_user={"id": 5},
    )
    closed = []
    service = SimpleNamespace(
        list_projects=lambda **_kwargs: (SimpleNamespace(project_id=42, name="云端工程"),),
        list_entries=lambda *_args, **_kwargs: tuple(remote),
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(ParaTranzService, "from_config", classmethod(lambda _cls, _config: service))
    gateway = patch_current_download_gateway(monkeypatch, service)
    receipts = {}
    feature = next(
        item
        for item in build_paratranz_sync_features(
            SimpleNamespace(), on_result=lambda run, result: receipts.update({run: result})
        )
        if item.kind is OperationKind.DOWNLOAD
    )
    ids = count()
    queued = []
    tasks = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: f"review-run-{next(ids)}"),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
        backend=CallbackThreadBackend(lambda _run_id, target: queued.append(target)),
    )
    return SimpleNamespace(
        context=context,
        feature=feature,
        remote=remote,
        tasks=tasks,
        queued=queued,
        owner=OwnerRef("gui", "gui.operation-plan"),
        first=first,
        conflict=conflict,
        untouched=untouched,
        receipts=receipts,
        closed=closed,
        gateway=gateway,
    )


def _check(case, choices=None):
    draft = case.feature.create_draft(
        case.context, False, {"download_decisions": json.dumps(choices or {}, ensure_ascii=False)}
    )
    return draft, case.feature.mapper.preflight(draft)


def _execute(case, draft, preflight):
    ref = case.feature.submit(draft, preflight, case.owner, case.tasks)
    case.queued.pop(0)()
    assert case.tasks.get(ref, case.owner).state is JobState.COMPLETED
    return case.receipts[ref.run_id]


def test_partial_download_then_explicit_candidate_updates_only_the_selected_entry(review_case):
    case = review_case
    draft, preflight = _check(case)
    assert preflight.ready
    counts = dict(preflight.estimated_impact)
    assert (counts["update_local"], counts["deferred"], counts["keep_local"]) == (1, 1, 1)
    row = preflight.sync_review[0]
    assert row.key == case.conflict.key
    assert {candidate.translation for candidate in row.candidates} == {"候选甲", "候选乙"}

    receipt = _execute(case, draft, preflight)
    assert receipt.counts.failed == 0
    assert receipt.value["deferred_count"] == 1
    assert case.context.collection.get(case.first.id).translation == "已确定的更新"
    assert case.context.collection.get(case.conflict.id) == case.conflict
    assert case.context.collection.get(case.untouched.id) == case.untouched
    chosen = next(candidate for candidate in row.candidates if candidate.translation == "候选乙")
    draft, preflight = _check(case, {row.row_id: chosen.candidate_id})
    assert preflight.ready
    assert dict(preflight.estimated_impact)["update_local"] == 1
    assert dict(preflight.estimated_impact)["deferred"] == 0
    _execute(case, draft, preflight)
    updated = case.context.collection.get(case.conflict.id)
    assert updated.translation == "候选乙" and not updated.external_refs
    assert case.context.collection.get(case.untouched.id) == case.untouched
    assert len(case.closed) == 2


def test_keep_local_is_a_real_decision_and_changes_confirmation_digest(review_case):
    case = review_case
    draft, preflight = _check(case)
    row = preflight.sync_review[0]
    decided, checked = _check(case, {row.row_id: KEEP_LOCAL})
    assert decided.request_digest != draft.request_digest
    assert checked.ready and dict(checked.estimated_impact)["deferred"] == 0
    assert checked.sync_review[0].decision == KEEP_LOCAL
    _execute(case, decided, checked)
    assert case.context.collection.get(case.conflict.id) == case.conflict
    case.feature.discard_draft(draft)


def test_remote_changes_do_not_discard_reviewed_snapshot_but_next_window_reads_new_content(review_case):
    case = review_case
    original, preview = _check(case)
    row = preview.sync_review[0]
    choices = {row.row_id: row.candidates[0].candidate_id}
    case.remote[1] = replace(case.remote[1], translation="选择后发生变化")
    fields = [
        (field.field_id, field.value) for field in original.editable_fields if field.field_id != "download_decisions"
    ]
    fields.append(("download_decisions", json.dumps(choices)))
    draft = case.feature.edit_draft(original, tuple(fields))
    preflight = case.feature.mapper.preflight(draft)
    assert preflight.ready
    assert preflight.sync_review[0].decision == row.candidates[0].candidate_id
    assert {item.translation for item in preflight.sync_review[0].candidates} == {"候选甲", "候选乙"}
    assert case.gateway.reads == [("inventory", 42), ("translation", 42, 11)]
    fresh, fresh_check = _check(case)
    assert "选择后发生变化" in {item.translation for item in fresh_check.sync_review[0].candidates}
    assert len(case.gateway.reads) == 4
    _execute(case, draft, preflight)
    assert case.context.collection.get(case.conflict.id).translation == "候选甲"
    case.feature.discard_draft(original)
    case.feature.discard_draft(fresh)


def test_switching_local_project_discards_choices_even_with_identical_remote_and_entries(review_case):
    case = review_case
    draft, checked = _check(case)
    row = checked.sync_review[0]
    fields = [(field.field_id, field.value) for field in draft.editable_fields]
    fields = [(key, value) for key, value in fields if key != "download_decisions"]
    fields.append(("download_decisions", json.dumps({row.row_id: KEEP_LOCAL})))
    case.context.active_version_identity = ("another-project", "another-variant")
    case.context.active_project_id = "another-project"
    edited = case.feature.edit_draft(draft, tuple(fields))
    assert edited.request.download_decisions == ()
    assert (
        json.loads(next(field.value for field in edited.editable_fields if field.field_id == "download_decisions"))
        == {}
    )
    case.feature.discard_draft(draft)


def test_review_ignores_historical_candidate_linked_to_another_local_entry(review_case):
    from transbridge.application.io.identity import ExternalEntryRef
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection

    case = review_case
    owner = replace(case.untouched, external_refs=(ExternalEntryRef("paratranz", "project:42", 8),))
    case.context.collection = TranslationEntryCollection([case.first, case.conflict, owner])
    draft, checked = _check(case)
    row = next(item for item in checked.sync_review if item.key == case.conflict.key)
    occupied = next(item for item in row.candidates if "#8" in item.label)
    assert occupied.selectable and not occupied.disabled_reason
    case.feature.discard_draft(draft)


def test_historical_ids_cannot_redirect_this_download_or_persist_as_new_bindings(review_case):
    from transbridge.application.io.identity import ExternalEntryRef
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection

    case = review_case
    stale_first = replace(case.first, external_refs=(ExternalEntryRef("paratranz", "project:42", 8),))
    stale_owner = replace(case.untouched, external_refs=(ExternalEntryRef("paratranz", "project:42", 7),))
    case.context.collection = TranslationEntryCollection([stale_first, case.conflict, stale_owner])

    draft, checked = _check(case)

    assert checked.ready
    assert dict(checked.estimated_impact)["update_local"] == 1
    assert len(checked.sync_review) == 1
    assert all(candidate.selectable for candidate in checked.sync_review[0].candidates)
    _execute(case, draft, checked)
    first = case.context.collection.get(case.first.identity)
    untouched = case.context.collection.get(case.untouched.identity)
    assert first.translation == "已确定的更新" and not first.external_refs
    assert untouched.translation == case.untouched.translation and not untouched.external_refs


@pytest.mark.parametrize("qualified_identity", [False, True])
def test_manual_download_preserves_source_identity_without_authoritative_projection(review_case, qualified_identity):
    from transbridge.application.io.identity import EntryKey
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection

    case = review_case
    qualified = replace(
        case.conflict,
        entry_key=EntryKey(
            case.conflict.identity.namespace,
            case.conflict.key,
            case.conflict.original if qualified_identity else None,
        ),
    )
    case.context.collection = TranslationEntryCollection([case.first, qualified, case.untouched])
    case.remote[1:] = [
        replace(entry, original="另一份来源的原文", context="另一份来源的上下文") for entry in case.remote[1:]
    ]
    old, preview = _check(case)
    row = preview.sync_review[0]
    chosen = next(candidate for candidate in row.candidates if candidate.translation == "候选乙")
    draft, checked = _check(case, {row.row_id: chosen.candidate_id})
    assert checked.ready and dict(checked.estimated_impact)["update_local"] == 2
    result = _execute(case, draft, checked)
    assert result.counts.succeeded == 2 and result.counts.failed == 0
    updated = case.context.collection.get(qualified.id)
    assert updated.identity == qualified.identity
    assert updated.original == qualified.original
    assert updated.context == qualified.context
    assert updated.translation == "候选乙" and not updated.external_refs
    unchanged, unchanged_check = _check(case)
    assert dict(unchanged_check.estimated_impact)["update_local"] == 0
    assert len(unchanged_check.sync_review) == 1
    assert not unchanged_check.sync_review[0].decision
    case.feature.discard_draft(old)
    case.feature.discard_draft(unchanged)
