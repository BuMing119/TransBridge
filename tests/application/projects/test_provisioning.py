from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import (
    EntryRevision,
    EntrySnapshot,
    FormatId,
    SourceDescriptor,
    SourceSnapshot,
)
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects import (
    PreparedProjectSource,
    PreparedSourceHydration,
    ProjectLifecycleService,
    ProjectProvisioningRequest,
    ProjectProvisioningService,
    ProjectSourceRequest,
)
from transbridge.persistence.project_lifecycle_uow import RepositoryLifecycleUnitOfWorkFactory
from transbridge.persistence.v2 import SourceBaseline, SourceFingerprint, VariantEntryState


class _UnusedLoader:
    def prepare_candidate(self, target, context):  # pragma: no cover - provisioning does not load repository state
        raise AssertionError("provisioning must not use the lifecycle candidate loader")


class _Store:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.mutations: dict[str, object] = {}
        self.fail_commit = False

    def begin(self, transaction_id: str) -> None:
        self.calls.append(("begin", transaction_id))

    def stage_save(self, transaction_id: str, value) -> None:
        self.calls.append(("save", transaction_id))

    def stage_activate(self, transaction_id: str, value) -> None:
        self.calls.append(("activate", transaction_id))

    def stage_snapshot(self, transaction_id: str, value) -> None:
        self.calls.append(("snapshot", transaction_id))

    def stage_provisioning(self, transaction_id: str, value) -> None:
        self.calls.append(("provision", transaction_id))
        self.mutations[transaction_id] = value

    def commit(self, transaction_id: str) -> None:
        self.calls.append(("commit", transaction_id))
        if self.fail_commit:
            raise OSError("injected provisioning commit failure")

    def rollback(self, transaction_id: str) -> None:
        self.calls.append(("rollback", transaction_id))
        self.mutations.pop(transaction_id, None)


class _Identities:
    def project_exists(self, ref) -> bool:
        return False

    def variant_exists(self, ref) -> bool:
        return False

    def project_name_exists(self, name_key: str) -> bool:
        return False


@dataclass
class _Sources:
    calls: list[tuple[str, str]]

    def prepare_source(self, request, context, *, role, common_options):
        self.calls.append((request.location, role))
        namespace = SourceNamespace(f"source:{role}")
        content = role.encode()
        source_snapshot = SourceSnapshot.from_bytes(
            SourceDescriptor(request.location),
            FormatId.PLUGIN_SSE,
            content,
        )
        fingerprint = SourceFingerprint(namespace, source_snapshot.sha256)
        entry_key = EntryKey(namespace, f"entry-{role}")
        entry = VariantEntryState(entry_key, "")
        hydration = None
        if role == "primary":
            hydration = PreparedSourceHydration(
                request.location,
                source_snapshot.sha256,
                FormatId.PLUGIN_SSE,
                source_snapshot,
                (
                    EntrySnapshot(
                        entry_key,
                        f"legacy-{role}",
                        f"original-{role}",
                        "",
                        0,
                        "FULL",
                        (),
                        EntryRevision(),
                        (),
                        (),
                    ),
                ),
            )
        return PreparedProjectSource(
            (
                ("source_id", namespace.value),
                ("format_id", "plugin.sse"),
                ("location", request.location),
                ("path", request.location),
                ("fingerprint", fingerprint.sha256),
                ("role", role),
            ),
            SourceBaseline(fingerprint, (entry,)),
            hydration=hydration,
        )


def _harness(*, fail_commit: bool = False):
    store = _Store()
    store.fail_commit = fail_commit
    transactions = iter(f"tx-{index}" for index in range(20))
    lifecycle = ProjectLifecycleService(
        _UnusedLoader(),
        RepositoryLifecycleUnitOfWorkFactory(store, lambda: next(transactions)),
    )
    identifiers = iter(f"id-{index}" for index in range(20))
    tokens = iter(f"preview-{index}" for index in range(20))
    sources = _Sources([])
    service = ProjectProvisioningService(
        lifecycle,
        sources,
        _Identities(),
        id_factory=lambda: next(identifiers),
        token_factory=lambda: next(tokens),
    )
    return service, lifecycle, store, sources


def _context(owner: str = "gui") -> RequestContext:
    return RequestContext(owner, run_id=f"run-{owner}")


def test_empty_project_prepare_has_no_side_effect_then_commit_publishes_once() -> None:
    service, lifecycle, store, sources = _harness()

    prepared = service.prepare(ProjectProvisioningRequest("本地工程"), _context())

    assert prepared.outcome is OperationOutcome.COMPLETED
    assert prepared.value is not None
    assert prepared.value.source_count == 0
    assert prepared.value.entry_count == 0
    assert store.calls == []
    assert lifecycle.active is None
    assert sources.calls == []

    committed = service.commit(
        prepared.value.token,
        _context(),
        request_fingerprint=prepared.value.request_fingerprint,
    )
    replay = service.commit(prepared.value.token, _context())

    assert committed.is_success
    assert replay.diagnostics[0].code == "PROJECT_PROVISIONING_TOKEN_INVALID"
    assert [name for name, _ in store.calls] == ["begin", "provision", "commit"]
    assert lifecycle.active is not None
    assert lifecycle.active.project.envelope.data["name"] == "本地工程"
    assert lifecycle.active.variant is not None
    assert lifecycle.active.variant.snapshot().entries == ()
    assert lifecycle.generation == 1


def test_owner_and_request_fingerprint_guards_do_not_consume_valid_preview() -> None:
    service, lifecycle, store, _sources = _harness()
    prepared = service.prepare(ProjectProvisioningRequest("工程"), _context())
    assert prepared.value is not None

    foreign = service.commit(prepared.value.token, _context("other"))
    edited = service.commit(
        prepared.value.token,
        _context(),
        request_fingerprint="0" * 64,
    )
    committed = service.commit(prepared.value.token, _context())

    assert foreign.diagnostics[0].code == "PROJECT_PROVISIONING_OWNER_MISMATCH"
    assert edited.diagnostics[0].code == "PROJECT_PROVISIONING_REQUEST_CHANGED"
    assert committed.is_success
    assert lifecycle.active is not None
    assert [name for name, _ in store.calls] == ["begin", "provision", "commit"]


def test_source_and_migration_candidates_are_parsed_once_before_commit() -> None:
    service, lifecycle, store, sources = _harness()
    request = ProjectProvisioningRequest(
        "迁移工程",
        source=ProjectSourceRequest("C:/mods/source.esp"),
        migration_sources=(ProjectSourceRequest("C:/mods/old.esp"),),
    )

    prepared = service.prepare(request, _context())
    assert prepared.value is not None
    assert prepared.value.source_count == 2
    assert prepared.value.entry_count == 2
    assert sources.calls == [
        ("C:/mods/source.esp", "primary"),
        ("C:/mods/old.esp", "migration"),
    ]

    committed = service.commit(prepared.value.token, _context())

    assert committed.is_success
    assert len(sources.calls) == 2
    assert lifecycle.active is not None and lifecycle.active.variant is not None
    assert len(lifecycle.active.variant.snapshot().source_fingerprints) == 2
    assert [name for name, _ in store.calls] == ["begin", "provision", "commit"]


def test_commit_failure_rolls_back_and_keeps_lifecycle_unpublished() -> None:
    service, lifecycle, store, _sources = _harness(fail_commit=True)
    prepared = service.prepare(ProjectProvisioningRequest("失败工程"), _context())
    assert prepared.value is not None

    result = service.commit(prepared.value.token, _context())

    assert result.diagnostics[0].code == "PROJECT_PROVISIONING_COMMIT_FAILED"
    assert lifecycle.active is None
    assert lifecycle.generation == 0
    assert [name for name, _ in store.calls] == ["begin", "provision", "commit", "rollback"]
    missing = service.consume_hydration(prepared.value.project_id, _context())
    assert missing.diagnostics[0].code == "PROJECT_HYDRATION_UNAVAILABLE"


def test_committed_hydration_is_owner_bound_and_consumed_once() -> None:
    service, _lifecycle, _store, sources = _harness()
    prepared = service.prepare(
        ProjectProvisioningRequest("工程", source=ProjectSourceRequest("C:/mods/source.esp")),
        _context(),
    )
    assert prepared.value is not None
    assert service.commit(prepared.value.token, _context()).is_success

    foreign = service.consume_hydration(prepared.value.project_id, _context("other"))
    consumed = service.consume_hydration(prepared.value.project_id, _context())
    replay = service.consume_hydration(prepared.value.project_id, _context())

    assert foreign.diagnostics[0].code == "PROJECT_HYDRATION_OWNER_MISMATCH"
    assert consumed.is_success and consumed.value is not None
    assert consumed.value.request_fingerprint == prepared.value.request_fingerprint
    assert consumed.value.source.entries[0].original == "original-primary"
    assert replay.diagnostics[0].code == "PROJECT_HYDRATION_UNAVAILABLE"
    assert sources.calls == [("C:/mods/source.esp", "primary")]


def test_discard_cleans_preview_hydration_without_publish() -> None:
    service, _lifecycle, _store, _sources = _harness()
    prepared = service.prepare(
        ProjectProvisioningRequest("工程", source=ProjectSourceRequest("C:/mods/source.esp")),
        _context(),
    )
    assert prepared.value is not None

    assert service.discard(prepared.value.token, _context()).is_success
    missing = service.consume_hydration(prepared.value.project_id, _context())

    assert missing.diagnostics[0].code == "PROJECT_HYDRATION_UNAVAILABLE"


def test_older_preview_is_rejected_after_another_project_commits() -> None:
    service, lifecycle, store, _sources = _harness()
    first = service.prepare(ProjectProvisioningRequest("工程一"), _context())
    second = service.prepare(ProjectProvisioningRequest("工程二"), _context())
    assert first.value is not None and second.value is not None

    assert service.commit(second.value.token, _context()).is_success
    stale = service.commit(first.value.token, _context())

    assert stale.diagnostics[0].code == "PROJECT_PROVISIONING_STALE"
    assert lifecycle.active is not None
    assert lifecycle.active.project.envelope.data["name"] == "工程二"
    assert [name for name, _ in store.calls] == ["begin", "provision", "commit"]
