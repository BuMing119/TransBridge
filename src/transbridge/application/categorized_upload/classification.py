"""Wrap shared classification with stable upload source directories."""

from collections import Counter
import hashlib

from transbridge.application.io.paratranz_classification import classify_entries, path_component

from .models import CategorizedFile, UploadSource


def categorize_sources(sources: tuple[UploadSource, ...]) -> tuple[CategorizedFile, ...]:
    """Use the same relative groups as script exports, once per complete source."""
    names = source_directory_names(sources)
    result = []
    for source in sources:
        directory = names[source.source_id]
        result.extend(
            CategorizedFile(source.source_id, group.category_id, f"{directory}/{group.path}", group.entries)
            for group in classify_entries(source.entries)
        )
    if len({file.path.casefold() for file in result}) != len(result):
        raise ValueError("categorized upload generated ambiguous paths")
    return tuple(result)


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
