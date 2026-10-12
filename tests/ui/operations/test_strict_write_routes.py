from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
import pytest

from tests.plugin_fixtures import write_plugin
from tests.ui.operations.test_production_facade import _Clock, _Ids
from transbridge.application.contracts import (
    Diagnostic,
    DomainError,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter
from transbridge.application.io.operation_write import (
    HydratedWriteDraft,
    HydratedWritePreflightService,
    HydratedWriteWorkload,
)
from transbridge.application.io.plugin_transfer import PluginTransferDraft, PluginTransferPreflightService
from transbridge.application.io.publish import ConflictPolicy
from transbridge.application.tasks import CallbackThreadBackend, OwnerRef, TaskRuntime
from transbridge.application.tasks.activity import activity_from_snapshot
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.operations.runtime_adapter import OperationTaskAdapter
from transbridge.ui.operations.write_batch import _HydratedWriteBatch, _HydratedWriteBatchWorkload, _write_batch_plan
from transbridge.ui.operations.write_dialog import WritePlanDialog
from transbridge.ui.operations.write_feature import build_write_feature
from transbridge.ui.workbench.cards.write_card import WriteCard


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _context(tmp_path, count=1, *, invalid_last=False):
    slots = {}
    for index in range(count):
        source = write_plugin(tmp_path / f"source-{index}.esp", [(0x800, "A", "First"), (0x801, "B", "Second")])
        parsed = SsePluginAdapter().parse(
            ParseRequest(SourceDescriptor(str(source)), RequestContext("gui"), FormatId.PLUGIN_SSE)
        )
        entries = tuple(
            replace(entry, translation=f"译文{number}", stage=1) for number, entry in enumerate(parsed.entries)
        )
        if invalid_last and index == count - 1:
            entries = (
                entries[0],
                replace(
                    entries[1],
                    key="NPC_UNKNOWN:FULL",
                    entry_key=replace(entries[1].entry_key, local_key="NPC_UNKNOWN:FULL"),
                ),
            )
        slots[str(source)] = SimpleNamespace(
            esp_path=str(source),
            source_snapshot=parsed.source_snapshot,
            format_id=FormatId.PLUGIN_SSE,
            collection=TranslationEntryCollection(entries),
        )
    return SimpleNamespace(
        slots=slots, active_slot=next(iter(slots.values())), collection=next(iter(slots.values())).collection
    )


@pytest.fixture
def feature(monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("Default and batch writes must never enter plugin transfer")

    monkeypatch.setattr(PluginTransferPreflightService, "preflight", unexpected)
    tasks = TaskRuntime(id_generator=_Ids(), clock=_Clock(), backend=CallbackThreadBackend(lambda _run_id, fn: fn()))
    adapter = OperationTaskAdapter(tasks)
    return SimpleNamespace(
        feature=build_write_feature(lambda _ctx: RequestContext("gui"), adapter, HydratedWritePreflightService()),
        tasks=tasks,
        adapter=adapter,
        owner=OwnerRef("gui", "gui.operation-plan"),
    )


def _submit(case, draft):
    checked = case.feature.mapper.preflight(draft)
    assert checked.ready, checked.checks
    ref = case.feature.submit(draft, checked, case.owner, case.tasks)
    return case.adapter.result(ref, case.owner)


def _texts(path):
    parsed = SsePluginAdapter().parse(
        ParseRequest(SourceDescriptor(str(path)), RequestContext("verify"), FormatId.PLUGIN_SSE)
    )
    assert parsed.outcome is OperationOutcome.COMPLETED
    return [entry.original for entry in parsed.entries]


def test_default_write_uses_current_source_and_does_not_treat_existing_output_as_template(tmp_path, feature):
    context = _context(tmp_path)
    output = write_plugin(tmp_path / "output.esp", [(0x900, "Foreign", "Only in output")])
    draft = feature.feature.create_draft(context, False, {"target_path": str(output), "overwrite_confirmed": True})
    assert isinstance(draft.request, HydratedWriteDraft)
    assert not isinstance(draft.request, PluginTransferDraft)

    result = _submit(feature, draft)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert _texts(output) == ["译文0", "译文1"]


@pytest.mark.parametrize("existing", [False, True])
def test_default_write_fails_the_whole_file_on_unlocatable_entry(tmp_path, feature, existing):
    context = _context(tmp_path, invalid_last=True)
    output = tmp_path / "output.esp"
    if existing:
        write_plugin(output, [(0x900, "Foreign", "Keep original output")])
    before = output.read_bytes() if existing else None
    draft = feature.feature.create_draft(context, False, {"target_path": str(output), "overwrite_confirmed": existing})

    result = _submit(feature, draft)

    assert result.outcome is OperationOutcome.FAILED
    assert "SOURCE_LOCATOR_NOT_FOUND" in " ".join(item.message for item in result.diagnostics)
    assert result.artifact_refs == ()
    assert (output.read_bytes() if output.exists() else None) == before


@pytest.mark.parametrize("invalid_last", [False, True])
def test_batch_same_name_outputs_are_destinations_and_invalid_members_are_not_partially_written(
    tmp_path, feature, invalid_last
):
    context = _context(tmp_path, 2, invalid_last=invalid_last)
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    initial = feature.feature.create_draft(context, True, {})
    drafts, targets = [], []
    for request in initial.request.drafts:
        output = write_plugin(
            output_dir / Path(request.source_snapshot.source.uri).name, [(0x900, "Foreign", "Unrelated")]
        )
        targets.append(output)
        drafts.append(replace(request, target_path=str(output), conflict_policy=ConflictPolicy.EXPLICIT_OVERWRITE))
    draft = _write_batch_plan(_HydratedWriteBatch(tuple(drafts)))
    assert all(isinstance(request, HydratedWriteDraft) for request in draft.request.drafts)
    assert all(field.field_id != "plugin_path" for field in draft.editable_fields)

    result = _submit(feature, draft)

    assert _texts(targets[0]) == ["译文0", "译文1"]
    if invalid_last:
        assert result.outcome is OperationOutcome.PARTIAL
        assert _texts(targets[1]) == ["Unrelated"]
        assert result.artifact_refs == (str(targets[0]),)
        assert result.value["outcomes"][1]["status"] == "failed"
    else:
        assert result.outcome is OperationOutcome.COMPLETED
        assert _texts(targets[1]) == ["译文0", "译文1"]


@pytest.mark.parametrize("outcomes", [("partial", "partial"), ("partial", "cancelled")])
def test_batch_preserves_committed_outputs_when_no_member_is_fully_successful(tmp_path, monkeypatch, outcomes):
    context = _context(tmp_path, 2)
    checked = []
    results = {}
    for index, slot in enumerate(context.slots.values()):
        target = tmp_path / f"committed-{index}.esp"
        draft = HydratedWriteDraft(
            slot.source_snapshot,
            slot.format_id,
            tuple(entry.snapshot() for entry in slot.collection),
            str(target),
            1,
            RequestContext("gui"),
        )
        checked.append(HydratedWritePreflightService().preflight(draft))
        if outcomes[index] == "partial":
            target.write_bytes(b"committed but durability uncertain")
            results[str(target)] = OperationResult.partial(
                {"published": True},
                counts=OperationCounts(succeeded=1, failed=1),
                diagnostics=(Diagnostic("DURABILITY_FAILED", "已提交，但持久化确认失败"),),
                artifact_refs=(str(target),),
            )
        else:
            results[str(target)] = OperationResult.cancelled()
    monkeypatch.setattr(
        HydratedWriteWorkload, "__call__", lambda self, _ctx: results[self._preflight.draft.target_path]
    )
    result = _HydratedWriteBatchWorkload(checked)(
        SimpleNamespace(ref=SimpleNamespace(run_id="batch"), cancellation=None)
    )

    assert result.outcome is OperationOutcome.PARTIAL
    assert result.counts.succeeded == outcomes.count("partial")
    assert result.counts.failed == outcomes.count("partial")
    assert result.counts.cancelled == outcomes.count("cancelled")
    assert result.artifact_refs == tuple(path for path, item in results.items() if item.artifact_refs)
    assert len(result.value["outcomes"]) == 2
    for item in result.value["outcomes"]:
        assert item["status"] != "succeeded"
    assert "DURABILITY_FAILED" in [item.code for item in result.diagnostics]


@pytest.mark.parametrize("states", [("failed", "failed"), ("failed", "cancelled"), ("cancelled", "cancelled")])
def test_failed_and_cancelled_batches_keep_all_receipts_and_recovery_details_in_ui(
    tmp_path, feature, qapp, monkeypatch, states
):
    context = _context(tmp_path, 2)
    draft = feature.feature.create_draft(context, True, {})
    results = {}
    for index, request in enumerate(draft.request.drafts):
        results[request.target_path] = (
            OperationResult.failed(
                DomainError(
                    ErrorCategory.EXTERNAL,
                    f"ROLLBACK_FAILED_{index}",
                    f"第{index}个来源回滚失败",
                    details={"recovery_directory": str(tmp_path / f"recovery-{index}")},
                )
            )
            if states[index] == "failed"
            else OperationResult.cancelled()
        )
    monkeypatch.setattr(
        HydratedWriteWorkload, "__call__", lambda self, _ctx: results[self._preflight.draft.target_path]
    )
    result = _submit(feature, draft)
    assert result.outcome is (OperationOutcome.FAILED if "failed" in states else OperationOutcome.CANCELLED)
    assert result.counts.failed == states.count("failed")
    assert result.counts.cancelled == states.count("cancelled")
    assert result.value is None
    batch_receipts = [
        dict(item.details)["outcomes"] for item in result.diagnostics if item.code == "WRITE_BATCH_RESULTS"
    ]
    assert len(batch_receipts) == 1
    assert len(batch_receipts[0]) == 2
    snapshot = feature.tasks.list(feature.owner)[0]
    assert len(feature.adapter.result_state(snapshot.ref, feature.owner).objects) == 2
    dialog = WritePlanDialog(feature.feature.mapper.present("batch", 1, draft))
    try:
        dialog.set_execution_running(True)
        dialog.render_execution(activity_from_snapshot(snapshot), result)
        for index, request in enumerate(draft.request.drafts):
            assert request.target_path in dialog._checks.text()
            if states[index] == "failed":
                assert str(tmp_path / f"recovery-{index}") in dialog._checks.text()
    finally:
        dialog.close()


def test_batch_uses_the_terminology_projection_confirmed_in_preflight(tmp_path, feature):
    class ProjectionSource:
        text = "预检确认译文"

        def freeze(self, _project, _variant):
            text = self.text
            return SimpleNamespace(
                identity=text,
                metadata=(),
                project_entries=lambda entries: (tuple(replace(entry, translation=text) for entry in entries), ()),
            )

    projection = ProjectionSource()
    composed = build_write_feature(
        lambda _ctx: RequestContext("gui", project_id="project", variant_id="variant"),
        feature.adapter,
        HydratedWritePreflightService(entry_projection=projection),
    )
    draft = composed.create_draft(_context(tmp_path, 2), True, {})
    checked = composed.mapper.preflight(draft)
    assert checked.ready
    projection.text = "预检后被修改的译文"
    ref = composed.submit(draft, checked, feature.owner, feature.tasks)
    result = feature.adapter.result(ref, feature.owner)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    for request in draft.request.drafts:
        assert _texts(request.target_path) == ["预检确认译文", "预检确认译文"]


def test_batch_rejects_incomplete_source_scope_instead_of_silently_skipping(tmp_path, feature):
    context = _context(tmp_path, 3)
    tuple(context.slots.values())[-1].source_snapshot = None
    assert not feature.feature.supports(context, True)
    with pytest.raises(RuntimeError, match="缺少来源快照"):
        feature.feature.create_draft(context, True, {})


def test_batch_preflight_identifies_files_and_marks_full_retry_scope(tmp_path, feature):
    context = _context(tmp_path, 2)
    draft = feature.feature.create_draft(context, True, {})
    checked = feature.feature.mapper.preflight(draft)
    view = feature.feature.mapper.present("batch-review", 1, draft)
    assert view.is_batch
    assert view.backup_summary == "覆盖输出文件前创建备份"
    labels = [check.label for check in checked.checks]
    assert any("source-0.esp" in label for label in labels)
    assert any("source-1.esp" in label for label in labels)
    assert not any(label.startswith("来源 1") for label in labels)


@pytest.mark.parametrize("entrypoint", ["write", "batch_write", "do_batch_write"])
@pytest.mark.parametrize("bound", [False, True])
def test_legacy_card_refuses_missing_publication_context_without_running_writers(
    tmp_path, qapp, monkeypatch, entrypoint, bound
):
    context = _context(tmp_path, 2)
    context.plugin = object()  # A legacy mutable plugin must not authorize writing.
    context.uses_authoritative_projection = False
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))
    card = WriteCard(context, lambda *_args, **_kwargs: pytest.fail("Legacy write worker must not run"))
    if bound:
        card.bind_operation_plan_facade(
            SimpleNamespace(supports=lambda *_a, **_kw: False, begin_write=lambda *_a, **_kw: None)
        )
    try:
        if entrypoint == "do_batch_write":
            card.do_batch_write(list(context.slots.values()))
        else:
            getattr(card, entrypoint)()
        assert warnings and ("安全写回" in warnings[-1] or "来源快照" in warnings[-1])
    finally:
        card.close()


def test_card_routes_selection_to_bound_facade_and_preserves_independent_dsd_export(tmp_path, qapp, monkeypatch):
    context = _context(tmp_path, 3)
    context.esp_path = context.active_slot.esp_path
    calls = []
    worker_calls = []

    def run_worker(fn, *, on_result, **_kwargs):
        worker_calls.append(True)
        on_result(fn())

    card = WriteCard(context, run_worker)
    card.bind_operation_plan_facade(
        SimpleNamespace(
            supports=lambda *_a, **_kw: True,
            begin_write=lambda ctx, **values: calls.append((ctx, values)),
        )
    )
    output = tmp_path / "translations.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_kw: (str(output), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    try:
        card.write()
        card.batch_write()
        card.do_batch_write(list(context.slots.values())[:2])
        assert [values["batch"] for _ctx, values in calls] == [False, True, True]
        assert len(calls[-1][0].slots) == 2
        assert len(context.slots) == 3
        assert not worker_calls
        card.dsd_btn.click()
        assert output.exists()
        assert worker_calls == [True]
        assert len(calls) == 3
    finally:
        card.close()
