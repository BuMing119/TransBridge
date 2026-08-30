"""Immutable terminology snapshots captured for one AI run."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
    EffectiveTermResolution,
    TerminologyLookupContext,
    resolve_snapshot,
)
from transbridge.application.terminology.identity import canonical_digest
from transbridge.application.terminology.models import TermDecision


class TerminologyRunSnapshotError(RuntimeError):
    """The exact terminology snapshot required by an AI run is unsafe to use."""


class TerminologyRunSnapshotSource(Protocol):
    def snapshot(
        self,
        local_project_id: str,
        local_variant_id: str,
        version_id: str | None = None,
    ) -> EffectiveTerminologySnapshot: ...


@dataclass(frozen=True, slots=True)
class TerminologyRunSnapshotRef:
    local_project_id: str
    local_variant_id: str
    status: EffectiveSnapshotStatus
    version_id: str | None
    content_digest: str | None
    snapshot_identity: str
    captured_at: str

    def __post_init__(self) -> None:
        if not self.local_project_id.strip() or not self.local_variant_id.strip():
            raise ValueError("terminology run snapshot requires local Project and Variant identities")
        object.__setattr__(self, "status", EffectiveSnapshotStatus(self.status))
        if not self.snapshot_identity.strip() or not self.captured_at.strip():
            raise ValueError("terminology run snapshot identity and capture time must not be empty")
        expected = _snapshot_identity(
            self.local_project_id,
            self.local_variant_id,
            self.status,
            self.version_id,
            self.content_digest,
        )
        if self.snapshot_identity != expected:
            raise ValueError("terminology run snapshot identity does not match its version fields")
        if self.status is EffectiveSnapshotStatus.READY:
            if not self.version_id or not self.content_digest:
                raise ValueError("ready terminology run snapshot requires version identity and content digest")
        elif self.version_id is not None or self.content_digest is not None:
            raise ValueError("non-ready terminology run snapshot cannot carry version identity")

    def metadata(self) -> tuple[tuple[str, str], ...]:
        values = [
            ("terminology_project_id", self.local_project_id),
            ("terminology_variant_id", self.local_variant_id),
            ("terminology_status", self.status.value),
            ("terminology_snapshot_identity", self.snapshot_identity),
            ("terminology_captured_at", self.captured_at),
        ]
        if self.version_id is not None:
            values.append(("terminology_version_id", self.version_id))
        if self.content_digest is not None:
            values.append(("terminology_content_digest", self.content_digest))
        return tuple(values)

    def to_dict(self) -> dict[str, str | None]:
        return {
            "local_project_id": self.local_project_id,
            "local_variant_id": self.local_variant_id,
            "status": self.status.value,
            "version_id": self.version_id,
            "content_digest": self.content_digest,
            "snapshot_identity": self.snapshot_identity,
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TerminologyRunSnapshotRef:
        return cls(
            local_project_id=str(value["local_project_id"]),
            local_variant_id=str(value["local_variant_id"]),
            status=EffectiveSnapshotStatus(str(value["status"])),
            version_id=None if value.get("version_id") is None else str(value["version_id"]),
            content_digest=None if value.get("content_digest") is None else str(value["content_digest"]),
            snapshot_identity=str(value["snapshot_identity"]),
            captured_at=str(value["captured_at"]),
        )


@dataclass(frozen=True, slots=True)
class FrozenTerminologyRunSnapshot:
    ref: TerminologyRunSnapshotRef
    decisions: tuple[TermDecision, ...] = ()
    decisions_digest: str = field(init=False)

    def __post_init__(self) -> None:
        decisions = tuple(sorted(self.decisions, key=lambda item: item.term_id))
        object.__setattr__(self, "decisions", decisions)
        if any(
            (item.project_id, item.variant_id) != (self.ref.local_project_id, self.ref.local_variant_id)
            for item in decisions
        ):
            raise ValueError("frozen terminology decisions belong to another Project/Variant")
        if self.ref.status is not EffectiveSnapshotStatus.READY and decisions:
            raise ValueError("non-ready terminology run snapshots cannot contain decisions")
        object.__setattr__(self, "decisions_digest", _decisions_digest(decisions))

    def verify(self) -> None:
        if _decisions_digest(self.decisions) != self.decisions_digest:
            raise TerminologyRunSnapshotError("frozen terminology snapshot decision digest mismatch")

    def effective_snapshot(self) -> EffectiveTerminologySnapshot:
        self.verify()
        return EffectiveTerminologySnapshot(
            self.ref.local_project_id,
            self.ref.local_variant_id,
            self.ref.status,
            version_id=self.ref.version_id,
            content_digest=self.ref.content_digest,
            decisions=self.decisions,
        )


class TerminologyRunSnapshotFactory:
    """Capture or restore one exact effective snapshot without following current later."""

    def __init__(
        self,
        source: TerminologyRunSnapshotSource,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._source = source
        self._now = now or (lambda: datetime.now(UTC))

    def freeze(self, local_project_id: str, local_variant_id: str) -> FrozenTerminologyRunSnapshot:
        snapshot = self._read(local_project_id, local_variant_id)
        return self._from_snapshot(snapshot, captured_at=self._now().astimezone(UTC).isoformat())

    def restore(self, ref: TerminologyRunSnapshotRef) -> FrozenTerminologyRunSnapshot:
        snapshot = self._read(ref.local_project_id, ref.local_variant_id, ref.version_id)
        if snapshot.status is not ref.status:
            raise TerminologyRunSnapshotError("terminology snapshot status changed while restoring the run")
        if snapshot.version_id != ref.version_id or snapshot.content_digest != ref.content_digest:
            raise TerminologyRunSnapshotError("exact terminology version or content digest is unavailable")
        if snapshot.snapshot_identity != ref.snapshot_identity:
            raise TerminologyRunSnapshotError("restored terminology snapshot identity mismatch")
        return self._from_snapshot(snapshot, captured_at=ref.captured_at)

    def _read(
        self,
        local_project_id: str,
        local_variant_id: str,
        version_id: str | None = None,
    ) -> EffectiveTerminologySnapshot:
        try:
            snapshot = self._source.snapshot(local_project_id, local_variant_id, version_id)
        except Exception as exc:
            raise TerminologyRunSnapshotError("effective terminology snapshot could not be read") from exc
        if (snapshot.local_project_id, snapshot.local_variant_id) != (local_project_id, local_variant_id):
            raise TerminologyRunSnapshotError("effective terminology source returned another Project/Variant")
        if version_id is not None and snapshot.version_id != version_id:
            raise TerminologyRunSnapshotError("effective terminology source returned another version")
        if snapshot.status in {EffectiveSnapshotStatus.UNAVAILABLE, EffectiveSnapshotStatus.CORRUPT}:
            diagnostic = "; ".join(snapshot.diagnostics) or snapshot.status.value
            raise TerminologyRunSnapshotError(
                f"effective terminology snapshot is {snapshot.status.value}: {diagnostic}"
            )
        if snapshot.status is EffectiveSnapshotStatus.READY and snapshot.snapshot_identity != _snapshot_identity(
            local_project_id,
            local_variant_id,
            snapshot.status,
            snapshot.version_id,
            snapshot.content_digest,
        ):
            raise TerminologyRunSnapshotError("effective terminology snapshot identity is inconsistent")
        return snapshot

    @staticmethod
    def _from_snapshot(snapshot: EffectiveTerminologySnapshot, *, captured_at: str) -> FrozenTerminologyRunSnapshot:
        ref = TerminologyRunSnapshotRef(
            snapshot.local_project_id,
            snapshot.local_variant_id,
            snapshot.status,
            snapshot.version_id,
            snapshot.content_digest,
            snapshot.snapshot_identity,
            captured_at,
        )
        return FrozenTerminologyRunSnapshot(ref, snapshot.decisions)


class FrozenEffectiveTerminologyPort:
    """Resolve solely from the immutable snapshot captured by an AI run."""

    def __init__(self, frozen: FrozenTerminologyRunSnapshot) -> None:
        frozen.verify()
        self._frozen = frozen

    def snapshot(
        self,
        local_project_id: str,
        local_variant_id: str,
        version_id: str | None = None,
    ) -> EffectiveTerminologySnapshot:
        ref = self._frozen.ref
        if (local_project_id, local_variant_id) != (ref.local_project_id, ref.local_variant_id):
            raise TerminologyRunSnapshotError("frozen terminology snapshot belongs to another Project/Variant")
        if version_id is not None and version_id != ref.version_id:
            raise TerminologyRunSnapshotError("requested terminology version does not match the frozen run")
        return self._frozen.effective_snapshot()

    def resolve(self, term: str, context: TerminologyLookupContext) -> EffectiveTermResolution:
        snapshot = self.snapshot(context.local_project_id, context.local_variant_id, context.version_id)
        return resolve_snapshot(snapshot, term, context)


def _snapshot_identity(
    project_id: str,
    variant_id: str,
    status: EffectiveSnapshotStatus,
    version_id: str | None,
    content_digest: str | None,
) -> str:
    version = version_id or status.value
    digest = content_digest or status.value
    return f"{project_id}:{variant_id}:{version}:{digest}"


def _decisions_digest(decisions: tuple[TermDecision, ...]) -> str:
    return canonical_digest(decisions, namespace="terminology.ai-run-decisions.v1")


__all__ = [
    "FrozenEffectiveTerminologyPort",
    "FrozenTerminologyRunSnapshot",
    "TerminologyRunSnapshotError",
    "TerminologyRunSnapshotFactory",
    "TerminologyRunSnapshotRef",
    "TerminologyRunSnapshotSource",
]
