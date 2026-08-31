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


class LegacyIdentityRegistry:
    """Explicit, process-scoped V1 display/path to V2 identity migration map."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], tuple[ProjectRef, VariantRef]] = {}
        self._lock = RLock()

    def register(
        self,
        legacy_project_key: str,
        legacy_variant_name: str,
        project_ref: ProjectRef,
        variant_ref: VariantRef,
    ) -> None:
        if not legacy_project_key.strip() or not legacy_variant_name.strip():
            raise ValueError("legacy identity keys must not be empty")
        if variant_ref.project_id != project_ref.identity:
            raise ValueError("mapped Variant must belong to the mapped Project")
        with self._lock:
            self._items[(legacy_project_key, legacy_variant_name)] = (project_ref, variant_ref)

    def resolve(self, legacy_project_key: str, legacy_variant_name: str) -> tuple[ProjectRef, VariantRef]:
        with self._lock:
            mapped = self._items.get((legacy_project_key, legacy_variant_name))
        if mapped is None:
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "LEGACY_ID_MAPPING_REQUIRED",
                "The legacy Project and Variant require an explicit V1 to V2 identity mapping.",
            )
        return mapped

    def close(self) -> None:
        with self._lock:
            self._items.clear()


__all__ = ["BaselineRegistry", "LegacyIdentityRegistry"]
