"""Exact dictionary lookup for entries whose source locator is ambiguous."""

from collections.abc import Iterable
import logging

from transbridge.application.io.identity import EntryKey

from .model import Dictionary

logger = logging.getLogger(__name__)


def qualified_identity(value: str) -> EntryKey | None:
    if not value.startswith("["):
        return None
    try:
        key = EntryKey.deserialize(value)
    except ValueError:
        return None  # A normal source key can itself start with a bracket.
    return key if key.original is not None else None


def dictionary_key(entry) -> str:
    return entry.identity.serialize() if entry.requires_original_match else entry.key


def exact_dictionary_candidates(
    dictionaries: Iterable[Dictionary], identity: EntryKey, original: str, preferred_mod: str
) -> list[tuple[Dictionary, str, str, str]]:
    """Use indexed key+original evidence; never normalize or fall back to text."""
    if identity.original != original:
        raise ValueError("dictionary lookup original does not match its identity")
    candidates = []
    for dictionary in dictionaries:
        for key in (identity.serialize(), identity.local_key):
            index = dictionary.key_index.get(key)
            entry = dictionary.entries.get(index.get("entry_id", "")) if index else None
            if entry is None:
                continue
            if entry.original != original:
                logger.warning("SOURCE_ORIGINAL_MATCH_REQUIRED: 词典词条 %s 原文不匹配，已跳过。", identity.local_key)
                continue
            candidate = (dictionary, key, "key", "EXACT")
            if dictionary.mod_file_id == preferred_mod:
                return [candidate]
            candidates.append(candidate)
            break
    return candidates
