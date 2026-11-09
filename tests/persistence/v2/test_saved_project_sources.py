"""Saved source results are authoritative across restarts and source file changes."""

from itertools import count
from pathlib import Path

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.project_store.repository import ProjectStore


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
        assert result.value.records.baselines[0].fingerprint == expected.source_fingerprints[0]
        assert result.value.sources[0]["display_name"] == source.name
        assert str(source) not in repr(result.value.sources)
        activated = services.current_project_opener.activate(result.value, context)
        assert activated.is_success, activated.diagnostics
        result = services.current_project_opener.prepare_identity(
            expected.ref.project_id.value, expected.ref.identity.value, context
        )
        assert result.is_success and result.value.recovery is None
        assert services.current_project_opener.activate(result.value, context).is_success
        assert preparer.calls == 0
        assert services.project_lifecycle.active.variant.snapshot().entries == expected.entries
        assert len(ProjectStore(path).summary().variants) == 2
        assert str(source).encode() not in path.read_bytes()
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
