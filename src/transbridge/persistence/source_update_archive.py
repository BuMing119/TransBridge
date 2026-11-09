# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read-only source-update preimages and their saved source dependency closure."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from .v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from .v2.migration import migrate_to_current
from .v2.models import ProjectDto, VariantDto
from .v2.schema import parse_json_bytes, serialize_document, validate_v2

_PREFIX = "assets/source-updates/"


def backup_sources(raw: bytes) -> tuple[dict, ...]:
    """Read historical documents through migration without activating their journal."""
    manifest = parse_json_bytes(raw)
    digest = manifest.pop("manifest_digest")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("status") not in {"prepared", "committed"}
        or digest != hashlib.sha256(serialize_document(manifest)).hexdigest()
    ):
        raise ValueError("项目包换源备份的版本或摘要无效")
    project_ref = ProjectRef(ProjectId(manifest["project_id"]))
    previous_project = None
    variants = set()
    records = manifest["records"]
    if not isinstance(records, list) or not records or records[-1]["kind"] != "project":
        raise ValueError("项目包换源备份缺少旧工程记录")
    for index, record in enumerate(records):
        if record["kind"] == "project":
            if index != len(records) - 1 or record["identity"] != project_ref.identity.value:
                raise ValueError("项目包换源备份的工程归属不匹配")
            ref = project_ref
        elif record["kind"] == "variant":
            ref = VariantRef(VariantId(record["identity"]), project_ref.identity)
            if ref.identity.value in variants:
                raise ValueError("项目包换源备份包含重复版本")
            variants.add(ref.identity.value)
        else:
            raise ValueError("项目包换源备份包含未知记录")
        previous = base64.b64decode(record["previous"], validate=True)
        if hashlib.sha256(previous).hexdigest() != record["previous_digest"]:
            raise ValueError("项目包换源备份的旧记录摘要不匹配")
        dto = validate_v2(migrate_to_current(parse_json_bytes(previous), ref).document, ref)
        if isinstance(dto, ProjectDto):
            previous_project = dto
        elif not isinstance(dto, VariantDto):
            raise ValueError("项目包换源备份的旧记录无效")
    if previous_project is None or set(previous_project.envelope.data["variant_ids"]) != variants:
        raise ValueError("项目包换源备份未包含全部旧版本")
    return tuple(previous_project.envelope.data["sources"])


def archive_backup_sources(archive) -> tuple[dict, ...]:
    sources = []
    for info in archive.infolist():
        if info.is_dir() or not info.filename.startswith(_PREFIX):
            continue
        name = info.filename.removeprefix(_PREFIX)
        if "/" in name or not name.endswith(".json"):
            raise ValueError("项目包换源备份路径无效")
        raw = archive.read(info)
        manifest = parse_json_bytes(raw)
        expected = hashlib.sha256(manifest["transaction_id"].encode()).hexdigest() + ".json"
        if name != expected:
            raise ValueError("项目包换源备份事务标识不匹配")
        sources.extend(backup_sources(raw))
    return tuple(sources)


def capture_backups(archive, native_directory: Path, imported_directory: Path) -> tuple[dict, ...]:
    """Keep permanent backups inert under the imported Project's owned directory."""
    seen = {}
    sources = []
    for directory in (native_directory, imported_directory):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("换源备份链接超出工程目录")
            raw = path.read_bytes()
            manifest = parse_json_bytes(raw)
            name = hashlib.sha256(manifest["transaction_id"].encode()).hexdigest() + ".json"
            if path.name != name:
                raise ValueError("换源备份文件名与事务标识不匹配")
            referenced = backup_sources(raw)
            if name in seen:
                if seen[name] != raw:
                    raise ValueError("工程包含同名但内容不同的换源备份")
                continue
            seen[name] = raw
            sources.extend(referenced)
            archive.writestr(_PREFIX + name, raw)
    return tuple(sources)
