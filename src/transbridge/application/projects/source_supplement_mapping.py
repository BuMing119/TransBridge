"""Lossless correspondence between saved translations and explicitly supplied input."""

from collections import defaultdict
from dataclasses import replace

from transbridge.persistence.v2.variant import VariantSnapshot

from .source_supplement_models import SourceSupplementChoice, SourceSupplementRow


def supplement_rows(variants, prepared, namespace, *, active_variant_id=None, covered_keys=()):
    saved = {}
    for variant in sorted(variants, key=lambda item: item.ref.identity.value != active_variant_id):
        for row in variant.entries:
            if (
                namespace is not None
                and row.entry_key.namespace.value == namespace
                and row.entry_key not in covered_keys
            ):
                saved.setdefault(row.entry_key, row)
    aliases = defaultdict(set)
    choices = []
    for row in prepared.hydration.entries:
        aliases[row.entry_key.local_key].add(row.entry_key)
        if row.legacy_id:
            aliases[row.legacy_id].add(row.entry_key)
        choices.append(SourceSupplementChoice(row.entry_key, row.legacy_id or row.entry_key.local_key, row.original))
    rows = []
    for key, row in sorted(saved.items()):
        candidates = {
            candidate
            for candidate in aliases[key.local_key]
            if key.original is None or candidate.original == key.original
        }
        matched = next(iter(candidates)) if len(candidates) == 1 else None
        rows.append(SourceSupplementRow(key, row.translation, matched))
    # Never guess which of several old translations owns the same new input row.
    owners = defaultdict(list)
    for row in rows:
        if row.matched_key is not None:
            owners[row.matched_key].append(row.old_key)
    rows = tuple(
        replace(row, matched_key=None) if row.matched_key is not None and len(owners[row.matched_key]) > 1 else row
        for row in rows
    )
    return rows, tuple(choices)


def remap_rows(rows, choices, mappings):
    old_keys = {row.old_key for row in rows}
    new_keys = {choice.key for choice in choices}
    if any(key not in old_keys or target is not None and target not in new_keys for key, target in mappings.items()):
        raise ValueError("条目对应不属于当前预览，请重新选择保存词条和输入词条。")
    result = tuple(replace(row, matched_key=mappings.get(row.old_key, row.matched_key)) for row in rows)
    selected = [row.matched_key for row in result if row.matched_key is not None]
    if len(set(selected)) != len(selected):
        raise ValueError("一个输入词条只能对应一条保存译文；其余译文可选择暂不对应并保留。")
    return result


def supplement_variants(variants, prepared, rows, namespace=None):
    mapping = {row.old_key: row.matched_key for row in rows if row.matched_key is not None}
    new_baseline = {row.entry_key: row for row in prepared.baseline.entries}
    result = []
    for variant in variants:
        entries = {}
        for row in variant.entries:
            key = mapping.get(row.entry_key, row.entry_key)
            if key in entries:
                raise ValueError("所选对应会合并不同的保存译文，请调整对应关系。")
            entries[key] = replace(row, entry_key=key, revision=row.revision.next()) if key != row.entry_key else row
        # The input closes missing source content; it must never erase saved rows.
        for key, row in new_baseline.items():
            entries.setdefault(key, row)
        namespaces = {key.namespace for key in entries}
        replaced_namespaces = {key.namespace for key in mapping if key.namespace != mapping[key].namespace}
        if namespace is not None:
            replaced_namespaces.update(
                item.namespace for item in variant.source_fingerprints if item.namespace.value == namespace
            )
        fingerprints = {
            item.namespace: item
            for item in variant.source_fingerprints
            if item.namespace not in replaced_namespaces or item.namespace in namespaces
        }
        fingerprints[prepared.baseline.fingerprint.namespace] = prepared.baseline.fingerprint
        result.append(
            VariantSnapshot(
                variant.ref,
                tuple(fingerprints.values()),
                tuple(entries.values()),
                variant.revision + 1,
                variant.label_library,
            )
        )
    return tuple(result)
