"""Opening older source namespaces preserves complete saved states or fails closed."""

from dataclasses import replace
from itertools import count
import json
from pathlib import Path

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.v2 import SourceBaseline
from transbridge.persistence.v2.schema import serialize_document


@pytest.fixture
def legacy_source(tmp_path):
    source = tmp_path / "source.xml"
    source.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    identities = count()
    services = build_persistence_v2_services(
        tmp_path / "data",
        id_factory=lambda: str(next(identities)),
        timestamp_factory=lambda: "2026-10-19T00:00:00+00:00",
    )
    context = RequestContext("source-identity", run_id="source-identity")
    try:
        created = services.gui_project_commands.create_project(
            ProjectProvisioningRequest(
                "Old source identity", source=ProjectSourceRequest(str(source), FormatId.XML_EET)
            ),
            context,
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        key = active.variant.snapshot().entries[0].entry_key
        assert services.gui_project_commands.update_entry(
            key, context, translation="Saved translation", stage=5
        ).is_success
        assert services.gui_project_commands.save(context).is_success
        active = services.project_lifecycle.active
        namespace = SourceNamespace(
            "source:older-parser:sha256:" + active.variant.snapshot().source_fingerprints[0].sha256
        )
        stored = active.variant.snapshot()
        stored = replace(
            stored,
            source_fingerprints=tuple(replace(item, namespace=namespace) for item in stored.source_fingerprints),
            entries=tuple(
                replace(item, entry_key=replace(item.entry_key, namespace=namespace)) for item in stored.entries
            ),
        )
        variant_path = Path(services.variants.path_for(stored.ref))
        variant_path.write_bytes(serialize_document(stored.to_dto().envelope.to_dict()))
        project_path = Path(services.projects.path_for(active.project_ref))
        document = json.loads(project_path.read_bytes())
        record = document["data"]["sources"][0]
        record.pop("fingerprint", None)
        record["legacy"] = {"namespace": str(source), "path": str(source)}
        record["format_options"].pop("source_namespace", None)
        project_path.write_bytes(serialize_document(document))
        yield services, context, project_path, variant_path, stored
    finally:
        services.close()


@pytest.mark.parametrize("changed_external", [False, True])
def test_legacy_path_metadata_uses_saved_content_and_keeps_translation_state(legacy_source, changed_external):
    services, context, path, variant_path, stored = legacy_source
    before = variant_path.read_bytes()
    if changed_external:
        source = json.loads(path.read_bytes())["data"]["sources"][0]["location"]
        with Path(source).open("ab") as stream:
            stream.write(b"\n")
    prepared = services.current_project_opener.prepare_path(str(path), context)
    assert prepared.is_success, prepared.diagnostics
    assert prepared.value.recovery is None
    assert prepared.value.baselines[0].fingerprint.sha256 == stored.source_fingerprints[0].sha256
    assert not prepared.value.missing_inputs
    opened = services.current_project_opener.activate(prepared.value, context)
    assert opened.is_success, opened.diagnostics
    actual = services.project_lifecycle.active.variant.snapshot()
    _assert_saved_states(actual, stored)
    assert variant_path.read_bytes() != before
    assert json.loads(path.read_bytes())["data"]["sources"][0]["content_state"] == "captured"


@pytest.mark.parametrize("conflict", ["ambiguous", "explicit-option", "unmatched-entry", "unmatched-empty-entry"])
def test_unverifiable_identity_preserves_files_and_refuses_activation(legacy_source, conflict):
    services, context, path, variant_path, stored = legacy_source
    if conflict == "ambiguous":
        other = SourceNamespace("source:other-copy")
        stored = replace(
            stored,
            source_fingerprints=(*stored.source_fingerprints, replace(stored.source_fingerprints[0], namespace=other)),
        )
        variant_path.write_bytes(serialize_document(stored.to_dto().envelope.to_dict()))
    elif conflict in {"unmatched-entry", "unmatched-empty-entry"}:
        entry = stored.entries[0]
        if conflict == "unmatched-empty-entry":
            entry = replace(entry, translation="", stage=type(entry.stage)(0))
        stored = replace(
            stored,
            entries=(
                replace(entry, entry_key=replace(entry.entry_key, local_key="unknown-entry")),
                *stored.entries[1:],
            ),
        )
        variant_path.write_bytes(serialize_document(stored.to_dto().envelope.to_dict()))
    else:
        document = json.loads(path.read_bytes())
        source = document["data"]["sources"][0]
        source["format_options"]["source_namespace"] = "source:conflicting-option"
        path.write_bytes(serialize_document(document))
    before = {p: p.read_bytes() for p in Path(services.root).rglob("*.json")}
    active = services.project_lifecycle.active
    opened = services.current_project_opener.open_path(str(path), context)
    assert not opened.is_success
    assert opened.diagnostics[0].code == "PROJECT_CONTENT_REPAIR_FAILED"
    assert services.project_lifecycle.active is active
    assert {p: p.read_bytes() for p in before} == before


def test_lifecycle_loader_recovers_saved_content_even_without_gui_preparation(legacy_source):
    services, context, path, _variant_path, stored = legacy_source
    active = services.project_lifecycle.active
    other = SourceNamespace("source:wrong-source")
    baseline = SourceBaseline(
        replace(stored.source_fingerprints[0], namespace=other),
        tuple(replace(item, entry_key=replace(item.entry_key, namespace=other)) for item in stored.entries),
    )
    services.baselines.replace_many(active.project_ref, (stored.ref,), (baseline,))
    # The facade reaches the same candidate loader as a direct or background switch.
    result = services.gui_project_commands.switch_v2(active.project_ref, stored.ref, context)
    assert result.is_success, result.diagnostics
    assert not services.project_lifecycle.active.missing_inputs
    _assert_saved_states(services.project_lifecycle.active.variant.snapshot(), stored)


def test_known_fingerprint_is_not_confused_by_an_older_snapshot_namespace(legacy_source):
    services, context, path, _variant_path, stored = legacy_source
    document = json.loads(path.read_bytes())
    document["data"]["sources"][0]["fingerprint"] = stored.source_fingerprints[0].sha256
    path.write_bytes(serialize_document(document))
    namespace = SourceNamespace("source:previous-revision")
    historical = replace(
        stored,
        source_fingerprints=(replace(stored.source_fingerprints[0], namespace=namespace, sha256="a" * 64),),
        entries=tuple(replace(row, entry_key=replace(row.entry_key, namespace=namespace)) for row in stored.entries),
    )
    snapshot = Path(services.root) / "snapshots" / ("a" * 64 + ".json")
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(
        serialize_document({
            "schema_version": 1,
            "project_id": stored.ref.project_id.value,
            "name": "Previous source revision",
            "variant": historical.to_dto().envelope.to_dict(),
        })
    )

    opened = services.current_project_opener.open_path(path, context)

    assert opened.is_success, opened.diagnostics
    assert not services.project_lifecycle.active.missing_inputs
    _assert_saved_states(services.project_lifecycle.active.variant.snapshot(), stored)


def _assert_saved_states(actual, expected):
    previous = {row.entry_key.local_key: row for row in expected.entries}
    assert len(actual.entries) == len(previous)
    for row in actual.entries:
        saved = previous[row.entry_key.local_key]
        assert replace(row, entry_key=saved.entry_key, revision=saved.revision) == saved
    assert actual.label_library == expected.label_library
