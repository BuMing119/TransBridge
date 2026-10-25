"""Discover and capture localized plugin templates without retaining whole archives."""

from contextlib import ExitStack
from dataclasses import dataclass
import logging
from pathlib import Path, PurePosixPath

from sse_bsa import bsa_archive

log = logging.getLogger("SkyrimStringsFile")
STRINGS_SUFFIXES = (".strings", ".dlstrings", ".ilstrings")
# _ResourcePack.esl stores its strings in its own _ResourcePack.bsa.
BASE_GAME_PLUGINS = frozenset({"skyrim", "update", "hearthfires", "dragonborn", "dawnguard"})


@dataclass(frozen=True, slots=True)
class LocalizedResource:
    name: str
    content: bytes
    source_path: Path
    member_path: str | None = None

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.name).suffix.lower()


def candidate_input_paths(
    plugin_path: Path,
    language: str = "english",
    *,
    base_game_plugins: frozenset[str] = BASE_GAME_PLUGINS,
) -> tuple[Path, ...]:
    """Include missing loose companions and the archive that could supply them."""
    plugin_path = Path(plugin_path)
    loose = _loose_paths(plugin_path.parent / "Strings", plugin_path.stem, language)
    archive_name = (
        "Skyrim - Interface.bsa" if plugin_path.stem.casefold() in base_game_plugins else f"{plugin_path.stem}.bsa"
    )
    return (*loose, _case_child(plugin_path.parent, archive_name))


def capture_plugin_resources(
    plugin_path: Path,
    language: str = "english",
    *,
    base_game_plugins: frozenset[str] = BASE_GAME_PLUGINS,
) -> tuple[LocalizedResource, ...]:
    """Loose files own their type, including empty tables; only absent types use BSA."""
    plugin_path = Path(plugin_path)
    candidates = candidate_input_paths(plugin_path, language, base_game_plugins=base_game_plugins)
    resources = {resource.suffix: resource for resource in _capture_loose(candidates[:-1])}
    missing = frozenset(STRINGS_SUFFIXES).difference(resources)
    archive = candidates[-1]
    if missing and archive.exists():
        resources.update(
            (resource.suffix, resource) for resource in _capture_archive(archive, plugin_path.stem, language, missing)
        )
    return tuple(resources[suffix] for suffix in STRINGS_SUFFIXES if suffix in resources)


def capture_strings_directory(
    strings_dir: Path, plugin_stem: str, language: str = "english"
) -> tuple[LocalizedResource, ...]:
    return _capture_loose(_loose_paths(Path(strings_dir), plugin_stem, language))


def _case_child(parent: Path, name: str, *, children=None) -> Path:
    if children is None:
        children = tuple(parent.iterdir()) if parent.exists() else ()
    matches = tuple(child for child in children if child.name.casefold() == name.casefold())
    exact = next((child for child in matches if child.name == name), None)
    if exact is not None:
        return exact
    if len(matches) > 1:
        raise ValueError(f"本地化资源大小写不唯一：{parent / name}")
    return matches[0] if matches else parent / name


def _loose_paths(strings_dir: Path, plugin_stem: str, language: str) -> tuple[Path, ...]:
    directory = _case_child(strings_dir.parent, strings_dir.name)
    children = tuple(directory.iterdir()) if directory.exists() else ()
    prefix = f"{plugin_stem}_{language.capitalize()}"
    return tuple(_case_child(directory, f"{prefix}{suffix}", children=children) for suffix in STRINGS_SUFFIXES)


def _capture_loose(paths: tuple[Path, ...]) -> tuple[LocalizedResource, ...]:
    resources = []
    for path in paths:
        if path.exists():
            try:
                resources.append(LocalizedResource(path.name, path.read_bytes(), path))
            except Exception as exc:
                log.warning("Failed to read localized resource %s: %s", path, exc)
                raise ValueError(f"无法读取本地化资源 {path}：{exc}") from exc
    return tuple(resources)


def _capture_archive(path: Path, stem: str, language: str, missing: frozenset[str]) -> tuple[LocalizedResource, ...]:
    member = None
    try:
        with ExitStack() as stack:
            archive = bsa_archive.BSAArchive(path)
            close = getattr(archive, "close", None)
            if callable(close):
                stack.callback(close)
            matching = {}
            prefix = f"{stem}_{language}".casefold()
            for candidate in archive.glob(f"strings/{prefix}.*"):
                normalized = PurePosixPath(str(candidate).replace("\\", "/"))
                suffix = normalized.suffix.lower()
                if (
                    normalized.parent.as_posix().casefold() == "strings"
                    and suffix in missing
                    and normalized.name.casefold() == f"{prefix}{suffix}"
                ):
                    if suffix in matching and matching[suffix] != str(candidate):
                        raise ValueError(f"BSA本地化资源大小写不唯一：{matching[suffix]}、{candidate}")
                    matching[suffix] = str(candidate)
            resources = []
            for suffix in STRINGS_SUFFIXES:
                member = matching.get(suffix)
                if member is not None:
                    with archive.get_file_stream(member) as stream:
                        content = stream.read()
                    name = PurePosixPath(member.replace("\\", "/")).name
                    resources.append(LocalizedResource(name, content, path, member))
            if resources:
                log.info("Found strings in BSA %s: %s", path.name, [resource.member_path for resource in resources])
            return tuple(resources)
    except Exception as exc:
        source = f"{path}/{member}" if member is not None else str(path)
        log.warning("Failed to extract localized resource %s: %s", source, exc)
        raise ValueError(f"无法读取BSA本地化资源 {source}：{exc}") from exc
