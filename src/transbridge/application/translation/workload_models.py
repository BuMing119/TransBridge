"""Immutable AI translation workload, candidate and batch contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any, Protocol

from transbridge.application.contracts import Diagnostic
from transbridge.application.io.identity import EntryKey, EntryRevision, Provenance
from transbridge.application.ports.paratranz import CancellationPort

from .models import TranslationAction, TranslationRunSpec


class TranslationBatchStatus(StrEnum):
    ACCEPTED = "accepted"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RESTORED = "restored"


@dataclass(frozen=True, slots=True)
class TranslationInput:
    entry_key: EntryKey
    revision: EntryRevision
    original: str
    translation: str
    stage: int
    context: str = ""
    terminology_plugin_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.entry_key, EntryKey) or not isinstance(self.revision, EntryRevision):
            raise TypeError("translation input requires EntryKey and EntryRevision")
        if not all(isinstance(value, str) for value in (self.original, self.translation, self.context)):
            raise TypeError("translation input text fields must be strings")
        if isinstance(self.stage, bool) or not isinstance(self.stage, int):
            raise TypeError("translation input stage must be an integer")
        if self.terminology_plugin_id is not None and not self.terminology_plugin_id.strip():
            raise ValueError("terminology plugin ID must be absent or non-empty")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "entry_key": self.entry_key.to_dict(),
            "revision": self.revision.value,
            "original": self.original,
            "translation": self.translation,
            "stage": self.stage,
            "context": self.context,
        }
        if self.terminology_plugin_id is not None:
            payload["terminology_plugin_id"] = self.terminology_plugin_id
        return payload


@dataclass(frozen=True, slots=True)
class TranslationBatchRequest:
    batch_id: str
    action: TranslationAction
    entries: tuple[TranslationInput, ...]
    run_spec: TranslationRunSpec
    round_number: int
    category: str
    quest_id: str = ""

    def __post_init__(self) -> None:
        if not _is_sha256(self.batch_id):
            raise ValueError("translation batch id must be a SHA-256 digest")
        if self.action not in {TranslationAction.TRANSLATE, TranslationAction.POLISH}:
            raise ValueError("translation batches must be translate or polish operations")
        if not self.entries or len({entry.entry_key for entry in self.entries}) != len(self.entries):
            raise ValueError("translation batch entries must be non-empty and unique")
        if self.round_number < 1 or not self.category.strip():
            raise ValueError("translation batch context is invalid")


@dataclass(frozen=True, slots=True)
class TranslationBatchResponse:
    translations: tuple[tuple[EntryKey, str], ...]
    response_sha256: str

    def __post_init__(self) -> None:
        keys = tuple(key for key, _ in self.translations)
        if len(keys) != len(set(keys)):
            raise ValueError("translation response contains duplicate EntryKeys")
        if any(not isinstance(text, str) or not text for _, text in self.translations):
            raise ValueError("translation response texts must be non-empty strings")
        if not _is_sha256(self.response_sha256):
            raise ValueError("translation response summary must be a SHA-256 digest")


class TranslationServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
        response_sha256: str | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.retry_after = retry_after
        self.response_sha256 = response_sha256


class TranslationLlmPort(Protocol):
    def translate(
        self,
        request: TranslationBatchRequest,
        *,
        cancellation: CancellationPort | None = None,
    ) -> TranslationBatchResponse: ...


@dataclass(frozen=True, slots=True)
class CandidateTranslation:
    run_id: str
    entry_key: EntryKey
    before_revision: EntryRevision
    action: TranslationAction
    text: str
    batch_id: str
    attempt: int
    response_sha256: str
    provenance: Provenance
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not self.run_id.strip() or not self.text:
            raise ValueError("candidate run_id and text must not be empty")
        if not _is_sha256(self.batch_id) or not _is_sha256(self.response_sha256):
            raise ValueError("candidate batch and response hashes are invalid")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ValueError("candidate attempt must be a positive integer")
        if self.provenance.run_id != self.run_id:
            raise ValueError("candidate provenance run_id mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "entry_key": self.entry_key.to_dict(),
            "before_revision": self.before_revision.value,
            "action": self.action.value,
            "text": self.text,
            "batch_id": self.batch_id,
            "attempt": self.attempt,
            "response_sha256": self.response_sha256,
            "provenance": self.provenance.to_dict(),
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CandidateTranslation:
        revision = value["before_revision"]
        attempt = value["attempt"]
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (revision, attempt)):
            raise TypeError("candidate revision and attempt must be integers")
        return cls(
            run_id=_string(value["run_id"], "run_id"),
            entry_key=EntryKey.from_dict(value["entry_key"]),
            before_revision=EntryRevision(revision),
            action=TranslationAction(value["action"]),
            text=_string(value["text"], "text"),
            batch_id=_string(value["batch_id"], "batch_id"),
            attempt=attempt,
            response_sha256=_string(value["response_sha256"], "response_sha256"),
            provenance=Provenance.from_dict(value["provenance"]),
            diagnostics=tuple(Diagnostic.from_dict(item) for item in value.get("diagnostics", ())),
        )


@dataclass(frozen=True, slots=True)
class TranslationBatchOutcome:
    batch_id: str
    action: TranslationAction
    status: TranslationBatchStatus
    entry_keys: tuple[EntryKey, ...]
    attempts: int
    code: str
    message: str
    retryable: bool = False
    response_sha256: str | None = None

    def __post_init__(self) -> None:
        if not _is_sha256(self.batch_id) or not self.entry_keys:
            raise ValueError("batch outcome identity is invalid")
        if self.attempts < 0 or not self.code.strip() or not self.message.strip():
            raise ValueError("batch outcome attempts/code/message are invalid")
        if self.response_sha256 is not None and not _is_sha256(self.response_sha256):
            raise ValueError("batch outcome response summary is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "action": self.action.value,
            "status": self.status.value,
            "entry_keys": [key.to_dict() for key in self.entry_keys],
            "attempts": self.attempts,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "response_sha256": self.response_sha256,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TranslationBatchOutcome:
        attempts = value["attempts"]
        retryable = value.get("retryable", False)
        if isinstance(attempts, bool) or not isinstance(attempts, int):
            raise TypeError("batch outcome attempts must be an integer")
        if not isinstance(retryable, bool):
            raise TypeError("batch outcome retryable must be a boolean")
        response_hash = value.get("response_sha256")
        return cls(
            batch_id=_string(value["batch_id"], "batch_id"),
            action=TranslationAction(value["action"]),
            status=TranslationBatchStatus(value["status"]),
            entry_keys=tuple(EntryKey.from_dict(item) for item in value["entry_keys"]),
            attempts=attempts,
            code=_string(value["code"], "code"),
            message=_string(value["message"], "message"),
            retryable=retryable,
            response_sha256=None if response_hash is None else _string(response_hash, "response_sha256"),
        )


@dataclass(frozen=True, slots=True)
class CandidateSet:
    run_id: str
    spec_fingerprint: str
    input_fingerprint: str
    candidates: tuple[CandidateTranslation, ...]
    batch_outcomes: tuple[TranslationBatchOutcome, ...]

    def __post_init__(self) -> None:
        if not self.run_id.strip() or not _is_sha256(self.spec_fingerprint) or not _is_sha256(self.input_fingerprint):
            raise ValueError("candidate set identity is invalid")
        keys = tuple(candidate.entry_key for candidate in self.candidates)
        if len(keys) != len(set(keys)):
            raise ValueError("candidate set contains duplicate EntryKeys")
        if any(candidate.run_id != self.run_id for candidate in self.candidates):
            raise ValueError("candidate run_id does not match its CandidateSet")
        batch_ids = tuple(outcome.batch_id for outcome in self.batch_outcomes)
        if len(batch_ids) != len(set(batch_ids)):
            raise ValueError("candidate set contains duplicate batch outcomes")

    @property
    def fingerprint(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "spec_fingerprint": self.spec_fingerprint,
            "input_fingerprint": self.input_fingerprint,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "batch_outcomes": [outcome.to_dict() for outcome in self.batch_outcomes],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CandidateSet:
        return cls(
            run_id=_string(value["run_id"], "run_id"),
            spec_fingerprint=_string(value["spec_fingerprint"], "spec_fingerprint"),
            input_fingerprint=_string(value["input_fingerprint"], "input_fingerprint"),
            candidates=tuple(CandidateTranslation.from_dict(item) for item in value.get("candidates", ())),
            batch_outcomes=tuple(TranslationBatchOutcome.from_dict(item) for item in value.get("batch_outcomes", ())),
        )


def translation_input_fingerprint(entries: tuple[TranslationInput, ...]) -> str:
    ordered = sorted((entry.to_dict() for entry in entries), key=lambda item: EntryKey.from_dict(item["entry_key"]))
    return canonical_hash(ordered)


def translation_batch_id(
    spec: TranslationRunSpec,
    action: TranslationAction,
    entries: tuple[TranslationInput, ...],
    *,
    round_number: int,
    category: str,
    quest_id: str,
) -> str:
    return canonical_hash({
        "spec_fingerprint": spec.fingerprint,
        "action": action.value,
        "entry_keys": [entry.entry_key.to_dict() for entry in entries],
        "round_number": round_number,
        "category": category,
        "quest_id": quest_id,
    })


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value
