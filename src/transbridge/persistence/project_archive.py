"""Import/export V2 project bundles without writing through legacy UI state."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from transbridge.application.contracts import RequestContext
from transbridge.application.projects.snapshots import ProjectSnapshotPort

from .project_archive_files import capture_owned_assets, capture_sources, member_destination, validate_archive
from .project_catalog_document import (
    ProjectCatalogRecord,
    build_project_catalog,
    parse_project_catalog,
    project_name_key,
)
from .v2.atomic_documents import AtomicDocumentStore
from .v2.filesystem import RepositoryPaths
from .v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from .v2.migration import migrate_to_current
from .v2.models import ProjectDto, VariantDto
from .v2.schema import parse_json_bytes, validate_v2
from .v2.variant import VariantSnapshot


@dataclass(frozen=True, slots=True)
class ProjectArchiveInspection:
    """Read-only import facts used by the GUI before any archive is published."""

    project_id: str
    name: str
    identity_conflict: bool
    name_conflict: bool

    @property
    def requires_copy(self) -> bool:
        return self.identity_conflict

    @property
    def requires_rename(self) -> bool:
        return self.name_conflict


class ProjectArchiveService:
    def __init__(
        self,
        root,
        filesystem,
        projects,
        variants,
        lifecycle,
        snapshots: ProjectSnapshotPort,
        *,
        id_factory,
        source_preparer,
    ) -> None:
        self._filesystem = filesystem
        self._paths = RepositoryPaths(root, filesystem)
        self._documents = AtomicDocumentStore(root, filesystem)
        self._projects = projects
        self._variants = variants
        self._lifecycle = lifecycle
        self._snapshots = snapshots
        self._ids = id_factory
        self._source_preparer = source_preparer

    def export_project(self, destination: str, context: RequestContext) -> str:
        active = self._lifecycle.active
        if active is None or active.variant is None:
            raise ValueError("请先打开一个翻译版本")
        if active.dirty:
            raise ValueError("请先保存当前工程再导出")
        if context.project_id not in (None, active.project_ref.identity.value):
            raise ValueError("导出的工程已改变，请重试")
        if context.variant_id not in (None, active.formal_variant_ref.identity.value):
            raise ValueError("导出的版本已改变，请重试")
        destination_path = Path(destination).resolve()
        if destination_path.is_relative_to(Path(self._paths.root)):
            raise ValueError("请将项目包导出到持久化目录之外，避免覆盖工程数据")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        project = deepcopy(active.project.envelope.to_dict())
        ref = active.project_ref
        lease = self._lifecycle.acquire_export_lease(context)
        if not lease.is_success:
            raise ValueError(lease.diagnostics[0].message)
        token = lease.value["token"]
        consumed = False
        try:
            with tempfile.TemporaryDirectory(prefix=".transbridge-export-", dir=destination_path.parent) as temporary:
                staging = Path(temporary) / "project.transbridge"
                with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED) as archive:
                    with self._projects.mutation_lock:
                        if self._projects.read_snapshot(ref) != active.project:
                            raise ValueError("磁盘上的工程记录已改变，请重新打开后导出")
                        variants = []
                        snapshots = []
                        for identity in project["data"]["variant_ids"]:
                            variant_ref = VariantRef(VariantId(identity), ref.identity)
                            dto = self._variants.read_snapshot(variant_ref)
                            if (
                                variant_ref == active.formal_variant_ref
                                and VariantSnapshot.from_dto(dto, variant_ref) != active.variant.snapshot()
                            ):
                                raise ValueError("磁盘上的活动版本已改变，请重新打开后导出")
                            variants.append(dto.envelope.to_dict())
                            for info in self._snapshots.list(variant_ref):
                                snapshot = self._snapshots.load(info.identity, variant_ref)
                                snapshots.append({
                                    "schema_version": 1,
                                    "name": info.name,
                                    "project_id": ref.identity.value,
                                    "variant": snapshot.to_dto().envelope.to_dict(),
                                })
                        sources = capture_sources(archive, project["data"]["sources"])
                        owned = Path(self._paths.root) / "projects" / ref.identity.encoded
                        capture_owned_assets(archive, owned)
                        archive.writestr(
                            "archive.json",
                            json.dumps(
                                {
                                    "archive_version": 1,
                                    "project": project,
                                    "variants": variants,
                                    "snapshots": snapshots,
                                    "sources": sources,
                                },
                                ensure_ascii=False,
                            ).encode("utf-8"),
                        )
                with zipfile.ZipFile(staging) as verification:
                    validate_archive(verification)
                checked = self._lifecycle.validate_export_lease(token, context)
                consumed = True
                if not checked.is_success:
                    raise ValueError(checked.diagnostics[0].message)
                os.replace(staging, destination_path)
            return str(destination_path)
        finally:
            if not consumed:
                self._lifecycle.validate_export_lease(token, context)

    def inspect_import(self, source: str, context: RequestContext) -> ProjectArchiveInspection:
        """Validate an archive and report conflicts without changing persistence."""

        with zipfile.ZipFile(source) as archive:
            project, _variants, _snapshots, _members = self._decode_archive(archive, context)
        ref = ProjectRef(ProjectId(project.envelope.identity))
        records = self._catalog_records()
        return ProjectArchiveInspection(
            ref.identity.value,
            project.envelope.data["name"],
            self._filesystem.exists(self._projects.path_for(ref))
            or any(item.project_id == ref.identity.value for item in records),
            any(item.name_key == project_name_key(project.envelope.data["name"]) for item in records),
        )

    def import_project(
        self,
        source: str,
        context: RequestContext,
        *,
        requested_name: str | None = None,
        copy_on_identity_conflict: bool = False,
    ) -> str:
        with zipfile.ZipFile(source) as archive:
            project, variants, snapshots, members = self._decode_archive(archive, context)
            records = self._catalog_records()
            ref = ProjectRef(ProjectId(project.envelope.identity))
            identity_conflict = self._filesystem.exists(self._projects.path_for(ref)) or any(
                item.project_id == ref.identity.value for item in records
            )
            if identity_conflict:
                if not copy_on_identity_conflict:
                    raise ValueError("此工程已存在；请选择“作为副本导入”以保留现有工程")
                project, variants, snapshots = self._copy_identity(
                    project,
                    variants,
                    snapshots,
                    requested_name=requested_name,
                )
            elif requested_name is not None:
                project = self._rename_project(project, requested_name)
            return self._publish(archive, project, variants, snapshots, members)

    def _decode_archive(self, archive, context):
        validate_archive(archive)
        if "archive.json" in archive.namelist():
            return self._read_current(archive)
        if "project.json" in archive.namelist():
            from .legacy_project_archive import decode_legacy_archive

            project, variants, snapshots = decode_legacy_archive(
                archive, project_id=ProjectId(self._ids()), source_preparer=self._source_preparer, context=context
            )
            return project, variants, snapshots, ()
        raise ValueError("项目包缺少 archive.json 或 project.json")

    def _catalog_records(self) -> tuple[ProjectCatalogRecord, ...]:
        path = self._documents.path("project-catalog.json")
        if not self._filesystem.exists(path):
            return ()
        return parse_project_catalog(self._filesystem.read_bytes(path))

    def _rename_project(self, project: ProjectDto, requested_name: str) -> ProjectDto:
        document = deepcopy(project.envelope.to_dict())
        # The strict catalog helper is the canonical display-name validator.
        name = ProjectCatalogRecord(document["id"], requested_name.strip(), project_name_key(requested_name)).name
        document["data"]["name"] = name
        ref = ProjectRef(ProjectId(document["id"]))
        renamed = validate_v2(document, ref)
        if not isinstance(renamed, ProjectDto):
            raise ValueError("项目包中的工程记录无法重命名")
        return renamed

    def _copy_identity(self, project, variants, snapshots, *, requested_name):
        old_ref = ProjectRef(ProjectId(project.envelope.identity))
        new_ref = ProjectRef(ProjectId(self._ids()))
        variant_ids = {item.ref.identity.value: VariantId(self._ids()) for item in variants}

        project_document = deepcopy(project.envelope.to_dict())
        project_document["id"] = new_ref.identity.value
        data = project_document["data"]
        data["variant_ids"] = [variant_ids[item].value for item in data["variant_ids"]]
        active_id = data.get("active_variant_id")
        data["active_variant_id"] = None if active_id is None else variant_ids[active_id].value
        if requested_name is not None:
            data["name"] = requested_name.strip()

        old_owned = self._paths.guard(os.path.join(self._paths.root, "projects", old_ref.identity.encoded))
        new_owned = self._paths.guard(os.path.join(self._paths.root, "projects", new_ref.identity.encoded))
        for source in data["sources"]:
            location = self._paths.guard(source["location"])
            relative = os.path.relpath(location, old_owned)
            if relative == os.pardir or relative.startswith(os.pardir + os.sep):
                raise ValueError("项目包来源路径无法安全换用副本身份")
            source["location"] = self._paths.guard(os.path.join(new_owned, relative))
            if "path" in source.get("legacy", {}):
                source["legacy"]["path"] = source["location"]

        project_copy = validate_v2(project_document, new_ref)
        if not isinstance(project_copy, ProjectDto):
            raise ValueError("项目包中的工程记录无法复制")

        variant_copies = []
        for item in variants:
            document = deepcopy(item.to_dto().envelope.to_dict())
            new_variant_id = variant_ids[item.ref.identity.value]
            document["id"] = new_variant_id.value
            document["data"]["project_id"] = new_ref.identity.value
            ref = VariantRef(new_variant_id, new_ref.identity)
            dto = validate_v2(document, ref)
            if not isinstance(dto, VariantDto):
                raise ValueError("项目包中的翻译版本无法复制")
            variant_copies.append(VariantSnapshot.from_dto(dto, ref))

        snapshot_copies = []
        for item in snapshots:
            document = deepcopy(item)
            old_variant_id = document["variant"]["id"]
            new_variant_id = variant_ids[old_variant_id]
            document["project_id"] = new_ref.identity.value
            document["variant"]["id"] = new_variant_id.value
            document["variant"]["data"]["project_id"] = new_ref.identity.value
            validate_v2(document["variant"], VariantRef(new_variant_id, new_ref.identity))
            snapshot_copies.append(document)
        return project_copy, tuple(variant_copies), tuple(snapshot_copies)

    def _read_current(self, archive):
        if archive.getinfo("archive.json").file_size > 256 * 1024**2:
            raise ValueError("项目包清单过大")
        manifest = parse_json_bytes(archive.read("archive.json"))
        if manifest.get("archive_version") != 1:
            raise ValueError("不支持的项目包版本")
        project_ref = ProjectRef(ProjectId(manifest["project"]["id"]))
        project = validate_v2(migrate_to_current(manifest["project"], project_ref).document, project_ref)
        if not isinstance(project, ProjectDto):
            raise ValueError("项目包缺少有效工程记录")
        variants = []
        for document in manifest["variants"]:
            ref = VariantRef(VariantId(document["id"]), project_ref.identity)
            dto = validate_v2(migrate_to_current(document, ref).document, ref)
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
            if (
                ref.identity.value not in ids
                or not isinstance(snapshot.get("name"), str)
                or not snapshot["name"].strip()
            ):
                raise ValueError("项目包快照名称或版本无效")
            validate_v2(snapshot["variant"], ref)
        owned = self._paths.guard(os.path.join(self._paths.root, "projects", project_ref.identity.encoded))
        members = tuple(
            info.filename for info in archive.infolist() if not info.is_dir() and info.filename != "archive.json"
        )
        destinations = [self._paths.guard(member_destination(name, owned)) for name in members]
        if len({os.path.normcase(path) for path in destinations}) != len(destinations):
            raise ValueError("项目包资产目标重复")
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
            item["location"] = self._paths.guard(member_destination(member, owned))
            if "path" in item.get("legacy", {}):
                item["legacy"]["path"] = item["location"]
        validate_v2(project.envelope.to_dict(), project_ref)
        return project, tuple(variants), snapshots, members

    def _publish(self, archive, project, variants, snapshots, members):
        ref = ProjectRef(ProjectId(project.envelope.identity))
        owned = self._paths.guard(os.path.join(self._paths.root, "projects", ref.identity.encoded))
        catalog_path = self._documents.path("project-catalog.json")
        with self._projects.mutation_lock:
            if self._filesystem.exists(self._projects.path_for(ref)):
                raise ValueError("此工程已存在；导入不会覆盖已有工程，请在其他数据目录恢复")
            previous = self._filesystem.read_bytes(catalog_path) if self._filesystem.exists(catalog_path) else None
            records = () if previous is None else parse_project_catalog(previous)
            if any(item.project_id == ref.identity.value for item in records):
                raise ValueError("此工程标识已在目录中登记；请先修复已有工程，导入不会覆盖目录记录")
            name = project.envelope.data["name"]
            if any(item.name_key == project_name_key(name) for item in records):
                raise ValueError("已有同名工程；导入已取消，现有工程未改变")
            required = sum(archive.getinfo(member).file_size for member in members)
            Path(self._paths.root).mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(self._paths.root).free < required + 64 * 1024**2:
                raise ValueError("磁盘空间不足，无法安全导入项目包")
            token = self._ids()
            created = []
            catalog_started = False
            try:
                for member in members:
                    path = self._paths.guard(member_destination(member, owned))
                    if self._filesystem.exists(path):
                        raise ValueError("工程资产目录已存在，拒绝覆盖")
                    created.append(path)
                    self._documents.write_bytes(path, archive.read(member), token)
                for variant in variants:
                    path = self._variants.path_for(variant.ref)
                    if self._filesystem.exists(path):
                        raise ValueError("翻译版本已存在，拒绝覆盖")
                    created.append(path)
                    self._variants.save(variant.ref, variant.to_dto())
                for snapshot in snapshots:
                    variant = snapshot["variant"]
                    digest = hashlib.sha256(
                        f"{ref.identity.value}:{variant['id']}:{variant['revision']}:{snapshot['name']}".encode()
                    ).hexdigest()
                    relative = os.path.join("snapshots", f"{digest}.json")
                    path = self._documents.path(relative)
                    if self._filesystem.exists(path):
                        raise ValueError("同一快照已存在，拒绝覆盖")
                    created.append(path)
                    self._documents.write_json(relative, snapshot, token)
                path = self._projects.path_for(ref)
                created.append(path)
                self._projects.save(ref, project)
                catalog_started = True
                record = ProjectCatalogRecord(ref.identity.value, name, project_name_key(name))
                self._documents.write_json("project-catalog.json", build_project_catalog((*records, record)), token)
                return path
            except Exception as exc:
                try:
                    if catalog_started:
                        if previous is None:
                            self._filesystem.remove(catalog_path, missing_ok=True)
                        elif self._filesystem.read_bytes(catalog_path) != previous:
                            self._documents.write_bytes(catalog_path, previous, token)
                    for path in reversed(created):
                        self._filesystem.remove(path, missing_ok=True)
                except Exception as rollback_error:
                    raise RuntimeError(f"项目包导入失败，回滚未完成；请保留数据目录用于恢复: {rollback_error}") from exc
                raise
