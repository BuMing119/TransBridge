"""Select one plugin naming generation without mixing old and new files."""

from collections import defaultdict
from collections.abc import Mapping
from pathlib import PurePosixPath
import re


def _path_families(path):
    if not path.endswith(".json"):
        return ()
    first, separator, _ = path.partition("/")
    if separator:
        return (first,)
    root = first[:-5]
    split = re.fullmatch(r"(.+)_[1-9][0-9]*", root)
    return (root, split[1]) if split else (root,)


def matches_current_path(path, current_paths):
    """Match the plugin root and every JSON below its exact directory boundary."""
    families = {candidate.partition("/")[0].removesuffix(".json") for candidate in current_paths}
    return not families.isdisjoint(_path_families(path))


def validate_download_path(path):
    if (
        not isinstance(path, str)
        or not path.endswith(".json")
        or PurePosixPath(path).is_absolute()
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ValueError("当前 ParaTranz 文件路径无效。")


def legacy_plugin_paths(paths):
    """Remove only the plugin suffix in the root name or first directory."""
    result = []
    for path in paths:
        first, separator, rest = path.partition("/")
        legacy = re.sub(r"\.(esp|esm|esl)(?=(_[0-9a-f]{10})?(\.json)?$)", "", first, flags=re.IGNORECASE)
        if legacy != first:
            result.append(legacy + separator + rest)
    return tuple(dict.fromkeys(result))


def _family(path):
    first = path.partition("/")[0]
    return (first[:-5] if first.endswith(".json") else first).casefold()


def unambiguous_legacy_paths(source_paths: Mapping):
    """An old name may not stand for another current or legacy plugin source."""
    aliases = {source: legacy_plugin_paths(paths) for source, paths in source_paths.items()}
    owners = defaultdict(set)
    for source, paths in source_paths.items():
        for path in (*paths, *aliases[source]):
            owners[_family(path)].add(source)
    for family in tuple(owners):
        partition = re.fullmatch(r"(.+)_[1-9][0-9]*", family)
        if partition and partition[1] in owners:
            shared = owners[family] | owners[partition[1]]
            owners[family].update(shared)
            owners[partition[1]].update(shared)
    return {
        source: tuple(path for path in paths if len(owners[_family(path)]) == 1) for source, paths in aliases.items()
    }


def preferred_download_paths(inventory, current_paths, legacy_paths=()):
    """Any existing new-layout file takes precedence for this entire source."""
    folded = frozenset(path.casefold() for path in current_paths)
    if any(matches_current_path(path.casefold(), folded) for path in inventory):
        return tuple(current_paths)
    return tuple(legacy_paths)


def select_download_files(inventory, current_paths, legacy_paths=(), *, blocked_legacy_paths=()):
    """Use actual inventory spelling; never hide an unreadable preferred file."""
    paths = preferred_download_paths(inventory, current_paths, legacy_paths)
    folded = frozenset(path.casefold() for path in paths)
    groups = defaultdict(list)
    for path in inventory:
        if matches_current_path(path.casefold(), folded):
            groups[path.casefold()].append(path)
    result = []
    for variants in groups.values():
        exact = [path for path in variants if path in paths]
        if not exact:
            exact = [path for path in variants if matches_current_path(path, paths)]
        if len(exact) == 1:
            selected = exact[0]
        elif len(variants) == 1:
            selected = variants[0]
        else:
            raise ValueError(f"云端存在仅大小写不同的多个路径，无法唯一定位：{'、'.join(variants)}。")
        result.append((selected, inventory[selected]))
    if not result and blocked_legacy_paths:
        blocked = frozenset(path.casefold() for path in blocked_legacy_paths)
        existing = [path for path in inventory if matches_current_path(path.casefold(), blocked)]
        if existing:
            raise ValueError(f"旧版路径同时对应多个插件，无法唯一定位：{'、'.join(existing)}。")
    return tuple(result)
