"""One prepared open reuses content evidence without accepting stale files."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from scripts.benchmark_project_open import _services, create_fixture
from transbridge.application.contracts import RequestContext
from transbridge.persistence.v2 import EntityKind, VariantSnapshot, project_activation_journal, repository


@pytest.fixture
def opening(tmp_path):
    root = tmp_path / "project"
    path = create_fixture(root, 30)
    services = _services(root)
    try:
        yield services, path, RequestContext("open-test", run_id="open-test")
    finally:
        services.close()


def test_prepared_open_across_threads_validates_each_record_once_and_reuses_variant(opening, monkeypatch):
    services, path, context = opening
    calls, decoded = [], []
    original = repository.validate_v2

    def validate(document, ref):
        calls.append(ref.kind)
        return original(document, ref)

    monkeypatch.setattr(repository, "validate_v2", validate)
    monkeypatch.setattr(project_activation_journal, "validate_v2", validate)
    deserialize = VariantSnapshot.from_dto.__func__

    def from_dto(cls, dto, ref):
        decoded.append(ref)
        return deserialize(cls, dto, ref)

    monkeypatch.setattr(VariantSnapshot, "from_dto", classmethod(from_dto))
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(services.current_project_opener.prepare_path, path, context).result()
    assert result.is_success, result.diagnostics
    views = []
    activated = services.current_project_opener.activate(result.value, context, prepare_view=views.append)
    assert activated.is_success, activated.diagnostics
    # The legacy fixture also publishes the newly bound source artifact reference.
    assert calls.count(EntityKind.PROJECT) == 2
    assert calls.count(EntityKind.VARIANT) == 1
    assert decoded == [result.value.variant_ref]
    final = services.project_projection.snapshot()
    assert views[0].values["entries"] is final.values["entries"]
    before = tuple(calls)
    assert services.gui_project_commands.save(context).is_success
    assert services.gui_project_commands.save(context).is_success
    assert tuple(calls) == before
    assert services.project_projection.snapshot() is final


@pytest.mark.parametrize("kind", ["project", "variant"])
def test_prepared_open_refuses_same_revision_external_change(opening, kind):
    services, path, context = opening
    prepared = services.current_project_opener.prepare_path(path, context).value
    record_path = path if kind == "project" else Path(services.variants.path_for(prepared.variant_ref))
    document = json.loads(record_path.read_text(encoding="utf-8"))
    if kind == "project":
        document["data"]["name"] = "changed externally"
    else:
        document["data"]["entries"][0]["stage"] = 5
    record_path.write_text(json.dumps(document), encoding="utf-8")
    changed = record_path.read_bytes()
    result = services.current_project_opener.activate(prepared, context)
    assert not result.is_success
    assert result.diagnostics[0].code == "PREPARED_PROJECT_INPUT_CHANGED"
    assert services.project_lifecycle.active is None
    assert record_path.read_bytes() == changed


def test_final_activation_still_rejects_changes_after_candidate_loading(opening):
    services, path, context = opening
    prepared = services.current_project_opener.prepare_path(path, context).value

    def change_during_view(_projection):
        document = json.loads(path.read_text(encoding="utf-8"))
        document["data"]["name"] = "changed after preparation"
        path.write_text(json.dumps(document), encoding="utf-8")

    result = services.current_project_opener.activate(prepared, context, prepare_view=change_during_view)
    assert not result.is_success
    assert services.project_lifecycle.active is None
    assert services.project_projection.snapshot() is None
    assert json.loads(path.read_text(encoding="utf-8"))["data"]["name"] == "changed after preparation"


def test_changed_caller_dto_cannot_pollute_prepared_input_evidence(opening):
    from transbridge.persistence.v2.validated_reads import reuse_validated_reads

    services, path, context = opening
    prepared = services.current_project_opener.prepare_path(path, context).value
    with reuse_validated_reads(prepared.prepared_read.evidence):
        dto = services.projects.read_snapshot(prepared.project_ref)
        dto.envelope.data["name"] = "tampered in caller"
        dto.envelope.data["sources"].clear()
    result = services.current_project_opener.activate(prepared, context)
    assert result.is_success, result.diagnostics
    assert services.project_lifecycle.active.project.envelope.data["name"] == "Synthetic benchmark"
    assert len(services.project_lifecycle.active.variant.snapshot().entries) == 30


@pytest.mark.parametrize("migrate_between", [False, True])
def test_old_records_remain_usable_when_normal_migration_occurs_between_phases(opening, migrate_between):
    services, path, context = opening
    current = services.current_project_opener.prepare_path(path, context).value
    variant_path = Path(services.variants.path_for(current.variant_ref))
    for record_path in (path, variant_path):
        document = json.loads(record_path.read_text(encoding="utf-8"))
        document["schema_version"] = 3
        record_path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes(), variant_path.read_bytes()
    result = services.current_project_opener.prepare_path(path, context)
    assert result.is_success, result.diagnostics
    assert (path.read_bytes(), variant_path.read_bytes()) == before
    if migrate_between:
        services.projects.load(current.project_ref)
        services.variants.load(current.variant_ref)
    activated = services.current_project_opener.activate(result.value, context)
    assert activated.is_success, activated.diagnostics
    assert len(services.project_lifecycle.active.variant.snapshot().entries) == 30


def test_migration_does_not_hide_changed_preparation_bytes(opening):
    services, path, context = opening
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = 3
    path.write_text(json.dumps(document), encoding="utf-8")
    prepared = services.current_project_opener.prepare_path(path, context).value
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    activated = services.current_project_opener.activate(prepared, context)
    assert not activated.is_success
    assert activated.diagnostics[0].code == "PREPARED_PROJECT_INPUT_CHANGED"
    assert services.project_lifecycle.active is None


@pytest.mark.parametrize("field", ["provenance", "external_refs"])
def test_mutated_nested_variant_metadata_cannot_reuse_prepared_snapshot(opening, field):
    services, path, context = opening
    original = services.current_project_opener.prepare_path(path, context).value
    variant_path = Path(services.variants.path_for(original.variant_ref))
    document = json.loads(variant_path.read_text(encoding="utf-8"))
    metadata = {"nested": {"items": ["saved"]}}
    document["data"]["entries"][0][field] = [
        {"run_id": "saved", "actor": "test", "source": "import", "metadata": metadata}
        if field == "provenance"
        else {"system": "test", "scope": "saved", "opaque_id": "one", "metadata": metadata}
    ]
    variant_path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes(), variant_path.read_bytes()
    prepared = services.current_project_opener.prepare_path(path, context).value
    entry = prepared.prepared_read.snapshot.entries[0]
    owner = getattr(entry, field)[0]
    dict(owner.metadata)["nested"]["items"].append("tampered")
    activated = services.current_project_opener.activate(prepared, context)
    assert not activated.is_success
    assert activated.diagnostics[0].code == "PREPARED_PROJECT_INPUT_CHANGED"
    assert services.project_lifecycle.active is None
    assert (path.read_bytes(), variant_path.read_bytes()) == before


def test_prepared_label_library_returns_detached_nested_json(opening):
    services, path, context = opening
    original = services.current_project_opener.prepare_path(path, context).value
    variant_path = Path(services.variants.path_for(original.variant_ref))
    document = json.loads(variant_path.read_text(encoding="utf-8"))
    document["data"]["label_library"] = {"review": {"nested": {"items": ["saved"]}}}
    variant_path.write_text(json.dumps(document), encoding="utf-8")
    before = variant_path.read_bytes()
    prepared = services.current_project_opener.prepare_path(path, context).value
    prepared.prepared_read.snapshot.label_library_data()["review"]["nested"]["items"].append("tampered")
    activated = services.current_project_opener.activate(prepared, context)
    assert activated.is_success, activated.diagnostics
    assert not services.project_lifecycle.active.dirty
    assert (
        services.project_lifecycle.active.variant.snapshot().label_library_data() == document["data"]["label_library"]
    )
    assert variant_path.read_bytes() == before
