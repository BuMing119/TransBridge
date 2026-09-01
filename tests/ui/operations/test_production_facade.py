from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import gc
from pathlib import Path
from types import SimpleNamespace
import weakref

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskRuntime
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.operations.plan_view import OperationKind
from transbridge.ui.operations.production import build_operation_plan_facade


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"production-operation-{self.value}"


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 24, tzinfo=UTC)

    def now(self):
        self.value += timedelta(microseconds=1)
        return self.value


class _Signal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def emit(self, *values) -> None:
        for callback in tuple(self.callbacks):
            callback(*values)


class _ConfirmingDialog:
    def __init__(self, plan, _parent=None) -> None:
        self.plan = plan
        self.preflight = None
        self.preflight_requested = _Signal()
        self.return_to_edit_requested = _Signal()
        self.confirm_requested = _Signal()
        self.rejected = _Signal()
        self.destroyed = _Signal()
        self.shown = False
        self.accepted = False
        self.exec_called = False

    def render_plan(self, plan) -> None:
        self.plan = plan

    def render_preflight(self, result) -> None:
        self.preflight = result

    def show(self) -> None:
        self.shown = True

    def exec(self) -> int:
        self.exec_called = True
        raise AssertionError("operation plans must never use blocking exec()")

    def accept(self) -> None:
        self.accepted = True


def _runtime():
    tasks = TaskRuntime(
        id_generator=_Ids(),
        clock=_Clock(),
        backend=CallbackThreadBackend(lambda _run_id, target: target()),
    )
    return SimpleNamespace(tasks=tasks)


def test_production_builder_installs_all_four_features_without_remote_work() -> None:
    runtime = _runtime()
    facade = build_operation_plan_facade(
        runtime,
        RequestContext("gui", project_id="p", variant_id="v"),
        dialog_factory=_ConfirmingDialog,
    )
    context = SimpleNamespace(
        collection=TranslationEntryCollection(),
        current_project={"id": 1},
        paratranz_project_id=1,
        config=SimpleNamespace(
            token="configured",
            base_url="https://paratranz.cn",
            user_id=7,
            config_revision=1,
        ),
        current_user={"id": 7},
        active_project_id="local-project",
        dirty=False,
        is_member=lambda: True,
    )

    dialog = facade.begin_upload(context)
    assert dialog.shown and not dialog.exec_called
    assert facade.active_plan_count == 1
    retained = weakref.ref(dialog)
    del dialog
    gc.collect()
    assert retained() is not None
    retained().destroyed.emit()
    assert facade.active_plan_count == 0
    cancelled = facade.begin_upload(context, paratranz_project_id="42", set_as_default=True)
    fields = {field.field_id: field.value for field in cancelled.plan.editable_fields}
    assert fields == {
        "paratranz_project_id": "42",
        "set_as_default": "true",
        "conflict_policy": "prefer_local",
        "apply_remote_deletions": "true",
    }
    cancelled.rejected.emit()
    assert facade.active_plan_count == 0
    assert facade.tasks is runtime.tasks
    assert not facade.supports(OperationKind.UPLOAD, context, batch=True)
    assert not facade.supports(
        OperationKind.WRITE,
        SimpleNamespace(active_slot=SimpleNamespace(source_snapshot=None, format_id=None, collection=None)),
    )


def test_production_write_uses_hydration_and_task_runtime_after_one_confirmation(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    source.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    parsed = TranslationIoUseCase().parse(
        ParseRequest(
            SourceDescriptor(str(source), source.name, source.stat().st_size),
            RequestContext("gui"),
            FormatId.XML_EET,
        )
    )
    collection = TranslationEntryCollection((replace(parsed.entries[0], translation="hydrated", stage=1),))
    target = tmp_path / "translated.xml"
    context = SimpleNamespace(
        active_slot=SimpleNamespace(
            source_snapshot=parsed.source_snapshot,
            format_id=FormatId.XML_EET,
            collection=collection,
            esp_path=str(source),
        )
    )
    runtime = _runtime()
    events = []
    runtime.tasks.subscribe(events.append)
    facade = build_operation_plan_facade(
        runtime,
        RequestContext("gui", project_id="p", variant_id="v"),
        dialog_factory=_ConfirmingDialog,
    )

    dialog = facade.begin_write(context, target_path=str(target))
    assert dialog.shown and not dialog.exec_called
    assert facade.active_plan_count == 1
    dialog.preflight_requested.emit(dialog.plan.session_id, ())
    assert dialog.preflight is not None and dialog.preflight.ready
    dialog.confirm_requested.emit(dialog.plan.session_id, dialog.preflight.confirmation_token)
    assert dialog.accepted
    assert facade.active_plan_count == 0
    jobs = runtime.tasks.list(OwnerRef("gui", "gui.operation-plan", project_id="p", variant_id="v"))
    assert len(jobs) == 1
    assert jobs[0].state is JobState.COMPLETED, [(event.code, event.message) for event in events]
    assert target.is_file()


def test_production_batch_write_preflights_all_hydrated_sources_before_submission(tmp_path: Path) -> None:
    fixture = Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes()
    slots = {}
    for index in (1, 2):
        source = tmp_path / f"source-{index}.xml"
        source.write_bytes(fixture)
        parsed = TranslationIoUseCase().parse(
            ParseRequest(
                SourceDescriptor(str(source), source.name, source.stat().st_size),
                RequestContext("gui"),
                FormatId.XML_EET,
            )
        )
        collection = TranslationEntryCollection((replace(parsed.entries[0], translation=f"译文 {index}", stage=1),))
        slots[str(source)] = SimpleNamespace(
            source_snapshot=parsed.source_snapshot,
            format_id=FormatId.XML_EET,
            collection=collection,
            esp_path=str(source),
        )
    context = SimpleNamespace(slots=slots, variant_revision=3)
    runtime = _runtime()
    facade = build_operation_plan_facade(
        runtime,
        RequestContext("gui", project_id="p", variant_id="v"),
        dialog_factory=_ConfirmingDialog,
    )

    assert facade.supports(OperationKind.WRITE, context, batch=True)
    dialog = facade.begin_write(context, batch=True)
    dialog.preflight_requested.emit(dialog.plan.session_id, ())

    assert dialog.preflight is not None and dialog.preflight.ready
    assert sum(item.check_id.endswith("SOURCE_SNAPSHOT_BOUND") for item in dialog.preflight.checks) == 2
    dialog.confirm_requested.emit(dialog.plan.session_id, dialog.preflight.confirmation_token)
    jobs = runtime.tasks.list(OwnerRef("gui", "gui.operation-plan", project_id="p", variant_id="v"))
    assert len(jobs) == 1
    assert jobs[0].state is JobState.COMPLETED
    assert (tmp_path / "source-1_translated.xml").is_file()
    assert (tmp_path / "source-2_translated.xml").is_file()
