"""Ordinary sync writeback preserves metadata of a retained remote identity."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.conftest import make_test_collection
from transbridge.application.contracts import OperationResult
from transbridge.application.io.identity import ExternalEntryRef
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.operations.production_support import local_snapshots, replace_local_snapshots


def _context(reference, *, authoritative=False):
    entry = replace(next(iter(make_test_collection(1))), external_refs=(reference,))
    patches = []

    def commit(states, *_args, **_kwargs):
        patches.append(states)
        return OperationResult.completed({"revision": 1})

    return (
        SimpleNamespace(
            collection=TranslationEntryCollection((entry,)),
            uses_authoritative_projection=authoritative,
            project_commands=SimpleNamespace(replace_entry_records=commit),
            runtime_context=object(),
        ),
        patches,
    )


@pytest.mark.parametrize("authoritative", [False, True], ids=["legacy", "variant-patch"])
@pytest.mark.parametrize("change", [{"translation": "云端新译文"}, {"stage": 3}], ids=["translation", "stage"])
def test_state_update_retains_category_file_and_endpoint_metadata(authoritative, change):
    reference = ExternalEntryRef("paratranz", "project:7", 11, (("endpoint", "https://paratranz.cn"), ("file_id", 101)))
    context, patches = _context(reference, authoritative=authoritative)
    before = next(iter(context.collection))
    snapshot = local_snapshots(context, 7)[0]
    # Immutable sync DTOs intentionally omit arbitrary metadata from plan hashes.
    assert snapshot.external_ref.metadata == ()

    replace_local_snapshots(
        context,
        (replace(snapshot, **change),),
        7,
        active_version_identity=("project", "variant"),
        project_revision=0,
        variant_revision=0,
    )

    after = context.collection.get(before.identity)
    assert after.external_refs == (reference,)
    assert after.revision == before.revision.next()
    if authoritative:
        assert patches[0][before.identity].external_refs == (reference,)


def test_changed_remote_identity_never_inherits_previous_file_or_endpoint_metadata():
    old = ExternalEntryRef("paratranz", "project:7", 11, (("endpoint", "https://paratranz.cn"), ("file_id", 101)))
    replacement = ExternalEntryRef("paratranz", "project:7", 22)
    context, _ = _context(old)
    snapshot = local_snapshots(context, 7)[0]

    replace_local_snapshots(context, (replace(snapshot, external_ref=replacement, translation="新目标译文"),), 7)

    assert next(iter(context.collection)).external_refs == (replacement,)


def test_ordinary_reference_without_metadata_keeps_existing_writeback_behavior():
    reference = ExternalEntryRef("paratranz", "project:7", 11)
    context, _ = _context(reference)
    before = next(iter(context.collection))
    snapshot = local_snapshots(context, 7)[0]

    replace_local_snapshots(context, (replace(snapshot, translation="普通下载"),), 7)

    after = next(iter(context.collection))
    assert after.external_refs == (reference,)
    assert after.translation == "普通下载"
    assert after.revision == before.revision.next()
