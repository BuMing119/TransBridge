"""Read bounded exports and capture independent plugin/Strings inputs."""

from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from zipfile import ZipFile

from transbridge.application.io.stage_policy import Stage
from transbridge.application.io.strings_adapter import _parse_records
from transbridge.parser.localized_resources import capture_plugin_resources

from .matching import RemoteRow

SUFFIXES = (".strings", ".dlstrings", ".ilstrings")


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def project_plugins(path: Path) -> list[Path]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    sources = document["data"]["sources"]
    result = []
    for source in sources:
        if source.get("enabled", True) and source.get("format_id") == "plugin.sse":
            result.append(Path(source["location"]).resolve(strict=True))
    if not result or len({p.stem.casefold() for p in result}) != len(result):
        raise ValueError("Project must contain uniquely named, enabled plugin sources")
    return sorted(result, key=lambda p: p.name.casefold())


def read_export(content: bytes, plugin_stems: set[str]) -> list[RemoteRow]:
    from io import BytesIO

    with ZipFile(BytesIO(content)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or sum(i.file_size for i in archive.infolist()) > 512 * 1024**2:
            raise ValueError("Duplicate ZIP members or export exceeding 512 MiB")
        selected = [n for n in names if n.startswith("utf8/") and n.lower().endswith(".json")]
        if not selected:
            selected = [n for n in names if n.lower().endswith(".json") and not n.startswith("raw/")]
        if not selected:
            raise ValueError("Export has no utf8/ or ordinary JSON files")
        rows = []
        for name in sorted(selected):
            entries = json.loads(archive.read(name).decode("utf-8-sig"))
            if not isinstance(entries, list):
                raise ValueError(f"Expected a JSON array: {name}")
            counterpart = "raw/" + name.removeprefix("utf8/") + ".json"
            if name.startswith("utf8/") and counterpart in names:
                if json.loads(archive.read(counterpart).decode("utf-8-sig")) != entries:
                    raise ValueError(f"raw/utf8 disagree: {name}")
            stem = PurePosixPath(name).stem.casefold()
            if stem.endswith((".esm", ".esp", ".esl")):
                stem = stem.rsplit(".", 1)[0]
            if stem not in plugin_stems:
                partition = re.fullmatch(r"(.+)_[1-9][0-9]*", stem)
                if partition and partition[1] in plugin_stems:
                    stem = partition[1]
            for position, entry in enumerate(entries):
                if not isinstance(entry, dict) or any(
                    not isinstance(entry.get(key), str) for key in ("key", "original", "translation")
                ):
                    raise ValueError(f"Invalid export row: {name}:{position}")
                stage = entry.get("stage", 0)
                if Stage.from_value(stage) is None or not entry["key"]:
                    raise ValueError(f"Invalid key/stage: {name}:{position}")
                rows.append(
                    RemoteRow(
                        f"{name}:{position}", stem, entry["key"], entry["original"], entry["translation"], stage, entry
                    )
                )
        return rows


def read_table(path: Path):
    content = path.read_bytes()
    records = _parse_records(content, path.suffix.lower() != ".strings")
    if any(count > 1 for count in Counter(record.string_id for record in records).values()):
        raise ValueError(f"Duplicate Strings ID: {path}")
    return {r.string_id: r.text for r in records}, digest(content)


def capture_plugin(plugin: Path, destination: Path, language: str):
    resources = capture_plugin_resources(plugin, language)
    if {resource.suffix for resource in resources} != set(SUFFIXES):
        raise ValueError(f"All three localized tables are required: {plugin}")
    content = plugin.read_bytes()
    # This tool intentionally handles external localized tables only.
    if content[:4] != b"TES4" or not int.from_bytes(content[8:12], "little") & 0x80:
        raise ValueError(f"Not a localized Skyrim plugin: {plugin}")
    destination.mkdir(parents=True, exist_ok=False)
    target = destination / plugin.name
    target.write_bytes(content)
    strings = destination / "Strings"
    strings.mkdir()
    fingerprints = [{"path": str(plugin), "sha256": digest(content)}]
    for resource in resources:
        (strings / resource.name).write_bytes(resource.content)
        fingerprints.append({
            "path": str(resource.source_path),
            "member": resource.member_path,
            "sha256": digest(resource.content),
        })
    return target, fingerprints
