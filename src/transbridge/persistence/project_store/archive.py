# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Current-format portable projects with verified SQLite backup and admission."""

from __future__ import annotations

from contextlib import closing, contextmanager
import os
from pathlib import Path
import shutil
import sqlite3
import stat
from uuid import uuid4
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from transbridge.application.contracts import DomainError
from transbridge.persistence.project_archive import ProjectArchiveInspection
from transbridge.persistence.project_catalog_document import project_display_name
from transbridge.persistence.terminology.portable_assets import (
    PROFILE_ASSET_MEMBER,
    export_profile_assets,
    install_profile_assets,
    validate_profile_assets,
)
from transbridge.persistence.v2.ids import ProjectId, ProjectRef

from .alignment_cache_schema import remove as remove_alignment_cache
from .archive_validation import archive_json, validate_archive_database
from .connection import project_connection
from .entry_codec import encode_json, pack_text
from .format import (
    PROJECT_FILENAME,
    PROJECT_FORMAT,
    PROJECT_FORMAT_VERSION,
    ProjectStorageConflict,
    UnsupportedProjectFormat,
)
from .reads import read_summary

_MANIFEST = "manifest.json"
_MAX_DATABASE_SIZE = 2 * 1024**3
_MAX_PROFILE_SIZE = 64 * 1024**2


class ProjectPackage:
    def __init__(self, projects, catalog, lifecycle) -> None:
        self.projects = projects
        self.catalog = catalog
        self.lifecycle = lifecycle

    def export_project(self, destination: str, context, *, include_snapshots: bool = True) -> str:
        active = self.lifecycle.active
        if active is None or active.variant is None or active.dirty:
            raise ValueError("请先保存当前项目再导出。")
        target = Path(destination).resolve()
        if target.is_relative_to(Path(self.projects.root)):
            raise ValueError("请把项目包导出到数据目录之外。")
        lease = self.lifecycle.acquire_export_lease(context)
        if not lease.is_success:
            raise ValueError(lease.diagnostics[0].message)
        token = lease.value["token"]
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with _staging(target.parent, ".transbridge-package-") as directory:
                copy = directory / PROJECT_FILENAME
                source = self.projects.store_for(active.project_ref.identity).path
                with project_connection(source) as connection, closing(sqlite3.connect(copy)) as backup:
                    summary = read_summary(connection)
                    if summary.revision != active.persisted_project_revision or not any(
                        item.identity == active.formal_variant_ref.identity
                        and item.revision == active.persisted_variant_revision
                        for item in summary.variants
                    ):
                        raise ProjectStorageConflict("project changed before export")
                    connection.backup(backup)
                with project_connection(copy, write=True) as connection:
                    remove_alignment_cache(connection)
                    connection.execute("DELETE FROM commit_receipt")
                    connection.execute("""
                        DELETE FROM entry_state WHERE entry_id IN (
                            SELECT id FROM source_entry WHERE source_id NOT IN (SELECT source_id FROM project_source)
                        )
                    """)
                    if not include_snapshots:
                        connection.execute("DELETE FROM named_snapshot")
                    connection.execute("""
                        DELETE FROM source WHERE id NOT IN (SELECT source_id FROM project_source)
                            AND id NOT IN (SELECT source_id FROM snapshot_source)
                    """)
                    validate_archive_database(connection, summary.identity.value)
                _compact(copy)
                assets = export_profile_assets(copy, Path(self.projects.root), summary.identity.value)
                members = [PROJECT_FILENAME]
                if assets is not None:
                    members.append(PROFILE_ASSET_MEMBER)
                staged = directory / "package.transbridge"
                with ZipFile(staged, "w", ZIP_DEFLATED) as archive:
                    archive.writestr(
                        _MANIFEST,
                        encode_json({
                            "format": PROJECT_FORMAT,
                            "version": PROJECT_FORMAT_VERSION,
                            "project_id": summary.identity.value,
                            "members": members,
                        }),
                    )
                    archive.write(copy, PROJECT_FILENAME)
                    if assets is not None:
                        archive.writestr(PROFILE_ASSET_MEMBER, encode_json(assets))
                result = self.lifecycle.validate_export_lease(token, context)
                if not result.is_success:
                    raise ProjectStorageConflict(result.diagnostics[0].message)
                os.replace(staged, target)
        finally:
            self.lifecycle.validate_export_lease(token, context)
        return str(target)

    def inspect_import(self, source: str, _context) -> ProjectArchiveInspection:
        with self._prepared(source) as (staged, summary, assets):
            try:
                validate_profile_assets(assets, staged, summary.identity.value)
            except (KeyError, TypeError, ValueError) as exc:
                raise UnsupportedProjectFormat(f"Invalid portable terminology content: {exc}") from exc
            return self._conflicts(summary)

    inspect_import_conflicts = inspect_import

    def import_project(
        self, source: str, _context, *, requested_name: str | None = None, copy_on_identity_conflict: bool = False
    ) -> str:
        with self._prepared(source) as (staged, summary, assets):
            conflicts = self._conflicts(summary)
            project_id = summary.identity
            if conflicts.identity_conflict:
                if not copy_on_identity_conflict:
                    raise ProjectStorageConflict("工程已存在，请作为副本导入。")
                project_id = ProjectId(f"project-{uuid4().hex}")
            try:
                name = summary.name if requested_name is None else project_display_name(requested_name)
            except ValueError as exc:
                raise ValueError("项目名称必须为 1 至 80 个可显示字符。") from exc
            try:
                install_profile_assets(
                    assets,
                    staged.parent / "terminology" / "db" / "terminology.sqlite3",
                    staged,
                    summary.identity.value,
                    project_id.value,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise UnsupportedProjectFormat(f"Invalid portable terminology content: {exc}") from exc
            copied = project_id != summary.identity
            with project_connection(staged, write=True) as connection:
                remove_alignment_cache(connection)
                active_variant = (
                    _reidentify_variants(connection, summary.active_variant.value)
                    if copied
                    else (summary.active_variant.value)
                )
                connection.execute(
                    "UPDATE project SET identity = ?, name = ?, active_variant = ?",
                    (project_id.value, pack_text(name), active_variant),
                )
                connection.execute("DELETE FROM commit_receipt")
                if copied:
                    validate_archive_database(connection, project_id.value)
            _compact(staged)
            target = self.projects.store_for(project_id).path
            try:
                with self.catalog.publish_name(name):
                    if target.parent.exists():
                        raise ProjectStorageConflict("项目身份已被另一项操作使用。")
                    staged.parent.rename(target.parent)
            except FileExistsError as exc:
                raise ProjectStorageConflict("项目身份已被另一项操作使用。") from exc
            except DomainError as exc:
                raise ProjectStorageConflict(exc.message) from exc
            return str(target)

    @contextmanager
    def _prepared(self, source: str):
        try:
            mode = Path(source).stat().st_mode
        except FileNotFoundError as exc:
            raise UnsupportedProjectFormat("project package file does not exist") from exc
        if not stat.S_ISREG(mode):
            raise UnsupportedProjectFormat("project package must be a regular file")
        self.projects.directory.mkdir(parents=True, exist_ok=True)
        with _staging(self.projects.directory, ".package-stage-") as directory:
            staged = directory / PROJECT_FILENAME
            try:
                with ZipFile(source) as archive:
                    members = archive.infolist()
                    names = {member.filename for member in members}
                    required = {_MANIFEST, PROJECT_FILENAME}
                    if len(members) != len(names) or not required <= names <= required | {PROFILE_ASSET_MEMBER}:
                        raise UnsupportedProjectFormat("package contains unknown, missing or repeated members")
                    if archive.getinfo(_MANIFEST).file_size > 65536:
                        raise UnsupportedProjectFormat("package manifest is too large")
                    database = archive.getinfo(PROJECT_FILENAME)
                    if not 0 < database.file_size <= _MAX_DATABASE_SIZE:
                        raise UnsupportedProjectFormat("package database exceeds the supported size")
                    manifest = archive_json(archive.read(_MANIFEST))
                    if (
                        not isinstance(manifest, dict)
                        or set(manifest) != {"format", "version", "project_id", "members"}
                        or manifest["format"] != PROJECT_FORMAT
                        or type(manifest["version"]) is not int
                        or manifest["version"] != PROJECT_FORMAT_VERSION
                        or manifest["members"]
                        != (
                            [PROJECT_FILENAME, PROFILE_ASSET_MEMBER]
                            if PROFILE_ASSET_MEMBER in names
                            else [PROJECT_FILENAME]
                        )
                    ):
                        raise UnsupportedProjectFormat("package manifest differs from the supported format")
                    with archive.open(database) as input_file, staged.open("xb") as output:
                        shutil.copyfileobj(input_file, output, length=1024 * 1024)
                    assets = None
                    if PROFILE_ASSET_MEMBER in names:
                        if archive.getinfo(PROFILE_ASSET_MEMBER).file_size > _MAX_PROFILE_SIZE:
                            raise UnsupportedProjectFormat("portable terminology assets exceed the supported size")
                        assets = archive_json(archive.read(PROFILE_ASSET_MEMBER))
            except (BadZipFile, KeyError, UnicodeError, ValueError, RecursionError) as exc:
                raise UnsupportedProjectFormat("package cannot be read") from exc
            with project_connection(staged) as connection:
                summary = validate_archive_database(connection, manifest["project_id"])
            yield staged, summary, assets

    def _conflicts(self, summary) -> ProjectArchiveInspection:
        entries = self.catalog.list_projects().projects
        return ProjectArchiveInspection(
            summary.identity.value,
            summary.name,
            Path(self.projects.path_for(ProjectRef(summary.identity))).exists(),
            any(item.name.casefold() == summary.name.casefold() for item in entries),
        )


def _reidentify_variants(connection: sqlite3.Connection, active_variant: str) -> str:
    connection.execute("PRAGMA defer_foreign_keys = ON")
    variants = {
        row["identity"]: (f"variant-{uuid4().hex}", row["terminology_selection"])
        for row in connection.execute("SELECT identity, terminology_selection FROM variant")
    }
    historical_variants = {
        row["variant_id"]: f"variant-{uuid4().hex}"
        for row in connection.execute("SELECT DISTINCT variant_id FROM named_snapshot")
        if row["variant_id"] not in variants
    }
    for previous_id, (new_id, raw_selection) in variants.items():
        selection = None
        if raw_selection is not None:
            payload = archive_json(raw_selection)
            payload["variant_id"] = new_id
            selection = encode_json(payload)
        connection.execute(
            "UPDATE variant SET identity = ?, terminology_selection = ? WHERE identity = ?",
            (new_id, selection, previous_id),
        )
        connection.execute("UPDATE entry_state SET variant_id = ? WHERE variant_id = ?", (new_id, previous_id))
        historical_variants[previous_id] = new_id
    connection.executemany(
        "UPDATE named_snapshot SET variant_id = ? WHERE variant_id = ?",
        ((new_id, previous_id) for previous_id, new_id in historical_variants.items()),
    )
    return variants[active_variant][0]


def _compact(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("VACUUM")


@contextmanager
def _staging(parent: Path, prefix: str):
    root = parent.resolve()
    directory = root / f"{prefix}{uuid4().hex}"
    directory.mkdir()
    try:
        yield directory
    finally:
        if directory.resolve().parent != root:
            raise ValueError("project package staging escaped its working directory")
        if directory.exists():
            shutil.rmtree(directory)
