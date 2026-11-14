# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Resolve archive members against full inventory paths, independent of export folders."""

from collections import defaultdict


def selected_members(members, inventory, selected):
    """Order candidates by export directory; callers stop at the first valid member."""
    indexed = _index_members(members, inventory)
    coverage = defaultdict(set)
    for file_id, path in selected.items():
        for name, owners in indexed.get(file_id, ()):
            if len(owners) == 1:
                coverage[_directory(name, path)].add(file_id)
    result = {}
    for file_id, path in selected.items():
        appended = f"raw/{path}.json"
        canonical = []
        if appended in members:
            canonical.append(appended)
        if path.endswith(".json"):
            unchanged = f"raw/{path}"
            if unchanged in members:
                if path[:-5] in inventory and not canonical:
                    raise ValueError(f"压缩包原始词条文件归属不明确：{unchanged}；本轮未采用压缩包。")
                if path[:-5] not in inventory:
                    canonical.append(unchanged)
        candidates = indexed.get(file_id, ())
        for name, owners in candidates:
            if len(owners) != 1 and not canonical:
                raise ValueError(f"压缩包词条文件归属不明确：{name}；本轮未采用压缩包。")
        if not candidates:
            raise ValueError(
                f"新压缩包缺少所选文件的词条文件：{path}"
                f"（已检索全包 {len(members)} 个文件，按完整路径 {path} 或 {path}.json 匹配，"
                f"包括 raw/{path}.json、raw/{path}；允许任意外层目录）；本轮未采用压缩包。"
            )
        discovered = [name for name, owners in candidates if len(owners) == 1 and name not in canonical]
        discovered.sort(key=lambda name: _priority(name, path, coverage))
        result[file_id] = (*canonical, *discovered)
    return result


def _directory(name, path):
    suffix = path + ".json" if name.endswith(path + ".json") else path
    return name[: -len(suffix)].rstrip("/")


def _priority(name, path, coverage):
    directory = _directory(name, path)
    kind = directory.rsplit("/", 1)[-1].casefold()
    rank = 0 if kind == "raw" else 1 if kind in {"utf8", "utf-8"} else 2
    # A shared ordering keeps each file in the preferred directory when available.
    return rank, -len(coverage[directory]), directory, not name.endswith(path + ".json"), name


def _index_members(members, inventory):
    aliases = defaultdict(set)
    for path, file_id in inventory.items():
        aliases[path].add(file_id)
        aliases[path + ".json"].add(file_id)
    indexed = defaultdict(list)
    for name in sorted(members):
        parts = name.split("/")
        owners = set()
        # Compare at component boundaries; never shorten the inventory path to a basename.
        for start in range(len(parts)):
            owners.update(aliases.get("/".join(parts[start:]), ()))
        for file_id in owners:
            indexed[file_id].append((name, owners))
    return indexed
