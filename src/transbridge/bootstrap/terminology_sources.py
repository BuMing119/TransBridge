"""Terminology leases over the project's stored translation entries."""

from contextlib import closing
from dataclasses import replace

from transbridge.application.contracts import DomainError, ErrorCategory, OperationOutcome
from transbridge.application.io import ParseResult, ParseStats, SourceDescriptor, SourceNamespace, SourceSnapshot
from transbridge.application.projects.source_content import source_content_identity
from transbridge.application.projects.source_registry import SourceRegistrySnapshot
from transbridge.application.terminology.input_capture import BuildInputSnapshot, SourceLease
from transbridge.persistence.project_store.records import WorkspaceProjects


def lightweight_build_snapshot(snapshot: BuildInputSnapshot) -> BuildInputSnapshot:
    """Keep only the lease identity needed for the commit guard."""
    sources = tuple(
        replace(
            source,
            lease=replace(
                source.lease,
                saved_result=None,
                snapshot=replace(
                    source.lease.snapshot,
                    content=None,
                    metadata=(),
                    lease_id=source.lease.snapshot.lease_id or "project-translation-content",
                ),
            ),
        )
        for source in snapshot.sources
    )
    return replace(snapshot, sources=sources)


class ProjectSourceLeases:
    def __init__(self, root, lifecycle) -> None:
        self._projects = WorkspaceProjects(root)
        self._lifecycle = lifecycle

    def acquire(self, registration) -> SourceLease:
        return self.acquire_bounded(registration, max_bytes=None)

    def acquire_bounded(self, registration, *, max_bytes: int | None) -> SourceLease:
        active = self._lifecycle.active
        identity = source_content_identity(registration.to_dict())
        if active is None or active.formal_variant_ref is None or identity is None:
            raise _unavailable(registration)
        store = self._projects.store_for(active.project_ref.identity)
        variant = active.formal_variant_ref.identity
        namespace = SourceNamespace(identity)
        before = store.summary()
        info = next((item for item in store.sources() if item.namespace == namespace), None)
        registry = SourceRegistrySnapshot.from_project_data(active.project.envelope.data)
        folded = next(
            (
                relation
                for relation in registry.relations
                if relation.from_source_id == registration.source_id and relation.folded_into_baseline
            ),
            None,
        )
        if (
            info is None
            or registration.fingerprint is None
            or (folded is None and info.fingerprint != registration.fingerprint)
        ):
            raise _unavailable(registration)
        # A folded source has no independent translation rows. Its primary
        # registration already supplies the Variant's canonical content.
        rows, size_bytes = (
            ((), 0) if folded is not None else _read_rows(store, variant, namespace, registration.source_id, max_bytes)
        )
        if store.summary() != before:
            raise DomainError(
                ErrorCategory.CONFLICT, "TERMINOLOGY_SOURCE_CHANGED", "项目来源正在变化，请重新捕获术语输入。"
            )
        descriptor = SourceDescriptor(f"project-source:{identity}", registration.display_name or info.name, size_bytes)
        snapshot = SourceSnapshot(
            descriptor,
            info.format_id,
            registration.fingerprint,
            size_bytes,
            lease_id="project-translation-content",
            metadata=(("source_namespace", identity),),
        )
        result = ParseResult(
            OperationOutcome.COMPLETED,
            info.format_id,
            descriptor,
            snapshot,
            rows,
            (),
            ParseStats(parsed=len(rows)),
        )
        return SourceLease(registration.source_id, snapshot, snapshot.sha256, result)

    def current_fingerprint(self, lease: SourceLease) -> str:
        active = self._lifecycle.active
        if active is None:
            return ""
        registry = SourceRegistrySnapshot.from_project_data(active.project.envelope.data)
        registration = next((item for item in registry.sources if item.source_id == lease.source_id), None)
        if registration is None or registration.content_state != "translation":
            return ""
        return registration.fingerprint or ""


def _read_rows(store, variant, namespace, source_id, max_bytes):
    rows, size_bytes = [], 0
    with closing(store.iter_source_entries(variant, namespace)) as entries:
        for item in entries:
            entry = item.snapshot
            size_bytes += sum(
                len(value.encode("utf-8", errors="surrogatepass"))
                for value in (entry.original, entry.context or "", item.state.translation)
            )
            if max_bytes is not None and size_bytes > max_bytes:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "TERMINOLOGY_STREAMING_REQUIRED",
                    "已保存的翻译来源超过本次术语构建的读取上限。",
                    details={"source_id": source_id, "limit_bytes": max_bytes},
                )
            rows.append(replace(entry, translation=item.state.translation, stage=item.state.stage.value))
    return tuple(rows), size_bytes


def _unavailable(registration):
    return DomainError(
        ErrorCategory.PREREQUISITE,
        "TERMINOLOGY_SOURCE_REQUIRED",
        "当前版本没有可读取的已保存翻译来源。",
        details={"source_id": registration.source_id},
    )
