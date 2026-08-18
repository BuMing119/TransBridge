"""Explicit format catalog with evidence-based content probing."""

from __future__ import annotations

from collections.abc import Iterable
import json
import math
import xml.etree.ElementTree as ET

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity

from .contracts import (
    CapabilityLevel,
    FormatCapability,
    FormatCapabilitySnapshot,
    FormatId,
    FormatProbe,
    ProbeConfidence,
    ProbeEvidence,
    ProbeEvidenceKind,
    ProbeRequest,
    ProbeStatus,
)
from .ports import FormatAdapter


def _capability(
    *,
    read: CapabilityLevel = CapabilityLevel.SUPPORTED,
    write: CapabilityLevel = CapabilityLevel.SUPPORTED,
    round_trip: CapabilityLevel = CapabilityLevel.SUPPORTED,
    localized: CapabilityLevel = CapabilityLevel.UNAVAILABLE,
    gui: CapabilityLevel = CapabilityLevel.SUPPORTED,
    agent: CapabilityLevel = CapabilityLevel.SUPPORTED,
    mcp: CapabilityLevel = CapabilityLevel.UNAVAILABLE,
    publish: CapabilityLevel = CapabilityLevel.SUPPORTED,
) -> FormatCapability:
    return FormatCapability(
        read=read,
        write=write,
        round_trip=round_trip,
        localized=localized,
        cancel=read,
        fidelity=round_trip,
        gui=gui,
        agent=agent,
        mcp=mcp,
        publish=publish,
    )


_EXPERIMENTAL = CapabilityLevel.EXPERIMENTAL
_UNAVAILABLE = CapabilityLevel.UNAVAILABLE

FORMAT_POLICY_CEILINGS: dict[FormatId, FormatCapability] = {
    FormatId.PLUGIN_SSE: _capability(localized=CapabilityLevel.SUPPORTED),
    FormatId.XML_EET: _capability(),
    FormatId.BINARY_EET: _capability(write=_UNAVAILABLE, round_trip=_UNAVAILABLE, publish=_UNAVAILABLE),
    FormatId.XML_XT: _capability(),
    FormatId.JSON_PARATRANZ: _capability(mcp=CapabilityLevel.SUPPORTED),
    FormatId.JSON_DSD: _capability(
        read=_EXPERIMENTAL,
        write=_EXPERIMENTAL,
        round_trip=_EXPERIMENTAL,
        gui=_EXPERIMENTAL,
        agent=_EXPERIMENTAL,
        publish=_EXPERIMENTAL,
    ),
    FormatId.JSON_TRANSBRIDGE: _capability(),
    FormatId.SST_SSU8: _capability(
        read=_EXPERIMENTAL,
        write=_UNAVAILABLE,
        round_trip=_UNAVAILABLE,
        gui=_EXPERIMENTAL,
        agent=_EXPERIMENTAL,
        publish=_UNAVAILABLE,
    ),
    FormatId.SST_SSU9: _capability(
        read=_EXPERIMENTAL,
        write=_UNAVAILABLE,
        round_trip=_UNAVAILABLE,
        gui=_EXPERIMENTAL,
        agent=_EXPERIMENTAL,
        publish=_UNAVAILABLE,
    ),
    FormatId.STRINGS: _capability(localized=CapabilityLevel.SUPPORTED),
    FormatId.DLSTRINGS: _capability(localized=CapabilityLevel.SUPPORTED),
    FormatId.ILSTRINGS: _capability(localized=CapabilityLevel.SUPPORTED),
}

_EXTENSION_HINTS: dict[str, tuple[FormatId, ...]] = {
    ".esp": (FormatId.PLUGIN_SSE,),
    ".esm": (FormatId.PLUGIN_SSE,),
    ".esl": (FormatId.PLUGIN_SSE,),
    ".eet": (FormatId.BINARY_EET, FormatId.XML_EET),
    ".xml": (FormatId.XML_EET, FormatId.XML_XT),
    ".json": (FormatId.JSON_PARATRANZ, FormatId.JSON_DSD, FormatId.JSON_TRANSBRIDGE),
    ".sst": (FormatId.SST_SSU8, FormatId.SST_SSU9),
    ".strings": (FormatId.STRINGS,),
    ".dlstrings": (FormatId.DLSTRINGS,),
    ".ilstrings": (FormatId.ILSTRINGS,),
}


class FormatCatalog:
    """Known formats, registered implementations, and bounded capabilities."""

    def __init__(
        self,
        policy_ceilings: dict[FormatId, FormatCapability] | None = None,
        adapters: Iterable[FormatAdapter] = (),
    ) -> None:
        self._policy_ceilings = dict(policy_ceilings or FORMAT_POLICY_CEILINGS)
        self._adapters: dict[FormatId, FormatAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: FormatAdapter) -> None:
        if adapter.format_id not in self._policy_ceilings:
            raise ValueError(f"Unknown format id: {adapter.format_id}")
        if adapter.format_id in self._adapters:
            raise ValueError(f"Format adapter already registered: {adapter.format_id}")
        self._adapters[adapter.format_id] = adapter

    def adapter(self, format_id: FormatId) -> FormatAdapter | None:
        return self._adapters.get(format_id)

    def known_formats(self) -> tuple[FormatId, ...]:
        return tuple(sorted(self._policy_ceilings, key=lambda item: item.value))

    def resolve(self, request: ProbeRequest) -> FormatProbe:
        candidates, evidence = _probe_content(request)
        for format_id, adapter in self._adapters.items():
            adapter_probe = adapter.probe(request)
            candidates.update(adapter_probe.candidates)
            evidence.extend(adapter_probe.evidence)
            if adapter_probe.status is ProbeStatus.EXACT:
                candidates.add(format_id)

        hinted_formats = _EXTENSION_HINTS.get(request.source.suffix, ())
        evidence.extend(
            ProbeEvidence(format_id, ProbeEvidenceKind.EXTENSION, request.source.suffix, ProbeConfidence.HINT)
            for format_id in hinted_formats
        )

        if request.format_hint is not None:
            evidence.append(
                ProbeEvidence(
                    request.format_hint,
                    ProbeEvidenceKind.EXPLICIT_HINT,
                    request.format_hint.value,
                    ProbeConfidence.EXACT,
                )
            )
            if not candidates or request.format_hint in candidates:
                return FormatProbe(ProbeStatus.EXACT, (request.format_hint,), tuple(evidence))
            candidates.add(request.format_hint)
            return _ambiguous(candidates, evidence, code="FORMAT_HINT_CONFLICT")

        if len(candidates) == 1:
            return FormatProbe(ProbeStatus.EXACT, tuple(candidates), tuple(evidence))
        if len(candidates) > 1:
            return _ambiguous(candidates, evidence)
        return FormatProbe(
            ProbeStatus.UNSUPPORTED,
            evidence=tuple(evidence),
            diagnostics=(
                Diagnostic(
                    "FORMAT_UNSUPPORTED",
                    "The input content does not match a registered format signature.",
                    DiagnosticSeverity.ERROR,
                ),
            ),
        )

    def capability_snapshot(self) -> tuple[FormatCapabilitySnapshot, ...]:
        snapshots: list[FormatCapabilitySnapshot] = []
        for format_id in self.known_formats():
            ceiling = self._policy_ceilings[format_id]
            adapter = self._adapters.get(format_id)
            if adapter is None:
                snapshots.append(
                    FormatCapabilitySnapshot(
                        format_id,
                        FormatCapability.unavailable(),
                        ceiling,
                        reasons=("No V2 format adapter is registered.",),
                    )
                )
                continue
            snapshots.append(
                FormatCapabilitySnapshot(
                    format_id,
                    adapter.capabilities().bounded_by(ceiling),
                    ceiling,
                    adapter.adapter_id,
                    adapter.adapter_version,
                )
            )
        return tuple(snapshots)

    def capability_matrix(self) -> tuple[dict[str, object], ...]:
        return tuple(snapshot.to_dict() for snapshot in self.capability_snapshot())


def default_format_catalog() -> FormatCatalog:
    from .legacy_adapters import EetXmlAdapter, SsePluginAdapter, XtXmlAdapter
    from .paratranz import ParatranzJsonAdapter
    from .strings_adapter import LocalizedStringsAdapter

    return FormatCatalog(
        adapters=(
            ParatranzJsonAdapter(),
            SsePluginAdapter(),
            EetXmlAdapter(),
            XtXmlAdapter(),
            LocalizedStringsAdapter(FormatId.STRINGS),
            LocalizedStringsAdapter(FormatId.DLSTRINGS),
            LocalizedStringsAdapter(FormatId.ILSTRINGS),
        )
    )


def _ambiguous(
    candidates: set[FormatId],
    evidence: list[ProbeEvidence],
    *,
    code: str = "FORMAT_AMBIGUOUS",
) -> FormatProbe:
    ordered = tuple(sorted(candidates, key=lambda item: item.value))
    return FormatProbe(
        ProbeStatus.AMBIGUOUS,
        ordered,
        tuple(evidence),
        (
            Diagnostic(
                code,
                "The input matches multiple format contracts; an explicit format choice is required.",
                DiagnosticSeverity.ERROR,
                details=(("candidates", tuple(item.value for item in ordered)),),
            ),
        ),
    )


def _probe_content(request: ProbeRequest) -> tuple[set[FormatId], list[ProbeEvidence]]:
    candidates: set[FormatId] = set()
    evidence: list[ProbeEvidence] = []
    data = request.content

    magic_formats = {
        b"TES4": FormatId.PLUGIN_SSE,
        b"EET_": FormatId.BINARY_EET,
        b"SSU8": FormatId.SST_SSU8,
        b"SSU9": FormatId.SST_SSU9,
    }
    magic = magic_formats.get(data[:4])
    if magic is not None:
        candidates.add(magic)
        evidence.append(ProbeEvidence(magic, ProbeEvidenceKind.MAGIC, data[:4].decode("ascii"), ProbeConfidence.EXACT))

    root = _xml_root(data)
    xml_roots = {
        "DocumentElement": FormatId.XML_EET,
        "SSTXMLRessources": FormatId.XML_XT,
    }
    xml_format = xml_roots.get(root)
    if xml_format is not None:
        candidates.add(xml_format)
        evidence.append(ProbeEvidence(xml_format, ProbeEvidenceKind.ROOT_ELEMENT, root, ProbeConfidence.EXACT))

    json_formats = json_format_evidence(data)
    candidates.update(json_formats)
    evidence.extend(
        ProbeEvidence(format_id, ProbeEvidenceKind.SCHEMA, schema, ProbeConfidence.EXACT)
        for format_id, schema in json_formats.items()
    )

    strings_format = _strings_format(request.source.suffix, data)
    if strings_format is not None:
        candidates.add(strings_format)
        evidence.append(
            ProbeEvidence(strings_format, ProbeEvidenceKind.STRUCTURE, "localized-strings-v1", ProbeConfidence.EXACT)
        )
    return candidates, evidence


def _xml_root(data: bytes) -> str | None:
    if not data.lstrip().startswith(b"<"):
        return None
    parser = ET.XMLPullParser(events=("start",))
    try:
        parser.feed(data)
        _, element = next(iter(parser.read_events()))
    except (ET.ParseError, StopIteration):
        return None
    return element.tag.rsplit("}", 1)[-1]


def json_format_evidence(data: bytes) -> dict[FormatId, str]:
    try:
        payload = json.loads(
            data.decode("utf-8-sig"),
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {}

    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        if "schema_version" in payload:
            return {FormatId.JSON_TRANSBRIDGE: "transbridge-envelope"}
        records = payload["entries"]
        if not records or all(isinstance(item, dict) and {"key", "original"}.issubset(item) for item in records):
            return {FormatId.JSON_PARATRANZ: "paratranz-entries-envelope"}
    if not isinstance(payload, list):
        return {}
    if not payload:
        return {
            FormatId.JSON_DSD: "empty-entry-array",
            FormatId.JSON_PARATRANZ: "empty-entry-array",
            FormatId.JSON_TRANSBRIDGE: "empty-entry-array",
        }
    if not all(isinstance(item, dict) for item in payload):
        return {}

    result: dict[FormatId, str] = {}
    if all({"form_id", "type", "string"}.issubset(item) for item in payload):
        result[FormatId.JSON_DSD] = "dsd-entry-array"

    paratranz_shape = all({"key", "original"}.issubset(item) for item in payload)
    internal_only = {"string_id", "full_form_id", "dsd_type", "dsd_index", "dsd_editor_id"}
    has_internal_only = any(internal_only.intersection(item) for item in payload)
    ids = [item.get("id") for item in payload if "id" in item]
    if paratranz_shape and not has_internal_only:
        result[FormatId.JSON_PARATRANZ] = "paratranz-entry-array"
    if paratranz_shape and ids and all(isinstance(value, str) for value in ids):
        result[FormatId.JSON_TRANSBRIDGE] = "legacy-transbridge-entry-array"
    if paratranz_shape and has_internal_only:
        result = {FormatId.JSON_TRANSBRIDGE: "legacy-transbridge-entry-array"}
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number is not permitted: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"Non-finite JSON number is not permitted: {value}")
    return parsed


def _strings_format(suffix: str, data: bytes) -> FormatId | None:
    variants = {
        ".strings": FormatId.STRINGS,
        ".dlstrings": FormatId.DLSTRINGS,
        ".ilstrings": FormatId.ILSTRINGS,
    }
    format_id = variants.get(suffix)
    if format_id is None or len(data) < 8:
        return None
    count = int.from_bytes(data[:4], "little")
    data_size = int.from_bytes(data[4:8], "little")
    directory_end = 8 + count * 8
    if directory_end > len(data) or directory_end + data_size > len(data):
        return None
    return format_id
