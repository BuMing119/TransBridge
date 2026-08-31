from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import (
    EetXmlAdapter,
    FormatId,
    ParatranzJsonAdapter,
    ParseRequest,
    SourceDescriptor,
    WriteRequest,
    XtXmlAdapter,
)
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.io.publish import (
    FormatAdapterRenderer,
    FormatRoundTripValidator,
    ImmediateCommitGuard,
    OsPublishFilesystem,
    PublishCoordinator,
    PublishTarget,
)
from transbridge.converter.translation_entry import TranslationEntry

FIXTURES = Path("tests/contracts/io/fixtures")
XML_FORMATS = [
    (EetXmlAdapter(), FormatId.XML_EET, "eet-small.xml"),
    (XtXmlAdapter(), FormatId.XML_XT, "xt-small.xml"),
]


def _request(adapter, format_id, source: Path, target: Path) -> WriteRequest:
    context = RequestContext("fidelity-test", run_id="publish-fidelity")
    parsed = adapter.parse(
        ParseRequest(
            SourceDescriptor(str(source), source.name),
            context,
            format_id,
            SourceNamespace("test:round-trip"),
        )
    )
    assert parsed.outcome is OperationOutcome.COMPLETED
    return WriteRequest(
        SourceDescriptor(str(target), target.name),
        format_id,
        parsed.entries,
        0,
        context,
        source_snapshot=parsed.source_snapshot,
    )


def _publish(adapter, request: WriteRequest, *, renderer=None):
    filesystem = OsPublishFilesystem()
    return PublishCoordinator(filesystem).publish(
        request,
        PublishTarget(request.target.uri),
        renderer=renderer or FormatAdapterRenderer(adapter),
        validator=FormatRoundTripValidator(adapter, filesystem),
        commit_guard=ImmediateCommitGuard(request.context.run_id),
    )


@pytest.mark.parametrize(("adapter", "format_id", "fixture"), XML_FORMATS)
def test_xml_publication_preserves_untranslated_entries_in_a_partial_translation(
    tmp_path: Path, adapter, format_id, fixture
):
    source = tmp_path / fixture
    tree = ET.parse(FIXTURES / fixture)
    first = tree.find(".//ESP" if format_id is FormatId.XML_EET else ".//Content/String")
    second = ET.fromstring(ET.tostring(first))
    second.find("EDID").text = "SecondGreeting"
    parent = tree.getroot() if format_id is FormatId.XML_EET else tree.find("Content")
    parent.append(second)
    tree.write(source, encoding="utf-8")
    target = tmp_path / "published.xml"
    request = _request(adapter, format_id, source, target)
    request = replace(
        request,
        entries=(request.entries[0], replace(request.entries[1], translation="Translated", stage=1)),
    )

    result = _publish(adapter, request)

    assert result.outcome is OperationOutcome.COMPLETED, result.message
    reparsed = adapter.parse(ParseRequest(request.target, request.context, format_id))
    assert [entry.translation for entry in reparsed.entries] == ["", "Translated"]
    assert [entry.stage for entry in reparsed.entries] == [0, 1]


@pytest.mark.parametrize(("adapter", "format_id", "fixture"), XML_FORMATS)
@pytest.mark.parametrize("stage", [0, -1])
def test_xml_publication_preserves_draft_translation_fields(tmp_path: Path, adapter, format_id, fixture, stage):
    source = tmp_path / fixture
    shutil.copyfile(FIXTURES / fixture, source)
    request = _request(adapter, format_id, source, tmp_path / "published.xml")
    request = replace(request, entries=(replace(request.entries[0], translation="Draft translation", stage=stage),))

    result = _publish(adapter, request)

    assert result.outcome is OperationOutcome.COMPLETED, result.message
    reparsed = adapter.parse(ParseRequest(request.target, request.context, format_id))
    assert reparsed.entries[0].translation == "Draft translation"


@pytest.mark.parametrize("canonical_input", [False, True])
@pytest.mark.parametrize(("translation", "stage"), [("", 0), ("Translated", 1)])
def test_paratranz_publication_uses_canonical_entry_keys(tmp_path: Path, canonical_input, translation, stage):
    source = tmp_path / "source.json"
    source.write_text('[{"key":"greeting","original":"Hello","translation":"","stage":0}]', encoding="utf-8")
    adapter = ParatranzJsonAdapter()
    request = _request(adapter, FormatId.JSON_PARATRANZ, source, tmp_path / "published.json")
    entry = replace(request.entries[0], translation=translation, stage=stage)
    if canonical_input:
        entry = TranslationEntry(
            entry.key,
            entry.key,
            entry.original,
            entry.translation,
            entry.stage,
            entry.context,
            entry_key=entry.entry_key,
        )
    request = replace(request, entries=(entry,))

    result = _publish(adapter, request)

    assert result.outcome is OperationOutcome.COMPLETED, result.message
    assert result.validation.fidelity_valid
    reparsed = adapter.parse(
        ParseRequest(request.target, request.context, request.format_id, entry.entry_key.namespace)
    )
    assert reparsed.entries[0].entry_key == entry.entry_key
    assert reparsed.entries[0].translation == translation
    assert reparsed.entries[0].stage == stage


@pytest.mark.parametrize(("adapter", "format_id", "fixture"), XML_FORMATS)
def test_round_trip_still_rejects_changed_translation_content(tmp_path: Path, adapter, format_id, fixture):
    source = tmp_path / fixture
    shutil.copyfile(FIXTURES / fixture, source)
    target = tmp_path / "published.xml"
    request = _request(adapter, format_id, source, target)
    request = replace(request, entries=(replace(request.entries[0], translation="Expected", stage=1),))

    class CorruptingRenderer(FormatAdapterRenderer):
        def render(self, request, staging_path):
            corrupted = replace(request, entries=(replace(request.entries[0], translation="Corrupted"),))
            return super().render(corrupted, staging_path)

    result = _publish(adapter, request, renderer=CorruptingRenderer(adapter))

    assert result.outcome is OperationOutcome.FAILED
    assert result.code == "FIDELITY_MISMATCH"
    assert not target.exists()
