from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import shutil

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.io.operation_write import (
    HydratedWriteDraft,
    HydratedWritePreflightService,
    HydratedWriteWorkload,
)
from transbridge.application.io.publish import BackupPolicy, ConflictPolicy
from transbridge.application.tasks import CallbackThreadBackend, OwnerRef, TaskRuntime
from transbridge.ui.operations import OperationKind, OperationTaskAdapter, OperationTaskRequest

FIXTURE = Path("tests/contracts/io/fixtures/eet-small.xml")


class Ids:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"write-run-{self.value}"


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 24, tzinfo=UTC)

    def now(self):
        self.value += timedelta(microseconds=1)
        return self.value


def test_s04_hydration_writes_without_plugin_or_reparsing_legacy_source(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    shutil.copyfile(FIXTURE, source)
    parsed = TranslationIoUseCase().parse(
        ParseRequest(
            SourceDescriptor(str(source), source.name, source.stat().st_size),
            RequestContext("gui"),
            FormatId.XML_EET,
        )
    )
    changed = replace(parsed.entries[0], translation="来自 hydration", stage=1)
    # Reproduce S04: no mutable plugin/parser survives hydration.  Deleting the
    # legacy source proves the workload renders from SourceSnapshot bytes.
    source.unlink()
    target = tmp_path / "translated.xml"
    draft = HydratedWriteDraft(
        parsed.source_snapshot,
        FormatId.XML_EET,
        (changed.snapshot(),),
        str(target),
        1,
        RequestContext("gui", project_id="p", variant_id="v"),
        ConflictPolicy.FAIL,
        BackupPolicy.IF_EXISTS,
    )
    preflight = HydratedWritePreflightService().preflight(draft)
    assert preflight.ready

    runtime = TaskRuntime(
        id_generator=Ids(),
        clock=Clock(),
        backend=CallbackThreadBackend(lambda _run_id, target: target()),
    )
    owner = OwnerRef("gui", "workbench", project_id="p", variant_id="v")
    adapter = OperationTaskAdapter(runtime)
    events = []
    runtime.subscribe(events.append)
    ref = adapter.submit(
        OperationTaskRequest(
            OperationKind.WRITE,
            preflight.request_digest,
            "variant:v",
            "写回 translated.xml",
            HydratedWriteWorkload(preflight),
            True,
            (str(target),),
        ),
        owner,
    )
    result = adapter.result(ref, owner)

    assert result is not None, [(event.code, event.message) for event in events]
    assert result.outcome is OperationOutcome.COMPLETED
    assert target.is_file()
    reparsed = TranslationIoUseCase().parse(
        ParseRequest(
            SourceDescriptor(str(target), target.name, target.stat().st_size),
            RequestContext("verify"),
            FormatId.XML_EET,
            changed.identity.namespace,
        )
    )
    assert reparsed.entries[0].translation == "来自 hydration"


def test_write_preflight_blocks_existing_target_without_explicit_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    shutil.copyfile(FIXTURE, source)
    parsed = TranslationIoUseCase().parse(
        ParseRequest(
            SourceDescriptor(str(source), source.name, source.stat().st_size),
            RequestContext("gui"),
            FormatId.XML_EET,
        )
    )
    target = tmp_path / "translated.xml"
    target.write_text("existing", encoding="utf-8")
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(
            parsed.source_snapshot,
            FormatId.XML_EET,
            (parsed.entries[0],),
            str(target),
            1,
            RequestContext("gui"),
        )
    )

    assert not checked.ready
    assert "OVERWRITE_CONFIRMED" in {item.code for item in checked.checks if not item.passed}
