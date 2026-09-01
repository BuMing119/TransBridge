"""V2 adapters over the existing SSE plugin, EET XML, and XT XML implementations."""

from __future__ import annotations

import codecs
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from transbridge.application.contracts import (
    Diagnostic,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)
from transbridge.converter.translation_entry import STAGE_TRANSLATED, TranslationEntry
from transbridge.parser.eet_parser import EET_XmlParser
from transbridge.parser.plugin_parser import PluginParser
from transbridge.parser.xt import XT_XmlParser

from .contracts import (
    CapabilityLevel,
    FormatCapability,
    FormatId,
    FormatProbe,
    ParseRequest,
    ParseResult,
    ParseStats,
    ProbeConfidence,
    ProbeEvidence,
    ProbeEvidenceKind,
    ProbeRequest,
    ProbeStatus,
    SourceSnapshot,
    WriteRequest,
)
from .identity import EntryKey, SourceNamespace
from .plugin_write import _blocking_stage_diagnostics, _localized_source_snapshots, write_plugin
from .stage_policy import DEFAULT_STAGE_POLICY


class _LegacyFormatAdapter:
    format_id: FormatId
    adapter_id: str
    adapter_version = "2.0"

    def validate_write(self, request: WriteRequest) -> OperationResult[None]:
        if _cancelled(request.cancellation):
            return OperationResult.cancelled(
                Diagnostic("WRITE_CANCELLED", "The format write was cancelled."),
                run_id=request.context.run_id,
            )
        diagnostic = self._write_precondition(request)
        if diagnostic is not None:
            return _failed_operation(diagnostic, request.context.run_id)
        return OperationResult.completed(
            counts=OperationCounts(succeeded=len(request.entries)),
            run_id=request.context.run_id,
        )

    def capabilities(self) -> FormatCapability:
        supported = CapabilityLevel.SUPPORTED
        unavailable = CapabilityLevel.UNAVAILABLE
        return FormatCapability(
            read=supported,
            write=supported,
            round_trip=supported,
            localized=unavailable,
            streaming=unavailable,
            cancel=supported,
            fidelity=supported,
            gui=supported,
            agent=supported,
            mcp=unavailable,
            publish=unavailable,
        )

    def _write_precondition(self, request: WriteRequest) -> Diagnostic | None:
        if request.format_id is not self.format_id:
            return Diagnostic("FORMAT_MISMATCH", f"{self.adapter_id} cannot write {request.format_id.value}.")
        snapshot = request.source_snapshot
        if snapshot is None or snapshot.format_id is not self.format_id:
            return Diagnostic("SOURCE_SNAPSHOT_REQUIRED", "This format requires its original source snapshot.")
        # A hydrated in-memory snapshot is the immutable source authority.  UI
        # project provisioning deliberately does not retain a mutable parser or
        # plugin, and writing must not parse/reopen the legacy source merely to
        # reconstruct one.  Path-backed snapshots still require lease recheck.
        hydration_authority = dict(request.options).get("source_authority") == "hydration-v2"
        if snapshot.content is not None and hydration_authority:
            if hashlib.sha256(snapshot.content).hexdigest() != snapshot.sha256:
                return Diagnostic("SOURCE_FINGERPRINT_CONFLICT", "The hydrated source snapshot is invalid.")
            return None
        try:
            current = _local_path(snapshot.source.uri).read_bytes()
        except (OSError, ValueError) as exc:
            return Diagnostic(
                "SOURCE_SNAPSHOT_UNAVAILABLE",
                f"The source snapshot cannot be reopened ({type(exc).__name__}).",
            )
        if hashlib.sha256(current).hexdigest() != snapshot.sha256:
            return Diagnostic(
                "SOURCE_FINGERPRINT_CONFLICT",
                "The source changed after parsing; reparse before writing.",
                details=(("expected_sha256", snapshot.sha256), ("actual_sha256", hashlib.sha256(current).hexdigest())),
            )
        return None

    def _cancelled_parse(self, request: ParseRequest) -> ParseResult:
        return ParseResult(
            OperationOutcome.CANCELLED,
            self.format_id,
            request.source,
            diagnostics=(Diagnostic("PARSE_CANCELLED", "The format parse was cancelled."),),
            stats=ParseStats(cancelled=1),
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            capability=self.capabilities(),
        )

    def _failed_parse(self, request: ParseRequest, diagnostic: Diagnostic, failed: int = 1) -> ParseResult:
        return ParseResult(
            OperationOutcome.FAILED,
            self.format_id,
            request.source,
            diagnostics=(diagnostic,),
            stats=ParseStats(failed=failed),
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            capability=self.capabilities(),
        )

    def _parse_result(
        self,
        request: ParseRequest,
        snapshot: SourceSnapshot,
        entries: list[TranslationEntry],
        locator: Callable[[TranslationEntry], str],
    ) -> ParseResult:
        locator_groups: dict[str, list[int]] = defaultdict(list)
        for index, entry in enumerate(entries):
            locator_groups[locator(entry)].append(index)
        conflicted = {index for indices in locator_groups.values() if len(indices) > 1 for index in indices}
        diagnostics = tuple(
            Diagnostic(
                "SOURCE_LOCATOR_CONFLICT",
                "More than one source entry has the same write locator.",
                details=(
                    ("record_index", index),
                    ("locator", source_locator),
                    ("conflicting_indices", tuple(indices)),
                ),
            )
            for source_locator, indices in locator_groups.items()
            if len(indices) > 1
            for index in indices
        )
        valid = [entry for index, entry in enumerate(entries) if index not in conflicted]
        namespace = request.source_namespace or _namespace_from_locators(
            self.format_id,
            [locator(entry) for entry in entries],
        )
        mapped = tuple(
            replace(
                entry,
                entry_key=EntryKey(namespace, entry.key),
                metadata=(*entry.metadata, ("io.format", self.format_id.value), ("io.locator", locator(entry))),
            )
            for entry in valid
        )
        snapshot = replace(snapshot, metadata=(*snapshot.metadata, ("source_namespace", namespace.value)))
        if not diagnostics:
            return ParseResult.completed(
                self.format_id,
                request.source,
                snapshot,
                mapped,
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                capability=self.capabilities(),
            )
        if mapped:
            return ParseResult(
                OperationOutcome.PARTIAL,
                self.format_id,
                request.source,
                snapshot,
                mapped,
                diagnostics,
                ParseStats(parsed=len(mapped), failed=len(conflicted)),
                self.adapter_id,
                self.adapter_version,
                self.capabilities(),
            )
        return ParseResult(
            OperationOutcome.FAILED,
            self.format_id,
            request.source,
            diagnostics=diagnostics,
            stats=ParseStats(failed=max(len(conflicted), 1)),
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            capability=self.capabilities(),
        )


class EetXmlAdapter(_LegacyFormatAdapter):
    format_id = FormatId.XML_EET
    adapter_id = "transbridge.io.eet-xml"

    def probe(self, request: ProbeRequest) -> FormatProbe:
        return _xml_probe(request, self.format_id, "DocumentElement")

    def parse(self, request: ParseRequest) -> ParseResult:
        if _cancelled(request.cancellation):
            return self._cancelled_parse(request)
        try:
            content = _local_path(request.source.uri).read_bytes()
            root = ET.fromstring(content)
            if root.tag.rsplit("}", 1)[-1] != "DocumentElement":
                raise ValueError("unexpected EET root element")
            parser = EET_XmlParser.from_file(_local_path(request.source.uri))
        except (OSError, ValueError, ET.ParseError) as exc:
            return self._failed_parse(
                request,
                Diagnostic("EET_PARSE_FAILED", f"EET XML is invalid ({type(exc).__name__})."),
            )
        snapshot = _snapshot(request, self.format_id, content, xml=True)
        entries = [TranslationEntry.create_from_eet_entry(entry) for entry in parser.entries]
        return self._parse_result(request, snapshot, entries, lambda entry: entry.key)

    def write(self, request: WriteRequest) -> OperationResult[tuple[str, ...]]:
        validation = self.validate_write(request)
        if validation.outcome is not OperationOutcome.COMPLETED:
            return _copy_failure(validation)
        snapshot = request.source_snapshot
        if snapshot is None or snapshot.content is None:
            return _failed_operation(
                Diagnostic("SOURCE_SNAPSHOT_REQUIRED", "EET writes require an in-memory source template."),
                request.context.run_id,
            )
        try:
            root = ET.fromstring(snapshot.content)
            nodes = _eet_nodes(root)
            diagnostics = _apply_xml_entries(
                request.entries,
                nodes,
                "TRADUIT",
                snapshot=snapshot,
                status_tag="STATUS",
            )
            if diagnostics:
                return _failed_diagnostics(diagnostics, request.context.run_id)
            if _cancelled(request.cancellation):
                return OperationResult.cancelled(run_id=request.context.run_id)
            target = _local_path(request.target.uri)
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_xml_template(target, root, snapshot)
        except (OSError, ValueError, ET.ParseError) as exc:
            return _failed_operation(
                Diagnostic("EET_WRITE_FAILED", f"EET XML write failed ({type(exc).__name__})."),
                request.context.run_id,
            )
        return _completed_write(target, len(request.entries), request.context.run_id)


class XtXmlAdapter(_LegacyFormatAdapter):
    format_id = FormatId.XML_XT
    adapter_id = "transbridge.io.xt-xml"

    def probe(self, request: ProbeRequest) -> FormatProbe:
        return _xml_probe(request, self.format_id, "SSTXMLRessources")

    def parse(self, request: ParseRequest) -> ParseResult:
        if _cancelled(request.cancellation):
            return self._cancelled_parse(request)
        try:
            content = _local_path(request.source.uri).read_bytes()
            root = ET.fromstring(content)
            if root.tag.rsplit("}", 1)[-1] != "SSTXMLRessources":
                raise ValueError("unexpected XT root element")
            parser = XT_XmlParser.from_file(str(_local_path(request.source.uri)))
        except (OSError, ValueError, ET.ParseError) as exc:
            return self._failed_parse(
                request,
                Diagnostic("XT_PARSE_FAILED", f"XT XML is invalid ({type(exc).__name__})."),
            )
        snapshot = _snapshot(request, self.format_id, content, xml=True)
        entries = [
            TranslationEntry(
                _xt_locator(entry.list_id, entry.edid, entry.rec, entry.index),
                _xt_locator(entry.list_id, entry.edid, entry.rec, entry.index),
                entry.source,
                entry.dest,
                STAGE_TRANSLATED if entry.dest else 0,
                entry.rec,
            )
            for entry in parser.entries
        ]
        return self._parse_result(request, snapshot, entries, lambda entry: entry.key)

    def write(self, request: WriteRequest) -> OperationResult[tuple[str, ...]]:
        validation = self.validate_write(request)
        if validation.outcome is not OperationOutcome.COMPLETED:
            return _copy_failure(validation)
        snapshot = request.source_snapshot
        if snapshot is None or snapshot.content is None:
            return _failed_operation(
                Diagnostic("SOURCE_SNAPSHOT_REQUIRED", "XT writes require an in-memory source template."),
                request.context.run_id,
            )
        try:
            root = ET.fromstring(snapshot.content)
            nodes = _xt_nodes(root)
            diagnostics = _apply_xml_entries(request.entries, nodes, "Dest", snapshot=snapshot)
            if diagnostics:
                return _failed_diagnostics(diagnostics, request.context.run_id)
            if _cancelled(request.cancellation):
                return OperationResult.cancelled(run_id=request.context.run_id)
            target = _local_path(request.target.uri)
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_xml_template(target, root, snapshot)
        except (OSError, ValueError, ET.ParseError) as exc:
            return _failed_operation(
                Diagnostic("XT_WRITE_FAILED", f"XT XML write failed ({type(exc).__name__})."),
                request.context.run_id,
            )
        return _completed_write(target, len(request.entries), request.context.run_id)


class SsePluginAdapter(_LegacyFormatAdapter):
    format_id = FormatId.PLUGIN_SSE
    adapter_id = "transbridge.io.plugin-sse"

    def probe(self, request: ProbeRequest) -> FormatProbe:
        if request.content[:4] != b"TES4":
            return FormatProbe(ProbeStatus.UNSUPPORTED)
        return FormatProbe(
            ProbeStatus.EXACT,
            (self.format_id,),
            (
                ProbeEvidence(
                    self.format_id,
                    ProbeEvidenceKind.MAGIC,
                    "TES4",
                    ProbeConfidence.EXACT,
                ),
            ),
        )

    def parse(self, request: ParseRequest) -> ParseResult:
        if _cancelled(request.cancellation):
            return self._cancelled_parse(request)
        try:
            source_path = _local_path(request.source.uri)
            content = source_path.read_bytes()
            if content[:4] != b"TES4":
                raise ValueError("missing TES4 header")
            parser = PluginParser()
            options = dict(request.options)
            entries = parser.parse_plugin(
                source_path,
                skip_empty=bool(options.get("skip_empty", True)),
                language=str(options.get("language", "english")),
                discover_sibling_strings=bool(options.get("discover_sibling_strings", True)),
            )
            if parser.get_plugin() is None:
                raise ValueError("plugin parser did not produce a source plugin")
        except (OSError, ValueError) as exc:
            return self._failed_parse(
                request,
                Diagnostic("PLUGIN_PARSE_FAILED", f"SSE plugin is invalid ({type(exc).__name__})."),
            )
        snapshot = _snapshot(request, self.format_id, content)
        try:
            localized_sources = (
                _localized_source_snapshots(
                    source_path,
                    str(options.get("language", "english")),
                    request,
                )
                if bool(options.get("discover_sibling_strings", True))
                else ()
            )
        except ValueError as exc:
            return self._failed_parse(
                request,
                Diagnostic(
                    "PLUGIN_LOCALIZED_SNAPSHOT_FAILED",
                    f"Localized strings snapshot capture failed ({type(exc).__name__}).",
                ),
            )
        snapshot = replace(
            snapshot,
            metadata=(
                *snapshot.metadata,
                ("localized_lookup_detected", parser.get_strings_lookup() is not None),
                ("localized_language", str(options.get("language", "english"))),
                ("localized_sources", localized_sources),
                ("stage_policy_version", DEFAULT_STAGE_POLICY.version),
            ),
        )
        return self._parse_result(request, snapshot, entries, lambda entry: entry.key)

    def validate_write(self, request: WriteRequest) -> OperationResult[None]:
        validation = super().validate_write(request)
        if validation.outcome is not OperationOutcome.COMPLETED:
            return validation
        diagnostics = _blocking_stage_diagnostics(request.entries, request.stage_policy or DEFAULT_STAGE_POLICY)
        if diagnostics:
            return _failed_diagnostics(diagnostics, request.context.run_id)
        return validation

    def write(self, request: WriteRequest) -> OperationResult[tuple[str, ...]]:
        validation = self.validate_write(request)
        if validation.outcome is not OperationOutcome.COMPLETED:
            return _copy_failure(validation)
        return write_plugin(request)

    def capabilities(self) -> FormatCapability:
        capability = super().capabilities()
        return replace(capability, localized=CapabilityLevel.EXPERIMENTAL)


def _snapshot(
    request: ParseRequest,
    format_id: FormatId,
    content: bytes,
    *,
    xml: bool = False,
) -> SourceSnapshot:
    encoding, bom = _xml_encoding(content) if xml else (None, b"")
    return SourceSnapshot.from_bytes(
        request.source,
        format_id,
        content,
        encoding=encoding,
        bom=bom,
    )


def _xml_encoding(content: bytes) -> tuple[str, bytes]:
    signatures = (
        (b"\xef\xbb\xbf", "utf-8"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
    )
    for bom, encoding in signatures:
        if content.startswith(bom):
            return encoding, bom
    if content.startswith(b"<\x00?\x00"):
        return "utf-16-le", b""
    if content.startswith(b"\x00<\x00?"):
        return "utf-16-be", b""
    declaration = re.search(rb"<\?xml[^>]+encoding=[\"']([^\"']+)[\"']", content[:256], re.IGNORECASE)
    if declaration is None:
        return "utf-8", b""
    try:
        return codecs.lookup(declaration.group(1).decode("ascii")).name, b""
    except (LookupError, UnicodeDecodeError):
        return "utf-8", b""


def _namespace_from_locators(format_id: FormatId, locators: list[str]) -> SourceNamespace:
    material = json.dumps(sorted(locators), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return SourceNamespace.from_fingerprint(format_id.value, hashlib.sha256(material).hexdigest())


def _xml_probe(request: ProbeRequest, format_id: FormatId, expected_root: str) -> FormatProbe:
    try:
        root = ET.fromstring(request.content)
    except ET.ParseError:
        return FormatProbe(ProbeStatus.UNSUPPORTED)
    actual = root.tag.rsplit("}", 1)[-1]
    if actual != expected_root:
        return FormatProbe(ProbeStatus.UNSUPPORTED)
    return FormatProbe(
        ProbeStatus.EXACT,
        (format_id,),
        (
            ProbeEvidence(
                format_id,
                ProbeEvidenceKind.ROOT_ELEMENT,
                actual,
                ProbeConfidence.EXACT,
            ),
        ),
    )


def _eet_nodes(root: ET.Element) -> dict[str, list[ET.Element]]:
    nodes: dict[str, list[ET.Element]] = defaultdict(list)
    for node in root.findall(".//ESP"):
        index_text = (node.findtext("INDEX", "") or "").strip()
        try:
            index = int(index_text) if index_text else None
        except ValueError:
            index = None
        locator = TranslationEntry._build_eet_id(
            (node.findtext("EDID", "") or "").strip(),
            (node.findtext("ID", "") or "").strip(),
            index,
            (node.findtext("GRUP", "") or "").strip(),
            (node.findtext("CHAMP", "") or "").strip(),
        )
        nodes[locator].append(node)
    return nodes


def _xt_locator(list_id: int | None, edid: str, rec: str, index: int) -> str:
    return json.dumps(["xt", list_id, edid, rec, index], ensure_ascii=False, separators=(",", ":"))


def _xt_nodes(root: ET.Element) -> dict[str, list[ET.Element]]:
    nodes: dict[str, list[ET.Element]] = defaultdict(list)
    for node in root.findall(".//Content/String"):
        list_id = _optional_int(node.attrib.get("List"))
        rec_node = node.find("REC")
        index = (_optional_int(rec_node.attrib.get("id")) or 0) + 1 if rec_node is not None else 1
        locator = _xt_locator(
            list_id,
            (node.findtext("EDID", "") or "").strip(),
            (node.findtext("REC", "") or "").strip(),
            index,
        )
        nodes[locator].append(node)
    return nodes


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _apply_xml_entries(
    entries: tuple[object, ...],
    nodes: dict[str, list[ET.Element]],
    translation_tag: str,
    *,
    snapshot: SourceSnapshot,
    status_tag: str | None = None,
) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()
    expected_namespace = _snapshot_namespace(snapshot)
    for index, entry in enumerate(entries):
        entry_key = getattr(entry, "entry_key", None)
        if not isinstance(entry_key, EntryKey) or entry_key.namespace != expected_namespace:
            diagnostics.append(
                Diagnostic(
                    "SOURCE_IDENTITY_CONFLICT",
                    "The write entry belongs to a different source snapshot.",
                    details=(("record_index", index), ("entry_key", str(entry_key))),
                )
            )
            continue
        locator = entry_key.local_key if isinstance(entry_key, EntryKey) else None
        candidates = nodes.get(locator or "", ())
        if locator is None or locator in seen or len(candidates) != 1:
            diagnostics.append(
                Diagnostic(
                    "SOURCE_LOCATOR_CONFLICT",
                    "The write entry cannot be uniquely located in the source snapshot.",
                    details=(("record_index", index), ("locator", locator), ("matches", len(candidates))),
                )
            )
            continue
        seen.add(locator)
        translation = getattr(entry, "translation", "")
        if not isinstance(translation, str):
            diagnostics.append(
                Diagnostic(
                    "ENTRY_TRANSLATION_INVALID",
                    "The write entry translation must be a string.",
                    details=(("record_index", index), ("locator", locator)),
                )
            )
            continue
        node = candidates[0]
        target = node.find(translation_tag)
        if target is None:
            diagnostics.append(
                Diagnostic(
                    "SOURCE_TRANSLATION_NODE_MISSING",
                    "The source locator has no writable translation node.",
                    details=(("record_index", index), ("locator", locator)),
                )
            )
            continue
        target.text = translation
        if status_tag is not None:
            status = node.find(status_tag)
            if status is not None:
                status.text = "99" if translation else "0"
    return tuple(diagnostics)


def _snapshot_namespace(snapshot: SourceSnapshot) -> SourceNamespace:
    metadata = dict(snapshot.metadata)
    value = metadata.get("source_namespace")
    if not isinstance(value, str):
        raise ValueError("source snapshot has no valid namespace")
    return SourceNamespace(value)


def _write_xml_template(target: Path, root: ET.Element, snapshot: SourceSnapshot) -> None:
    rendered = ET.tostring(root, encoding=snapshot.encoding or "utf-8", xml_declaration=True)
    if snapshot.bom and not rendered.startswith(snapshot.bom):
        rendered = snapshot.bom + rendered
    target.write_bytes(rendered)


def _local_path(uri: str) -> Path:
    if "://" in uri:
        raise ValueError("legacy format adapters only accept local paths")
    return Path(uri)


def _cancelled(token: object | None) -> bool:
    if token is None:
        return False
    state = token.is_cancelled
    return bool(state() if callable(state) else state)


def _failed_operation(diagnostic: Diagnostic, run_id: str | None) -> OperationResult:
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(diagnostic,),
        counts=OperationCounts(failed=1),
        run_id=run_id,
    )


def _failed_diagnostics(diagnostics: tuple[Diagnostic, ...], run_id: str | None) -> OperationResult:
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=diagnostics,
        counts=OperationCounts(failed=max(len(diagnostics), 1)),
        run_id=run_id,
    )


def _copy_failure(result: OperationResult) -> OperationResult:
    return OperationResult(
        result.outcome,
        diagnostics=result.diagnostics,
        counts=result.counts,
        run_id=result.run_id,
    )


def _completed_write(target: Path, count: int, run_id: str | None) -> OperationResult[tuple[str, ...]]:
    artifact = str(target)
    return OperationResult.completed(
        (artifact,),
        counts=OperationCounts(succeeded=count),
        artifact_refs=(artifact,),
        run_id=run_id,
    )
