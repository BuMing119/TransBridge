from dataclasses import replace
import json

import pytest

from tests.application.terminology.story08_support import build
from tests.application.terminology.test_build import _snapshot, _source
from transbridge.application.io import SourceSnapshot
from transbridge.application.terminology.build_freshness import build_input_digest, require_current_build
from transbridge.application.terminology.errors import StaleBuildError
from transbridge.application.terminology.input_capture import SourceLease
from transbridge.application.terminology.models import BuildResult
from transbridge.persistence.terminology.codec import dumps, loads


def test_source_proof_ignores_review_edits_and_effective_pointer_changes():
    snapshot = _snapshot((_source("source-a"),))
    edited = replace(
        snapshot,
        draft_id="new-draft",
        draft_revision=9,
        decision_digest="a" * 64,
        effective_version_id="published",
        effective_content_digest="b" * 64,
    )
    assert build_input_digest(snapshot) == build_input_digest(edited)


@pytest.mark.parametrize("field", ["project_revision", "variant_revision", "variant_content_digest", "config_digest"])
def test_changed_project_variant_or_configuration_invalidates_build(field):
    snapshot = _snapshot((_source("source-a"),))
    old = getattr(snapshot, field)
    changed = replace(snapshot, **{field: old + 1 if isinstance(old, int) else "a" * 64})
    result = replace(build(), input_digest=build_input_digest(snapshot))

    with pytest.raises(StaleBuildError, match="重新构建"):
        require_current_build(result, build_input_digest(changed))


def test_source_bytes_adapter_and_scope_are_part_of_freshness_proof():
    source = _source("source-a")
    snapshot = _snapshot((source,))
    changed_bytes = SourceSnapshot.from_bytes(source.lease.snapshot.source, source.registration.format_id, b"changed")
    for changed in (
        replace(source, lease=SourceLease("source-a", changed_bytes, changed_bytes.sha256)),
        replace(source, adapter_version="next"),
        replace(source, registration=replace(source.registration, plugin_scope="A.esm")),
    ):
        assert build_input_digest(snapshot) != build_input_digest(replace(snapshot, sources=(changed,)))


def test_old_serialized_build_remains_readable_but_cannot_be_newly_published():
    payload = json.loads(dumps(build()))
    del payload["fields"]["input_digest"]
    legacy = loads(json.dumps(payload), BuildResult)

    assert legacy.ref == build().ref
    assert legacy.input_digest is None
    with pytest.raises(StaleBuildError, match="重新构建"):
        require_current_build(legacy, "current")
