from base64 import b64encode
from dataclasses import replace
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from tests.contracts.io.test_eet_adapter import _parse as parse_eet
from tests.contracts.io.test_localized_hydrated_publication import localized  # noqa: F401
from tests.contracts.io.test_sst_adapter import _parse as parse_sst, _record as sst_record
from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import DomainError, OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, Stage, TranslationIoUseCase
from transbridge.application.io.operation_write import (
    HydratedWriteDraft,
    HydratedWritePreflightService,
    HydratedWriteWorkload,
)
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.projects import ProjectSourceRequest
from transbridge.application.projects.source_extraction import extract_source, reconstructed_source_snapshot
from transbridge.application.projects.working_copy import ProjectWorkingCopy
from transbridge.application.projects.write_inputs import bind_plugin_write_input
from transbridge.config.text_encoding import LEGACY_READ_POLICY, STRICT_READ_POLICY, TextEncodings
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2.ids import ProjectId, VariantId


def _create_project(root, source, format_id, *, role, options=()):
    context = RequestContext("storage-test", run_id="source-storage")
    prepared = TranslationIoProjectSourcePreparer().prepare_source(
        ProjectSourceRequest(str(source), format_hint=format_id, options=options), context, role=role, common_options=()
    )
    extracted = extract_source(prepared)
    variant = VariantId("main")
    project = ProjectId("extracted-source")
    path = project_path(root, project)
    ProjectStore(path).create(project, "Extracted source", variant, "Main", (extracted,))
    return path, variant


def _publish(snapshot, entries, target):
    context = RequestContext("storage-test", run_id="export-reopened")
    preflight = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(snapshot, snapshot.format_id, entries, str(target), 1, context)
    )
    assert preflight.ready, preflight.checks
    result = HydratedWriteWorkload(preflight)(
        SimpleNamespace(
            ref=SimpleNamespace(run_id=context.run_id),
            cancellation=None,
            publish_commit_guard=lambda: ImmediateCommitGuard(context.run_id),
        )
    )
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    return result


def test_project_retains_plugin_path_after_source_is_removed_without_saving_plugin_binary(tmp_path):
    inputs = tmp_path / "private-plugin-location-72"
    inputs.mkdir()
    source = write_plugin(inputs / "Characters.esp", [(0x800, "Greeter", "Welcome!")])
    marker = b"author-private-plugin-payload-528"
    author = struct.pack("<4sH", b"CNAM", len(marker) + 1) + marker + b"\0"
    plugin = source.read_bytes()
    plugin = plugin[:4] + struct.pack("<I", len(author)) + plugin[8:24] + author + plugin[24:]
    source.write_bytes(plugin)
    root = tmp_path / "data"
    path, variant = _create_project(root, source, FormatId.PLUGIN_SSE, role="primary")
    source.unlink()

    reopened = ProjectStore(path)
    (info,) = reopened.sources()
    (entry,) = reopened.read_page(variant)
    assert info.name == "Characters.esp"
    assert info.reference_path == str(source)
    assert entry.snapshot.original == "Welcome!"
    assert entry.snapshot.context == "NPC_:FULL"
    assert entry.snapshot.entry_key.namespace == info.namespace
    detached = reconstructed_source_snapshot(info, (entry,))
    assert detached.content is None
    assert inputs.name not in detached.source.uri

    working = ProjectWorkingCopy(reopened, variant)
    working.edit(entry.snapshot.entry_key, translation="欢迎！")
    working.save()
    assert ProjectStore(path).read_page(variant)[0].state.translation == "欢迎！"
    for file in root.rglob("*"):
        if file.is_file():
            content = file.read_bytes()
            assert plugin not in content
            assert b64encode(plugin) not in content
            assert marker not in content


@pytest.mark.parametrize("format_id", [FormatId.BINARY_EET, FormatId.SST_SSU8, FormatId.SST_SSU9])
def test_native_duplicate_records_export_independently_after_source_is_deleted(tmp_path, format_id):
    if format_id is FormatId.BINARY_EET:
        source, document, _ = parse_eet(tmp_path)
    else:
        native = sst_record()
        source, document, _ = parse_sst(tmp_path, format_id, records=(native, native))
    original_bytes = source.read_bytes()
    path, variant = _create_project(tmp_path / "data", source, format_id, role="migration")
    source.unlink()

    store = ProjectStore(path)
    original = store.read_page(variant)
    assert len(original) == 2 and original[0].snapshot.entry_key != original[1].snapshot.entry_key
    working = ProjectWorkingCopy(store, variant)
    translation = "Edited 42 😀"
    working.edit(original[1].snapshot.entry_key, translation=translation, stage=Stage.CHECKED)
    working.save()

    reopened = ProjectStore(path)
    (info,) = reopened.sources()
    entries = reopened.read_page(variant)
    assert entries[0] == original[0]
    assert entries[1].state.stage is Stage.CHECKED
    assert entries[1].state.translation == translation
    reconstructed = reconstructed_source_snapshot(info, entries)
    assert reconstructed.content == original_bytes
    target = tmp_path / ("output" + source.suffix)
    _publish(reconstructed, (entries[1].snapshot,), target)

    if format_id is FormatId.BINARY_EET:
        expected = document.with_translation(1, translation)
    else:
        expected = replace(
            document, records=(document.records[0], replace(document.records[1], translation=translation))
        )
    assert target.read_bytes() == expected.to_bytes()


def test_plugin_write_rejects_same_name_input_with_different_content(tmp_path):
    original_dir, selected_dir, wrong_dir = (tmp_path / name for name in ("prior", "selected", "wrong"))
    for directory in (original_dir, selected_dir, wrong_dir):
        directory.mkdir()
    original = write_plugin(original_dir / "Voice.esp", [(0x800, "Greeter", "Good morning")])
    wrong = write_plugin(wrong_dir / original.name, [(0x800, "Greeter", "A different source")])
    path, variant = _create_project(tmp_path / "data", original, FormatId.PLUGIN_SSE, role="primary")
    selected = original.rename(selected_dir / original.name)
    store = ProjectStore(path)
    (info,) = store.sources()
    (row,) = store.read_page(variant)
    detached = reconstructed_source_snapshot(info, (row,))
    assert detached.content is None
    edited = replace(row.snapshot, translation="Translated greeting", stage=Stage.TRANSLATED.value)
    context = RequestContext("storage-test", run_id="bind-plugin")
    preparer = TranslationIoProjectSourcePreparer()
    original_db = path.read_bytes()
    wrong_bytes = wrong.read_bytes()
    selected_bytes = selected.read_bytes()
    target = tmp_path / "output.esp"

    with pytest.raises(DomainError) as rejected:
        bind_plugin_write_input(detached, (edited,), str(wrong), context, preparer)
    assert rejected.value.code == "PROJECT_SOURCE_FINGERPRINT_MISMATCH"
    assert not target.exists()
    assert path.read_bytes() == original_db

    operation_source = bind_plugin_write_input(detached, (edited,), str(selected), context, preparer)
    _publish(operation_source, (edited,), target)
    written = TranslationIoUseCase().parse(ParseRequest(SourceDescriptor(str(target)), context, FormatId.PLUGIN_SSE))
    assert written.outcome is OperationOutcome.COMPLETED, written.diagnostics
    assert written.entries[0].original == edited.translation
    assert path.read_bytes() == original_db
    assert wrong.read_bytes() == wrong_bytes
    assert selected.read_bytes() == selected_bytes
    assert not original.exists()


@pytest.mark.parametrize("legacy", [True, False])
def test_plugin_write_reproduces_saved_read_facts_after_project_defaults_change(tmp_path, legacy):
    context = RequestContext("storage-test", run_id="bind-historical-plugin")
    # Byte 0x8C alone decodes as Ś in cp1250 and Œ in cp1252.
    if legacy:
        source = write_plugin(tmp_path / "Legacy.esp", [(0x800, "Legacy", "Ś")], encoding="cp1250")
        options = (("text_read_policy", LEGACY_READ_POLICY),)
        expected_original = "Ś"
    else:
        source = write_plugin(tmp_path / "Strict.esp", [(0x800, "Strict", "é")])
        options = (("input_encoding", "cp1252"), ("text_read_policy", STRICT_READ_POLICY))
        expected_original = "Ã©"
    path, variant = _create_project(tmp_path / "data", source, FormatId.PLUGIN_SSE, role="primary", options=options)
    with project_connection(Path(path), write=True) as connection:
        connection.execute(
            """UPDATE source SET
                format_fields = json_remove(format_fields, '$.source_language', '$.text_read_policy'),
                options = json_remove(options, '$.source_language', '$.text_read_policy')"""
        )
        connection.execute(
            """UPDATE project SET settings = json_set(
                settings, '$.text_input_encoding', 'utf-8', '$.text_output_encoding', 'utf-8')"""
        )
    store = ProjectStore(path)
    (info,) = store.sources()
    (row,) = store.read_page(variant)
    assert row.snapshot.original == expected_original
    detached = reconstructed_source_snapshot(info, (row,))
    edited = replace(row.snapshot, translation="Verified", stage=Stage.TRANSLATED.value)
    before = path.read_bytes()

    bound = bind_plugin_write_input(
        detached,
        (edited,),
        str(source),
        context,
        TranslationIoProjectSourcePreparer(),
        input_encoding=TextEncodings("cp1251").input_encoding,
    )

    assert dict(bound.metadata)["text_read_policy"] == (LEGACY_READ_POLICY if legacy else STRICT_READ_POLICY)
    assert path.read_bytes() == before


def test_localized_plugin_requires_current_strings_and_writes_companions(tmp_path, localized):  # noqa: F811
    path, variant = _create_project(tmp_path / "data", localized.source, FormatId.PLUGIN_SSE, role="primary")
    selected_dir = tmp_path / "new-operation-input"
    selected_dir.mkdir()
    selected = localized.source.rename(selected_dir / localized.source.name)
    original_strings = localized.source.parent / "Strings"
    store = ProjectStore(path)
    (row,) = store.read_page(variant)
    assert row.snapshot.string_id == 1
    detached = reconstructed_source_snapshot(store.sources()[0], (row,))
    edited = replace(row.snapshot, translation="New localized wording", stage=Stage.TRANSLATED.value)
    context = RequestContext("storage-test", run_id="bind-localized")
    preparer = TranslationIoProjectSourcePreparer()
    before = path.read_bytes()

    with pytest.raises(DomainError) as rejected:
        bind_plugin_write_input(detached, (edited,), str(selected), context, preparer)
    assert rejected.value.code == "WRITE_STRINGS_INPUT_REQUIRED"
    assert not localized.target.exists()
    assert path.read_bytes() == before

    original_strings.rename(selected_dir / "Strings")
    operation_source = bind_plugin_write_input(detached, (edited,), str(selected), context, preparer)
    written = _publish(operation_source, (edited,), localized.target)
    assert len(written.artifact_refs) == 4
    parsed = TranslationIoUseCase().parse(
        ParseRequest(SourceDescriptor(str(localized.target)), context, FormatId.PLUGIN_SSE)
    )
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    assert parsed.entries[0].original == edited.translation
    assert path.read_bytes() == before
