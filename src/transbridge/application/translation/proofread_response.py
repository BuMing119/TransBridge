"""Parse and validate one Proofread model response without scheduling retries."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re

from jsonschema import Draft202012Validator

from transbridge.ai_translator.structured_schemas import PROOFREAD_OUTPUT_SCHEMA
from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.application.io import EntryKey
from transbridge.converter.translation_entry import STAGE_QUESTIONABLE
from transbridge.infra.structured_validation_details import schema_validation_details

from .postprocess import PostProcessCandidate
from .proofread_advisory import REVIEW_CONTRACT_VERSION, REVIEW_STATUSES
from .protected_syntax import protected_syntax_matches

_FENCED_JSON_RE = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.IGNORECASE | re.DOTALL)
_THINK_PREFIX_RE = re.compile(r"\A(?:<think>.*?</think>\s*)+", re.IGNORECASE | re.DOTALL)
_ITEM_VALIDATOR = Draft202012Validator(PROOFREAD_OUTPUT_SCHEMA.schema["properties"]["results"]["items"])


@dataclass(frozen=True, slots=True)
class ProofreadResponseResult:
    candidates: tuple[PostProcessCandidate, ...]
    diagnostics: tuple[Diagnostic, ...]
    structurally_malformed: bool = False


def apply_proofread_response(
    candidates: tuple[PostProcessCandidate, ...],
    response: object,
    *,
    phase: str,
) -> ProofreadResponseResult:
    """Apply a response after removing only complete, non-semantic wrappers."""

    payload = _decode_payload(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return ProofreadResponseResult(
            tuple(candidate.with_accepted(False) for candidate in candidates),
            (
                Diagnostic(
                    "PROOFREAD_RESPONSE_MALFORMED",
                    "The Proofread response must be a JSON object with a results array.",
                    category=ErrorCategory.INPUT,
                    severity=DiagnosticSeverity.WARNING,
                    retryable=True,
                    details=(("entry_keys", tuple(candidate.entry_key.to_dict() for candidate in candidates)),),
                ),
            ),
            structurally_malformed=True,
        )

    requested = {candidate.entry_key for candidate in candidates}
    values: dict[EntryKey, object] = {}
    reviews: dict[EntryKey, tuple[str, str, bool]] = {}
    invalid_items: dict[EntryKey, dict] = {}
    duplicate_keys: set[EntryKey] = set()
    diagnostics: list[Diagnostic] = []
    extra_fields = sorted(set(payload) - {"results"})
    if extra_fields:
        diagnostics.append(
            Diagnostic(
                "PROOFREAD_RESPONSE_EXTRA_FIELDS",
                "响应包含额外的顶层字段，条目已分别检查。",
                category=ErrorCategory.INPUT,
                severity=DiagnosticSeverity.INFO,
                details=(("unexpected_fields", extra_fields),),
            )
        )
    for index, item in enumerate(payload["results"]):
        key = _result_key(item)
        if key is None:
            diagnostics.append(
                Diagnostic(
                    "PROOFREAD_RESULT_ITEM_MALFORMED",
                    "A Proofread result has an invalid entry_key.",
                    category=ErrorCategory.INPUT,
                    severity=DiagnosticSeverity.WARNING,
                    retryable=True,
                    details=(("result_index", index),),
                )
            )
            continue
        if key not in requested:
            diagnostics.append(
                Diagnostic(
                    "PROOFREAD_RESPONSE_UNKNOWN_KEY",
                    "The Proofread response contains an entry that was not requested.",
                    category=ErrorCategory.INPUT,
                    severity=DiagnosticSeverity.WARNING,
                    details=(("entry_key", key.to_dict()),),
                )
            )
            continue
        if key in values:
            duplicate_keys.add(key)
            continue
        values[key] = item.get("final_translation")
        status, reason = item.get("review_status"), item.get("review_reason")
        explicit = status in REVIEW_STATUSES if isinstance(status, str) else False
        explicit = explicit and isinstance(reason, str) and bool(reason.strip())
        if not explicit:
            status, reason = "needs_review", "模型未返回完整的校对判断与理由，已保留候选译文，待复核。"
        reviews[key] = (status, reason, explicit)
        # Older providers can return usable text without semantic review evidence.
        # Preserve it as pending review instead of treating it as reviewed or retrying forever.
        errors = tuple(_ITEM_VALIDATOR.iter_errors({**item, "review_status": status, "review_reason": reason}))
        if errors:
            invalid_items[key] = schema_validation_details(errors, schema_name=PROOFREAD_OUTPUT_SCHEMA.name)

    updated: list[PostProcessCandidate] = []
    for candidate in candidates:
        key = candidate.entry_key
        details = (("entry_key", key.to_dict()),)
        if key in duplicate_keys:
            diagnostics.append(
                Diagnostic(
                    "PROOFREAD_RESPONSE_DUPLICATE_KEY",
                    "The Proofread response returned an entry more than once.",
                    category=ErrorCategory.INPUT,
                    severity=DiagnosticSeverity.WARNING,
                    retryable=True,
                    details=details,
                )
            )
            updated.append(candidate.with_accepted(False))
            continue
        if key not in values:
            diagnostics.append(
                Diagnostic(
                    "PROOFREAD_RESPONSE_MISSING_KEY",
                    "The Proofread response omitted a requested entry.",
                    category=ErrorCategory.INPUT,
                    severity=DiagnosticSeverity.WARNING,
                    retryable=True,
                    details=details,
                )
            )
            updated.append(candidate.with_accepted(False))
            continue
        if key in invalid_items:
            validation = invalid_items[key]
            message = (
                "未返回校对译文"
                if "final_translation" in validation.get("missing_fields", ())
                else "条目响应格式不符合要求"
            )
            diagnostics.append(
                Diagnostic(
                    "PROOFREAD_RESPONSE_SCHEMA_INVALID",
                    message,
                    category=ErrorCategory.INPUT,
                    severity=DiagnosticSeverity.WARNING,
                    retryable=True,
                    details=(*details, ("validation_details", validation)),
                )
            )
            updated.append(candidate.with_accepted(False))
            continue
        value = values[key]
        if not isinstance(value, str) or not value.strip():
            diagnostics.append(
                Diagnostic(
                    "PROOFREAD_RESPONSE_EMPTY_TRANSLATION",
                    "The Proofread response returned an empty or invalid translation.",
                    category=ErrorCategory.INPUT,
                    severity=DiagnosticSeverity.WARNING,
                    retryable=True,
                    details=details,
                )
            )
            updated.append(candidate.with_accepted(False))
            continue
        if not protected_syntax_matches(candidate.original, value):
            diagnostics.append(
                Diagnostic(
                    "PROOFREAD_PROTECTED_SYNTAX_MISMATCH",
                    "The Proofread result changed a placeholder or program tag.",
                    category=ErrorCategory.INPUT,
                    severity=DiagnosticSeverity.WARNING,
                    retryable=True,
                    details=details,
                )
            )
            updated.append(candidate.with_accepted(False))
            continue
        status, reason, explicit = reviews[key]
        if status == "retained" and value != candidate.text:
            status = "revised"
        elif status == "revised" and value == candidate.text:
            status = "retained"
        details = {
            **dict(candidate.report_details),
            "review_status": status,
            "review_reason": reason,
            "review_contract_version": REVIEW_CONTRACT_VERSION if explicit else 0,
        }
        reviewed = candidate.with_text(value, phase).with_report_details(details)
        if status == "needs_review":
            reviewed = replace(reviewed, stage=STAGE_QUESTIONABLE)
        updated.append(reviewed)
    return ProofreadResponseResult(tuple(updated), tuple(diagnostics))


def _decode_payload(response: object) -> object:
    if isinstance(response, dict):
        return response
    if not isinstance(response, str):
        return None
    text = response.lstrip("\ufeff").strip()
    text = _THINK_PREFIX_RE.sub("", text, count=1).strip()
    if match := _FENCED_JSON_RE.fullmatch(text):
        text = match.group(1).strip()
    try:
        return json.loads(text, object_pairs_hook=_unique_object)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON object member")
        value[key] = item
    return value


def _result_key(item: object) -> EntryKey | None:
    if not isinstance(item, dict) or not isinstance(item.get("entry_key"), dict):
        return None
    if any(not isinstance(item["entry_key"].get(field), str) for field in ("namespace", "local_key")):
        return None
    try:
        return EntryKey.from_dict(item["entry_key"])
    except (KeyError, TypeError, ValueError):
        return None


__all__ = ["ProofreadResponseResult", "apply_proofread_response"]
