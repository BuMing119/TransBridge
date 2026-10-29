"""Proved transfers leave projects and unproved target records unchanged."""

from dataclasses import replace
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from tests.integration.publish.test_plugin_fidelity import _localized_quest, _texts
from tests.parser.test_original_match_plugin import quest_plugin
from tests.plugin_fixtures import _field, _record, write_plugin
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.io.operation_write import HydratedWriteDraft
from transbridge.application.io.plugin_transfer import (
    PluginTransferDraft,
    PluginTransferPreflightService,
    PluginTransferWorkload,
)
from transbridge.application.io.publish import ConflictPolicy, ImmediateCommitGuard, OsPublishFilesystem


def _draft(source, target, output, translations, *, stages=None, overwrite=False, language="english"):
    parsed = SsePluginAdapter().parse(
        ParseRequest(
            SourceDescriptor(str(source)),
            RequestContext("transfer-test", run_id="transfer-run"),
            FormatId.PLUGIN_SSE,
            SourceNamespace("project:old-source"),
            options=(("skip_empty", False), ("language", language)),
        )
    )
    entries = tuple(
        replace(entry, translation=translations.get(entry.original, ""), stage=(stages or {}).get(entry.original, 1))
        for entry in parsed.entries
    )
    write = HydratedWriteDraft(
        parsed.source_snapshot,
        FormatId.PLUGIN_SSE,
        tuple(entry.snapshot() for entry in entries),
        str(output),
        1,
        RequestContext("transfer-test", run_id="transfer-run"),
        conflict_policy=ConflictPolicy.EXPLICIT_OVERWRITE if overwrite else ConflictPolicy.FAIL,
    )
    return PluginTransferDraft(write, str(target))


def _run(checked, guard=None, cancellation=None):
    assert checked.ready, checked.checks
    return PluginTransferWorkload(checked)(
        SimpleNamespace(
            ref=SimpleNamespace(run_id="transfer-run"),
            cancellation=cancellation,
            publish_commit_guard=lambda: guard or ImmediateCommitGuard("transfer-run"),
        )
    )


def _case(tmp_path):
    source = write_plugin(tmp_path / "old.esp", [(0x800, "A", "First"), (0x801, "B", "Second")])
    target = write_plugin(tmp_path / "new.esp", [(0x800, "A", "First"), (0x801, "B", "Second")])
    return source, target, tmp_path / "output.esp"


def test_unselected_output_stays_empty_and_never_resolves_to_working_directory(tmp_path, monkeypatch):
    from transbridge.application.io import plugin_transfer

    source, target, _output = _case(tmp_path)
    draft = _draft(source, target, "", {"First": "一", "Second": "二"})
    original = plugin_transfer.canonical

    def require_selected_path(path):
        assert str(path).strip(), "An unselected output must not resolve to the working directory"
        return original(path)

    monkeypatch.setattr(plugin_transfer, "canonical", require_selected_path)
    checked = PluginTransferPreflightService().preflight(draft)

    assert not checked.ready
    assert checked.write_preflight is not None
    assert checked.write_preflight.draft.target_path == ""
    assert checked.write_preflight.artifact_fingerprints == ()
    assert {item.code for item in checked.checks if not item.passed and not item.warning} == {
        "OUTPUT_WRITABLE",
        "ATOMIC_REPLACE_AVAILABLE",
    }


def test_transfer_uses_target_identity_and_does_not_change_project_or_inputs(tmp_path):
    source, target, output = _case(tmp_path)
    draft = _draft(source, target, output, {"First": "一", "Second": "二"})
    source_bytes, target_bytes = source.read_bytes(), target.read_bytes()
    checked = PluginTransferPreflightService().preflight(draft)
    assert checked.write_preflight.draft.source_snapshot.source.uri == str(target)
    assert checked.write_preflight.draft.entries[0].identity.namespace != draft.write.entries[0].entry_key.namespace
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED
    assert _texts(output) == ["一", "二"]
    assert source.read_bytes() == source_bytes and target.read_bytes() == target_bytes
    assert draft.write.entries[0].translation == "一"
    assert result.value["transfer_report"]["writable_count"] == 2
    assert result.counts.succeeded == 2 and result.counts.failed == 0 and result.counts.skipped == 0
    assert result.artifact_refs == (str(output),)


def test_changed_original_deleted_and_added_records_do_not_block_safe_transfer(tmp_path):
    source = write_plugin(
        tmp_path / "old.esp", [(0x800, "A", "First"), (0x801, "B", "Second"), (0x802, "C", "Deleted")]
    )
    target = write_plugin(tmp_path / "new.esp", [(0x800, "A", "First"), (0x801, "B", "Changed"), (0x803, "D", "Added")])
    output = tmp_path / "output.esp"
    checked = PluginTransferPreflightService().preflight(
        _draft(source, target, output, {"First": "一", "Second": "二", "Deleted": "删"})
    )
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED
    assert result.counts.failed == 0 and result.counts.succeeded == 1 and result.counts.skipped == 2
    assert result.diagnostics[-1].code == "TRANSFER_PRESERVED"
    assert _texts(output) == ["一", "Changed", "Added"]
    codes = {issue.code for issue in checked.report.issues}
    assert {"TRANSFER_ORIGINAL_CHANGED", "TRANSFER_UNMATCHED", "TRANSFER_TARGET_PRESERVED"} <= codes
    assert result.artifact_refs


@pytest.mark.parametrize("translated", [("X", "Y"), ("Same", "Same"), ("Second", "First")])
def test_colliding_locators_are_disambiguated_by_exact_original(tmp_path, translated):
    source = quest_plugin(tmp_path / "old.esp", ["First", "Second"])
    target = quest_plugin(tmp_path / "new.esp", ["Second", "First", "Added"])
    output = tmp_path / "output.esp"
    checked = PluginTransferPreflightService().preflight(
        _draft(source, target, output, dict(zip(["First", "Second"], translated)))
    )
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED
    assert _texts(output) == [translated[1], translated[0], "Added"]


def test_identical_target_duplicates_are_preserved_while_unique_record_is_written(tmp_path):
    source = quest_plugin(tmp_path / "old.esp", ["First", "Second"])
    target = quest_plugin(tmp_path / "new.esp", ["First", "First", "Second"])
    output = tmp_path / "output.esp"
    checked = PluginTransferPreflightService().preflight(
        _draft(source, target, output, {"First": "一", "Second": "二"})
    )
    assert _run(checked).outcome is OperationOutcome.COMPLETED
    assert _texts(output) == ["First", "First", "二"]


@pytest.mark.parametrize("stage,translation", [(0, "Draft"), (-1, "Hidden"), (9, "")])
def test_unpublishable_stages_preserve_target_and_do_not_block_other_entries(tmp_path, stage, translation):
    source, target, output = _case(tmp_path)
    checked = PluginTransferPreflightService().preflight(
        _draft(source, target, output, {"First": translation, "Second": "二"}, stages={"First": stage})
    )
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED
    assert result.counts.failed == 0 and result.counts.skipped == 1
    assert _texts(output) == ["First", "二"]


def test_zero_safe_matches_blocks_output(tmp_path):
    source, target, output = _case(tmp_path)
    checked = PluginTransferPreflightService().preflight(_draft(source, target, output, {}))
    assert not checked.ready and checked.write_preflight is None
    assert any(item.code == "TRANSFER_EMPTY" for item in checked.checks)
    assert not output.exists()


@pytest.mark.parametrize("moment", ["before_worker", "inside_commit"])
@pytest.mark.parametrize("changed", ["template", "output"])
def test_changed_file_is_rejected_even_with_overwrite_authorization(tmp_path, moment, changed):
    source, target, output = _case(tmp_path)
    output.write_bytes(b"old output")
    checked = PluginTransferPreflightService().preflight(
        _draft(source, target, output, {"First": "一", "Second": "二"}, overwrite=True)
    )
    path = target if changed == "template" else output
    if moment == "before_worker":
        path.write_bytes(b"external change")
        guard = None
    else:

        class ChangingGuard:
            def commit(self, run_id, mutation):
                path.write_bytes(b"external change")
                return ImmediateCommitGuard(run_id).commit(run_id, mutation)

        guard = ChangingGuard()
    result = _run(checked, guard)
    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "TRANSFER_FILES_CHANGED"
    assert path.read_bytes() == b"external change"
    assert output.read_bytes() == (b"external change" if changed == "output" else b"old output")


def test_project_source_cannot_be_overwritten_by_a_different_template(tmp_path):
    source, target, _output = _case(tmp_path)
    original = source.read_bytes()
    checked = PluginTransferPreflightService().preflight(
        _draft(source, target, source, {"First": "一"}, overwrite=True)
    )
    assert not checked.ready
    assert "原项目来源" in checked.checks[-1].message
    assert source.read_bytes() == original


def test_same_template_in_place_requires_confirmation_and_creates_backup(tmp_path):
    source, _target, _output = _case(tmp_path)
    original = source.read_bytes()
    blocked = PluginTransferPreflightService().preflight(
        _draft(source, source, source, {"First": "一", "Second": "二"})
    )
    assert not blocked.ready
    checked = PluginTransferPreflightService().preflight(
        _draft(source, source, source, {"First": "一", "Second": "二"}, overwrite=True)
    )
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED
    assert _texts(source) == ["一", "二"]
    assert Path(result.value["backup_path"]).read_bytes() == original


def test_terminology_is_projected_once_before_target_namespace_mapping(tmp_path):
    source, target, output = _case(tmp_path)
    draft = _draft(source, target, output, {"First": "一", "Second": "二"})
    calls = []

    class Projection:
        metadata = (("profile", "frozen"),)

        def project_entries(self, entries):
            calls.append(entries[0].identity.namespace)
            return (replace(entries[0], translation="Projected"), entries[1]), ()

    service = SimpleNamespace(freeze_projection=lambda write: Projection())
    checked = PluginTransferPreflightService(service).preflight(draft)
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED
    assert calls == [draft.write.entries[0].entry_key.namespace]
    assert _texts(output) == ["Projected", "二"]
    assert result.value["terminology_profile"] == {"profile": "frozen"}


def _localized_case(tmp_path):
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    source = _localized_quest(old_dir / "old.esp")
    target = _localized_quest(new_dir / "new.esp")
    return source, target, new_dir / "output.esp"


def test_localized_transfer_keeps_target_string_ids_and_all_companions(tmp_path):
    source, target, output = _localized_case(tmp_path)
    checked = PluginTransferPreflightService().preflight(
        _draft(source, target, output, {"First": "Same", "Second": "Same"})
    )
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED
    assert _texts(output, localized=True) == ["Same", "Same", "Other"]
    assert len(result.artifact_refs) == 2


def test_localized_companion_change_invalidates_template(tmp_path):
    source, target, output = _localized_case(tmp_path)
    checked = PluginTransferPreflightService().preflight(_draft(source, target, output, {"First": "一"}))
    (target.parent / "Strings" / "new_English.dlstrings").write_bytes(b"changed")
    result = _run(checked)
    assert result.outcome is OperationOutcome.FAILED and not output.exists()


def _with_masters(path, masters):
    records = b"".join(
        _record(b"NPC_", value, _field(b"EDID", editor) + _field(b"FULL", text))
        for value, editor, text in [(0x800, "MasterNpc", "First"), (0x02000801, "OwnNpc", "Second")]
    )
    header = b"".join(_field(b"MAST", master) + b"DATA\x08\x00" + b"\0" * 8 for master in masters)
    path.write_bytes(
        _record(b"TES4", 0, header)
        + struct.pack("<4sI4siHHI", b"GRUP", 24 + len(records), b"NPC_", 0, 0, 0, 0)
        + records
    )
    return path


@pytest.mark.parametrize("masters,expected", [(["A.esm", "Added.esm"], ["一", "二"]), (["B.esm"], ["First", "二"])])
def test_record_owner_is_checked_without_blocking_unaffected_records(tmp_path, masters, expected):
    source = _with_masters(tmp_path / "old.esp", ["A.esm"])
    target = _with_masters(tmp_path / "new.esp", masters)
    output = tmp_path / "output.esp"
    checked = PluginTransferPreflightService().preflight(
        _draft(source, target, output, {"First": "一", "Second": "二"})
    )
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED
    assert _texts(output) == expected


def test_cancelled_transfer_has_no_outputs(tmp_path):
    source, target, output = _case(tmp_path)
    checked = PluginTransferPreflightService().preflight(_draft(source, target, output, {"First": "一"}))
    result = _run(checked, cancellation=SimpleNamespace(is_cancelled=True))
    assert result.outcome is OperationOutcome.CANCELLED and not output.exists()


def _localized_npcs(path, ids):
    _localized_quest(path)
    records = b"".join(
        _record(b"NPC_", 0x800 + index, _field(b"EDID", f"Npc{index}") + b"FULL\x04\x00" + struct.pack("<I", sid))
        for index, sid in enumerate(ids)
    )
    path.write_bytes(
        path.read_bytes()[:24] + struct.pack("<4sI4siHHI", b"GRUP", 24 + len(records), b"NPC_", 0, 0, 0, 0) + records
    )
    return path


@pytest.mark.parametrize("mode", ["unmatched_reference", "conflicting_translations", "consistent_translations"])
def test_shared_localized_ids_are_transferred_only_when_every_reference_agrees(tmp_path, mode):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    source = _localized_npcs(old / "old.esp", (1, 2, 3) if mode == "unmatched_reference" else (1, 1, 3))
    target = _localized_npcs(new / "new.esp", (1, 1, 3))
    output = new / "output.esp"
    draft = _draft(source, target, output, {"First": "一", "Second": "二", "Other": "其他"})
    if mode == "conflicting_translations":
        entries = draft.write.entries
        draft = replace(
            draft,
            write=replace(draft.write, entries=(entries[0], replace(entries[1], translation="不一致"), entries[2])),
        )
    checked = PluginTransferPreflightService().preflight(draft)
    result = _run(checked)
    if mode == "consistent_translations":
        assert result.outcome is OperationOutcome.COMPLETED
        assert _texts(output, localized=True) == ["一", "一", "其他"]
    else:
        assert result.outcome is OperationOutcome.COMPLETED
        assert _texts(output, localized=True) == ["First", "First", "其他"]
        assert any(issue.code == "TRANSFER_SHARED_STRING_CONFLICT" for issue in checked.report.issues)


def test_preflight_reconstructs_entries_from_captured_bytes_not_earlier_live_parse(tmp_path, monkeypatch):
    source, target, output = _case(tmp_path)
    draft = _draft(source, target, output, {"First": "一", "Second": "二"})
    real_parse = SsePluginAdapter.parse

    def mixed_read(adapter, request):
        parsed = real_parse(adapter, request)
        wrong = replace(parsed.entries[0], original="Earlier live read")
        return replace(parsed, entries=(wrong, parsed.entries[1]))

    monkeypatch.setattr(SsePluginAdapter, "parse", mixed_read)
    checked = PluginTransferPreflightService().preflight(draft)
    assert checked.ready
    assert _run(checked).outcome is OperationOutcome.COMPLETED
    assert _texts(output) == ["一", "二"]


def test_new_companion_after_preflight_is_not_silently_ignored(tmp_path):
    source, target, output = _localized_case(tmp_path)
    checked = PluginTransferPreflightService().preflight(_draft(source, target, output, {"First": "一"}))
    (target.parent / "Strings" / "new_English.strings").write_bytes(b"new external companion")
    result = _run(checked)
    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "TRANSFER_FILES_CHANGED"
    assert not output.exists()


def test_template_companion_change_inside_final_commit_is_rejected(tmp_path):
    source, target, output = _localized_case(tmp_path)
    checked = PluginTransferPreflightService().preflight(_draft(source, target, output, {"First": "一"}))

    class ChangingGuard:
        def commit(self, run_id, mutation):
            (target.parent / "Strings" / "new_English.dlstrings").write_bytes(b"external change")
            return ImmediateCommitGuard(run_id).commit(run_id, mutation)

    result = _run(checked, ChangingGuard())
    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "TRANSFER_FILES_CHANGED"
    assert not output.exists() and not (target.parent / "Strings" / "output_English.dlstrings").exists()


def test_renamed_output_may_not_overwrite_project_strings_sidecar(tmp_path):
    source, target, _output = _localized_case(tmp_path)
    # A different plugin suffix can still collide with the original Strings stem.
    output = source.with_suffix(".esm")
    checked = PluginTransferPreflightService().preflight(
        _draft(source, target, output, {"First": "一"}, overwrite=True)
    )
    assert not checked.ready
    assert "原项目来源" in checked.checks[-1].message


def test_in_place_localized_write_backs_up_all_replaced_artifacts(tmp_path):
    source, _target, _output = _localized_case(tmp_path)
    before = {
        source: source.read_bytes(),
        source.parent / "Strings" / "old_English.dlstrings": (
            source.parent / "Strings" / "old_English.dlstrings"
        ).read_bytes(),
    }
    checked = PluginTransferPreflightService().preflight(
        _draft(source, source, source, {"First": "一", "Second": "二", "Other": "其他"}, overwrite=True)
    )
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED
    assert _texts(source, localized=True) == ["一", "二", "其他"]
    assert len(result.value["backup_paths"]) == 2
    assert {Path(path).read_bytes() for path in result.value["backup_paths"]} == set(before.values())


def test_delegate_commit_rejection_preserves_files(tmp_path):
    source, target, output = _case(tmp_path)
    checked = PluginTransferPreflightService().preflight(_draft(source, target, output, {"First": "一"}))
    result = _run(checked, ImmediateCommitGuard("transfer-run", active=lambda: False))
    assert result.outcome is OperationOutcome.FAILED
    assert not output.exists()


def test_real_publish_partial_keeps_its_failure_semantics_and_artifact(tmp_path, monkeypatch):
    source, target, output = _case(tmp_path)
    checked = PluginTransferPreflightService().preflight(_draft(source, target, output, {"First": "一"}))

    def fail_directory_sync(_filesystem, _path):
        raise OSError("injected directory fsync failure")

    monkeypatch.setattr(OsPublishFilesystem, "fsync_directory", fail_directory_sync)
    result = _run(checked)
    assert result.outcome is OperationOutcome.PARTIAL
    assert result.counts.succeeded == 1 and result.counts.failed == 1
    assert result.diagnostics[0].code == "DIRECTORY_FSYNC_FAILED"
    assert result.artifact_refs == (str(output),)
    assert result.value["transfer_report"]["preserved_count"] == 1
    assert _texts(output) == ["一", "Second"]


@pytest.mark.parametrize("suffix", [".esp", ".esm", ".esl"])
@pytest.mark.parametrize("translate_blank", [False, True])
def test_empty_and_whitespace_originals_do_not_block_transfer(tmp_path, suffix, translate_blank):
    records = [(0x800, "A", ""), (0x801, "B", "  \t"), (0x802, "C", "Normal")]
    source = write_plugin(tmp_path / "old.esp", records)
    target = write_plugin(tmp_path / f"new{suffix}", records)
    output = tmp_path / f"output{suffix}"
    translations = {"Normal": "正常译文"}
    if translate_blank:
        translations.update({"": "空原文译文", "  \t": "空白原文译文"})
    checked = PluginTransferPreflightService().preflight(_draft(source, target, output, translations))
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED
    assert result.counts.failed == 0
    assert result.counts.skipped == (0 if translate_blank else 2)
    assert _texts(output) == (
        ["空原文译文", "空白原文译文", "正常译文"] if translate_blank else ["", "  \t", "正常译文"]
    )


def test_transfer_defaults_to_captured_project_localized_language(tmp_path):
    source, target, output = _localized_case(tmp_path)
    for plugin in (source, target):
        folder = plugin.parent / "Strings"
        (folder / f"{plugin.stem}_English.dlstrings").rename(folder / f"{plugin.stem}_French.dlstrings")
    draft = _draft(source, target, output, {"First": "Un", "Second": "Deux", "Other": "Autre"}, language="french")
    checked = PluginTransferPreflightService().preflight(draft)
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED
    assert (output.parent / "Strings" / "output_French.dlstrings").exists()
    assert not (output.parent / "Strings" / "output_English.dlstrings").exists()


def test_explicit_target_language_overrides_captured_project_language(tmp_path):
    source, target, output = _localized_case(tmp_path)
    strings = source.parent / "Strings"
    (strings / "old_English.dlstrings").rename(strings / "old_French.dlstrings")
    draft = _draft(
        source, target, output, {"First": "One", "Second": "Two", "Other": "Other translated"}, language="french"
    )
    draft = replace(draft, write=replace(draft.write, options=(("language", "english"),)))
    checked = PluginTransferPreflightService().preflight(draft)
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED
    assert _texts(output, localized=True) == ["One", "Two", "Other translated"]
    assert (output.parent / "Strings" / "output_English.dlstrings").exists()
    assert not (output.parent / "Strings" / "output_French.dlstrings").exists()
