"""Read-only access to saved translations when source hydration is unavailable.

Recovery never manufactures a source baseline or activates an editable Variant.
The saved state remains inspectable without risking an overlay onto changed files.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, DomainError, ErrorCategory, RequestContext
from transbridge.application.contracts.errors import map_exception

from .v2 import VariantRef, VariantRepository, VariantSnapshot
from .v2.models import PersistenceV2Error


@dataclass(frozen=True, slots=True)
class ProjectRecoverySnapshot:
    project_path: str
    name: str
    variant: VariantSnapshot
    diagnostics: tuple[Diagnostic, ...]


def source_recovery_diagnostic(source: dict, error: OSError | DomainError) -> Diagnostic:
    """Retain the source failure as a warning attached to a read-only result."""

    mapped = map_exception(error)
    if mapped.category in {ErrorCategory.INTERNAL, ErrorCategory.CANCELLED}:
        raise error
    location = str(source.get("location") or source.get("path") or "")
    return replace(
        Diagnostic.from_error(mapped),
        severity=DiagnosticSeverity.WARNING,
        details=(*tuple(mapped.details.items()), ("source_location", location)),
    )


def load_recovery_snapshot(
    project_path: str,
    name: str,
    variant_ref: VariantRef,
    variants: VariantRepository,
    diagnostics: tuple[Diagnostic, ...],
    context: RequestContext,
) -> ProjectRecoverySnapshot:
    validate_recovery_context(variant_ref, context)
    try:
        variant = variants.read_snapshot(variant_ref)
    except (OSError, PersistenceV2Error) as exc:
        raise DomainError(
            ErrorCategory.PREREQUISITE,
            "VARIANT_RECORD_UNAVAILABLE",
            "保存的版本记录不可读取，无法打开恢复视图。",
            cause=exc,
        ) from exc
    return ProjectRecoverySnapshot(project_path, name, VariantSnapshot.from_dto(variant, variant_ref), diagnostics)


def validate_recovery_context(variant_ref: VariantRef, context: RequestContext) -> None:
    # Recovery bypasses activation, so retain the same request identity boundary
    # that ProjectLifecycleService normally enforces before showing saved data.
    for requested, actual, code in (
        (context.project_id, variant_ref.project_id.value, "PROJECT_CONTEXT_MISMATCH"),
        (context.variant_id, variant_ref.identity.value, "VARIANT_CONTEXT_MISMATCH"),
    ):
        if requested is not None and requested != actual:
            raise DomainError(ErrorCategory.PERMISSION, code, "恢复视图与请求的工程或版本不一致。")


__all__ = [
    "ProjectRecoverySnapshot",
    "load_recovery_snapshot",
    "source_recovery_diagnostic",
    "validate_recovery_context",
]
