from __future__ import annotations

from dataclasses import dataclass

import pytest

from transbridge.application.contracts import OperationResult
from transbridge.application.io import (
    CapabilityLevel,
    FormatCapability,
    FormatCatalog,
    FormatId,
    FormatProbe,
    ParseRequest,
    ParseResult,
    ProbeRequest,
    ProbeStatus,
    SourceDescriptor,
    WriteRequest,
    default_format_catalog,
)


def _probe(name: str, content: bytes, hint: FormatId | None = None) -> FormatProbe:
    source = SourceDescriptor(f"memory:///{name}", name, len(content))
    return default_format_catalog().resolve(ProbeRequest(source, content, hint))


@pytest.mark.parametrize(
    ("name", "content", "format_id", "evidence_kind"),
    [
        ("plugin.json", b"TES4payload", FormatId.PLUGIN_SSE, "magic"),
        ("sample.eet", b"EET_\x01\x00\x00\x00", FormatId.BINARY_EET, "magic"),
        ("sample.sst", b"SSU8payload", FormatId.SST_SSU8, "magic"),
        ("sample.bin", b"SSU9payload", FormatId.SST_SSU9, "magic"),
        ("sample.json", b"<DocumentElement />", FormatId.XML_EET, "root_element"),
        ("sample.xml", b"<SSTXMLRessources />", FormatId.XML_XT, "root_element"),
        (
            "sample.json",
            b'[{"id":12,"key":"A","original":"a","translation":"b","stage":1}]',
            FormatId.JSON_PARATRANZ,
            "schema",
        ),
        ("sample.json", b'[{"form_id":"1","type":"NPC_ FULL","string":"x"}]', FormatId.JSON_DSD, "schema"),
        (
            "sample.json",
            b'{"schema_version":2,"entries":[]}',
            FormatId.JSON_TRANSBRIDGE,
            "schema",
        ),
    ],
)
def test_probe_uses_content_evidence_not_extension(name, content, format_id, evidence_kind) -> None:
    result = _probe(name, content)

    assert result.status is ProbeStatus.EXACT
    assert result.candidates == (format_id,)
    assert any(item.kind.value == evidence_kind and item.format_id is format_id for item in result.evidence)


def test_localized_strings_requires_extension_and_valid_binary_structure() -> None:
    empty_strings = (0).to_bytes(4, "little") + (0).to_bytes(4, "little")

    exact = _probe("Skyrim_English.strings", empty_strings)
    invalid = _probe("Skyrim_English.strings", b"not-a-valid-header")

    assert exact.status is ProbeStatus.EXACT
    assert exact.candidates == (FormatId.STRINGS,)
    assert invalid.status is ProbeStatus.UNSUPPORTED


def test_json_empty_array_is_ambiguous_until_explicitly_selected() -> None:
    ambiguous = _probe("empty.json", b"[]")
    selected = _probe("empty.json", b"[]", FormatId.JSON_PARATRANZ)

    assert ambiguous.status is ProbeStatus.AMBIGUOUS
    assert set(ambiguous.candidates) == {
        FormatId.JSON_DSD,
        FormatId.JSON_PARATRANZ,
        FormatId.JSON_TRANSBRIDGE,
    }
    assert selected.status is ProbeStatus.EXACT
    assert selected.candidates == (FormatId.JSON_PARATRANZ,)


def test_explicit_hint_conflicting_with_magic_is_not_silently_accepted() -> None:
    result = _probe("actually.esp", b"TES4payload", FormatId.XML_XT)

    assert result.status is ProbeStatus.AMBIGUOUS
    assert set(result.candidates) == {FormatId.PLUGIN_SSE, FormatId.XML_XT}
    assert result.diagnostics[0].code == "FORMAT_HINT_CONFLICT"


def test_extension_alone_is_never_exact() -> None:
    result = _probe("looks-like.json", b"not json")

    assert result.status is ProbeStatus.UNSUPPORTED
    assert result.candidates == ()
    assert any(item.kind.value == "extension" for item in result.evidence)


def test_catalog_registers_all_explicit_format_ids() -> None:
    assert set(default_format_catalog().known_formats()) == set(FormatId)


@dataclass
class _OverclaimingSstAdapter:
    format_id: FormatId = FormatId.SST_SSU9
    adapter_id: str = "test.overclaim"
    adapter_version: str = "1"

    def probe(self, request: ProbeRequest) -> FormatProbe:
        return FormatProbe(ProbeStatus.UNSUPPORTED)

    def parse(self, request: ParseRequest) -> ParseResult:
        raise NotImplementedError

    def validate_write(self, request: WriteRequest) -> OperationResult[None]:
        return OperationResult.completed()

    def write(self, request: WriteRequest) -> OperationResult[tuple[str, ...]]:
        return OperationResult.completed(())

    def capabilities(self) -> FormatCapability:
        supported = CapabilityLevel.SUPPORTED
        return FormatCapability(
            read=supported,
            write=supported,
            round_trip=supported,
            localized=supported,
            streaming=supported,
            cancel=supported,
            fidelity=supported,
            gui=supported,
            agent=supported,
            mcp=supported,
            publish=supported,
        )


def test_policy_ceiling_prevents_experimental_or_unavailable_promotion() -> None:
    catalog = FormatCatalog(adapters=(_OverclaimingSstAdapter(),))
    snapshot = next(item for item in catalog.capability_snapshot() if item.format_id is FormatId.SST_SSU9)

    assert snapshot.capability.read is CapabilityLevel.EXPERIMENTAL
    assert snapshot.capability.write is CapabilityLevel.UNAVAILABLE
    assert snapshot.capability.round_trip is CapabilityLevel.UNAVAILABLE
    assert snapshot.capability.publish is CapabilityLevel.UNAVAILABLE


def test_default_catalog_registers_only_implemented_adapters() -> None:
    snapshot = default_format_catalog().capability_snapshot()
    implemented_ids = {
        FormatId.PLUGIN_SSE,
        FormatId.XML_EET,
        FormatId.XML_XT,
        FormatId.JSON_PARATRANZ,
        FormatId.STRINGS,
        FormatId.DLSTRINGS,
        FormatId.ILSTRINGS,
    }
    implemented = tuple(item for item in snapshot if item.format_id in implemented_ids)
    paratranz = next(item for item in implemented if item.format_id is FormatId.JSON_PARATRANZ)
    unregistered = tuple(item for item in snapshot if item.format_id not in implemented_ids)

    assert snapshot
    assert paratranz.adapter_id == "transbridge.io.paratranz-json"
    assert paratranz.capability.read is CapabilityLevel.SUPPORTED
    assert paratranz.capability.write is CapabilityLevel.SUPPORTED
    assert paratranz.capability.publish is CapabilityLevel.UNAVAILABLE
    assert all(item.adapter_id for item in implemented)
    assert all(item.capability.read is CapabilityLevel.SUPPORTED for item in implemented)
    assert all(item.capability.publish is CapabilityLevel.UNAVAILABLE for item in implemented)
    assert all(item.capability.read is CapabilityLevel.UNAVAILABLE for item in unregistered)
    assert all(item.reasons for item in unregistered)
