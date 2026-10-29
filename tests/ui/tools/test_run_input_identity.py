"""Task input identity follows saved sources independently of external references."""

from types import SimpleNamespace

import pytest

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.tasks import OwnerRef
from transbridge.ui.tools.ai_translator.run_spec import build_run_spec


def _spec(reference, *, namespace="saved-source", revision=1):
    return build_run_spec(
        run_id="run",
        generation=1,
        mode="translate",
        owner=OwnerRef("owner", "ui.ai-translator", project_id="project", variant_id="variant"),
        config=SimpleNamespace(model="model", api_key="secret"),
        entries=(SimpleNamespace(id="entry", key="local", identity=EntryKey(SourceNamespace(namespace), "local")),),
        esp_path=reference,
        overwrite=False,
        project_revision=revision,
    )


@pytest.mark.parametrize("reference", [None, "", "renamed-reference.esp", "D:/missing-reference.esp"])
def test_input_identity_is_unchanged_when_reference_is_updated_or_cleared(reference):
    original = _spec("original-reference.esp")
    changed = _spec(reference)

    assert changed.input_ref == original.input_ref
    assert changed.input_fingerprint == original.input_fingerprint
    assert changed.input_ref.startswith("sources:")


def test_saved_source_and_project_revision_changes_still_change_input_identity():
    original = _spec(None)

    assert _spec(None, namespace="other-source").input_fingerprint != original.input_fingerprint
    assert _spec(None, revision=2).input_fingerprint != original.input_fingerprint
