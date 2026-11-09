# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Strict container and document reads for portable project archives."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import zipfile

from transbridge.application.preflight.sources import source_reference

from .project_archive_files import is_business_asset, member_destination, validate_archive
from .v2.filesystem import RepositoryPaths
from .v2.ids import EntityRef, ProjectId, ProjectRef, VariantId, VariantRef
from .v2.migration import migrate_to_current
from .v2.models import SCHEMA_VERSION, PersistenceDto, ProjectDto, VariantDto
from .v2.schema import parse_json_bytes, validate_v2, version_of
from .v2.variant import VariantSnapshot


@contextmanager
def open_import_archive(source: str):
    path = Path(source)
    if path.suffix.lower() != ".transbridge" or not path.is_file():
        raise ValueError("请选择 .transbridge 工程包。")
    with zipfile.ZipFile(path) as archive:
        validate_archive(archive)
        yield archive


def read_manifest(archive: zipfile.ZipFile) -> dict:
    if archive.getinfo("archive.json").file_size > 256 * 1024**2:
        raise ValueError("项目包清单过大")
    manifest = parse_json_bytes(archive.read("archive.json"))
    if manifest.get("archive_version") != 1:
        raise ValueError("不支持的项目包版本")
    return manifest


def read_project_record(manifest: dict) -> ProjectDto:
    document = manifest["project"]
    ref = ProjectRef(ProjectId(document["id"]))
    project = _validate_document(document, ref)
    if not isinstance(project, ProjectDto):
        raise ValueError("项目包缺少有效工程记录")
    return project


def read_current_archive(
    archive: zipfile.ZipFile, paths: RepositoryPaths
) -> tuple[ProjectDto, tuple[VariantSnapshot, ...], tuple[dict, ...], tuple[str, ...]]:
    manifest = read_manifest(archive)
    project = read_project_record(manifest)
    project_ref = ProjectRef(ProjectId(project.envelope.identity))
    variants = []
    for document in manifest["variants"]:
        ref = VariantRef(VariantId(document["id"]), project_ref.identity)
        dto = _validate_document(document, ref)
        if not isinstance(dto, VariantDto):
            raise ValueError("项目包版本记录无效")
        variants.append(VariantSnapshot.from_dto(dto, ref))
    ids = [item.ref.identity.value for item in variants]
    if len(ids) != len(set(ids)) or set(ids) != set(project.envelope.data["variant_ids"]):
        raise ValueError("项目包中的版本列表不完整或重复")
    snapshots = tuple(manifest.get("snapshots", ()))
    for snapshot in snapshots:
        ref = VariantRef(VariantId(snapshot["variant"]["id"]), project_ref.identity)
        if snapshot.get("schema_version") != 1 or snapshot.get("project_id") != project_ref.identity.value:
            raise ValueError("项目包快照归属不匹配")
        if ref.identity.value not in ids or not isinstance(snapshot.get("name"), str) or not snapshot["name"].strip():
            raise ValueError("项目包快照名称或版本无效")
        snapshot["variant"] = _validate_document(snapshot["variant"], ref).envelope.to_dict()
    members = _read_source_members(archive, paths, manifest, project, project_ref)
    return project, tuple(variants), snapshots, members


def _validate_document(document: dict, ref: EntityRef) -> PersistenceDto:
    if version_of(document) < SCHEMA_VERSION:
        document = migrate_to_current(document, ref).document
    return validate_v2(document, ref)


def _read_source_members(
    archive: zipfile.ZipFile, paths: RepositoryPaths, manifest: dict, project: ProjectDto, project_ref: ProjectRef
) -> tuple[str, ...]:
    from .source_artifact_archive import is_artifact_member, validate_artifacts
    from .source_update_archive import archive_backup_sources

    owned = paths.guard(os.path.join(paths.root, "projects", project_ref.identity.encoded))
    candidates = tuple(
        info.filename
        for info in archive.infolist()
        if not info.is_dir() and info.filename != "archive.json" and not is_artifact_member(info.filename)
    )
    destinations = [paths.guard(member_destination(name, owned)) for name in candidates]
    if len({os.path.normcase(path) for path in destinations}) != len(destinations):
        raise ValueError("项目包资产目标重复")
    members = tuple(
        name for name in candidates if name.startswith("sources/") or is_business_asset(name.removeprefix("assets/"))
    )
    locations = {item["source_id"]: item["member"] for item in manifest["sources"]}
    sources = project.envelope.data["sources"]
    if len(locations) != len(manifest["sources"]) or set(locations) != {item["source_id"] for item in sources}:
        raise ValueError("项目包来源列表不完整或重复")
    for item in sources:
        member = locations[item["source_id"]]
        if member not in members or not member.startswith("sources/"):
            raise ValueError("项目包缺少来源文件")
        payload = archive.read(member)
        if item.get("fingerprint") and hashlib.sha256(payload).hexdigest() != item["fingerprint"]:
            raise ValueError("项目包来源文件指纹不匹配")
        item.update(source_reference(item))
    validate_v2(project.envelope.to_dict(), project_ref)
    validate_artifacts(archive, sources, locations, backup_sources=archive_backup_sources(archive))
    return members
