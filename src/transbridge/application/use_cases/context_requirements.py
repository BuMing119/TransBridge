"""Validate invocation context before a use case performs side effects."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult, RequestContext
from transbridge.application.ports import SecretPort


@dataclass(frozen=True, slots=True)
class ContextRequirements:
    project: bool = False
    secrets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(not secret or not secret.strip() for secret in self.secrets):
            raise ValueError("secret requirement names must not be empty")


class ValidateContextUseCase:
    """Return a canonical prerequisite failure instead of fabricating context."""

    def __init__(self, secrets: SecretPort) -> None:
        self._secrets = secrets

    def execute(
        self,
        context: RequestContext | None,
        requirements: ContextRequirements,
    ) -> OperationResult[dict[str, object]]:
        if context is None:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.PREREQUISITE,
                    "RUNTIME_CONTEXT_REQUIRED",
                    "An initialized runtime context is required for this operation.",
                )
            )
        if requirements.project and not context.project_id:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.PREREQUISITE,
                    "PROJECT_CONTEXT_REQUIRED",
                    "An active project is required for this operation.",
                ),
                run_id=context.run_id,
            )

        try:
            missing = tuple(name for name in requirements.secrets if not self._secrets.has_secret(name, context))
        except Exception as exc:  # noqa: BLE001 - translate adapter failure to canonical result
            return OperationResult.from_exception(exc, run_id=context.run_id)
        if missing:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.PREREQUISITE,
                    "SECRET_REQUIRED",
                    "A required credential is unavailable.",
                    details={"missing_secret_names": list(missing)},
                ),
                run_id=context.run_id,
            )

        return OperationResult.completed(
            {"context": context.to_dict(), "requirements_satisfied": True},
            run_id=context.run_id,
        )
