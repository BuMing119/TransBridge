"""Read-only benchmark input copies for current projects and historical checkouts."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_records(source: Path, destination: Path) -> tuple[list[Path], dict[Path, str]]:
    """Copy records and their saved source closure without opening user services."""
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("copy destination must be outside the source data tree")
    if destination.exists():
        raise FileExistsError("copy destination must be new")
    databases = sorted((source / "project-store-v1").glob("id-*/project.sqlite3"))
    if databases:
        return _copy_sqlite_records(source, destination, databases)
    return _copy_legacy_records(source, destination)


def _copy_legacy_records(source: Path, destination: Path) -> tuple[list[Path], dict[Path, str]]:
    records = sorted((source / "projects").glob("*.json"))
    variants = sorted((source / "projects").glob("*/variants/*.json"))
    if not records:
        raise ValueError("source data contains no project records")
    hashes = {}
    copied_paths = [*records, *variants]
    for path in copied_paths:
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError("record links outside the source tree are unsupported")
        hashes[path] = _digest(path)
    artifact_ids = set()
    # Legacy records still track live input content. Saved source references
    # depend only on their immutable store closure, even if live paths are gone.
    for record in records:
        document = json.loads(record.read_text(encoding="utf-8"))
        for item in document.get("data", {}).get("sources", ()):
            artifact_id = item.get("source_artifact_id")
            if artifact_id is not None:
                if not isinstance(artifact_id, str):
                    raise ValueError("source artifact identity must be a string")
                artifact_ids.add(artifact_id)
                continue
            location = item.get("location") or item.get("path")
            if location and Path(location).is_file():
                path = Path(location).resolve()
                hashes[path] = _digest(path)
    if artifact_ids:
        artifact_hashes = _artifact_record_hashes(source, artifact_ids)
        hashes.update(artifact_hashes)
        copied_paths.extend(artifact_hashes)
    destination.mkdir()
    for path in copied_paths:
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        if _digest(target) != hashes[path]:
            raise RuntimeError("input changed while copying project records")
    return [destination / path.relative_to(source) for path in records], hashes


def _artifact_record_hashes(source: Path, artifact_ids: set[str]) -> dict[Path, str]:
    """Read and verify only formally referenced manifests and blob dependencies."""
    from transbridge.persistence.source_artifact_store import ProjectSourceArtifactStore

    store = ProjectSourceArtifactStore(str(source))
    hashes = {}
    for artifact_id in sorted(artifact_ids):
        for relative, content in store.iter_records(artifact_id):
            path = source / "source_artifacts" / relative
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise ValueError("artifact links outside the source tree are unsupported")
            digest = hashlib.sha256(content).hexdigest()
            if path in hashes and hashes[path] != digest:
                raise RuntimeError("input changed while reading source artifact dependencies")
            hashes[path] = digest
    return hashes


def verify_inputs(hashes: dict[Path, str]) -> bool:
    return all(path.is_file() and _digest(path) == digest for path, digest in hashes.items())


def _copy_sqlite_records(source: Path, destination: Path, records: list[Path]) -> tuple[list[Path], dict[Path, str]]:
    from transbridge.persistence.project_store.connection import project_connection
    from transbridge.persistence.project_store.reads import read_summary
    from transbridge.persistence.terminology.paths import TerminologyPaths
    from transbridge.persistence.terminology.portable_assets import export_profile_assets, install_profile_assets
    from transbridge.persistence.terminology.project_selection import read_project_selections

    if any(path.resolve() != path or not path.is_file() for path in records):
        raise ValueError("project database links or non-file records are unsupported")
    # Reuse the input preservation check for every source database measured.
    hashes = {path: _digest(path) for path in records}
    with TemporaryDirectory(prefix=".benchmark-copy-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "workspace"
        for path in records:
            target = staging / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(target)) as backup:
                with project_connection(path) as original:
                    summary = read_summary(original)
                    if path.parent.name != summary.identity.encoded:
                        raise ValueError("project database identity differs from its workspace directory")
                    original.backup(backup)
                with backup:
                    backup.execute("PRAGMA trusted_schema = OFF")
                    backup.execute("PRAGMA secure_delete = ON")
                    backup.execute("DELETE FROM commit_receipt")
            with project_connection(target) as copied:
                if read_summary(copied) != summary:
                    raise RuntimeError("project database backup did not retain its identity and revisions")
            project_id = summary.identity.value
            if read_project_selections(target, project_id):
                database = TerminologyPaths(source).database(project_id)
                hashes[database] = _digest(database)
                wal = database.with_name(database.name + "-wal")
                if wal.is_file():
                    hashes[wal] = _digest(wal)
                assets = export_profile_assets(target, source, project_id)
                install_profile_assets(
                    assets, TerminologyPaths(staging).database(project_id), target, project_id, project_id
                )
        if not verify_inputs(hashes):
            raise RuntimeError("input changed while copying project databases")
        staging.rename(destination)
    return [destination / path.relative_to(source) for path in records], hashes
