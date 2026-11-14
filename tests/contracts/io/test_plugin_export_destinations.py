from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.contracts.io.test_localized_hydrated_publication import _run, localized as localized_fixture
from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWritePreflightService
from transbridge.application.io.operation_write_batch import HydratedWriteBatchWorkload
from transbridge.application.io.plugin_destination import PLUGIN_DESTINATION_MODE
from transbridge.application.io.publish import ConflictPolicy, ImmediateCommitGuard, OsPublishFilesystem
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.writer.plugin_writer import PluginWriter

localized = localized_fixture


def _parse(path):
    return TranslationIoUseCase().parse(
        ParseRequest(SourceDescriptor(str(path)), RequestContext("test"), FormatId.PLUGIN_SSE)
    )


def _draft(parsed, target, mode="directory", text="Translated", *, overwrite=False, packed=False):
    return HydratedWriteDraft(
        parsed.source_snapshot,
        FormatId.PLUGIN_SSE,
        tuple(replace(entry, translation=text, stage=1).snapshot() for entry in parsed.entries),
        str(target),
        1,
        RequestContext("test"),
        conflict_policy=ConflictPolicy.EXPLICIT_OVERWRITE if overwrite else ConflictPolicy.FAIL,
        options=((PLUGIN_DESTINATION_MODE, mode), ("pack_bsa", packed)),
    )


@pytest.fixture
def inline(tmp_path):
    source = write_plugin(tmp_path / "origin.esp", [(0x800, "Person", "Original")])
    parsed = _parse(source)
    assert parsed.outcome is OperationOutcome.COMPLETED
    return source, parsed


def test_directory_exports_each_scheme_from_the_same_copied_bytes(inline, tmp_path, monkeypatch):
    source, parsed = inline
    original = source.read_bytes()
    targets = tuple(tmp_path / text / source.name for text in ("First", "Second"))
    preflights = HydratedWritePreflightService()
    checked = tuple(preflights.preflight(_draft(parsed, target, text=target.parent.name)) for target in targets)
    save = SSEPluginWithContext.save
    copied = []

    def save_copied_plugin(plugin, output):
        target = output.parent / source.name
        assert target in targets
        assert output != target and not target.exists()
        assert output.read_bytes() == original
        copied.append(output)
        return save(plugin, output)

    monkeypatch.setattr(SSEPluginWithContext, "save", save_copied_plugin)
    # Changes to the live source after confirmation cannot change either output.
    source.write_bytes(b"external edit after confirmation")
    run = SimpleNamespace(
        ref=SimpleNamespace(run_id="schemes"),
        cancellation=None,
        publish_commit_guard=lambda: ImmediateCommitGuard("schemes"),
    )
    result = HydratedWriteBatchWorkload(checked)(run)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert [_parse(target).entries[0].original for target in targets] == ["First", "Second"]
    assert source.read_bytes() == b"external edit after confirmation"
    assert len(copied) == 2
    assert all(not output.exists() for output in copied)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["copy", "translate"])
def test_failed_directory_export_removes_the_copied_plugin_stage(inline, tmp_path, monkeypatch, existing, failure):
    source, parsed = inline
    target = tmp_path / "export" / source.name
    target.parent.mkdir()
    if existing:
        target.write_bytes(b"existing output")
    checked = HydratedWritePreflightService().preflight(_draft(parsed, target, overwrite=existing))
    copied = []
    write = Path.write_bytes

    def record_private_copy(path, content):
        if path.parent == target.parent and path != target:
            copied.append(path)
            if failure == "copy":
                write(path, content[:1])
                raise OSError("injected failure while copying source bytes")
        return write(path, content)

    def fail_after_copy(*_args):
        assert len(copied) == 1
        assert copied[0].read_bytes() == source.read_bytes()
        raise OSError("injected failure while applying translations")

    monkeypatch.setattr(Path, "write_bytes", record_private_copy)
    monkeypatch.setattr(PluginWriter, "apply_collection", fail_after_copy)
    result = _run(checked)

    assert result.outcome is OperationOutcome.FAILED
    assert "injected failure" in result.diagnostics[0].message
    assert len(copied) == 1
    assert not copied[0].exists()
    assert tuple(target.parent.iterdir()) == ((target,) if existing else ())
    if existing:
        assert target.read_bytes() == b"existing output"


@pytest.mark.parametrize(
    ("mode", "target", "code"),
    [
        ("directory", "origin.esp", "PLUGIN_SOURCE_PROTECTED"),
        ("directory", "elsewhere/renamed.esp", "PLUGIN_FILENAME_CHANGED"),
        ("original", "elsewhere/origin.esp", "PLUGIN_ORIGINAL_TARGET_MISMATCH"),
    ],
)
def test_plugin_destination_checks_apply_even_with_overwrite(inline, tmp_path, mode, target, code):
    _source, parsed = inline
    checked = HydratedWritePreflightService().preflight(_draft(parsed, tmp_path / target, mode, overwrite=True))
    assert not checked.ready
    assert code in {item.code for item in checked.checks if not item.passed}


@pytest.mark.parametrize("change", ["none", "before", "after"])
def test_original_mode_only_overwrites_the_confirmed_plugin(inline, change):
    source, parsed = inline
    original = source.read_bytes()
    draft = _draft(parsed, source, "original", overwrite=True)
    if change == "before":
        source.write_bytes(b"external edit")
    checked = HydratedWritePreflightService().preflight(draft)
    if change == "before":
        assert not checked.ready
        assert "SOURCE_FINGERPRINT_CONFLICT" in {item.code for item in checked.checks if not item.passed}
        assert source.read_bytes() == b"external edit"
        return

    assert checked.ready
    if change == "after":
        source.write_bytes(b"external edit")
    result = _run(checked)
    if change == "after":
        assert result.outcome is OperationOutcome.FAILED
        assert result.diagnostics[0].code == "TARGET_FINGERPRINT_CONFLICT"
        assert source.read_bytes() == b"external edit"
    else:
        assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
        assert _parse(source).entries[0].original == "Translated"
        assert Path(result.value["backup_path"]).read_bytes() == original


@pytest.mark.parametrize("packed", [False, True])
def test_directory_keeps_localized_plugin_bytes_and_publishes_translated_resources(localized, packed, monkeypatch):
    target = localized.output / localized.source.name
    before = {path: path.read_bytes() for path in localized.source.parent.rglob("*") if path.is_file()}
    checked = HydratedWritePreflightService().preflight(_draft(localized.parsed, target, packed=packed))

    def unexpected_serialization(*_args):
        pytest.fail("Writing only localized Strings should preserve the copied plugin bytes")

    monkeypatch.setattr(SSEPluginWithContext, "save", unexpected_serialization)
    result = _run(checked)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert target.read_bytes() == before[localized.source]
    assert _parse(target).entries[0].original == "Translated"
    assert len(result.artifact_refs) == (2 if packed else 4)
    assert all(path.read_bytes() == content for path, content in before.items())


def test_original_mode_rejects_changed_captured_companion(localized):
    path = localized.source.parent / "Strings" / "localized_English.strings"
    path.write_bytes(b"external Strings edit")
    checked = HydratedWritePreflightService().preflight(
        _draft(localized.parsed, localized.source, "original", overwrite=True)
    )

    assert not checked.ready
    assert "SOURCE_FINGERPRINT_CONFLICT" in {item.code for item in checked.checks if not item.passed}
    assert path.read_bytes() == b"external Strings edit"


@pytest.mark.parametrize("packed", [False, True])
def test_directory_plugin_install_failure_rolls_back_localized_resources(localized, packed):
    target = localized.output / localized.source.name
    checked = HydratedWritePreflightService().preflight(_draft(localized.parsed, target, packed=packed))

    class FailingFilesystem(OsPublishFilesystem):
        def atomic_replace(self, source, destination):
            if Path(destination) == target:
                raise OSError("injected plugin install failure")
            super().atomic_replace(source, destination)

    result = _run(checked, FailingFilesystem())

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "BUNDLE_COMMIT_FAILED"
    assert not tuple(localized.output.iterdir())
    assert _parse(localized.source).entries[0].original == "Original 1"
