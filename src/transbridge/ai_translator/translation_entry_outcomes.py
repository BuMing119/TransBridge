"""Per-entry execution evidence for selective translation application and retry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from transbridge.application.contracts import DiagnosticSeverity, ErrorCategory
from transbridge.application.io.identity import EntryKey
from transbridge.application.translation.entry_alias import ai_entry_id, ai_entry_key

if TYPE_CHECKING:
    from transbridge.application.contracts import OperationResult
    from transbridge.application.translation.postprocess import ReportSnapshot
    from transbridge.application.translation.workload_models import TranslationInput
    from transbridge.converter.translation_entry import TranslationEntry


@dataclass(frozen=True, slots=True)
class TranslationEntryOutcome:
    entry_key: EntryKey
    status: str
    text: str
    stage: int
    reason: str = ""


def committed_entry_keys(result: OperationResult[dict] | None) -> set[EntryKey]:
    """Only a successful guarded write can supply commit evidence."""
    value = None if result is None else result.value
    if not value:
        return set()
    return {
        EntryKey.from_dict(key) for field in ("applied_keys", "already_committed_keys") for key in value.get(field, ())
    }


def build_translation_entry_outcomes(
    inputs: Sequence[TranslationInput],
    entries: Iterable[TranslationEntry],
    *,
    started: set[EntryKey],
    finished: set[EntryKey],
    accepted: set[EntryKey],
    commit_result: OperationResult[dict] | None,
    cancelled: bool,
    failures: Sequence[str] = (),
    post_process_enabled: bool = False,
    required_phases: tuple[str, ...] | None = None,
    post_process_snapshot: ReportSnapshot | None = None,
    post_process_commit: OperationResult[dict] | None = None,
) -> dict[EntryKey, TranslationEntryOutcome]:
    """Keep source failures, interrupted requests and valid candidates distinct.

    A nonempty value (even one equal to the generated translation) is never
    sufficient evidence. Both candidate acceptance and a guarded commit are
    required, followed by every configured post-processing stage when enabled.
    """
    current = {entry.identity: entry for entry in entries}
    committed = committed_entry_keys(commit_result)
    post_failures = (
        _post_process_failures(accepted & committed, required_phases, post_process_snapshot, post_process_commit)
        if post_process_enabled
        else {}
    )
    review_notes = {}
    if post_process_enabled and post_process_snapshot is not None:
        for diagnostic in post_process_snapshot.diagnostics:
            if diagnostic.code != "PROOFREAD_SYNTAX_REVIEW_REQUIRED":
                continue
            for candidate in post_process_snapshot.candidates:
                if (
                    candidate.accepted
                    and dict(candidate.report_details).get("questionable")
                    and _owns(dict(diagnostic.details), candidate.entry_key)
                ):
                    review_notes[candidate.entry_key] = f"[{diagnostic.code}] {diagnostic.message}"
    results = {}
    for item in inputs:
        key = item.entry_key
        reason = ""
        if key in accepted and key in committed:
            status = "succeeded"
            if post_process_enabled:
                reason = post_failures.get(key, "")
                if reason:
                    status = "cancelled" if cancelled else "failed"
        elif key in accepted:
            status = "cancelled" if cancelled else "failed"
            reason = "任务已取消" if cancelled else _commit_failure(commit_result, key)
        elif key in finished:
            status = "failed"
            entry = current.get(key)
            identities = (
                (key.serialize() if key.original is not None else key.local_key,)
                if entry is None
                else (ai_entry_key(entry), ai_entry_id(entry))
            )
            reason = next(
                (message for message in failures if any(message.startswith(f"{value}:") for value in identities)),
                "翻译未返回有效结果",
            )
        elif key in started:
            status = "cancelled" if cancelled else "failed"
            reason = "任务已取消" if cancelled else "翻译未完成"
        else:
            status = "not_started"
            reason = "未处理"
        entry = current.get(key)
        if status == "succeeded":
            reason = review_notes.get(key, reason)
        text = entry.translation if status == "succeeded" and entry is not None else item.translation
        stage = entry.stage if status == "succeeded" and entry is not None else item.stage
        results[key] = TranslationEntryOutcome(key, status, text, stage, reason)
    return results


def _owns(details: Mapping, key: EntryKey) -> bool:
    identity = details.get("entry_key")
    identities = details.get("entry_keys", ())
    if identity is None and not identities:
        return True
    return identity in (key.to_dict(), key.serialize()) or any(
        value in (key.to_dict(), key.serialize()) for value in identities
    )


def _commit_failure(result: OperationResult[dict] | None, key: EntryKey) -> str:
    if result is not None:
        for diagnostic in result.diagnostics:
            if _owns(dict(diagnostic.details), key):
                return f"{diagnostic.code}: {diagnostic.message}"
    return "翻译结果未提交"


def _post_process_failures(
    keys: set[EntryKey],
    phases: tuple[str, ...] | None,
    snapshot: ReportSnapshot | None,
    commit_result: OperationResult[dict] | None,
) -> dict[EntryKey, str]:
    if phases is None or snapshot is None:
        return dict.fromkeys(keys, "后处理未完成")
    completed = tuple(stage.phase for stage in snapshot.stage_outcomes)
    if completed != phases:
        return dict.fromkeys(keys, "后处理未完成")
    failures = {}
    for diagnostic in snapshot.diagnostics:
        if diagnostic.severity is DiagnosticSeverity.ERROR or diagnostic.category in {
            ErrorCategory.INTERNAL,
            ErrorCategory.EXTERNAL,
            ErrorCategory.CANCELLED,
        }:
            details = dict(diagnostic.details)
            identities = (details["entry_key"],) if "entry_key" in details else details.get("entry_keys", ())
            affected = (
                {
                    EntryKey.from_dict(value) if isinstance(value, dict) else EntryKey.deserialize(value)
                    for value in identities
                }
                if identities
                else keys
            )
            for key in keys & affected:
                failures.setdefault(key, f"{diagnostic.code}: {diagnostic.message}")
    candidates = {item.entry_key: item for item in snapshot.candidates}
    traversed = set(keys)
    for stage in snapshot.stage_outcomes:
        traversed.intersection_update(item.entry_key for item in stage.candidates)
    committed = committed_entry_keys(commit_result)
    for key in keys - failures.keys():
        candidate = candidates.get(key)
        if candidate is None or not candidate.accepted:
            failures[key] = "后处理未通过"
        elif key not in traversed:
            failures[key] = "后处理未完成"
        elif (
            candidate.text != candidate.before_text or dict(candidate.report_details).get("questionable")
        ) and key not in committed:
            failures[key] = _commit_failure(commit_result, key)
    return failures
