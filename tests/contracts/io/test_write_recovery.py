from dataclasses import replace
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from tests.contracts.io.test_localized_hydrated_publication import _run, localized as localized_fixture
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWritePreflightService
from transbridge.application.io.publish import OsPublishFilesystem, PublishCoordinator, PublishTarget
from transbridge.application.security.paths import PathAuthorizationPolicy, PathGrant

localized = localized_fixture
FIXTURES = Path("tests/contracts/io/fixtures")


@pytest.mark.parametrize(
    ("fixture", "format_id", "translation_tag"),
    [("eet-small.xml", FormatId.XML_EET, "TRADUIT"), ("xt-small.xml", FormatId.XML_XT, "Dest")],
)
@pytest.mark.parametrize("edited_original", [False, True])
def test_locked_empty_xml_keeps_source_translation_and_status_through_publication(
    tmp_path, fixture, format_id, translation_tag, edited_original
):
    source = tmp_path / fixture
    root = ET.fromstring((FIXTURES / fixture).read_bytes())
    root.find(f".//{translation_tag}").text = "源文件已有译文"
    status = root.find(".//STATUS")
    if status is not None:
        status.text = "99"
    source.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    parsed = TranslationIoUseCase().parse(
        ParseRequest(SourceDescriptor(str(source)), RequestContext("test"), format_id)
    )
    locked = replace(parsed.entries[0], translation="", stage=9)
    if edited_original:
        locked = replace(locked, original="项目中编辑过的原文")
    before = locked.snapshot()
    target = tmp_path / "new" / "nested" / "output.xml"
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(parsed.source_snapshot, format_id, (locked,), str(target), 1, RequestContext("gui"))
    )
    assert checked.ready
    assert not target.parent.exists()
    source.unlink()  # The captured template, not a mutable file, is authoritative.

    result = _run(checked)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    output = ET.parse(target).getroot()
    assert output.findtext(f".//{translation_tag}") == "源文件已有译文"
    assert output.findtext(".//ORIGINAL" if format_id is FormatId.XML_EET else ".//Source") == "Hello, traveler."
    if status is not None:
        assert output.findtext(".//STATUS") == "99"
    assert locked.snapshot() == before


@pytest.mark.parametrize("suffix", ["strings", "dlstrings", "ilstrings"])
def test_locked_empty_strings_preserves_bytes_through_roundtrip_and_nested_output(tmp_path, suffix):
    source = tmp_path / f"source.{suffix}"
    original = (FIXTURES / "strings" / f"integrity.{suffix}").read_bytes()
    source.write_bytes(original)
    format_id = FormatId(f"strings.{suffix}")
    parsed = TranslationIoUseCase().parse(
        ParseRequest(SourceDescriptor(str(source)), RequestContext("test"), format_id)
    )
    entries = tuple(replace(entry, translation="", stage=9) for entry in parsed.entries)
    target = tmp_path / "new" / "nested" / f"output.{suffix}"
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(parsed.source_snapshot, format_id, entries, str(target), 1, RequestContext("gui"))
    )
    assert checked.ready and not target.parent.exists()

    result = _run(checked)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert target.read_bytes() == original
    assert all(entry.stage == 9 and not entry.translation for entry in entries)


def test_localized_bundle_creates_missing_output_directory_and_preserves_locked_source(localized):
    target = localized.output / "new" / "nested" / "translated.esp"
    entries = tuple(replace(entry, translation="", stage=9) for entry in localized.parsed.entries)
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(
            localized.parsed.source_snapshot, FormatId.PLUGIN_SSE, entries, str(target), 1, RequestContext("gui")
        )
    )
    assert checked.ready and not target.parent.exists()

    result = _run(checked)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert target.read_bytes() == localized.source.read_bytes()
    for suffix in ("strings", "dlstrings", "ilstrings"):
        assert (target.parent / "Strings" / f"translated_English.{suffix}").read_bytes() == (
            localized.source.parent / "Strings" / f"localized_English.{suffix}"
        ).read_bytes()


@pytest.mark.parametrize("failure", ["file_ancestor", "permission"])
def test_directory_preparation_failure_keeps_existing_files_and_returns_failure(localized, failure):
    root = localized.output / "new"
    if failure == "file_ancestor":
        root.write_bytes(b"existing file")
    target = root / "nested" / "translated.esp"
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(
            localized.parsed.source_snapshot,
            FormatId.PLUGIN_SSE,
            tuple(localized.parsed.entries),
            str(target),
            1,
            RequestContext("gui"),
        )
    )
    if failure == "file_ancestor":
        assert not checked.ready
        assert root.read_bytes() == b"existing file"
    else:

        class DeniedFilesystem(OsPublishFilesystem):
            def create_directory(self, path):
                raise PermissionError("directory access denied")

        assert checked.ready and not root.exists()
        result = _run(checked, DeniedFilesystem())
        assert result.outcome is OperationOutcome.FAILED
        assert not root.exists()
        assert result.artifact_refs == ()


def test_nested_output_creation_obeys_path_grants_before_creating_directories(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    filesystem = OsPublishFilesystem()
    coordinator = PublishCoordinator(filesystem, path_policy=PathAuthorizationPolicy((PathGrant(allowed, True),)))
    with pytest.raises(PermissionError):
        coordinator.prepare(PublishTarget(str(outside / "new" / "output.xml")), "denied")
    assert not outside.exists()
    target = allowed / "new" / "nested" / "output.xml"
    staged = coordinator.prepare(PublishTarget(str(target)), "allowed")
    assert target.parent.is_dir()
    assert Path(staged.path).is_file()
    filesystem.remove(staged.path)
