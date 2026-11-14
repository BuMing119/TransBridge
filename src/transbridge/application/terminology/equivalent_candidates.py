# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Choose one normal terminology pair from judged surface variants."""

from .identity import normalize_translation
from .models import ConflictStatus


def equivalent_candidates(candidates, conflicts, baseline=()):
    equivalent = {item.normalized_original: item for item in conflicts if item.status is ConflictStatus.EQUIVALENT}
    existing = {(item.normalized_original, item.scope): item for item in baseline}
    scoped_translations = {}
    for candidate in candidates:
        scoped_translations.setdefault((candidate.normalized_original, candidate.scope), set()).add(
            candidate.normalized_translation
        )
    result = []
    for candidate in candidates:
        group = equivalent.get(candidate.normalized_original)
        if group is None:
            result.append(candidate)
            continue
        preferred = group.recommended_translation
        previous = existing.get((candidate.normalized_original, candidate.scope))
        if previous is not None and normalize_translation(previous.translation) in {
            variant.normalized_translation for variant in group.variants
        }:
            # Equivalence does not authorize replacing an existing human choice.
            preferred = normalize_translation(previous.translation)
        available = scoped_translations[(candidate.normalized_original, candidate.scope)]
        if preferred not in available:
            # Preserve a plugin's existing normal expression when the representative
            # occurs only in another plugin. Do not invent cross-plugin evidence.
            preferred = min(available)
        if candidate.normalized_translation == preferred:
            result.append(candidate)
    return tuple(result)
