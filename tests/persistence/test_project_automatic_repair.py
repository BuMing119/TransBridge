"""Automatic source repair is durable, evidence-based and preserves saved work."""

import base64
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.persistence.v2.test_saved_project_sources import CountingPreparer
from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import DomainError, ErrorCategory, OperationOutcome, RequestContext
from transbridge.application.io import EntryKey, FormatId
from transbridge.application.io.identity import ExternalEntryRef
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.source_update_store import ProjectSourceUpdateStore
from transbridge.persistence.v2 import SCHEMA_VERSION, ProjectId, VariantSnapshot
from transbridge.persistence.v2.filesystem import OsPersistenceFilesystem


def _services(root, preparer=None):
    return build_persistence_v2_services(
        root,
        id_factory=lambda: uuid4().hex,
        timestamp_factory=lambda: "2026-11-03T00:00:00+00:00",
        source_preparer=preparer,
    )


def _write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def _has_backup(root, original):
    for path in (root / "backups").rglob("*.json"):
        raw = path.read_bytes()
        if raw == original:
            return True
        document = json.loads(raw)
        if any(base64.b64decode(record["previous"]) == original for record in document.get("records", ())):
            return True
    return False


@pytest.fixture
def repair_case(tmp_path):
    root = tmp_path / "s"
    source = tmp_path / "source.xml"
    source.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    context = RequestContext("automatic-repair", run_id="repair")
    services = _services(root)
    try:
        created = services.gui_project_commands.create_project(
            ProjectProvisioningRequest(
                "Repair",
                source=ProjectSourceRequest(
                    str(source), FormatId.XML_EET, options=(("source_namespace", "source:repair"),)
                ),
            ),
            context,
        )
        assert created.is_success, created.diagnostics
        key = services.project_lifecycle.active.variant.snapshot().entries[0].entry_key
        assert services.gui_project_commands.update_entry(key, context, translation="Saved main", stage=5).is_success
        assert services.gui_project_commands.replace_labels(
            {key: {"reviewed"}}, {"reviewed": {"color": "#abcdef"}}, context
        ).is_success
        assert services.gui_project_commands.save(context).is_success
        main = services.project_lifecycle.active.variant.snapshot()
        assert services.gui_project_commands.create_variant("Branch", context, copy_active=True).is_success
        assert services.gui_project_commands.update_entry(key, context, translation="Saved branch", stage=3).is_success
        assert services.gui_project_commands.save(context).is_success
        active = services.project_lifecycle.active
        project_path = Path(services.projects.path_for(active.project_ref))
        variants = {}
        for snapshot in (main, active.variant.snapshot()):
            snapshot = replace(
                snapshot,
                entries=tuple(
                    replace(row, external_refs=(ExternalEntryRef("paratranz", "file:7", "row:9"),))
                    for row in snapshot.entries
                ),
            )
            path = Path(services.variants.path_for(snapshot.ref))
            _write(path, snapshot.to_dto().envelope.to_dict())
            variants[path] = snapshot
        project = json.loads(project_path.read_bytes())
        return SimpleNamespace(
            root=root, source=source, context=context, path=project_path, project=project, variants=variants
        )
    finally:
        services.close()


def _missing(case, *, schema=5, remove_manifests=False):
    document = json.loads(case.path.read_bytes())
    document["schema_version"] = schema
    for source in document["data"]["sources"]:
        source.pop("source_artifact_id", None)
        if schema == 4:
            source.pop("content_state", None)
        else:
            source["content_state"] = "missing"
    _write(case.path, document)
    if remove_manifests:
        for path in (case.root / "source_artifacts" / "manifests").glob("*.json"):
            path.unlink()
    return case.path.read_bytes()


def _assert_variants_unchanged(case, services):
    for path, expected in case.variants.items():
        actual = VariantSnapshot.from_dto(services.variants.read_snapshot(expected.ref), expected.ref)
        assert replace(actual, revision=expected.revision) == expected, path
    project = json.loads(case.path.read_bytes())
    for field in ("variant_ids", "active_variant_id", "source_relations"):
        assert project["data"].get(field) == case.project["data"].get(field)


@pytest.mark.parametrize("schema", [4, 5])
def test_open_repairs_saved_artifact_and_persists_all_versions_before_offline_reopen(repair_case, schema):
    case = repair_case
    before = _missing(case, schema=schema)
    case.source.unlink()
    preparer = CountingPreparer(forbidden=True)
    services = _services(case.root, preparer)
    try:
        opened = services.current_project_opener.open_path(case.path, case.context)
        assert opened.is_success, opened.diagnostics
        assert not services.project_lifecycle.active.missing_inputs
        assert preparer.calls == 0
        _assert_variants_unchanged(case, services)
        document = json.loads(case.path.read_bytes())
        assert document["schema_version"] == SCHEMA_VERSION
        assert document["data"]["sources"][0]["content_state"] == "captured"
        assert document["data"]["sources"][0]["source_artifact_id"]
        assert _has_backup(case.root, before)
        assert services.gui_project_commands.save(case.context).is_success
    finally:
        services.close()
    services = _services(case.root, CountingPreparer(forbidden=True))
    try:
        opened = services.current_project_opener.prepare_active(case.context)
        assert opened.is_success, opened.diagnostics
        assert not opened.value.missing_inputs
        assert opened.value.hydrations[0].source_snapshot.content
        _assert_variants_unchanged(case, services)
    finally:
        services.close()


def test_original_path_with_matching_hash_is_captured_once_and_reopens_offline(repair_case):
    case = repair_case
    _missing(case, remove_manifests=True)
    preparer = CountingPreparer()
    services = _services(case.root, preparer)
    try:
        opened = services.current_project_opener.open_path(case.path, case.context)
        assert opened.is_success, opened.diagnostics
        assert preparer.calls == 1
        _assert_variants_unchanged(case, services)
        assert services.gui_project_commands.save(case.context).is_success
    finally:
        services.close()
    case.source.unlink()
    services = _services(case.root, CountingPreparer(forbidden=True))
    try:
        opened = services.current_project_opener.open_path(case.path, case.context)
        assert opened.is_success, opened.diagnostics
        assert not services.project_lifecycle.active.missing_inputs
        _assert_variants_unchanged(case, services)
    finally:
        services.close()


@pytest.mark.parametrize("record_kind", ["manifest", "blob"])
def test_corrupt_captured_artifact_tries_original_path_and_replaces_unusable_reference(repair_case, record_kind):
    case = repair_case
    artifact_id = case.project["data"]["sources"][0]["source_artifact_id"]
    manifest = case.root / "source_artifacts" / "manifests" / f"{artifact_id}.json"
    damaged = manifest
    if record_kind == "blob":
        digest = json.loads(manifest.read_bytes())["blobs"][0]
        damaged = case.root / "source_artifacts" / "blobs" / f"{digest}.bin"
    damaged.write_bytes(b"broken artifact")
    services = _services(case.root)
    try:
        opened = services.current_project_opener.open_path(case.path, case.context)
        assert opened.is_success, opened.diagnostics
        assert not services.project_lifecycle.active.missing_inputs
        _assert_variants_unchanged(case, services)
        assert any(
            path.read_bytes() == b"broken artifact"
            for path in (case.root / "source_artifacts" / "quarantine").rglob("*")
            if path.is_file()
        )
    finally:
        services.close()


def test_failed_candidate_does_not_prevent_trying_a_later_valid_source(repair_case):
    case = repair_case
    invalid = case.source.with_name("invalid.xml")
    invalid.write_text("not an XML source", encoding="utf-8")
    donor = json.loads(case.path.read_bytes())
    donor["id"] = "candidate-project"
    donor["data"]["sources"][0]["location"] = str(invalid)
    donor["data"]["sources"][0]["legacy"]["path"] = str(invalid)
    _write(case.root / "projects" / f"{ProjectId(donor['id']).encoded}.json", donor)
    _missing(case, remove_manifests=True)
    preparer = CountingPreparer()
    services = _services(case.root, preparer)
    try:
        opened = services.current_project_opener.open_path(case.path, case.context)
        assert opened.is_success, opened.diagnostics
        assert not services.project_lifecycle.active.missing_inputs
        assert preparer.calls == 2
        _assert_variants_unchanged(case, services)
    finally:
        services.close()


@pytest.mark.parametrize("candidate", ["project", "backup"])
def test_recovery_uses_matching_source_location_in_other_project_or_history(repair_case, candidate):
    case = repair_case
    alternate = case.source.with_name("retained.xml")
    alternate.write_bytes(case.source.read_bytes())
    document = json.loads(case.path.read_bytes())
    document["data"]["sources"][0]["location"] = str(alternate)
    document["data"]["sources"][0]["legacy"]["path"] = str(alternate)
    if candidate == "project":
        document["id"] = "other-project"
        _write(case.root / "projects" / f"{ProjectId(document['id']).encoded}.json", document)
    else:
        _write(case.root / "backups" / "projects" / ProjectId(document["id"]).encoded / "retained.v4.json", document)
    _missing(case, remove_manifests=True)
    case.source.unlink()
    services = _services(case.root)
    try:
        opened = services.current_project_opener.open_path(case.path, case.context)
        assert opened.is_success, opened.diagnostics
        assert not services.project_lifecycle.active.missing_inputs
        _assert_variants_unchanged(case, services)
    finally:
        services.close()


@pytest.mark.parametrize("has_snapshot", [True, False])
def test_real_variant_snapshot_can_prove_a_lost_source_fingerprint(repair_case, has_snapshot):
    case = repair_case
    if has_snapshot:
        services = _services(case.root)
        try:
            assert services.current_project_opener.open_path(case.path, case.context).is_success
            saved = services.gui_project_commands.save_snapshot("Known source revision", case.context)
            assert saved.is_success, saved.diagnostics
        finally:
            services.close()
    retained = case.source.with_name("retained.xml")
    retained.write_bytes(case.source.read_bytes())
    donor = json.loads(case.path.read_bytes())
    donor["id"] = "snapshot-donor"
    donor["data"]["sources"][0]["location"] = str(retained)
    donor["data"]["sources"][0]["legacy"] = {"namespace": "source:donor", "path": str(retained)}
    donor["data"]["sources"][0]["format_options"]["source_namespace"] = "source:donor"
    _write(case.root / "projects" / f"{ProjectId(donor['id']).encoded}.json", donor)
    _missing(case, remove_manifests=True)
    case.source.unlink()
    document = json.loads(case.path.read_bytes())
    document["data"]["sources"][0].pop("fingerprint")
    _write(case.path, document)
    for path, snapshot in tuple(case.variants.items()):
        snapshot = replace(
            snapshot,
            source_fingerprints=tuple(replace(item, sha256=None) for item in snapshot.source_fingerprints),
            entries=tuple(replace(row, entry_key=replace(row.entry_key, original=None)) for row in snapshot.entries),
        )
        _write(path, snapshot.to_dto().envelope.to_dict())
        case.variants[path] = snapshot
    before = case.path.read_bytes()
    services = _services(case.root)
    try:
        opened = services.current_project_opener.open_path(case.path, case.context)
        if has_snapshot:
            assert opened.is_success, opened.diagnostics
            assert not services.project_lifecycle.active.missing_inputs
            for expected in case.variants.values():
                actual = VariantSnapshot.from_dto(services.variants.read_snapshot(expected.ref), expected.ref)
                assert [(row.translation, row.stage, row.labels, row.external_refs) for row in actual.entries] == [
                    (row.translation, row.stage, row.labels, row.external_refs) for row in expected.entries
                ]
        else:
            assert not opened.is_success
            assert opened.diagnostics[0].code == "PROJECT_CONTENT_REPAIR_FAILED"
            assert case.path.read_bytes() == before
    finally:
        services.close()


@pytest.mark.parametrize("known_original,changed_original", [(True, False), (True, True), (False, False)])
def test_changed_source_requires_known_unchanged_originals(repair_case, known_original, changed_original):
    case = repair_case
    for path, snapshot in tuple(case.variants.items()):
        updated = replace(
            snapshot,
            entries=tuple(
                replace(
                    row,
                    entry_key=EntryKey(
                        row.entry_key.namespace, row.entry_key.local_key, "Hello, traveler." if known_original else None
                    ),
                )
                for row in snapshot.entries
            ),
        )
        _write(path, updated.to_dto().envelope.to_dict())
        case.variants[path] = updated
    before = _missing(case, remove_manifests=True)
    content = case.source.read_text(encoding="utf-8").replace("<TRADUIT></TRADUIT>", "<TRADUIT>external</TRADUIT>")
    if changed_original:
        content = content.replace("Hello, traveler.", "A different original")
    case.source.write_text(content, encoding="utf-8")
    services = _services(case.root)
    try:
        opened = services.current_project_opener.open_path(case.path, case.context)
        assert not opened.is_success
        assert opened.diagnostics[0].code == "PROJECT_CONTENT_REPAIR_FAILED"
        assert services.project_lifecycle.active is None
        assert case.path.read_bytes() == before
    finally:
        services.close()


def test_exhausted_repair_keeps_original_and_backup_without_activation_or_deletion(repair_case):
    case = repair_case
    before = _missing(case, remove_manifests=True)
    case.source.unlink()
    variant_bytes = {path: path.read_bytes() for path in case.variants}
    pointer = case.root / "active-project.json"
    pointer_before = pointer.read_bytes()
    backup = case.root / "backups" / "projects" / ProjectId(case.project["id"]).encoded / "previous.v5.json"
    _write(backup, case.project)
    existing_backups = {path: path.read_bytes() for path in (case.root / "backups").rglob("*") if path.is_file()}
    services = _services(case.root)
    try:
        result = services.current_project_opener.open_path(case.path, case.context)
        assert not result.is_success
        assert result.diagnostics[0].code == "PROJECT_CONTENT_REPAIR_FAILED"
        assert "未打开" in result.diagnostics[0].message
        assert case.source.name in result.diagnostics[0].message
        assert services.project_lifecycle.active is None
        assert case.path.read_bytes() == before
        assert pointer.read_bytes() == pointer_before
        assert all(path.read_bytes() == content for path, content in variant_bytes.items())
        assert all(path.read_bytes() == raw for path, raw in existing_backups.items())
    finally:
        services.close()


def test_complete_project_does_not_scan_repair_candidates_or_read_external_file(repair_case, monkeypatch):
    case = repair_case
    services = _services(case.root, CountingPreparer(forbidden=True))
    original_read = Path.read_bytes

    def checked_read(path, *args, **kwargs):
        assert path != case.source, "Complete project must not read its optional external reference"
        return original_read(path, *args, **kwargs)

    original_scan = OsPersistenceFilesystem.list_files

    def forbidden_scan(filesystem, path, *args, **kwargs):
        assert Path(path).name not in {"manifests", "projects", "snapshots", "backups"}, (
            "Complete project must not enumerate recovery candidates"
        )
        return original_scan(filesystem, path, *args, **kwargs)

    monkeypatch.setattr(OsPersistenceFilesystem, "list_files", forbidden_scan)
    monkeypatch.setattr(Path, "read_bytes", checked_read)
    try:
        opened = services.current_project_opener.open_path(case.path, case.context)
        assert opened.is_success, opened.diagnostics
        assert services.gui_project_commands.save(case.context).is_success
    finally:
        services.close()


@pytest.mark.parametrize("schema", [4, 5])
@pytest.mark.parametrize("missing_scope", ["all", "translation"])
def test_folded_plugin_translation_does_not_require_an_independent_source_after_repair(tmp_path, schema, missing_scope):
    originals, translations = tmp_path / "o", tmp_path / "t"
    originals.mkdir()
    translations.mkdir()
    original = write_plugin(originals / "Example.esp", [(0x800, "NPC", "Original")])
    translated = write_plugin(translations / "Example.esp", [(0x800, "NPC", "Translation")])
    root = tmp_path / "s"
    context = RequestContext("folded-repair", run_id="folded")
    services = _services(root)
    try:
        created = services.gui_project_commands.create_project(
            ProjectProvisioningRequest(
                "Folded",
                source=ProjectSourceRequest(str(original)),
                migration_sources=(ProjectSourceRequest(str(translated)),),
            ),
            context,
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        expected = active.variant.snapshot()
        path = Path(services.projects.path_for(active.project_ref))
    finally:
        services.close()

    document = json.loads(path.read_bytes())
    document["schema_version"] = schema
    for source in document["data"]["sources"]:
        missing = missing_scope == "all" or source["legacy"].get("role") == "migration"
        if missing:
            source.pop("source_artifact_id", None)
        if schema == 4:
            source.pop("content_state", None)
        elif missing:
            source["content_state"] = "missing"
    _write(path, document)
    original.unlink()
    translated.unlink()
    services = _services(root, CountingPreparer(forbidden=True))
    try:
        opened = services.current_project_opener.open_path(path, context)
        assert opened.is_success, opened.diagnostics
        assert not services.project_lifecycle.active.missing_inputs
        assert services.project_lifecycle.active.variant.snapshot().entries == expected.entries
        assert services.gui_project_commands.save(context).is_success
    finally:
        services.close()


def test_eighty_missing_sources_share_one_candidate_scan_and_one_atomic_commit(tmp_path, monkeypatch):
    root = tmp_path / "s"
    context = RequestContext("many-repair", run_id="many")
    raw = Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes()
    sources = []
    for index in range(80):
        path = tmp_path / f"{index}.xml"
        path.write_bytes(raw.replace(b"Hello, traveler.", f"Original {index}".encode()))
        sources.append(
            ProjectSourceRequest(str(path), FormatId.XML_EET, options=(("source_namespace", f"source:many:{index}"),))
        )
    services = _services(root)
    try:
        created = services.gui_project_commands.create_project(
            ProjectProvisioningRequest("Many sources", source=sources[0], additional_sources=tuple(sources[1:])),
            context,
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        expected = active.variant.snapshot()
        path = Path(services.projects.path_for(active.project_ref))
    finally:
        services.close()

    document = json.loads(path.read_bytes())
    for source in document["data"]["sources"]:
        source.pop("source_artifact_id")
        source["content_state"] = "missing"
    _write(path, document)
    for source in sources:
        Path(source.location).unlink()
    commits, scans = [], []
    commit = ProjectSourceUpdateStore.commit
    scan = OsPersistenceFilesystem.list_files

    def counted_commit(store, *args, **kwargs):
        commits.append(True)
        return commit(store, *args, **kwargs)

    def counted_scan(filesystem, directory):
        if Path(directory).name == "manifests":
            scans.append(True)
        return scan(filesystem, directory)

    monkeypatch.setattr(ProjectSourceUpdateStore, "commit", counted_commit)
    monkeypatch.setattr(OsPersistenceFilesystem, "list_files", counted_scan)
    services = _services(root, CountingPreparer(forbidden=True))
    try:
        opened = services.current_project_opener.open_path(path, context)
        assert opened.is_success, opened.diagnostics
        assert len(opened.value["hydrations"]) == 80 and not opened.value["missing_inputs"]
        assert services.project_lifecycle.active.variant.snapshot().entries == expected.entries
        assert len(commits) == len(scans) == 1
    finally:
        services.close()


def test_cancelled_recovery_does_not_try_another_candidate_or_change_saved_records(repair_case):
    case = repair_case
    before = _missing(case, remove_manifests=True)
    variants = {path: path.read_bytes() for path in case.variants}

    class CancelledPreparer:
        calls = 0

        def prepare_source(self, *args, **kwargs):
            self.calls += 1
            raise DomainError(ErrorCategory.CANCELLED, "OPERATION_CANCELLED", "Recovery cancelled")

    preparer = CancelledPreparer()
    services = _services(case.root, preparer)
    try:
        result = services.current_project_opener.open_path(case.path, case.context)
        assert result.outcome is OperationOutcome.CANCELLED
        assert preparer.calls == 1
        assert services.project_lifecycle.active is None
        assert case.path.read_bytes() == before
        assert all(path.read_bytes() == raw for path, raw in variants.items())
    finally:
        services.close()


def test_verified_complete_artifact_recovers_legacy_skip_empty_option_difference(repair_case):
    case = repair_case
    _missing(case, schema=4)
    document = json.loads(case.path.read_bytes())
    document["data"]["sources"][0]["format_options"]["skip_empty"] = True
    _write(case.path, document)
    case.source.unlink()
    services = _services(case.root, CountingPreparer(forbidden=True))
    try:
        result = services.current_project_opener.open_path(case.path, case.context)
        assert result.is_success, result.diagnostics
        assert not services.project_lifecycle.active.missing_inputs
        _assert_variants_unchanged(case, services)
    finally:
        services.close()
