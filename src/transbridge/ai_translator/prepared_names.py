"""Validate project-prepared name results before normal candidate admission."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from transbridge.application.translation.entry_alias import ai_entry_id, ai_entry_key
from transbridge.application.translation.protected_syntax import protected_syntax_matches
from transbridge.application.translation.workload_models import canonical_hash

if TYPE_CHECKING:
    from transbridge.application.io.identity import EntryKey
    from transbridge.converter.translation_entry import TranslationEntry


@dataclass(frozen=True, slots=True)
class PreparedName:
    """One validated bootstrap result, or an explicit failure for that input."""

    original: str
    context: str
    text: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PreparedNameSelection:
    accepted: dict[str, str]
    remaining: tuple[TranslationEntry, ...]
    failures: tuple[str, ...]


def prepared_names_spec(
    prepared_names: Mapping[EntryKey, PreparedName], *, defer_generated_terms: bool = False
) -> dict[str, str]:
    """Bind candidate recovery to prepared outcomes while preserving legacy specs."""

    if not prepared_names and not defer_generated_terms:
        return {}
    return {
        "name_preparation": canonical_hash({
            "defer_generated_terms": defer_generated_terms,
            "prepared_names": [
                {
                    "entry_key": key.to_dict(),
                    "original": prepared.original,
                    "context": prepared.context,
                    "text": prepared.text,
                    "reason": prepared.reason,
                }
                for key, prepared in sorted(prepared_names.items())
            ],
        })
    }


def select_prepared_names(
    entries: Sequence[TranslationEntry], prepared_names: Mapping[EntryKey, PreparedName]
) -> PreparedNameSelection:
    """Separate identity-bound outcomes without changing entries or term state.

    Prepared failures never fall through to an exact-term shortcut or another
    model request. Accepted text still requires the caller's regular candidate
    persistence, guarded commit and configured postprocessing.
    """

    accepted, remaining, failures = {}, [], []
    for entry in entries:
        if entry.identity not in prepared_names:
            remaining.append(entry)
            continue
        prepared = prepared_names[entry.identity]
        reason = _failure(entry, prepared)
        if reason:
            failures.append(f"{ai_entry_id(entry)}: 名称预翻译失败：{reason}")
        else:
            accepted[ai_entry_key(entry)] = prepared.text
    return PreparedNameSelection(accepted, tuple(remaining), tuple(failures))


def _failure(entry: TranslationEntry, prepared: PreparedName) -> str:
    if prepared.original != entry.original or prepared.context != (entry.context or ""):
        return "词条原文或上下文与预翻译输入不匹配"
    if prepared.reason:
        return prepared.reason
    if not prepared.text.strip():
        return "未返回有效译文"
    if not protected_syntax_matches(entry.original, prepared.text):
        return "译文改变了原文中的受保护标记"
    return ""


__all__ = ["PreparedName", "PreparedNameSelection", "prepared_names_spec", "select_prepared_names"]
