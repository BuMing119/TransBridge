"""Candidate-buffer bridge for legacy AutoTranslator entry adapters."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any

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
        restored = checkpoint.load(run_id)
        if restored is not None:
            restored.validate(
                owner_id=owner_id,
                spec_fingerprint=spec_fingerprint,
                input_fingerprint=input_fingerprint,
            )

    def accept(
        self,
        translations: dict[str, str],
        collection: Any,
    ) -> AcceptedCandidateBatch:
        """Durably accept a legacy response without changing the formal collection."""

        if not translations:
            return AcceptedCandidateBatch(0, canonical_hash({"run_id": self.run_id, "empty": True}))
        with self._lock:
            checkpoint = self._load()
            response_sha256 = canonical_hash(translations)
            batch_id = canonical_hash({
                "run_id": self.run_id,
                "response_sha256": response_sha256,
                "entry_ids": sorted(translations),
            })
            candidates: list[CandidateTranslation] = []
            for entry_id, text in translations.items():
                if not isinstance(text, str) or not text:
                    continue
                entry = collection.get(entry_id)
                if entry is None:
                    entry = collection.get_by_id(entry_id)
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
            advanced = checkpoint.accept_batch(outcome, tuple(candidates))
            if advanced is checkpoint:
                return AcceptedCandidateBatch(0, batch_id)
            self._checkpoint.save(advanced)
            return AcceptedCandidateBatch(len(candidates), batch_id)

    def commit(
        self,
        collection: CollectionMutationPort,
        context: RequestContext,
        guard: PublishCommitGuard,
    ) -> OperationResult[dict]:
        checkpoint = self._load()
        candidate_set = CandidateSet(
            self.run_id,
            self.spec_fingerprint,
            self.input_fingerprint,
            checkpoint.candidates,
            checkpoint.outcomes,
        )
        return CommitTranslations().commit(
            CommitTranslationsRequest(
                candidate_set,
                collection,
                context,
                guard,
                self._checkpoint,
            )
        )

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
