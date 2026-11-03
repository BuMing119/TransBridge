"""Wrap shared classification with stable upload source directories."""

from collections import Counter
from dataclasses import replace
import hashlib

from transbridge.application.io.paratranz_classification import classify_entries, path_component
from transbridge.application.io.paratranz_context_order import PARATRANZ_CONTEXT_ORDER_METADATA

from .models import CategorizedFile, UploadSource


def categorize_sources(sources: tuple[UploadSource, ...]) -> tuple[CategorizedFile, ...]:
    """Use the same relative groups as script exports, once per complete source."""
    names = source_directory_names(sources)
    result = []
    for source in sources:
        directory = names[source.source_id]
        complete = (
            source.entries
            if source.classification_entries is None or source.dialogue_order is None
            else source.classification_entries
        )
        selected = {entry.identity: entry for entry in source.entries}
        if len(selected) != len(source.entries):
            raise ValueError("分类上传包含重复词条身份。")
        complete_keys = {entry.identity for entry in complete}
        if len(complete_keys) != len(complete):
            raise ValueError("分类上传的完整来源包含重复词条身份。")
        if not selected.keys() <= complete_keys:
            raise ValueError("分类上传的完整来源未覆盖所选词条。")
        result.extend(
            CategorizedFile(source.source_id, group.category_id, f"{directory}/{group.path}", entries)
            for group in classify_entries(complete, dialogue_order=source.dialogue_order)
            if (
                entries := tuple(
                    _selected_entry(selected[entry.identity], entry)
                    for entry in group.entries
                    if entry.identity in selected
                )
            )
        )
    if len({file.path.casefold() for file in result}) != len(result):
        raise ValueError("categorized upload generated ambiguous paths")
    return tuple(result)


def _selected_entry(actual, projected):
    """Keep current translations/states while borrowing only the export display projection."""
    metadata = dict(actual.metadata)
    order = dict(projected.metadata).get(PARATRANZ_CONTEXT_ORDER_METADATA)
    if order is not None:
        metadata[PARATRANZ_CONTEXT_ORDER_METADATA] = order
    return replace(actual, context=projected.context, metadata=tuple(metadata.items()))


def source_directory_names(sources: tuple[UploadSource, ...]) -> dict[str, str]:
    """Name sources identically in ordinary files and category directories."""
    if any(not source.source_id.strip() for source in sources):
        raise ValueError("categorized upload requires registered source IDs")
    if len({source.source_id for source in sources}) != len(sources):
        raise ValueError("categorized upload source IDs must be unique")
    names = {source.source_id: path_component(source.label, "插件") for source in sources}
    counts = Counter(name.casefold() for name in names.values())
    for source in sources:
        if counts[names[source.source_id].casefold()] > 1:
            names[source.source_id] += "_" + _digest(source.source_id)
    if len({name.casefold() for name in names.values()}) != len(names):
        raise ValueError("upload sources generated ambiguous paths")
    return names


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
