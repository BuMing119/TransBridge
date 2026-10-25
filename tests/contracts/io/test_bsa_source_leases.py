"""Archived localized templates retain physical source leases through publication."""

from dataclasses import replace
import hashlib
from pathlib import Path
import struct

import pytest

from tests.bsa_fixtures import write_bsa
from tests.integration.publish.test_plugin_fidelity import _localized_quest, _texts
from tests.integration.publish.test_plugin_transfer import _draft, _run
from tests.parser.test_original_match_plugin import quest_plugin
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter
from transbridge.application.io.plugin_transfer import PluginTransferPreflightService
from transbridge.application.io.plugin_transfer_snapshot import freeze_template, verify_leases
from transbridge.application.io.publish import ImmediateCommitGuard, OsPublishFilesystem
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.fomod.plugin_publishing import prepare_plugin_source, publish_plugin_collection


def _archived_plugin(tmp_path):
    plugin = _localized_quest(tmp_path / "mod.esp")
    loose = tmp_path / "Strings" / "mod_English.dlstrings"
    members = {
        "strings/mod_english.strings": struct.pack("<II", 0, 0),
        "strings/mod_english.dlstrings": loose.read_bytes(),
        "strings/mod_english.ilstrings": struct.pack("<II", 0, 0),
    }
    loose.unlink()
    archive = write_bsa(tmp_path / "mod.BSA", members)
    return plugin, archive, members


def _snapshot(plugin):
    result = SsePluginAdapter().parse(
        ParseRequest(SourceDescriptor(str(plugin)), RequestContext("bsa-source-lease"), FormatId.PLUGIN_SSE)
    )
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    return result.source_snapshot


def _collection(source):
    return TranslationEntryCollection(
        replace(entry, translation=f"Translated {entry.original}", stage=1) for entry in source.collection_entries()
    )


def test_fomod_captures_one_archive_fingerprint_for_three_virtual_members(tmp_path):
    plugin, archive, _members = _archived_plugin(tmp_path)

    source = prepare_plugin_source(plugin)

    assert len(source.fingerprints) == 5
    captured_paths = [Path(path) for _requested, path, _fingerprint in source.fingerprints]
    assert archive.resolve() in captured_paths
    assert all(not requested.startswith("bsa:") for requested, _path, _fingerprint in source.fingerprints)
    fingerprint = next(value for _requested, path, value in source.fingerprints if Path(path) == archive.resolve())
    assert fingerprint.sha256 == hashlib.sha256(archive.read_bytes()).hexdigest()
    companions = dict(source.snapshot.metadata)["localized_sources"]
    assert len(companions) == 3
    assert all(companion.source.uri.startswith("bsa:") for companion, _entries in companions)
    assert all(companion.sha256 != fingerprint.sha256 for companion, _entries in companions)
    assert [entry.original for entry in source.collection_entries()] == ["First", "Second", "Other"]


def test_fomod_publishes_archived_translations_as_loose_outputs_without_modifying_archive(tmp_path):
    plugin, archive, _members = _archived_plugin(tmp_path)
    original_archive = archive.read_bytes()
    source = prepare_plugin_source(plugin)

    result = publish_plugin_collection(plugin, _collection(source), source=source)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert _texts(plugin, localized=True) == ["Translated First", "Translated Second", "Translated Other"]
    assert archive.read_bytes() == original_archive


@pytest.mark.parametrize("change", ["archive", "loose-shadow"])
@pytest.mark.parametrize("phase", ["before_write", "inside_commit"])
def test_fomod_blocks_changed_archive_or_new_loose_shadow_until_final_commit(tmp_path, monkeypatch, change, phase):
    plugin, archive, members = _archived_plugin(tmp_path)
    source = prepare_plugin_source(plugin)
    original_plugin = plugin.read_bytes()

    def mutate():
        if change == "archive":
            # Selected member bytes remain identical; the whole archive lease
            # must still detect unrelated archive changes.
            write_bsa(archive, {**members, "other/unrelated.txt": b"external archive modification"})
        else:
            (tmp_path / "Strings" / "mod_English.dlstrings").write_bytes(members["strings/mod_english.dlstrings"])

    if phase == "before_write":
        mutate()
    else:
        commit = ImmediateCommitGuard.commit

        def mutate_before_commit(guard, run_id, mutation):
            mutate()
            return commit(guard, run_id, mutation)

        monkeypatch.setattr(ImmediateCommitGuard, "commit", mutate_before_commit)
    with pytest.raises(RuntimeError, match="FOMOD_PLUGIN_SOURCE_CHANGED"):
        publish_plugin_collection(plugin, _collection(source), source=source)
    assert plugin.read_bytes() == original_plugin


def test_fomod_detects_archive_change_during_snapshot_capture(tmp_path, monkeypatch):
    plugin, archive, members = _archived_plugin(tmp_path)
    parse = SsePluginAdapter.parse

    def mutate_after_parse(adapter, request):
        result = parse(adapter, request)
        write_bsa(archive, {**members, "other/unrelated.txt": b"changed during capture"})
        return result

    monkeypatch.setattr(SsePluginAdapter, "parse", mutate_after_parse)
    with pytest.raises(RuntimeError, match="FOMOD_PLUGIN_SOURCE_CHANGED"):
        prepare_plugin_source(plugin)


def test_transfer_template_leases_physical_archive_and_missing_shadow_paths(tmp_path):
    plugin, archive, _members = _archived_plugin(tmp_path)

    leases = freeze_template(_snapshot(plugin))

    assert len(leases) == 5
    assert all(not lease.requested_path.startswith("bsa:") for lease in leases)
    archived = next(lease for lease in leases if Path(lease.path) == archive.resolve())
    assert archived.fingerprint.sha256 == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert sum(not lease.fingerprint.exists for lease in leases) == 3
    verify_leases(leases)


def test_unused_archive_is_leased_when_loose_files_own_every_variant(tmp_path):
    plugin, archive, members = _archived_plugin(tmp_path)
    for name, content in members.items():
        (tmp_path / "Strings" / Path(name).name).write_bytes(content)

    source = prepare_plugin_source(plugin)
    leases = freeze_template(source.snapshot)

    assert all(
        dict(companion.metadata)["strings.storage"] == "loose"
        for companion, _ in dict(source.snapshot.metadata)["localized_sources"]
    )
    assert len(leases) == 5
    assert all(lease.fingerprint.exists for lease in leases)
    verify_leases(leases)
    write_bsa(archive, {**members, "other/unrelated.txt": b"unused archive still changed"})
    with pytest.raises(FileExistsError, match="目标模板在预检后变化"):
        verify_leases(leases)


@pytest.mark.parametrize("change", ["archive", "loose-shadow"])
def test_transfer_leases_reject_full_archive_change_and_identical_loose_shadow(tmp_path, change):
    plugin, archive, members = _archived_plugin(tmp_path)
    leases = freeze_template(_snapshot(plugin))
    if change == "archive":
        write_bsa(archive, {**members, "other/unrelated.txt": b"archive changed after freeze"})
    else:
        (tmp_path / "Strings" / "mod_English.dlstrings").write_bytes(members["strings/mod_english.dlstrings"])

    with pytest.raises(FileExistsError, match="目标模板在预检后变化"):
        verify_leases(leases)


@pytest.mark.parametrize("change", ["member-bytes", "loose-shadow"])
def test_transfer_freeze_checks_the_current_member_bytes_and_resource_selection(tmp_path, change):
    plugin, archive, members = _archived_plugin(tmp_path)
    snapshot = _snapshot(plugin)
    if change == "member-bytes":
        updated = members["strings/mod_english.dlstrings"].replace(b"First", b"Other")
        write_bsa(archive, {**members, "strings/mod_english.dlstrings": updated})
    else:
        (tmp_path / "Strings" / "mod_English.dlstrings").write_bytes(members["strings/mod_english.dlstrings"])

    with pytest.raises(FileExistsError, match="目标字符串文件在读取期间变化"):
        freeze_template(snapshot)


def test_transfer_freeze_rechecks_members_after_physical_fingerprinting(tmp_path, monkeypatch):
    plugin, archive, members = _archived_plugin(tmp_path)
    snapshot = _snapshot(plugin)
    fingerprint = OsPublishFilesystem.fingerprint
    changed = False

    def mutate_after_fingerprint(filesystem, path):
        nonlocal changed
        value = fingerprint(filesystem, path)
        if Path(path) == archive.resolve() and not changed:
            changed = True
            updated = members["strings/mod_english.dlstrings"].replace(b"First", b"Other")
            write_bsa(archive, {**members, "strings/mod_english.dlstrings": updated})
        return value

    monkeypatch.setattr(OsPublishFilesystem, "fingerprint", mutate_after_fingerprint)
    with pytest.raises(FileExistsError, match="目标字符串文件在读取期间变化"):
        freeze_template(snapshot)
    assert changed


def test_real_transfer_preflight_and_publication_accept_archived_target(tmp_path):
    plugin, archive, _members = _archived_plugin(tmp_path)
    source = quest_plugin(tmp_path / "old.esp", ["First", "Second", "Other"])
    output = tmp_path / "output.esp"
    original_archive = archive.read_bytes()
    checked = PluginTransferPreflightService().preflight(
        _draft(source, plugin, output, {"First": "一", "Second": "二", "Other": "三"})
    )

    result = _run(checked)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert _texts(output, localized=True) == ["一", "二", "三"]
    assert archive.read_bytes() == original_archive


@pytest.mark.parametrize("archive_role", ["source", "template"])
def test_transfer_rejects_output_overwriting_a_source_or_template_bsa_archive(tmp_path, archive_role):
    plugin, archive, _members = _archived_plugin(tmp_path)
    inline = quest_plugin(tmp_path / "inline.esp", ["First", "Second", "Other"])
    source, template = (plugin, inline) if archive_role == "source" else (inline, plugin)
    originals = {path: path.read_bytes() for path in (plugin, inline, archive)}
    assert originals[archive].startswith(b"BSA\0")

    checked = PluginTransferPreflightService().preflight(
        _draft(source, template, archive, {"First": "一", "Second": "二", "Other": "三"}, overwrite=True)
    )

    assert not checked.ready
    assert checked.write_preflight is None
    assert any(
        item.code == "TRANSFER_PREFLIGHT_FAILED" and "BSA" in item.message and not item.passed
        for item in checked.checks
    )
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert not tuple(tmp_path.glob(".transbridge-publish-*"))
    assert not tuple(tmp_path.glob("*.bak"))
