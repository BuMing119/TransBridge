"""Correlated, credential-safe execution evidence for ParaTranz tasks."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import hashlib
import json
import logging
import re

from transbridge.application.contracts import (
    Diagnostic,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)
from transbridge.application.io.identity import EntryKey
from transbridge.application.ports.paratranz import ExternalServiceError
from transbridge.application.security.diagnostic_logging import DiagnosticLogFormatter, diagnostic_exception_text
from transbridge.application.security.redaction import SecretRedactor

_logger = logging.getLogger(__name__)
_PHASES = {
    "preparation": "核对操作与最新数据",
    "recovery_snapshot": "创建工程还原点",
    "commit_permission": "取得工程提交权限",
    "execution": "执行同步",
    "remote_snapshot": "读取并校验最新云端内容",
    "result_publication": "保存执行结果",
    "cleanup": "释放同步资源",
}


@dataclass(frozen=True, slots=True)
class SyncExecutionDiagnostics:
    run_id: str
    plan_id: str
    operation: str
    project_id: int

    def phase(self, phase: str, status: str) -> None:
        self._write(phase=phase, status=status)

    def failure(
        self, phase: str, exc: Exception, *, item_id: str | None = None, entry_key: EntryKey | None = None
    ) -> Diagnostic:
        code = str(getattr(exc, "code", "") or f"SYNC_{phase.upper()}_FAILED")
        category = ErrorCategory.INTERNAL
        if isinstance(exc, ExternalServiceError):
            code = f"REMOTE_{exc.category.value.upper()}"
            category = ErrorCategory.EXTERNAL
        elif code in {"STALE_PLAN", "STALE_SYNC_PLAN"}:
            category = ErrorCategory.CONFLICT
        reason = _without_identity_original(diagnostic_exception_text(exc), entry_key)
        trace = DiagnosticLogFormatter().formatException((type(exc), exc, exc.__traceback__))
        self._write(
            level=logging.ERROR,
            phase=phase,
            status="failed",
            code=code,
            error_type=type(exc).__name__,
            reason=reason,
            item_id=item_id,
            entry_key=_entry_key(entry_key),
            traceback=_without_identity_original(trace, entry_key),
        )
        return Diagnostic(
            code,
            f"{_PHASES.get(phase, phase)}失败：{reason or type(exc).__name__}",
            category=category,
            details=(("phase", phase), ("run_id", self.run_id)),
        )

    def result(self, result: OperationResult) -> None:
        diagnostics = [item for item in result.diagnostics if item.code != "SYNC_RETRY_AVAILABLE"]
        self._write(
            phase="result",
            status=result.outcome.value,
            counts=result.counts.to_dict(),
            reasons=dict(sorted(Counter(item.code for item in diagnostics).items())),
            deferred_count=result.value.get("deferred_count", 0) if isinstance(result.value, dict) else 0,
        )
        for item in diagnostics:
            details = dict(item.details)
            key = _parse_entry_key(details.get("entry_key"))
            self._write(
                level=logging.WARNING,
                phase="item_result",
                status=item.severity.value,
                code=item.code,
                reason=_without_identity_original(item.message, key),
                entry_key=_entry_key(key),
                action=details.get("action"),
            )

    def _write(self, *, level=logging.INFO, **payload) -> None:
        payload = {
            "event": "PARATRANZ_SYNC_EXECUTION",
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "operation": self.operation,
            "project_id": self.project_id,
            **payload,
        }
        _logger.log(
            level,
            "%s",
            json.dumps(SecretRedactor.default().redact(payload), ensure_ascii=False, sort_keys=True),
        )


def _parse_entry_key(value: object) -> EntryKey | None:
    if isinstance(value, EntryKey):
        return value
    if isinstance(value, str):
        try:
            return EntryKey.deserialize(value)
        except (TypeError, ValueError):
            # An invalid serialized identity may contain arbitrary source text.
            # Do not echo it into a technical log as a fallback.
            return None
    return None


def _entry_key(key: EntryKey | None) -> dict | None:
    if key is None:
        return None
    value = {"namespace": key.namespace.value, "local_key": key.local_key}
    if key.original is not None:
        value.update(
            identity_original_sha256=hashlib.sha256(key.original.encode("utf-8")).hexdigest(),
            identity_original_length=len(key.original),
        )
    return value


def _without_identity_original(text: str, key: EntryKey | None) -> str:
    if key is None or not key.original:
        return text
    representations = {
        key.original,
        repr(key.original),
        json.dumps(key.original, ensure_ascii=False),
        json.dumps(key.original, ensure_ascii=True),
    }
    pattern = "|".join(re.escape(value) for value in sorted(representations, key=len, reverse=True))
    return re.sub(pattern, "[original omitted]", text)


def with_execution_failure(result: OperationResult | None, diagnostic: Diagnostic, *, run_id: str) -> OperationResult:
    """Keep committed successes visible when later publication or cleanup fails."""
    if result is None:
        return OperationResult(
            OperationOutcome.FAILED,
            diagnostics=(diagnostic,),
            counts=OperationCounts(failed=1),
            run_id=run_id,
        )
    counts = replace(result.counts, failed=result.counts.failed + 1)
    return replace(
        result,
        outcome=OperationOutcome.PARTIAL if counts.succeeded else OperationOutcome.FAILED,
        value=result.value if counts.succeeded else None,
        artifact_refs=result.artifact_refs if counts.succeeded else (),
        diagnostics=(*result.diagnostics, diagnostic),
        counts=counts,
    )
