from __future__ import annotations

from pathlib import Path
import time

import pytest

from transbridge.application.capabilities import CapabilityState
from transbridge.application.contracts import DomainError, OperationOutcome
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.source_registry import BilingualCapability, SourceKind, SourceRegistration
from transbridge.application.tasks import JobState, OwnerRef
from transbridge.application.terminology.changelog_queries import ChangeLogQueryService
from transbridge.application.terminology.decisions import DecisionOperation
from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
from transbridge.application.terminology.report_queries import TerminologyReportQueryService
from transbridge.application.terminology.runtime import (
    TerminologyTaskEntrypoint,
    TerminologyWorkloadRegistry,
)
from transbridge.application.terminology.workloads import TerminologyWorkloadType
from transbridge.bootstrap import build_runtime
from transbridge.bootstrap.adapters import DenyByDefaultSecurity, NullSecretStore, SystemClock
from transbridge.bootstrap.runtime import RuntimePorts
from transbridge.bootstrap.terminology import ProjectTerminologyRepositories
from transbridge.bootstrap.terminology_storage import FilesystemSourceLeases
from transbridge.persistence.terminology import SqliteTerminologyRepository
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices

_TERMINAL = {JobState.COMPLETED, JobState.CANCELLED, JobState.FAILED}


def _wait_for_job(app, ref, context, events=()):
    owner = OwnerRef(
        context.owner_id,
        "gui",
        project_id=context.project_id,
        variant_id=context.variant_id,
        permissions=context.permissions,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snapshot = app.tasks.get(ref, owner)
        if snapshot.state in _TERMINAL:
            diagnostics = tuple(
                (event.code, event.message) for event in events if event.snapshot.ref == ref and event.code is not None
            )
            assert snapshot.state is JobState.COMPLETED, diagnostics
            return app.use_cases.resolve("terminology_tasks").result(ref, owner)
        time.sleep(0.01)
    raise AssertionError("terminology TaskRuntime job did not reach a terminal state")


class _ShortIds:
    def __init__(self) -> None:
        self._value = 0

    def new_id(self) -> str:
        self._value += 1
        return f"i{self._value}"


def test_composition_exposes_project_terminology_without_runtime_release_evidence(tmp_path):
    app = build_runtime(
        settings={
            "persistence_v2_root": tmp_path,
            "terminology_max_unstreamed_source_count": 7,
        }
    )
    try:
        registry = app.use_cases.resolve("terminology_workloads")
        entrypoint = app.use_cases.resolve("terminology_tasks")
        repositories = app.use_cases.resolve("terminology_repository")
        context = app.context("operator", project_id="project-1", variant_id="variant-1")
        services = TerminologyUiServices.from_runtime(app, context)

        assert isinstance(registry, TerminologyWorkloadRegistry)
        assert registry.workload_types == tuple(TerminologyWorkloadType)
        assert isinstance(entrypoint, TerminologyTaskEntrypoint)
        assert entrypoint.runtime is app.tasks
        assert isinstance(repositories, ProjectTerminologyRepositories)
        assert isinstance(services.queries, SqliteTerminologyRepository)
        assert services.commands is app.use_cases.resolve("terminology_ui_commands")
        assert services.build_inputs is app.use_cases.resolve("terminology_build_input")
        assert services.build_inputs._max_unstreamed_source_count == 7
        assert app.use_cases.resolve("effective_terminology_factory") is app.use_cases.resolve(
            "terminology_ui_services_factory"
        )
        profile_factory = app.use_cases.resolve("terminology_profile_service_factory")
        assert profile_factory is app.use_cases.resolve("effective_terminology_factory")
        assert profile_factory.profile_service_for("project-1") is profile_factory.profile_service_for("project-1")
        assert app.capabilities.report("terminology.analysis-report").state is CapabilityState.AVAILABLE
        assert "terminology_feature_gates" not in app.use_cases.names()
        assert "terminology_release_evidence_diagnostics" not in app.use_cases.names()
    finally:
        app.close()


def test_project_terminology_sqlite_asset_reopens_after_runtime_restart(tmp_path):
    first = build_runtime(settings={"persistence_v2_root": tmp_path})
    first_repository = first.use_cases.resolve("terminology_repository").for_project("project-1")
    database_path = first_repository.path
    first.close()

    second = build_runtime(settings={"persistence_v2_root": tmp_path})
    try:
        reopened = second.use_cases.resolve("terminology_repository").for_project("project-1")

        assert reopened.path == database_path
        assert reopened.list_versions("project-1", "variant-1").items == ()
    finally:
        second.close()


def test_production_source_lease_rejects_oversized_file_before_open(tmp_path, monkeypatch) -> None:
    source = tmp_path / "oversized.esp"
    source.write_bytes(b"12345")
    registration = SourceRegistration(
        source_id="source-1",
        enabled=True,
        format_id=FormatId.PLUGIN_SSE,
        location=str(source),
        kind=SourceKind.PLUGIN,
        bilingual_capability=BilingualCapability.NONE,
        display_name="Oversized source",
    )
    leases = FilesystemSourceLeases(max_unstreamed_source_bytes=4)

    def fail_if_open(_path: Path, *_args, **_kwargs):
        raise AssertionError("oversized source must be rejected before opening the file")

    monkeypatch.setattr(Path, "open", fail_if_open)

    with pytest.raises(DomainError) as error:
        leases.acquire(registration)

    assert error.value.code == "TERMINOLOGY_STREAMING_REQUIRED"
    assert error.value.details["source_bytes"] == 5


def test_production_source_lease_bounded_read_rejects_growth_after_stat(tmp_path, monkeypatch) -> None:
    source = tmp_path / "growing.esp"
    source.write_bytes(b"12345")
    registration = SourceRegistration(
        source_id="source-1",
        enabled=True,
        format_id=FormatId.PLUGIN_SSE,
        location=str(source),
        kind=SourceKind.PLUGIN,
        bilingual_capability=BilingualCapability.NONE,
        display_name="Growing source",
    )
    leases = FilesystemSourceLeases(max_unstreamed_source_bytes=4)
    original_stat = Path.stat

    class _SmallStat:
        st_size = 4

    def stale_stat(path: Path, *args, **kwargs):
        if path == source:
            return _SmallStat()
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stale_stat)

    with pytest.raises(DomainError) as error:
        leases.acquire(registration)

    assert error.value.code == "TERMINOLOGY_STREAMING_REQUIRED"
    assert error.value.details["source_bytes"] == 5
    assert error.value.details["limit_bytes"] == 4


def test_production_source_lease_uses_path_backed_snapshot_without_retaining_bytes(tmp_path) -> None:
    source = tmp_path / "bounded.esp"
    source.write_bytes(b"1234")
    registration = SourceRegistration(
        source_id="source-1",
        enabled=True,
        format_id=FormatId.PLUGIN_SSE,
        location=str(source),
        kind=SourceKind.PLUGIN,
        bilingual_capability=BilingualCapability.NONE,
        display_name="Bounded source",
    )

    lease = FilesystemSourceLeases(max_unstreamed_source_bytes=4).acquire(registration)

    assert lease.snapshot.size_bytes == 4
    assert lease.snapshot.content is None
    assert lease.snapshot.lease_id == f"filesystem-sha256:{lease.actual_fingerprint}"


def test_composition_exposes_project_repository_reporting_ports(tmp_path):
    repository = InMemoryTerminologyRepository()
    app = build_runtime(
        settings={"persistence_v2_root": tmp_path},
        use_cases={"terminology_repository": repository},
    )
    try:
        assert app.use_cases.resolve("terminology_artifact_ledger") is repository
        assert isinstance(app.use_cases.resolve("terminology_report_queries"), TerminologyReportQueryService)
        assert isinstance(app.use_cases.resolve("terminology_changelog_queries"), ChangeLogQueryService)
    finally:
        app.close()


def test_production_runtime_closes_build_decision_publish_history_restore_and_render_loop(tmp_path):
    source = tmp_path / "terms.xml"
    source.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<DocumentElement>
  <ESP>
    <GRUP>INFO</GRUP><ID>00000001</ID><EDID>GreetingTopic</EDID><CHAMP>NAM1</CHAMP>
    <ORIGINAL>Dragon</ORIGINAL><TRADUIT>巨龙</TRADUIT><PERSO></PERSO><INDEX>1</INDEX>
    <STATUS>1</STATUS><IDSTEXTE>1</IDSTEXTE><COMMENTAIRE></COMMENTAIRE><ICON>0</ICON>
  </ESP>
</DocumentElement>
""",
        encoding="utf-8",
    )
    app = build_runtime(
        settings={"persistence_v2_root": tmp_path / "state"},
        ports=RuntimePorts(SystemClock(), _ShortIds(), NullSecretStore(), DenyByDefaultSecurity()),
    )
    try:
        provisioning = app.use_cases.resolve("project_provisioning")
        owner_context = app.context("operator", metadata=(("entrypoint", "gui"),))
        prepared = provisioning.prepare(
            ProjectProvisioningRequest(
                "Terminology production project",
                source=ProjectSourceRequest(str(source), format_hint=FormatId.XML_EET),
            ),
            owner_context,
        )
        assert prepared.outcome is OperationOutcome.COMPLETED and prepared.value is not None
        assert provisioning.commit(prepared.value.token, owner_context).is_success
        context = app.context(
            "operator",
            project_id=prepared.value.project_id,
            variant_id=prepared.value.variant_id,
            metadata=(("entrypoint", "gui"), ("manual_actor_id", "operator")),
        )
        services = TerminologyUiServices.from_runtime(app, context)
        events = []
        app.tasks.subscribe(events.append)
        preflight = TerminologyPresenter(services, context).preflight()
        assert preflight.ready
        assert preflight.action_label == "创建术语库"
        assert not any("发布验证" in detail.label for detail in preflight.technical_details)
        captured = services.build_inputs.capture_build_input(context, config={})
        assert captured.outcome is OperationOutcome.COMPLETED and captured.value is not None

        first_build = services.commands.start_build(captured.value, context)
        assert _wait_for_job(app, first_build, context, events).committed
        repositories = app.use_cases.resolve("terminology_repository")
        repository = repositories.for_project(context.project_id)
        first_draft = repository.active_draft(context.project_id, context.variant_id)
        assert first_draft is not None
        edited = services.commands.apply_decision(
            DecisionOperation.ADD,
            context,
            original="Dragon",
            translation="龙裔",
        )
        assert edited.ref.revision == 1
        assert repository.list_draft_terms(edited.ref).items == edited.decisions
        first_publish = services.commands.publish(context)
        assert _wait_for_job(app, first_publish, context).committed
        first_version = repository.effective_version(context.project_id, context.variant_id)
        assert first_version is not None and first_version.decisions[0].translation == "龙裔"

        recaptured = services.build_inputs.capture_build_input(context, config={})
        assert recaptured.value is not None
        second_build = services.commands.start_build(recaptured.value, context)
        assert _wait_for_job(app, second_build, context).committed
        second_draft = repository.active_draft(context.project_id, context.variant_id)
        assert second_draft is not None
        services.commands.apply_decision(
            DecisionOperation.ADD,
            context,
            original="Dragon",
            translation="飞龙",
        )
        second_publish = services.commands.publish(context)
        assert _wait_for_job(app, second_publish, context).committed
        second_version = repository.effective_version(context.project_id, context.variant_id)
        assert second_version is not None and second_version.ref != first_version.ref

        comparison = services.commands.compare(first_version.ref, context)
        assert _wait_for_job(app, comparison, context).committed
        compared = services.commands.latest_comparison(context.project_id, context.variant_id)
        assert compared is not None and compared.changes
        restored = services.commands.restore(first_version.ref, context)
        assert _wait_for_job(app, restored, context).committed
        restored_version = repository.effective_version(context.project_id, context.variant_id)
        assert restored_version is not None
        assert restored_version.ref.version_id not in {first_version.ref.version_id, second_version.ref.version_id}
        assert restored_version.decisions == first_version.decisions

        report = services.commands.render_report(context)
        assert _wait_for_job(app, report, context).committed
        changelog = services.commands.render_changelog(context)
        changelog_result = _wait_for_job(app, changelog, context)
        assert changelog_result.committed
        markdown = repositories.paths.artifact_directory(context.project_id) / f"{restored_version.ref.version_id}.md"
        assert markdown.is_file()
        retry = services.commands.retry_changelog(context)
        assert _wait_for_job(app, retry, context).committed
        assert len(repository.list_versions(context.project_id, context.variant_id).items) == 3
    finally:
        app.close()
