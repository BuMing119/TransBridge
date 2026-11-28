# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Freeze the formal Project/Variant terminology for one dictionary operation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transbridge.application.terminology.effective import EffectiveSnapshotStatus

if TYPE_CHECKING:
    from transbridge.application.terminology_profiles.dictionary_projection import DictionaryTextProjection
    from transbridge.translation_memory.library import ProjectionFactory


def bind_dictionary_projection(source, identity: tuple[str, str] | None) -> ProjectionFactory | None:
    factory = getattr(source, "terminology_profile_service_factory", None)
    if factory is None or identity is None:
        return None

    project_id, variant_id = identity

    def freeze(*, to_default: bool) -> DictionaryTextProjection | None:
        # Only immutable scope and the injected read service cross the worker boundary.
        try:
            captured = factory.dictionary_terminology(project_id, variant_id)
        except Exception as exc:  # noqa: BLE001 - never reinterpret a failed read as a default Variant
            raise RuntimeError(f"无法读取工程 {project_id} 的版本 {variant_id} 的正文译名方案：{exc}") from exc
        if captured is None:
            return None
        profile, base = captured
        if profile.project_id != project_id or (base.local_project_id, base.local_variant_id) != identity:
            raise ValueError("词典操作读取到了其他工程或版本的译名方案。")
        if base.status in {EffectiveSnapshotStatus.UNAVAILABLE, EffectiveSnapshotStatus.CORRUPT}:
            raise ValueError(f"无法读取词典操作所需的项目默认术语：{'；'.join(base.diagnostics)}")

        from transbridge.application.terminology_profiles.dictionary_projection import DictionaryTextProjection

        return DictionaryTextProjection(profile, base, to_default=to_default)

    return freeze
