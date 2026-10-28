"""Optional localized BSA output, with loose files as the default."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from sse_bsa import BSAArchive

from transbridge.parser.localized_resources import candidate_input_paths

from .contracts import FormatId, SourceDescriptor
from .plugin_localized_sources import _snapshot_localized_sources, localized_physical_inputs

_SUFFIXES = {FormatId.STRINGS: ".strings", FormatId.DLSTRINGS: ".dlstrings", FormatId.ILSTRINGS: ".ilstrings"}


def bsa_output_enabled(snapshot, options=()) -> bool:
    return (
        snapshot is not None
        and snapshot.format_id is FormatId.PLUGIN_SSE
        and dict(options).get("pack_bsa", False) is True
        and bool(_snapshot_localized_sources(snapshot))
    )


def localized_loose_paths(snapshot, target: str, options=()) -> tuple[str, ...]:
    language = str(dict(options).get("language", dict(snapshot.metadata).get("localized_language", "english")))
    if not language or any(char in language for char in "/\\:") or language in {".", ".."}:
        raise ValueError("localized output language must be a filename component")
    plugin = Path(target)
    paths = tuple(
        str(plugin.parent / "Strings" / f"{plugin.stem}_{language.capitalize()}{_SUFFIXES[source.format_id]}")
        for source, _entries in _snapshot_localized_sources(snapshot)
    )
    if len(set(paths)) != len(paths):
        raise ValueError("localized outputs must have unique filenames")
    return paths


def bsa_output_path(plugin: Path) -> Path:
    """Use the same archive naming rules as resource discovery and reparse."""
    return candidate_input_paths(plugin)[-1]


def conflicting_loose_outputs(snapshot, target: str, options=()) -> tuple[str, ...]:
    if not bsa_output_enabled(snapshot, options):
        return ()
    return tuple(path for path in localized_loose_paths(snapshot, target, options) if Path(path).exists())


def validate_bsa_destination(snapshot, target: Path, options=()) -> None:
    if not bsa_output_enabled(snapshot, options):
        return
    archive = bsa_output_path(target).resolve()
    if any(
        archive == Path(path).resolve() for path, captured in localized_physical_inputs(snapshot) if captured is None
    ):
        raise ValueError("输出不能覆盖来源依赖的 BSA 归档，请选择其他另存目录。")
    if conflicting_loose_outputs(snapshot, str(target), options):
        raise ValueError("目标位置已有同名散装 STRINGS，会优先于 BSA 读取；请选择其他另存目录。")


@contextmanager
def localized_render_target(target: Path, *, packed: bool):
    if not packed:
        yield target
        return
    with TemporaryDirectory(prefix=".transbridge-bsa-input-", dir=target.parent) as directory:
        yield Path(directory) / target.name


def pack_localized_strings(target: Path, render_target: Path, companions: list[str]) -> str:
    """Pack only the private rendered STRINGS and verify every member's bytes."""
    archive_path = bsa_output_path(target)
    expected = {f"strings/{Path(path).name}".casefold(): Path(path).read_bytes() for path in companions}
    BSAArchive.create_archive(render_target.parent, archive_path)
    archive = BSAArchive(archive_path)
    actual = {str(path).replace("\\", "/").casefold(): path for path in archive.files}
    if set(actual) != set(expected) or len(actual) != len(archive.files):
        raise ValueError("BSA output members differ from the confirmed localized resources")
    for name, content in expected.items():
        with archive.get_file_stream(actual[name]) as stream:
            if stream.read() != content:
                raise ValueError(f"BSA output changed localized resource bytes: {name}")
    return str(archive_path)


@contextmanager
def materialized_bsa_members(plugin: Path, requests):
    """Validate packed bytes using the same per-table fidelity checks as loose output."""
    archive = BSAArchive(bsa_output_path(plugin))
    actual = {str(path).replace("\\", "/").casefold(): path for path in archive.files}
    expected = {f"strings/{Path(member.target.uri).name}".casefold() for _adapter, member in requests}
    if set(actual) != expected or len(actual) != len(archive.files):
        raise ValueError("BSA output contains missing, duplicate or unexpected members")
    with TemporaryDirectory(prefix="transbridge-bsa-verify-") as directory:
        materialized = []
        for adapter, member in requests:
            name = Path(member.target.uri).name
            target = Path(directory) / name
            with archive.get_file_stream(actual[f"strings/{name}".casefold()]) as stream:
                target.write_bytes(stream.read())
            materialized.append((adapter, replace(member, target=SourceDescriptor(str(target), name))))
        yield tuple(materialized)
