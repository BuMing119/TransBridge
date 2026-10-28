"""Saved source results are authoritative across restarts and source file changes."""

from itertools import count
import json
from pathlib import Path

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer


class CountingPreparer:
    def __init__(self, *, forbidden=False):
        self.calls = 0
        self.forbidden = forbidden
        self.inner = TranslationIoProjectSourcePreparer()

    def prepare_source(self, *args, **kwargs):
        self.calls += 1
        assert not self.forbidden, "Opening a saved source must not parse live input"
        return self.inner.prepare_source(*args, **kwargs)


def _services(root, preparer=None):
    identities = count()
    return build_persistence_v2_services(
        root,
        id_factory=lambda: f"source-test-{next(identities)}",
        timestamp_factory=lambda: "2026-10-26T00:00:00+00:00",
        source_preparer=preparer,
    )


@pytest.fixture
def saved(tmp_path):
    source = tmp_path / "original.xml"
    source.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    root = tmp_path / "store"
    services = _services(root)
    context = RequestContext("saved-source-test", run_id="saved-source-open")
    created = services.gui_project_commands.create_project(
        ProjectProvisioningRequest("Saved", source=ProjectSourceRequest(str(source), FormatId.XML_EET)), context
    )
    assert created.is_success, created.diagnostics
    active = services.project_lifecycle.active
    path = Path(services.projects.path_for(active.project_ref))
    expected = active.variant.snapshot()
    assert services.gui_project_commands.create_variant("Branch", context, copy_active=True).is_success
    services.close()
    return root, source, path, context, expected


@pytest.mark.parametrize("remove", [False, True])
def test_restart_and_variant_switch_use_saved_hydration_without_live_reads(saved, remove):
    root, source, path, context, expected = saved
    if remove:
        source.unlink()
    else:
        source.write_text("changed and no longer valid XML", encoding="utf-8")
    preparer = CountingPreparer(forbidden=True)
    services = _services(root, preparer)
    try:
        result = services.current_project_opener.prepare_active(context)
        assert result.is_success, result.diagnostics
        assert result.value.recovery is None
        assert result.value.hydrations[0].source_snapshot.content
        assert result.value.baselines[0].fingerprint == expected.source_fingerprints[0]
        activated = services.current_project_opener.activate(result.value, context)
        assert activated.is_success, activated.diagnostics
        result = services.current_project_opener.prepare_identity(
            expected.ref.project_id.value, expected.ref.identity.value, context
        )
        assert result.is_success and result.value.recovery is None
        assert services.current_project_opener.activate(result.value, context).is_success
        assert preparer.calls == 0
        assert services.project_lifecycle.active.variant.snapshot().entries == expected.entries
        assert len(json.loads(path.read_text(encoding="utf-8"))["data"]["variant_ids"]) == 2
    finally:
        services.close()


@pytest.mark.parametrize("missing_fingerprint", [False, True])
def test_legacy_backfill_publishes_only_with_activation_and_reuses_after_restart(saved, missing_fingerprint):
    root, source, path, context, _expected = saved
    document = json.loads(path.read_text(encoding="utf-8"))
    for item in document["data"]["sources"]:
        item.pop("source_artifact_id")
        if missing_fingerprint:
            item.pop("fingerprint")
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()
    preparer = CountingPreparer()
    services = _services(root, preparer)
    try:
        prepared = services.current_project_opener.prepare_path(path, context)
        assert prepared.is_success, prepared.diagnostics
        assert prepared.value.recovery is None
        assert preparer.calls == 1
        assert path.read_bytes() == before
        assert services.project_lifecycle.active is None
        original_variants = document["data"]["variant_ids"]
        activated = services.current_project_opener.activate(prepared.value, context)
        assert activated.is_success, activated.diagnostics
        after = json.loads(path.read_text(encoding="utf-8"))
        assert after["data"]["sources"][0]["source_artifact_id"]
        assert after["data"]["variant_ids"] == original_variants
        assert after["revision"] == document["revision"] + 1
        assert not services.project_lifecycle.active.dirty
    finally:
        services.close()
    source.unlink()
    services = _services(root, CountingPreparer(forbidden=True))
    try:
        restored = services.current_project_opener.prepare_active(context)
        assert restored.is_success and restored.value.recovery is None
    finally:
        services.close()


def test_failed_legacy_activation_does_not_bind_source_or_change_active_pointer(saved):
    root, _source, path, context, _expected = saved
    document = json.loads(path.read_text(encoding="utf-8"))
    document["data"]["sources"][0].pop("source_artifact_id")
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes(), (root / "active-project.json").read_bytes()
    services = _services(root)
    try:
        prepared = services.current_project_opener.prepare_path(path, context)
        assert prepared.is_success and prepared.value.recovery is None

        def failed_view(_view):
            raise ValueError("view preparation failed")

        activated = services.current_project_opener.activate(prepared.value, context, prepare_view=failed_view)
        assert not activated.is_success
        assert services.project_lifecycle.active is None
        assert (path.read_bytes(), (root / "active-project.json").read_bytes()) == before
    finally:
        services.close()


@pytest.mark.parametrize("mutation", ["missing", "options", "namespace", "fingerprint"])
def test_invalid_saved_reference_preserves_variant_in_recovery_without_parsing(saved, mutation):
    root, _source, path, context, expected = saved
    document = json.loads(path.read_text(encoding="utf-8"))
    registration = document["data"]["sources"][0]
    if mutation == "missing":
        registration["source_artifact_id"] = "f" * 64
    elif mutation == "options":
        registration["format_options"]["skip_empty"] = True
    elif mutation == "namespace":
        registration["legacy"]["source_id"] = "different-namespace"
    else:
        registration["fingerprint"] = "f" * 64
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()
    services = _services(root, CountingPreparer(forbidden=True))
    try:
        result = services.current_project_opener.prepare_path(path, context)
        assert result.is_success and result.value.recovery is not None
        assert result.diagnostics[0].code == "PROJECT_SOURCE_ARTIFACT_UNAVAILABLE"
        assert result.value.recovery.variant.entries == expected.entries
        assert services.current_project_opener.activate(result.value, context).is_success
        assert services.project_lifecycle.active is None
        assert path.read_bytes() == before
    finally:
        services.close()


def test_saved_reference_retains_project_context_authorization(saved):
    root, _source, path, _context, _expected = saved
    services = _services(root, CountingPreparer(forbidden=True))
    try:
        result = services.current_project_opener.prepare_path(path, RequestContext("owner", project_id="another"))
        assert not result.is_success
        assert result.diagnostics[0].code == "PROJECT_CONTEXT_MISMATCH"
    finally:
        services.close()


def test_caller_source_mutation_cannot_change_bound_metadata_or_artifact_evidence(saved):
    root, _source, path, context, _expected = saved
    document = json.loads(path.read_text(encoding="utf-8"))
    document["data"]["sources"][0].pop("source_artifact_id")
    path.write_text(json.dumps(document), encoding="utf-8")
    services = _services(root)
    try:
        result = services.current_project_opener.prepare_path(path, context)
        assert result.is_success and result.value.recovery is None
        prepared = result.value
        original_id = prepared.sources[0]["source_artifact_id"]
        prepared.sources[0]["source_artifact_id"] = "f" * 64
        prepared.sources[0]["format_options"]["skip_empty"] = True
        activated = services.current_project_opener.activate(prepared, context)
        assert activated.is_success, activated.diagnostics
        source = json.loads(path.read_text(encoding="utf-8"))["data"]["sources"][0]
        assert source["source_artifact_id"] == original_id
        assert not source["format_options"]["skip_empty"]
    finally:
        services.close()
