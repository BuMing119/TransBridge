"""Portable source history keeps old text without activating update journals."""

import base64
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from tests.application.projects import test_source_update as update_support
from tests.persistence.v2 import test_project_archive as archive_support
from transbridge.persistence.source_artifact_store import ProjectSourceArtifactStore
from transbridge.persistence.source_update_archive import backup_sources
from transbridge.persistence.v2.schema import serialize_document

project = update_support.project
services_factory = archive_support.services_factory


def _updated_bundle(tmp_path, project):
    services, service, context, source, _path, _source_id = project
    old_source = dict(services.project_lifecycle.active.project.envelope.data["sources"][0])
    preview, replacement = update_support._prepare(project)
    result = service.commit(preview.token, context)
    assert result.is_success, result.diagnostics
    raw = Path(result.value.backup_path).read_bytes()
    source.unlink()
    replacement.unlink()
    bundle = tmp_path / "history.transbridge"
    services.project_archive.export_project(str(bundle), context)
    return services, context, old_source, raw, bundle


def test_updated_project_bundle_keeps_preimage_sources_after_copy_and_reexport(tmp_path, project, services_factory):
    origin, context, old_source, backup_raw, bundle = _updated_bundle(tmp_path, project)
    target = services_factory("restored")
    imported = target.project_archive.import_project(str(bundle), context)
    assert target.current_project_opener.open_path(imported, context).is_success
    copied = target.project_archive.import_project(
        str(bundle), context, copy_on_identity_conflict=True, requested_name="Copy with history"
    )
    opened = target.current_project_opener.open_path(copied, context)
    assert opened.is_success, opened.diagnostics
    active = target.project_lifecycle.active
    assert active.project_ref != origin.project_lifecycle.active.project_ref
    backups = list((Path(target.root) / "projects" / active.project_ref.identity.encoded / "source-updates").glob("*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == backup_raw
    assert backup_sources(backup_raw)[0]["source_artifact_id"] == old_source["source_artifact_id"]
    store = ProjectSourceArtifactStore(target.root)
    old = store.get(old_source["source_artifact_id"])
    assert [entry.original for entry in old.hydration.entries] == ["One", "Two", "Three"]
    old_documents = [base64.b64decode(record["previous"]) for record in json.loads(backup_raw)["records"]]
    assert any("译文" in raw.decode() for raw in old_documents)
    assert not (Path(target.root) / "source-update-journal").exists()

    again = tmp_path / "copy-history.transbridge"
    target.project_archive.export_project(str(again), context)
    with zipfile.ZipFile(again) as archive:
        assert archive.read(f"source_artifacts/manifests/{old_source['source_artifact_id']}.json")
        assert archive.read("assets/source-updates/" + backups[0].name) == backup_raw


def test_backup_dependency_cannot_be_satisfied_by_destination_cache(tmp_path, project):
    origin, context, old_source, _raw, bundle = _updated_bundle(tmp_path, project)
    manifest, members = archive_support._bundle_content(bundle)
    old_member = f"source_artifacts/manifests/{old_source['source_artifact_id']}.json"
    damaged = archive_support._write_bundle(
        tmp_path / "missing-old.transbridge", manifest, [(name, raw) for name, raw in members if name != old_member]
    )

    with pytest.raises(ValueError, match="来源成果"):
        origin.project_archive.inspect_import(str(damaged), context)


def test_backup_preimages_upgrade_through_the_canonical_migration_chain(tmp_path, project):
    _origin, _context, old_source, raw, _bundle = _updated_bundle(tmp_path, project)
    manifest = json.loads(raw)
    for record in manifest["records"]:
        previous = json.loads(base64.b64decode(record["previous"]))
        previous["schema_version"] = 4
        if record["kind"] == "project":
            for source in previous["data"]["sources"]:
                source.pop("content_state")
        content = serialize_document(previous)
        record["previous"] = base64.b64encode(content).decode("ascii")
        record["previous_digest"] = hashlib.sha256(content).hexdigest()
    manifest.pop("manifest_digest")
    manifest["manifest_digest"] = hashlib.sha256(serialize_document(manifest)).hexdigest()

    migrated = backup_sources(serialize_document(manifest))

    assert migrated[0]["source_artifact_id"] == old_source["source_artifact_id"]
    assert migrated[0]["content_state"] == "captured"
