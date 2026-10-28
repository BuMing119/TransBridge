"""Explicit source-baseline and legacy identity migration registries."""

from __future__ import annotations

from threading import RLock

from transbridge.application.contracts import DomainError, ErrorCategory, RequestContext

from .ids import ProjectRef, VariantRef
from .models import ProjectDto
from .variant import SourceBaseline


class BaselineRegistry:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], tuple[SourceBaseline, ...]] = {}
        self._lock = RLock()

    def register(
        self,
        project_ref: ProjectRef,
        variant_ref: VariantRef,
        baselines: tuple[SourceBaseline, ...],
        *,
        allow_empty: bool = False,
    ) -> None:
        if variant_ref.project_id != project_ref.identity:
            raise ValueError("baseline Variant must belong to its Project")
        if not baselines and not allow_empty:
            raise ValueError("an authoritative baseline registration must not be empty")
        with self._lock:
            self._items[(project_ref.identity.value, variant_ref.identity.value)] = baselines

    def remove(self, project_ref: ProjectRef, variant_ref: VariantRef) -> None:
        with self._lock:
            self._items.pop((project_ref.identity.value, variant_ref.identity.value), None)

    def replace_many(
        self,
        project_ref: ProjectRef,
        variant_refs: tuple[VariantRef, ...],
        baselines: tuple[SourceBaseline, ...],
        *,
        allow_empty: bool = False,
    ) -> None:
        """Atomically replace one Project's baseline set for all formal Variants."""

        if not variant_refs:
            raise ValueError("a Project baseline replacement requires at least one Variant")
        if any(ref.project_id != project_ref.identity for ref in variant_refs):
            raise ValueError("baseline Variants must belong to their Project")
        if not baselines and not allow_empty:
            raise ValueError("an authoritative baseline registration must not be empty")
        if len(set(variant_refs)) != len(variant_refs):
            raise ValueError("baseline replacement contains duplicate Variants")
        with self._lock:
            for variant_ref in variant_refs:
                self._items[(project_ref.identity.value, variant_ref.identity.value)] = baselines

    def provide(
        self,
        project: ProjectDto,
        variant_ref: VariantRef,
        context: RequestContext,
    ) -> tuple[SourceBaseline, ...]:
        key = (project.envelope.identity, variant_ref.identity.value)
        with self._lock:
            baselines = self._items.get(key)
        if baselines is None:
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "LEGACY_SOURCE_BASELINE_REQUIRED",
                "An authoritative source baseline is required before activating this Variant.",
            )
        return baselines

    def close(self) -> None:
        with self._lock:
            self._items.clear()

    def capture_catalog(self, project_ref, variant_refs):
        """Capture only registrations participating in one catalog publication."""
        with self._lock:
            return tuple(
                (ref, self._items.get((project_ref.identity.value, ref.identity.value))) for ref in variant_refs
            )

    def restore_catalog(self, project_ref, captured, *, expected_baselines=None) -> None:
        """Restore exact process-local preimages after a failed disk publication."""
        with self._lock:
            for ref, baselines in captured:
                key = (project_ref.identity.value, ref.identity.value)
                if expected_baselines is not None and self._items.get(key) is not expected_baselines:
                    continue
                if baselines is None:
                    self._items.pop(key, None)
                else:
                    self._items[key] = baselines


__all__ = ["BaselineRegistry"]
