"""Persistence/I/O adapters for authoritative Project provisioning."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from transbridge.application.contracts import (
    DiagnosticSeverity,
    DomainError,
    ErrorCategory,
    OperationOutcome,
    RequestContext,
)
from transbridge.application.io import (
    FormatId,
    ParseRequest,
    SourceDescriptor,
    TranslationIoUseCase,
)
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.projects.provisioning import (
    PreparedProjectSource,
    ProjectSourceRequest,
)

_PLUGIN_SUFFIXES = {".esp", ".esm", ".esl"}


class TranslationIoProjectSourcePreparer:
    """Parse one source into an immutable baseline artifact during prepare."""

    def __init__(self, io_use_case: TranslationIoUseCase | None = None) -> None:
        self._io = io_use_case or TranslationIoUseCase()

    def prepare_source(
        self,
        request: ProjectSourceRequest,
        context: RequestContext,
        *,
        role: str,
        common_options: tuple[tuple[str, Any], ...],
    ) -> PreparedProjectSource:
        if role not in {"primary", "migration"}:
            raise ValueError("Project source role must be primary or migration")
        try:
            path = Path(request.location).resolve(strict=True)
        except FileNotFoundError as exc:
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "PROJECT_SOURCE_NOT_FOUND",
                "工程来源文件不存在。",
            ) from exc
        except PermissionError as exc:
            raise DomainError(
                ErrorCategory.PERMISSION,
                "PROJECT_SOURCE_PERMISSION_DENIED",
                "无权读取工程来源文件。",
            ) from exc
        if not path.is_file():
            raise DomainError(
                ErrorCategory.INPUT,
                "PROJECT_SOURCE_FILE_REQUIRED",
                "工程来源必须是可读取的文件。",
            )
        format_hint = request.format_hint or _infer_format(path)
        options = dict(common_options)
        options.update(request.options)
        options.setdefault("skip_empty", False)
        if format_hint is FormatId.PLUGIN_SSE:
            options.setdefault("discover_sibling_strings", True)
        parsed = self._io.parse(
            ParseRequest(
                SourceDescriptor(str(path), path.name, path.stat().st_size),
                context,
                format_hint=format_hint,
                source_namespace=SourceNamespace(options["source_namespace"])
                if "source_namespace" in options
                else None,
                options=tuple(sorted(options.items())),
            )
        )
        if parsed.format_id is FormatId.JSON_PARATRANZ:
            parsed = replace(parsed, entries=tuple(entry.to_translation_entry() for entry in parsed.entries))
        allowed_partial = parsed.outcome is OperationOutcome.PARTIAL and (
            all(item.code == "SOURCE_LOCATOR_CONFLICT" for item in parsed.diagnostics)
            or parsed.format_id is FormatId.JSON_PARATRANZ
            and options.get("invalid_record_policy", "partial") == "partial"
        )
        if (parsed.outcome is not OperationOutcome.COMPLETED and not allowed_partial) or parsed.source_snapshot is None:
            code = parsed.diagnostics[0].code if parsed.diagnostics else "PROJECT_SOURCE_PARSE_FAILED"
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                code,
                "工程来源无法解析为可验证基线。",
            )
        if request.expected_fingerprint is not None and request.expected_fingerprint != parsed.source_snapshot.sha256:
            raise DomainError(
                ErrorCategory.CONFLICT,
                "PROJECT_SOURCE_FINGERPRINT_MISMATCH",
                "工程来源内容与预期指纹不一致。",
            )
        _source_namespace(parsed)
        diagnostics = tuple(replace(item, severity=DiagnosticSeverity.WARNING) for item in parsed.diagnostics)
        from transbridge.application.projects.prepared_source_results import prepare_parsed_source

        return prepare_parsed_source(
            str(path),
            parsed.entries,
            parsed.source_snapshot,
            parsed.format_id,
            tuple(sorted(options.items())),
            role=role,
            diagnostics=diagnostics,
        )


def _infer_format(path: Path) -> FormatId:
    if path.suffix.lower() in _PLUGIN_SUFFIXES:
        return FormatId.PLUGIN_SSE
    raise DomainError(
        ErrorCategory.INPUT,
        "PROJECT_SOURCE_FORMAT_REQUIRED",
        "无法识别工程来源格式，请明确选择格式。",
    )


def _source_namespace(parsed) -> SourceNamespace:
    if parsed.entries:
        namespace = parsed.entries[0].identity.namespace
        if any(entry.identity.namespace != namespace for entry in parsed.entries):
            raise DomainError(
                ErrorCategory.CONFLICT,
                "PROJECT_SOURCE_NAMESPACE_AMBIGUOUS",
                "单个工程来源产生了多个来源身份。",
            )
        return namespace
    namespace_value = dict(parsed.source_snapshot.metadata).get("source_namespace")
    if isinstance(namespace_value, str):
        return SourceNamespace(namespace_value)
    return SourceNamespace.from_fingerprint(parsed.format_id.value, parsed.source_snapshot.sha256)


__all__ = ["TranslationIoProjectSourcePreparer"]
