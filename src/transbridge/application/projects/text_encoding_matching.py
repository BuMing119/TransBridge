# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Match re-decoded rows using saved format locators and the same source bytes."""

from collections import Counter
from itertools import chain

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.io import FormatId
from transbridge.converter.plugin_entry_metadata import plugin_source_order

from .source_update_migration import LocalEntryKey


def match_encoding_entries(previous, prepared) -> dict[LocalEntryKey, LocalEntryKey]:
    before = previous.hydration
    after = prepared.hydration
    if before is None or after is None:
        raise _unsafe_match()
    if any(item.code == "SOURCE_LOCATOR_CONFLICT" for item in prepared.diagnostics):
        raise _unsafe_match()
    return match_encoding_rows(
        before.entries,
        after.entries,
        plugin_order=before.format_id is FormatId.PLUGIN_SSE and before.fingerprint == after.fingerprint,
    )


def match_encoding_rows(before, after, *, plugin_order: bool) -> dict[LocalEntryKey, LocalEntryKey]:
    if len(before) != len(after):
        raise _unsafe_match()
    counts = Counter(entry.entry_key.local_key for entry in before)
    if counts != Counter(entry.entry_key.local_key for entry in after):
        raise _unsafe_match()
    qualified = {entry.entry_key.local_key for entry in chain(before, after) if entry.entry_key.original is not None}

    def physical_key(entry):
        key = entry.entry_key.local_key
        if counts[key] == 1 and key not in qualified:
            return key, None
        # The verified plugin bytes and the same extraction options anchor this
        # ordinal to a physical field, including unqualified IDs repeated in a mod.
        order = plugin_source_order(entry) if plugin_order else None
        if order is None:
            raise _unsafe_match()
        return key, order

    destination = {physical_key(entry): entry for entry in after}
    if len(destination) != len(after):
        raise _unsafe_match()
    matched = {}
    for entry in before:
        found = destination.pop(physical_key(entry), None)
        if found is None or entry.string_id != found.string_id:
            raise _unsafe_match()
        matched[(entry.entry_key.local_key, entry.entry_key.original)] = (
            found.entry_key.local_key,
            found.entry_key.original,
        )
    if len(matched) != len(before) or len(set(matched.values())) != len(matched):
        raise _unsafe_match()
    return matched


def _unsafe_match() -> DomainError:
    return DomainError(
        ErrorCategory.CONFLICT,
        "TEXT_ENCODING_ENTRY_MAPPING_UNSAFE",
        "无法把重读后的词条与已保存译文可靠地一一对应。请核对原始来源及配套 Strings。",
    )
