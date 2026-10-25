"""Candidate-buffer bridge for legacy AutoTranslator entry adapters."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any
import warnings

from transbridge.application.contracts import OperationResult, RequestContext
from transbridge.application.io.identity import EntryRevision, Provenance
from transbridge.application.io.mutation import CollectionMutationPort
from transbridge.application.io.publish import PublishCommitGuard

from .candidate_checkpoint import TranslationCheckpoint, TranslationCheckpointPort
from .commit import CommitTranslations, CommitTranslationsRequest
from .models import TranslationAction
from .workload_models import (
    CandidateSet,
    CandidateTranslation,
    TranslationBatchOutcome,
    TranslationBatchStatus,
    canonical_hash,
)


@dataclass(frozen=True, slots=True)
class AcceptedCandidateBatch:
    accepted: int
    batch_id: str


class LegacyTranslationCandidateSession:
    """Turn legacy batch responses into durable candidates and one final ChangeSet."""

    def __init__(
        self,
        *,
        run_id: str,
        owner_id: str,
        spec_fingerprint: str,
        input_fingerprint: str,
        checkpoint: TranslationCheckpointPort,
        provider: str,
        model: str,
    ) -> None:
        self.run_id = run_id
        self.owner_id = owner_id
        self.spec_fingerprint = spec_fingerprint
        self.input_fingerprint = input_fingerprint
        self._checkpoint = checkpoint
        self._provider = provider
        self._model = model
        self._lock = threading.Lock()
        self._lookup: _LegacyEntryLookup | None = None
        restored = checkpoint.load(run_id)
        if restored is not None:
            restored.validate(
                owner_id=owner_id,
                spec_fingerprint=spec_fingerprint,
                input_fingerprint=input_fingerprint,
            )
        self._identity = TranslationCheckpoint(run_id, owner_id, spec_fingerprint, input_fingerprint)
        self._revision = restored.revision if restored is not None else 0
        self._completed = set(restored.completed_batch_ids) if restored is not None else set()

    def accept(
        self,
        translations: dict[str, str],
        collection: Any,
    ) -> AcceptedCandidateBatch:
        """Durably accept a legacy response without changing the formal collection."""

        if not translations:
            return AcceptedCandidateBatch(0, canonical_hash({"run_id": self.run_id, "empty": True}))
        with self._lock:
            response_sha256 = canonical_hash(translations)
            batch_id = canonical_hash({
                "run_id": self.run_id,
                "response_sha256": response_sha256,
                "entry_ids": sorted(translations),
            })
            append = getattr(self._checkpoint, "append_batch", None)
            if append is not None:
                self._checkpoint.check_revision(self.run_id, self._revision)
                if batch_id in self._completed:
                    return AcceptedCandidateBatch(0, batch_id)
            else:
                checkpoint = self._load()
                if batch_id in checkpoint.completed_batch_ids:
                    return AcceptedCandidateBatch(0, batch_id)
            if self._lookup is None or self._lookup.revision is None or not self._lookup.matches(collection):
                self._lookup = _LegacyEntryLookup(collection)
            candidates: list[CandidateTranslation] = []
            for entry_id, text in translations.items():
                if not isinstance(text, str) or not text:
                    continue
                entry = self._lookup.get(entry_id)
                if entry is None:
                    continue
                candidates.append(
                    CandidateTranslation(
                        self.run_id,
                        entry.identity,
                        EntryRevision(entry.revision.value),
                        TranslationAction.TRANSLATE,
                        text,
                        batch_id,
                        1,
                        response_sha256,
                        Provenance(
                            self.run_id,
                            self.owner_id,
                            "legacy-auto-translator-v2",
                            metadata=(
                                ("batch_id", batch_id),
                                ("model", self._model),
                                ("provider", self._provider),
                                ("response_sha256", response_sha256),
                            ),
                        ),
                    )
                )
            if not candidates:
                return AcceptedCandidateBatch(0, batch_id)
            if not self._lookup.matches(collection):
                raise ValueError("translation collection changed while resolving candidates")
            outcome = TranslationBatchOutcome(
                batch_id,
                TranslationAction.TRANSLATE,
                TranslationBatchStatus.ACCEPTED,
                tuple(candidate.entry_key for candidate in candidates),
                1,
                "LEGACY_BATCH_ACCEPTED",
                "The legacy translation response was accepted as candidates.",
                response_sha256=response_sha256,
            )
            if append is not None:
                append(self._identity, self._revision, outcome, tuple(candidates))
                self._revision += 1
                self._completed.add(batch_id)
            else:
                self._checkpoint.save(checkpoint.accept_batch(outcome, tuple(candidates)))
            return AcceptedCandidateBatch(len(candidates), batch_id)

    def commit(
        self,
        collection: CollectionMutationPort,
        context: RequestContext,
        guard: PublishCommitGuard,
    ) -> OperationResult[dict]:
        with self._lock:
            checkpoint = self._load()
            candidate_set = CandidateSet(
                self.run_id,
                self.spec_fingerprint,
                self.input_fingerprint,
                checkpoint.candidates,
                checkpoint.outcomes,
            )
            result = CommitTranslations().commit(
                CommitTranslationsRequest(candidate_set, collection, context, guard, self._checkpoint)
            )
            evidence_saved = bool((result.value or {}).get("applied_keys")) and not any(
                item.code == "TRANSLATION_COMMIT_EVIDENCE_FAILED" for item in result.diagnostics
            )
            self._revision = checkpoint.revision + int(evidence_saved)
            self._completed = set(checkpoint.completed_batch_ids)
            return result

    def _load(self) -> TranslationCheckpoint:
        checkpoint = self._checkpoint.load(self.run_id)
        if checkpoint is None:
            return TranslationCheckpoint(
                self.run_id,
                self.owner_id,
                self.spec_fingerprint,
                self.input_fingerprint,
            )
        checkpoint.validate(
            owner_id=self.owner_id,
            spec_fingerprint=self.spec_fingerprint,
            input_fingerprint=self.input_fingerprint,
        )
        return checkpoint


class _LegacyEntryLookup:
    """Read-only response aliases invalidated by the collection's revision."""

    def __init__(self, collection):
        self.collection = collection
        self.revision = getattr(collection, "collection_revision", None)
        self.exact = {}
        self.local = {}
        self.ids = {}
        try:
            entries = iter(collection)
        except TypeError:
            self.exact = None
            return
        for entry in entries:
            identity = entry.identity
            self.exact[identity.serialize()] = identity
            for index, alias in ((self.local, identity.local_key), (self.ids, entry.id)):
                index[alias] = None if alias in index else identity

    def matches(self, collection) -> bool:
        return self.collection is collection and self.revision == getattr(collection, "collection_revision", None)

    def get(self, alias):
        if self.exact is None:
            entry = self.collection.get(alias)
            return entry if entry is not None else self.collection.get_by_id(alias)
        key = self.exact.get(alias)
        if key is None:
            key = self.local.get(alias) if alias in self.local else self.ids.get(alias)
            if key is None and alias in self.local:
                warnings.warn(
                    f"Legacy key {alias!r} is ambiguous across source namespaces", RuntimeWarning, stacklevel=3
                )
                key = self.ids.get(alias)
        return None if key is None else self.collection.get(key)
