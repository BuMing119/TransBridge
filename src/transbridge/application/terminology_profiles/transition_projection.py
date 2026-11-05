"""Build scoped source-to-target mappings for text already saved in a scheme."""

from __future__ import annotations

from dataclasses import replace

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot

from .models import ProfileTermMapping, PublishedTerminologyProfile, TerminologyProfileContent, logical_term_key


def same_profile_revision(
    source: PublishedTerminologyProfile | None, target: PublishedTerminologyProfile | None
) -> bool:
    if source is None or target is None:
        return source is target
    return (source.project_id, source.profile_id, source.revision, source.content_digest) == (
        target.project_id,
        target.profile_id,
        target.revision,
        target.content_digest,
    )


def transition_content(
    source: PublishedTerminologyProfile,
    target: PublishedTerminologyProfile | None,
    base: EffectiveTerminologySnapshot | None,
) -> TerminologyProfileContent:
    """Pair identical logical keys; unpaired scopes shadow less specific pairs."""

    sources = {item.term_key: item for item in source.content.mappings}
    if target is not None:
        if source.project_id != target.project_id:
            raise ValueError("source and target terminology schemes must belong to the same project")
        targets = {item.term_key: item for item in target.content.mappings}
    else:
        targets = _base_mappings(source, base)
    mappings = []
    for key in sorted(sources.keys() | targets.keys()):
        source_mapping = sources.get(key)
        target_mapping = targets.get(key)
        scope = source_mapping or target_mapping
        assert scope is not None
        if source_mapping is not None and target_mapping is not None:
            old, new = source_mapping.translation, target_mapping.translation
        else:
            # Keep the scope in the index even without a common term. Dropping
            # it could apply a project mapping to text owned by a plugin scope.
            old = new = scope.translation
        mappings.append(
            ProfileTermMapping(
                scope.original,
                new,
                old,
                scope_kind=scope.scope_kind,
                plugin_id=scope.plugin_id,
            )
        )
    # Bindings and full-entry overrides describe the old base projection, not
    # positions or complete text in the currently saved source scheme.
    return TerminologyProfileContent(tuple(mappings))


def base_source_content(
    target: PublishedTerminologyProfile, base: EffectiveTerminologySnapshot | None
) -> TerminologyProfileContent:
    """Use the frozen base's actual spelling without modifying a published scheme."""
    if base is None or base.status is not EffectiveSnapshotStatus.READY:
        return target.content
    sources = _base_mappings(target, base)
    targets = {mapping.term_key: mapping for mapping in target.content.mappings}
    mappings = []
    for key in sorted(sources.keys() | targets.keys()):
        source, destination = sources.get(key), targets.get(key)
        if destination is not None:
            mappings.append(
                replace(
                    destination,
                    base_translation=destination.translation if source is None else source.translation,
                )
            )
        else:
            # Preserve an unmatched plugin scope as a no-op shadow so a lower
            # project mapping cannot reinterpret its source-owned text.
            assert source is not None
            mappings.append(replace(source, base_translation=source.translation))
    return replace(
        target.content,
        mappings=tuple(mappings),
    )


def _base_mappings(
    source: PublishedTerminologyProfile, base: EffectiveTerminologySnapshot | None
) -> dict[str, ProfileTermMapping]:
    if base is not None and base.local_project_id != source.project_id:
        raise ValueError("base terminology snapshot must belong to the source scheme project")
    if base is None or base.status is not EffectiveSnapshotStatus.READY:
        return {
            item.term_key: ProfileTermMapping(
                item.original,
                item.base_translation,
                scope_kind=item.scope_kind,
                plugin_id=item.plugin_id,
            )
            for item in source.content.mappings
            if item.base_translation
        }
    sources = {item.term_key: item for item in source.content.mappings}
    decisions = {}
    for decision in base.decisions:
        key = logical_term_key(
            decision.original, scope_kind=decision.scope.kind.value, plugin_id=decision.scope.plugin_id
        )
        previous = decisions.get(key)
        if previous is None or decision.is_effective or not previous.is_effective:
            decisions[key] = decision
    mappings = {}
    for key, decision in decisions.items():
        source_mapping = sources.get(key)
        # Non-effective scopes remain shadows and must not expose a lower
        # project mapping. Their source spelling is an explicit no-op.
        if decision.is_effective:
            translation = decision.translation
        elif source_mapping is not None:
            translation = source_mapping.translation
        else:
            translation = decision.translation or decision.original
        mappings[key] = ProfileTermMapping(
            decision.original,
            translation,
            scope_kind=decision.scope.kind.value,
            plugin_id=decision.scope.plugin_id,
        )
    return mappings


__all__ = ["base_source_content", "same_profile_revision", "transition_content"]
