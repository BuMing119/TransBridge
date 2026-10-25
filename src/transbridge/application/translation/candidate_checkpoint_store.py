"""Incremental candidate state, schema migration, and journal compaction."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import secrets

from . import candidate_checkpoint_journal as journal
from .candidate_checkpoint import TranslationCheckpoint
from .workload_models import CandidateTranslation, TranslationBatchOutcome

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CandidateBatchUpdate:
    """Internal save request retaining the observable synchronous save boundary."""

    identity: TranslationCheckpoint
    expected_revision: int
    outcome: TranslationBatchOutcome
    candidates: tuple[CandidateTranslation, ...]

    @property
    def run_id(self) -> str:
        return self.identity.run_id

    @property
    def committed_candidate_ids(self) -> frozenset[str]:
        return self.identity.committed_candidate_ids


class _State:
    def __init__(self, checkpoint: TranslationCheckpoint):
        self.identity = checkpoint
        self.revision = checkpoint.revision
        self.candidates = {item.entry_key: item for item in checkpoint.candidates}
        self.outcomes = list(checkpoint.outcomes)
        self.completed = set(checkpoint.completed_batch_ids)
        self.committed = set(checkpoint.committed_candidate_ids)
        self._snapshot = checkpoint

    def snapshot(self) -> TranslationCheckpoint:
        if self._snapshot is None:
            self._snapshot = TranslationCheckpoint(
                self.identity.run_id,
                self.identity.owner_id,
                self.identity.spec_fingerprint,
                self.identity.input_fingerprint,
                self.revision,
                frozenset(self.completed),
                frozenset(self.committed),
                tuple(sorted(self.candidates.values(), key=lambda item: item.entry_key)),
                tuple(self.outcomes),
            )
        return self._snapshot

    def batch_record(self, update: CandidateBatchUpdate) -> dict:
        self.validate_identity(update.identity)
        if self.revision != update.expected_revision:
            raise ValueError("translation checkpoint revision changed before batch acceptance")
        if update.outcome.batch_id in self.completed:
            raise ValueError("translation checkpoint batch was already accepted")
        candidates = tuple({item.entry_key: item for item in update.candidates}.values())
        # Validate only the new batch, using the same model checks as snapshots.
        TranslationCheckpoint(
            update.run_id,
            update.identity.owner_id,
            update.identity.spec_fingerprint,
            update.identity.input_fingerprint,
            candidates=candidates,
        )
        record = {
            "kind": "batch",
            "previous_revision": self.revision,
            "revision": self.revision + 1,
            "candidates": [item.to_dict() for item in candidates],
            "outcome": update.outcome.to_dict(),
        }
        # Match the former snapshot round-trip validation before publication.
        for item in record["candidates"]:
            CandidateTranslation.from_dict(item)
        TranslationBatchOutcome.from_dict(record["outcome"])
        return record

    def validate_identity(self, checkpoint: TranslationCheckpoint) -> None:
        if checkpoint.run_id != self.identity.run_id:
            raise ValueError("translation checkpoint run_id mismatch")
        self.identity.validate(
            owner_id=checkpoint.owner_id,
            spec_fingerprint=checkpoint.spec_fingerprint,
            input_fingerprint=checkpoint.input_fingerprint,
        )

    def apply(self, record: dict) -> None:
        revision = record.get("revision")
        if (
            record.get("previous_revision") != self.revision
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision <= self.revision
        ):
            raise ValueError("translation checkpoint journal revision is invalid")
        if record.get("kind") == "batch":
            candidates = tuple(CandidateTranslation.from_dict(item) for item in record["candidates"])
            outcome = TranslationBatchOutcome.from_dict(record["outcome"])
            self.batch_record(CandidateBatchUpdate(self.identity, self.revision, outcome, candidates))
            if revision != self.revision + 1:
                raise ValueError("translation checkpoint batch revision is invalid")
            self.candidates.update((item.entry_key, item) for item in candidates)
            self.outcomes.append(outcome)
            self.completed.add(outcome.batch_id)
        elif record.get("kind") == "committed":
            values = record.get("ids")
            if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError("translation checkpoint commit ids are invalid")
            self.committed.update(values)
        else:
            raise ValueError("translation checkpoint journal record kind is invalid")
        self.revision = revision
        self._snapshot = None


@dataclass
class _Cached:
    state: _State
    head: dict | None
    signature: tuple


class CandidateCheckpointStore:
    """A port-owned cache; each write is conditional under an OS storage lease."""

    COMPACT_MIN_RECORDS = 128

    def __init__(self, root: Path, path_for_run):
        self.root = root
        self.path_for_run = path_for_run
        self._cache: dict[str, _Cached] = {}

    def load(self, run_id: str) -> TranslationCheckpoint | None:
        target = self.path_for_run(run_id)
        with journal.storage_lease(target):
            cached = self._load(target, run_id)
            return None if cached is None else cached.state.snapshot()

    def load_readonly(self, run_id: str) -> TranslationCheckpoint | None:
        """Inspect a durable generation without creating directories or lock files.

        Recovery discovery never owns an executing workload. If a writer replaces
        the head while it is being inspected, retry the complete generation;
        corruption in a stable generation remains an error, not empty progress.
        """
        target = self.path_for_run(run_id)
        for _ in range(3):
            before = journal.signature(target)
            self._cache.pop(run_id, None)
            try:
                cached = self._load(target, run_id)
            except (OSError, ValueError):
                if journal.signature(target) == before:
                    raise
                continue
            if journal.signature(target) == before:
                return None if cached is None else cached.state.snapshot()
        self._cache.pop(run_id, None)
        raise ValueError("translation checkpoint changed while recovery was reading it")

    def check_revision(self, run_id: str, expected_revision: int) -> None:
        target = self.path_for_run(run_id)
        with journal.storage_lease(target):
            cached = self._load(target, run_id)
            actual = 0 if cached is None else cached.state.revision
            if actual != expected_revision:
                raise ValueError("translation checkpoint revision changed before batch acceptance")

    def _signature(self, target: Path, head: dict | None) -> tuple:
        return (
            journal.signature(target),
            None if head is None else journal.signature(journal.journal_path(target, head)),
        )

    def _load(self, target: Path, run_id: str) -> _Cached | None:
        cached = self._cache.get(run_id)
        if cached is not None and self._signature(target, cached.head) == cached.signature:
            return cached
        if not target.exists():
            if cached is not None:
                raise ValueError("translation checkpoint was removed outside this port")
            return None
        value = journal.read_object(target)
        head = None
        try:
            if value.get("schema_version") == 2:
                head = value
                records = iter(journal.read_records(target, head))
                first = next(records)
                if first.get("kind") != "snapshot":
                    raise ValueError("translation checkpoint journal must begin with a snapshot")
                state = _State(TranslationCheckpoint.from_dict(first["checkpoint"]))
                count = 1
                for record in records:
                    state.apply(record)
                    count += 1
                if self._identity(state) != head.get("identity") or state.revision != head.get("revision"):
                    raise ValueError("translation checkpoint journal identity/revision mismatch")
                if count != head.get("records"):
                    raise ValueError("translation checkpoint journal record count mismatch")
            else:
                state = _State(TranslationCheckpoint.from_dict(value))
        except (KeyError, TypeError, StopIteration) as exc:
            raise ValueError("translation checkpoint is corrupt") from exc
        if state.identity.run_id != run_id:
            raise ValueError("translation checkpoint run_id mismatch")
        if cached is not None:
            cached.state.validate_identity(state.identity)
            if state.revision <= cached.state.revision:
                raise ValueError("translation checkpoint changed outside this port without advancing its revision")
        cached = _Cached(state, head, self._signature(target, head))
        self._cache[run_id] = cached
        return cached

    def save(self, checkpoint: TranslationCheckpoint | CandidateBatchUpdate) -> None:
        target = self.path_for_run(checkpoint.run_id)
        with journal.storage_lease(target):
            cached = self._load(target, checkpoint.run_id)
            identity = checkpoint.identity if isinstance(checkpoint, CandidateBatchUpdate) else checkpoint
            state = (
                cached.state
                if cached
                else _State(
                    TranslationCheckpoint(
                        identity.run_id, identity.owner_id, identity.spec_fingerprint, identity.input_fingerprint
                    )
                )
            )
            state.validate_identity(identity)
            if isinstance(checkpoint, CandidateBatchUpdate):
                record = state.batch_record(checkpoint)
            else:
                if cached is not None and checkpoint.revision <= state.revision:
                    raise ValueError("translation checkpoint revision did not advance")
                previous = state.snapshot()
                if (
                    cached is not None
                    and checkpoint.candidates == previous.candidates
                    and checkpoint.outcomes == previous.outcomes
                    and checkpoint.completed_batch_ids == previous.completed_batch_ids
                    and checkpoint.committed_candidate_ids >= previous.committed_candidate_ids
                ):
                    if any(
                        not isinstance(item, str) or not item.strip() for item in checkpoint.committed_candidate_ids
                    ):
                        raise ValueError("translation checkpoint commit ids are invalid")
                    record = {
                        "kind": "committed",
                        "previous_revision": state.revision,
                        "revision": checkpoint.revision,
                        "ids": sorted(checkpoint.committed_candidate_ids - previous.committed_candidate_ids),
                    }
                else:
                    self._replace(target, _State(checkpoint), cached)
                    return
            head = cached.head if cached else None
            if head is None or self._needs_compaction(head):
                # A full copy occurs only at migration/compaction, never per batch.
                advanced = _State(state.snapshot())
                advanced.apply(record)
                self._replace(target, advanced, cached)
                return
            try:
                offset, digest = journal.write_record(
                    journal.journal_path(target, head), record, offset=head["offset"], previous=head["sha256"]
                )
                advanced_head = {
                    **head,
                    "revision": record["revision"],
                    "offset": offset,
                    "sha256": digest,
                    "records": head["records"] + 1,
                }
                journal.publish_head(target, advanced_head)
                state.apply(record)
                self._cache[checkpoint.run_id] = _Cached(state, advanced_head, self._signature(target, advanced_head))
            except Exception:
                self._cache.pop(checkpoint.run_id, None)
                raise

    def _needs_compaction(self, head: dict) -> bool:
        return head["records"] >= self.COMPACT_MIN_RECORDS and head["offset"] >= 2 * head["snapshot_bytes"]

    @staticmethod
    def _identity(state: _State) -> dict:
        identity = state.identity
        return {
            "run_id": identity.run_id,
            "owner_id": identity.owner_id,
            "spec_fingerprint": identity.spec_fingerprint,
            "input_fingerprint": identity.input_fingerprint,
        }

    def _replace(self, target: Path, state: _State, old: _Cached | None) -> None:
        path = target.with_name(f"{target.stem}-{secrets.token_hex(16)}.jsonl")
        published = False
        try:
            payload = state.snapshot().to_dict()
            TranslationCheckpoint.from_dict(payload)
            offset, digest = journal.write_record(path, {"kind": "snapshot", "checkpoint": payload})
            head = {
                "schema_version": 2,
                "identity": self._identity(state),
                "revision": state.revision,
                "journal": path.name,
                "offset": offset,
                "sha256": digest,
                "records": 1,
                "snapshot_bytes": offset,
            }
            # The log's directory entry must be durable before its head can refer to it.
            journal.fsync_directory(target.parent)
            journal.publish_head(target, head)
            published = True
            self._cache[state.identity.run_id] = _Cached(state, head, self._signature(target, head))
        except Exception:
            self._cache.pop(state.identity.run_id, None)
            # An exception after replace/fsync may already have published the head.
            current = journal.read_object(target) if target.exists() else {}
            if current.get("journal") != path.name:
                path.unlink(missing_ok=True)
            raise
        if published and old is not None and old.head is not None:
            try:
                journal.journal_path(target, old.head).unlink(missing_ok=True)
            except OSError:
                _logger.warning(
                    "Obsolete translation checkpoint journal could not be removed: %s", target, exc_info=True
                )
