"""Optional real BSA output must share the loose bundle's validation and rollback."""

from dataclasses import replace
from pathlib import Path

import pytest
from sse_bsa import BSAArchive

from tests.bsa_fixtures import write_bsa
from tests.contracts.io.test_localized_hydrated_publication import (
    _preflight,
    _run,
    localized as localized_fixture,
)
from tests.integration.publish.test_bsa_localized_write import (
    _preflight as bsa_preflight,
    bsa_source as bsa_source_fixture,
)
from tests.integration.publish.test_bsa_shared_variant_ids import (
    _TRANSLATIONS,
    _output_originals,
    _request,
    shared_source as shared_source_fixture,
)
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter, plugin_write
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWritePreflightService
from transbridge.application.io.plugin_bsa_output import bsa_output_path
from transbridge.application.io.publish import ConflictPolicy, OsPublishFilesystem

localized = localized_fixture
bsa_source = bsa_source_fixture
shared_source = shared_source_fixture


def _packed(case, *, overwrite=False):
    initial = _preflight(case)
    return HydratedWritePreflightService().preflight(
        replace(
            initial.draft,
            options=(("pack_bsa", True),),
            conflict_policy=ConflictPolicy.EXPLICIT_OVERWRITE if overwrite else ConflictPolicy.FAIL,
        )
    )


def _parsed_texts(target):
    parsed = SsePluginAdapter().parse(
        ParseRequest(SourceDescriptor(str(target)), RequestContext("verify-bsa-output"), FormatId.PLUGIN_SSE)
    )
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    return {entry.string_id: entry.original for entry in parsed.entries}


@pytest.mark.parametrize("packed", [None, False, True])
def test_localized_output_is_loose_by_default_and_bsa_only_when_explicitly_enabled(localized, packed):
    checked = _preflight(localized)
    if packed is not None:
        checked = HydratedWritePreflightService().preflight(replace(checked.draft, options=(("pack_bsa", packed),)))
    assert checked.ready, checked.checks
    originals = {path: path.read_bytes() for path in localized.source.parent.rglob("*") if path.is_file()}

    result = _run(checked)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert _parsed_texts(localized.target) == {1: "Translated"}
    assert all(path.read_bytes() == content for path, content in originals.items())
    if packed:
        archive_path = localized.target.with_suffix(".bsa")
        assert set(result.artifact_refs) == {str(localized.target), str(archive_path)}
        assert {path.name for path in localized.output.iterdir()} == {"translated.esp", "translated.bsa"}
        assert {str(path).replace("\\", "/").lower() for path in BSAArchive(archive_path).files} == {
            "strings/translated_english.strings",
            "strings/translated_english.dlstrings",
            "strings/translated_english.ilstrings",
        }
    else:
        assert not localized.target.with_suffix(".bsa").exists()
        assert (localized.output / "Strings" / "translated_English.strings").is_file()


def test_bsa_source_can_produce_new_packed_output_without_modifying_original_archive(bsa_source):
    checked = HydratedWritePreflightService().preflight(
        replace(bsa_preflight(bsa_source).draft, options=(("pack_bsa", True),))
    )
    assert checked.ready, checked.checks

    result = _run(checked)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert _parsed_texts(bsa_source.target) == {
        1: "Translated 1",
        2: "Translated 2",
        3: "Translated 3",
        4: "Unselected name",
    }
    assert bsa_source.archive.read_bytes() == bsa_source.original_archive
    assert bsa_source.source.read_bytes() == bsa_source.original_plugin
    assert not (bsa_source.output / "Strings").exists()


@pytest.mark.parametrize("plugin_name", ["Skyrim.esm", "_ResourcePack.esl"])
def test_packed_output_uses_archive_names_supported_by_plugin_resource_discovery(localized, plugin_name):
    case = type(localized)(**{**vars(localized), "target": localized.output / plugin_name})
    checked = _packed(case)
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert bsa_output_path(case.target).is_file()
    assert _parsed_texts(case.target) == {1: "Translated"}


def test_packed_output_does_not_replace_an_input_archive_even_with_overwrite_consent(bsa_source):
    draft = replace(
        bsa_preflight(bsa_source).draft,
        target_path=str(bsa_source.source),
        options=(("pack_bsa", True),),
        conflict_policy=ConflictPolicy.EXPLICIT_OVERWRITE,
    )
    checked = HydratedWritePreflightService().preflight(draft)
    assert not checked.ready
    assert any(item.code == "SOURCE_ARCHIVE_PROTECTED" and not item.passed for item in checked.checks)
    assert bsa_source.archive.read_bytes() == bsa_source.original_archive


@pytest.mark.parametrize("late", [False, True])
def test_existing_or_late_loose_files_cannot_silently_override_packed_translations(localized, late):
    checked = _packed(localized)
    strings = localized.output / "Strings"
    strings.mkdir()
    loose = strings / "translated_English.strings"
    loose.write_bytes(b"existing loose translation")
    if not late:
        checked = HydratedWritePreflightService().preflight(checked.draft)
        assert any(item.code == "BSA_LOOSE_OUTPUT_CONFLICT" and not item.passed for item in checked.checks)
        with pytest.raises(ValueError, match="blocked preflight"):
            _run(checked)
    else:
        result = _run(checked)
        assert result.outcome is OperationOutcome.FAILED
        assert any(item.code == "BSA_LOOSE_OUTPUT_CONFLICT" for item in result.diagnostics)
    assert loose.read_bytes() == b"existing loose translation"
    assert not localized.target.exists()
    assert not localized.target.with_suffix(".bsa").exists()


def test_existing_output_bsa_requires_overwrite_and_remains_unchanged_without_it(localized):
    archive = localized.target.with_suffix(".bsa")
    archive.write_bytes(b"old output")
    checked = _packed(localized)
    assert not checked.ready
    with pytest.raises(ValueError, match="blocked preflight"):
        _run(checked)
    assert archive.read_bytes() == b"old output"
    assert not localized.target.exists()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("after_replace", [False, True])
def test_bsa_and_plugin_rollback_together_when_the_final_plugin_replace_fails(localized, existing, after_replace):
    archive = localized.target.with_suffix(".bsa")
    originals = {}
    if existing:
        for path in (localized.target, archive):
            path.write_bytes(f"old {path.name}".encode())
            originals[path] = path.read_bytes()
    checked = _packed(localized, overwrite=existing)
    assert checked.ready, checked.checks

    class FailPluginReplace(OsPublishFilesystem):
        failed = False

        def atomic_replace(self, source, destination):
            if Path(destination) == localized.target and not self.failed:
                self.failed = True
                if after_replace:
                    super().atomic_replace(source, destination)
                raise OSError("injected plugin replace failure")
            return super().atomic_replace(source, destination)

    result = _run(checked, FailPluginReplace())
    assert result.outcome is OperationOutcome.FAILED
    assert {path for path in localized.output.rglob("*") if path.is_file()} == set(originals)
    assert all(path.read_bytes() == content for path, content in originals.items())


def test_successful_bsa_overwrite_creates_verified_backups_for_both_outputs(localized):
    archive = localized.target.with_suffix(".bsa")
    originals = {localized.target: b"old plugin", archive: b"old archive"}
    for path, content in originals.items():
        path.write_bytes(content)
    result = _run(_packed(localized, overwrite=True))
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert {Path(path).read_bytes() for path in result.value["backup_paths"]} == set(originals.values())
    assert _parsed_texts(localized.target) == {1: "Translated"}


def test_packed_output_preserves_independent_translations_for_ids_shared_across_tables(shared_source):
    request = _request(shared_source, _TRANSLATIONS)
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(
            request.source_snapshot,
            request.format_id,
            tuple(entry.snapshot() for entry in request.entries),
            request.target.uri,
            1,
            request.context,
            options=(("pack_bsa", True),),
        )
    )
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert _output_originals(shared_source) == _TRANSLATIONS
    assert shared_source.archive.read_bytes() == shared_source.original_archive


@pytest.mark.parametrize("corruption", ["missing", "changed", "extra"])
def test_packed_members_are_validated_before_any_final_output_is_replaced(localized, monkeypatch, corruption):
    pack = plugin_write.pack_localized_strings

    def corrupt_archive(target, rendered, companions):
        path = Path(pack(target, rendered, companions))
        archive = BSAArchive(path)
        members = {str(name).replace("\\", "/"): archive.get_file_stream(name).read() for name in archive.files}
        first = next(name for name in members if name.endswith(".strings"))
        if corruption == "missing":
            members.pop(first)
        elif corruption == "extra":
            members["strings/unexpected.strings"] = members[first]
        else:
            members[first] = members[first].replace(b"Translated", b"WRONG-TEXT")
        write_bsa(path, members)
        return str(path)

    monkeypatch.setattr(plugin_write, "pack_localized_strings", corrupt_archive)
    result = _run(_packed(localized))
    assert result.outcome is OperationOutcome.FAILED
    assert not localized.target.exists()
    assert not localized.target.with_suffix(".bsa").exists()
    assert not tuple(localized.output.iterdir())
