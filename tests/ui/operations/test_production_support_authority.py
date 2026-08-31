from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.conftest import make_test_collection
from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.io.identity import EntryKey
from transbridge.ui.operations.production_support import local_snapshots, replace_local_snapshots


class _Commands:
    def __init__(self, result=None) -> None:
        self.result = result or OperationResult.completed({"revision": 4})
        self.calls = []

    def replace_entry_records(self, states, context, **expected):
        self.calls.append((states, context, expected))
        return self.result


def _context(commands: _Commands):
    return SimpleNamespace(
        collection=make_test_collection(2),
        uses_authoritative_projection=True,
        project_commands=commands,
        runtime_context=object(),
    )


def test_paratranz_replacement_commits_variant_before_updating_projection() -> None:
    commands = _Commands()
    context = _context(commands)
    snapshots = tuple(
        replace(item, translation=f"远端-{index}") for index, item in enumerate(local_snapshots(context, 7))
    )

    replace_local_snapshots(
        context,
        snapshots,
        7,
        active_version_identity=("project", "variant"),
        project_revision=3,
        variant_revision=2,
    )

    assert [entry.translation for entry in context.collection] == ["远端-0", "远端-1"]
    states, _runtime, expected = commands.calls[0]
    assert [(item.translation, item.stage) for item in states.values()] == [("远端-0", 0), ("远端-1", 1)]
    assert expected["expected_project_revision"] == 3
    assert expected["expected_variant_revision"] == 2
    assert expected["expected_variant_ref"].identity.value == "variant"


def test_paratranz_replacement_rolls_back_projection_when_variant_commit_fails() -> None:
    failed = OperationResult.failed(DomainError(ErrorCategory.CONFLICT, "STALE", "版本已变化"))
    commands = _Commands(failed)
    context = _context(commands)
    before = [entry.translation for entry in context.collection]
    snapshots = tuple(replace(item, translation="远端") for item in local_snapshots(context, 7))

    with pytest.raises(RuntimeError, match="版本已变化"):
        replace_local_snapshots(
            context,
            snapshots,
            7,
            active_version_identity=("project", "variant"),
            project_revision=3,
            variant_revision=2,
        )

    assert [entry.translation for entry in context.collection] == before


def test_paratranz_replacement_rejects_entry_creation_for_v2_project() -> None:
    commands = _Commands()
    context = _context(commands)
    snapshots = local_snapshots(context, 7)
    extra = replace(snapshots[0], entry_key=EntryKey(snapshots[0].entry_key.namespace, "remote-new"))

    with pytest.raises(RuntimeError, match="新增或删除"):
        replace_local_snapshots(
            context,
            (*snapshots, extra),
            7,
            active_version_identity=("project", "variant"),
            project_revision=3,
            variant_revision=2,
        )

    assert commands.calls == []


def test_paratranz_late_ui_projection_is_not_applied_after_active_version_switch() -> None:
    context = None

    class SwitchingCommands(_Commands):
        def replace_entry_records(self, states, runtime_context, **expected):
            result = super().replace_entry_records(states, runtime_context, **expected)
            context.active_version_identity = ("other-project", "other-variant")
            return result

    commands = SwitchingCommands()
    context = _context(commands)
    context.active_version_identity = ("project", "variant")
    original = context.collection
    snapshots = tuple(replace(item, translation="远端") for item in local_snapshots(context, 7))

    replace_local_snapshots(
        context,
        snapshots,
        7,
        active_version_identity=("project", "variant"),
        project_revision=3,
        variant_revision=2,
    )

    assert context.collection is original


def test_paratranz_external_reference_is_part_of_authoritative_patch() -> None:
    commands = _Commands()
    context = _context(commands)
    snapshots = local_snapshots(context, 7)
    remote_ref = snapshots[0].external_ref
    if remote_ref is None:
        from transbridge.application.io.identity import ExternalEntryRef

        remote_ref = ExternalEntryRef("paratranz", "project:7", 99)
    snapshots = (replace(snapshots[0], external_ref=remote_ref), *snapshots[1:])

    replace_local_snapshots(
        context,
        snapshots,
        7,
        active_version_identity=("project", "variant"),
        project_revision=3,
        variant_revision=2,
    )

    patches, _runtime, _expected = commands.calls[0]
    assert patches[snapshots[0].entry_key].external_refs == (remote_ref,)
    assert context.collection.get(snapshots[0].entry_key.local_key).external_refs == (remote_ref,)


def test_paratranz_download_preserves_source_text_while_applying_translation_for_v2_project() -> None:
    commands = _Commands()
    context = _context(commands)
    snapshots = local_snapshots(context, 7)

    replace_local_snapshots(
        context,
        (
            replace(
                snapshots[0], original="remote original", context="remote context", translation="远端译文", stage=1
            ),
            *snapshots[1:],
        ),
        7,
        active_version_identity=("project", "variant"),
        project_revision=3,
        variant_revision=2,
    )

    entry = context.collection.get(snapshots[0].entry_key.local_key)
    assert (entry.original, entry.context) == (snapshots[0].original, snapshots[0].context)
    assert (entry.translation, entry.stage) == ("远端译文", 1)
    patches = commands.calls[0][0]
    assert patches[entry.identity].translation == "远端译文"
