"""Strict, atomic publication of one plugin inside a disposable FOMOD workspace."""

from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from transbridge.application.contracts import OperationOutcome, OperationResult, RequestContext
from transbridge.application.io import (
    EntryKey,
    FormatId,
    ParseRequest,
    SourceDescriptor,
    SourceNamespace,
    SsePluginAdapter,
)
from transbridge.application.io.contracts import SourceSnapshot
from transbridge.application.io.mutation import EntrySnapshot
from transbridge.application.io.operation_write import (
    HydratedWriteDraft,
    HydratedWritePreflightService,
    HydratedWriteWorkload,
    _translation_entries,
)
from transbridge.application.io.plugin_write import (
    _blocking_stage_diagnostics,
    _parser_from_snapshot,
    _snapshot_localized_sources,
)
from transbridge.application.io.publish import (
    BackupPolicy,
    ConflictPolicy,
    FileFingerprint,
    ImmediateCommitGuard,
    OsPublishFilesystem,
)
from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY
from transbridge.application.tasks import TaskCancelled


@dataclass(frozen=True, slots=True)
class FomodPluginSource:
    snapshot: SourceSnapshot
    entries: tuple[EntrySnapshot, ...]
    fingerprints: tuple[tuple[str, str, FileFingerprint], ...]

    def collection_entries(self):
        return _translation_entries(self.entries)


def prepare_plugin_source(path, *, namespace: SourceNamespace | None = None, cancellation=None) -> FomodPluginSource:
    """Capture the new plugin before migration/TM/AI can change its collection."""
    token = _CancellationToken(cancellation)
    token.raise_if_cancelled()
    source = Path(path)
    fs = OsPublishFilesystem()
    requested_paths = {str(source)} | {
        str(source.parent / "Strings" / f"{source.stem}_English.{suffix}")
        for suffix in ("strings", "dlstrings", "ilstrings")
    }
    baseline = {}
    for requested in sorted(requested_paths):
        canonical = fs.canonicalize(requested)
        baseline[requested] = (canonical, fs.fingerprint(canonical))
    parsed = SsePluginAdapter().parse(
        ParseRequest(
            SourceDescriptor(str(source), source.name),
            RequestContext("fomod-pipeline"),
            FormatId.PLUGIN_SSE,
            namespace,
            options=(("language", "english"), ("skip_empty", False)),
            cancellation=token,
        )
    )
    if parsed.outcome is OperationOutcome.CANCELLED:
        raise TaskCancelled("FOMOD plugin source capture cancelled")
    if parsed.outcome is not OperationOutcome.COMPLETED or parsed.source_snapshot is None:
        codes = ",".join(item.code for item in parsed.diagnostics)
        raise RuntimeError(f"FOMOD_PLUGIN_PARSE_FAILED:{codes}")
    snapshot = parsed.source_snapshot
    if dict(snapshot.metadata).get("localized_lookup_detected") and not _snapshot_localized_sources(snapshot):
        raise RuntimeError(
            "FOMOD_PLUGIN_LOCALIZED_SNAPSHOT_UNAVAILABLE:localized Strings cannot be captured for writing"
        )
    # Reconstruct from captured bytes, rather than trusting entries read before
    # companion Strings snapshots were captured by the format adapter.
    _parser, entries = _parser_from_snapshot(snapshot, {})
    frozen_namespace = SourceNamespace(dict(snapshot.metadata)["source_namespace"])
    snapshots = tuple(
        replace(entry, entry_key=EntryKey(frozen_namespace, entry.key, entry.identity.original)).snapshot()
        for entry in entries
    )
    inputs = (snapshot, *(item for item, _entries in _snapshot_localized_sources(snapshot)))
    captured = {fs.canonicalize(item.source.uri): item for item in inputs}
    if not set(captured).issubset(path for path, _fingerprint in baseline.values()):
        raise RuntimeError("FOMOD_PLUGIN_SOURCE_CHANGED:captured sources differ from the initial source paths")
    fingerprints = []
    for requested, (path, fingerprint) in baseline.items():
        item = captured.get(path)
        if (
            fs.canonicalize(requested) != path
            or fs.fingerprint(path) != fingerprint
            or (item is not None and fingerprint.sha256 != item.sha256)
        ):
            raise RuntimeError("FOMOD_PLUGIN_SOURCE_CHANGED:source changed while its snapshot was captured")
        fingerprints.append((requested, path, fingerprint))
    token.raise_if_cancelled()
    return FomodPluginSource(snapshot, snapshots, tuple(fingerprints))


def publish_plugin_collection(
    path, collection, *, source: FomodPluginSource | None = None, cancellation=None, run_id: str | None = None
) -> OperationResult:
    """Publish the exact new-plugin collection; unmatched proposals are errors.

    This directory is disposable extracted input, not a user's original plugin.
    The original archive remains the recovery source. No persistent backup or
    diagnostic artifact is placed here, because every file may enter the package.
    """
    token = _CancellationToken(cancellation)
    token.raise_if_cancelled()
    prepared = source or prepare_plugin_source(path, cancellation=cancellation)
    fs = OsPublishFilesystem()
    if fs.canonicalize(str(path)) != fs.canonicalize(prepared.snapshot.source.uri):
        raise RuntimeError("FOMOD_PLUGIN_SOURCE_MISMATCH:write target is not the captured new plugin")
    _verify_sources(prepared, fs)
    entries = tuple(item.snapshot() for item in collection)
    expected = {item.entry_key: item for item in prepared.entries}
    proposed = {item.entry_key: item for item in entries}
    if len(proposed) != len(entries) or set(proposed) != set(expected):
        raise RuntimeError("FOMOD_PLUGIN_IDENTITY_MISMATCH:collection does not exactly cover the captured new plugin")
    for key, item in proposed.items():
        original = expected[key]
        if (item.original, item.string_id, item.requires_original_match) != (
            original.original,
            original.string_id,
            original.requires_original_match,
        ):
            raise RuntimeError("FOMOD_PLUGIN_LOCATOR_MISMATCH:source text or localized locator changed")
    if not entries:
        return OperationResult.completed(run_id=run_id)
    publication_id = run_id or uuid4().hex
    draft = HydratedWriteDraft(
        prepared.snapshot,
        FormatId.PLUGIN_SSE,
        entries,
        str(path),
        collection.collection_revision.value,
        RequestContext("fomod-pipeline", run_id=publication_id),
        ConflictPolicy.EXPLICIT_OVERWRITE,
        BackupPolicy.NONE,
        options=(("language", "english"),),
    )
    checked = HydratedWritePreflightService().preflight(draft)
    if not checked.ready:
        codes = ",".join(item.code for item in checked.checks if not item.passed and not item.warning)
        stage_codes = ",".join(
            item.code for item in _blocking_stage_diagnostics(_translation_entries(entries), DEFAULT_STAGE_POLICY)
        )
        if stage_codes:
            codes = f"{codes},{stage_codes}"
        raise RuntimeError(f"FOMOD_PLUGIN_PREFLIGHT_FAILED:{codes}")
    guard = _SourceCommitGuard(prepared, fs, token, publication_id)
    result = HydratedWriteWorkload(checked)(
        SimpleNamespace(
            ref=SimpleNamespace(run_id=publication_id),
            cancellation=token,
            publish_commit_guard=lambda: guard,
        )
    )
    if result.outcome is OperationOutcome.CANCELLED or token.is_cancelled:
        raise TaskCancelled("FOMOD plugin publication cancelled")
    if guard.failure is not None:
        raise RuntimeError(f"FOMOD_PLUGIN_WRITE_FAILED:{guard.failure}") from guard.failure
    if result.outcome is not OperationOutcome.COMPLETED:
        diagnostics = "; ".join(f"{item.code}: {item.message}" for item in result.diagnostics)
        raise RuntimeError(f"FOMOD_PLUGIN_WRITE_FAILED:{diagnostics}")
    return result


def _verify_sources(source: FomodPluginSource, fs: OsPublishFilesystem) -> None:
    for requested, path, fingerprint in source.fingerprints:
        if fs.canonicalize(requested) != path or fs.fingerprint(path) != fingerprint:
            raise RuntimeError("FOMOD_PLUGIN_SOURCE_CHANGED:new plugin or Strings changed after capture")


class _SourceCommitGuard:
    def __init__(self, source, filesystem, token, run_id):
        self._source = source
        self._filesystem = filesystem
        self._token = token
        self._delegate = ImmediateCommitGuard(run_id, active=lambda: not token.is_cancelled)
        self.failure: RuntimeError | None = None

    def commit(self, run_id, mutation):
        def commit():
            self._token.raise_if_cancelled()
            try:
                _verify_sources(self._source, self._filesystem)
            except RuntimeError as exc:
                self.failure = exc
                raise
            mutation()

        return self._delegate.commit(run_id, commit)


class _CancellationToken:
    def __init__(self, signal):
        self._signal = signal

    @property
    def is_cancelled(self):
        state = getattr(self._signal, "is_cancelled", None)
        if state is not None:
            return bool(state() if callable(state) else state)
        state = getattr(self._signal, "is_set", None)
        return bool(state()) if callable(state) else False

    def raise_if_cancelled(self):
        if self.is_cancelled:
            raise TaskCancelled("FOMOD plugin publication cancelled")
