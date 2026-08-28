"""Resolve the optional project terminology binding for translation consumers."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from transbridge.application.terminology.effective import TerminologyLookupContext

if TYPE_CHECKING:
    from .project_terminology_adapter import ProjectTerminologyAdapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProjectTerminologyBinding:
    """One Project/Variant-scoped read-only translation dependency."""

    adapter: ProjectTerminologyAdapter | None = None
    context: TerminologyLookupContext | None = None

    def translator_kwargs(self) -> dict[str, object]:
        if self.adapter is None or self.context is None:
            return {}
        return {
            "effective_terminology": self.adapter,
            "terminology_context": self.context,
        }

    def term_database_kwargs(self) -> dict[str, object]:
        if self.adapter is None or self.context is None:
            return {}
        return {
            "effective_loader": self.adapter,
            "terminology_context": self.context,
        }


def resolve_project_terminology(owner: object) -> ProjectTerminologyBinding:
    """Build a binding from an AppContext-like object, otherwise preserve legacy behavior.

    The factory is deliberately injected into the context instead of discovered
    from process globals.  Smart Assistant's execution context delegates these
    two properties to the same AppContext, so GUI and Agent entrypoints consume
    exactly the same Project/Variant line and gate.
    """

    identity = getattr(owner, "active_version_identity", None)
    factory = getattr(owner, "effective_terminology_factory", None)
    if not _valid_identity(identity) or factory is None:
        return ProjectTerminologyBinding()
    project_id, variant_id = identity
    try:
        create = getattr(factory, "effective_adapter", None)
        adapter = create(project_id, variant_id) if callable(create) else factory(project_id, variant_id)
        if adapter is None:
            return ProjectTerminologyBinding()
        return ProjectTerminologyBinding(
            adapter=adapter,
            context=TerminologyLookupContext(project_id, variant_id),
        )
    except Exception:  # noqa: BLE001 - read-only terminology is an optional legacy-compatible layer
        logger.warning(
            "Project terminology is unavailable for %s/%s; using legacy terminology",
            project_id,
            variant_id,
            exc_info=True,
        )
        return ProjectTerminologyBinding()


def _valid_identity(value: object) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


__all__ = ["ProjectTerminologyBinding", "resolve_project_terminology"]
