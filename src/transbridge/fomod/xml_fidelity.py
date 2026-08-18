"""Semantic-fidelity XML translation for FOMOD metadata files."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import xml.etree.ElementTree as ET

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity
from transbridge.application.tasks import TaskCancelled

XML_FIDELITY_POLICY_VERSION = "fomod-xml-semantic-v2"

_BOMS = {
    b"\xef\xbb\xbf": "utf-8",
    b"\xff\xfe": "utf-16-le",
    b"\xfe\xff": "utf-16-be",
}
_TRANSLATABLE_TEXT_TAGS = frozenset({"modulename", "description", "name"})
_TRANSLATABLE_NAME_ATTRIBUTE_TAGS = frozenset({"installstep", "group", "plugin"})
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tga", ".dds"})
_NAMESPACE_LOCK = threading.RLock()


class XmlValueKind(StrEnum):
    TEXT = "text"
    ATTRIBUTE = "attribute"


@dataclass(frozen=True, slots=True)
class XmlPathSegment:
    qname: str
    child_index: int


@dataclass(frozen=True, slots=True)
class XmlLocator:
    path: tuple[XmlPathSegment, ...]
    kind: XmlValueKind
    attribute: str | None = None

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("XML locator path must not be empty")
        if self.kind is XmlValueKind.ATTRIBUTE and not self.attribute:
            raise ValueError("attribute locator requires an attribute QName")
        if self.kind is XmlValueKind.TEXT and self.attribute is not None:
            raise ValueError("text locator cannot contain an attribute QName")

    def serialize(self) -> str:
        return json.dumps(
            {
                "path": [[item.qname, item.child_index] for item in self.path],
                "kind": self.kind.value,
                "attribute": self.attribute,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class XmlTextValue:
    locator: XmlLocator
    text: str


@dataclass(frozen=True, slots=True)
class XmlTextCandidate:
    locator: XmlLocator
    original: str
    translation: str
    source: str

    def __post_init__(self) -> None:
        if not self.original or not self.translation or not self.source:
            raise ValueError("XML candidate original, translation and source must not be empty")


@dataclass(frozen=True, slots=True)
class FomodXmlSnapshot:
    raw: bytes
    encoding: str
    bom: bytes
    prolog: str
    trailing: str
    namespaces: tuple[tuple[str, str], ...]
    values: tuple[XmlTextValue, ...]
    resource_references: tuple[str, ...]
    source_hash: str
    lexical_diagnostics: tuple[Diagnostic, ...] = ()

    def value(self, locator: XmlLocator) -> str | None:
        return dict((item.locator.serialize(), item.text) for item in self.values).get(locator.serialize())


@dataclass(frozen=True, slots=True)
class XmlFidelityReport:
    source_hash: str
    output_hash: str
    encoding: str
    bom: str
    changed_nodes: tuple[str, ...]
    candidate_sources: tuple[tuple[str, int], ...]
    preserved_resources: tuple[str, ...]
    namespaces: tuple[tuple[str, str], ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    policy_version: str = XML_FIDELITY_POLICY_VERSION

    def to_dict(self) -> dict:
        return {
            "policy_version": self.policy_version,
            "source_hash": self.source_hash,
            "output_hash": self.output_hash,
            "encoding": self.encoding,
            "bom": self.bom,
            "changed_nodes": list(self.changed_nodes),
            "candidate_sources": dict(self.candidate_sources),
            "preserved_resources": list(self.preserved_resources),
            "namespaces": dict(self.namespaces),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


class FomodXmlError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def parse_fomod_xml(raw: bytes) -> FomodXmlSnapshot:
    encoding, bom = _detect_encoding(raw)
    content = raw[len(bom) :]
    try:
        text = content.decode(encoding)
    except UnicodeDecodeError as exc:
        raise FomodXmlError("FOMOD_XML_ENCODING_INVALID", str(exc)) from exc
    try:
        root = _parse_root(text)
        namespaces = _extract_namespaces(text)
    except ET.ParseError as exc:
        raise FomodXmlError("FOMOD_XML_PARSE_FAILED", str(exc)) from exc
    prolog, trailing = _outer_lexical_text(text)
    values = tuple(_extract_values(root))
    references = tuple(sorted(_extract_resource_references(root)))
    diagnostics: list[Diagnostic] = []
    if "<![CDATA[" in text or "<!ENTITY" in text or "<!DOCTYPE" in text:
        diagnostics.append(
            Diagnostic(
                "FOMOD_XML_LEXICAL_FIDELITY_LIMITED",
                "CDATA/entity/DOCTYPE lexical spelling is outside the declared semantic-fidelity scope.",
                DiagnosticSeverity.WARNING,
            )
        )
    return FomodXmlSnapshot(
        raw,
        encoding,
        bom,
        prolog,
        trailing,
        namespaces,
        values,
        references,
        hashlib.sha256(raw).hexdigest(),
        tuple(diagnostics),
    )


def build_candidates(
    snapshot: FomodXmlSnapshot,
    *,
    old_snapshot: FomodXmlSnapshot | None,
    llm,
    target_locale: str,
    cancellation: object | None = None,
) -> tuple[XmlTextCandidate, ...]:
    old_values = (
        {item.locator.serialize(): item.text for item in old_snapshot.values} if old_snapshot is not None else {}
    )
    candidates: list[XmlTextCandidate] = []
    for value in snapshot.values:
        _raise_if_cancelled(cancellation)
        old_value = old_values.get(value.locator.serialize())
        if old_value and old_value != value.text:
            candidates.append(XmlTextCandidate(value.locator, value.text, old_value, "old_archive"))
            continue
        if llm is None:
            continue
        try:
            translated = str(llm.chat(_build_prompt(value.text, target_locale))).strip()
        except TaskCancelled:
            raise
        except Exception as exc:
            raise FomodXmlError(
                "FOMOD_XML_TRANSLATION_FAILED",
                f"{type(exc).__name__}: XML translation provider failed",
            ) from exc
        _raise_if_cancelled(cancellation)
        if translated and translated != value.text:
            candidates.append(XmlTextCandidate(value.locator, value.text, translated, "llm"))
    return tuple(candidates)


def patch_and_validate(
    snapshot: FomodXmlSnapshot,
    candidates: tuple[XmlTextCandidate, ...],
) -> tuple[bytes, XmlFidelityReport]:
    if candidates and snapshot.lexical_diagnostics:
        raise FomodXmlError(
            "FOMOD_XML_FIDELITY_UNSUPPORTED",
            "XML uses lexical constructs that cannot be rewritten without loss",
        )
    if len({item.locator.serialize() for item in candidates}) != len(candidates):
        raise FomodXmlError("FOMOD_XML_CANDIDATE_DUPLICATE", "duplicate XML candidate locator")
    known = {item.locator.serialize(): item.text for item in snapshot.values}
    root = _parse_root(_decode(snapshot))
    changed: list[str] = []
    sources: dict[str, int] = {}
    for candidate in candidates:
        key = candidate.locator.serialize()
        if known.get(key) != candidate.original:
            raise FomodXmlError(
                "FOMOD_XML_SOURCE_CHANGED",
                f"XML source changed before patch: {key}",
            )
        element = _locate(root, candidate.locator)
        if candidate.locator.kind is XmlValueKind.TEXT:
            element.text = _replace_core(element.text or "", candidate.translation)
        else:
            attribute = candidate.locator.attribute
            if attribute is None:
                raise AssertionError("validated attribute locator lost its QName")
            element.set(attribute, candidate.translation)
        changed.append(key)
        sources[candidate.source] = sources.get(candidate.source, 0) + 1

    output = snapshot.raw if not candidates else _serialize(snapshot, root)
    output_snapshot = parse_fomod_xml(output)
    _validate_fidelity(snapshot, output_snapshot, frozenset(changed))
    report = XmlFidelityReport(
        snapshot.source_hash,
        hashlib.sha256(output).hexdigest(),
        snapshot.encoding,
        _bom_name(snapshot.bom),
        tuple(changed),
        tuple(sorted(sources.items())),
        snapshot.resource_references,
        snapshot.namespaces,
        snapshot.lexical_diagnostics,
    )
    return output, report


def process_fomod_xml_file(
    path: str | Path,
    *,
    old_path: str | Path | None,
    llm,
    target_locale: str,
    cancellation: object | None = None,
) -> XmlFidelityReport:
    target = Path(path)
    snapshot = parse_fomod_xml(target.read_bytes())
    old_snapshot = None
    if old_path is not None and Path(old_path).is_file():
        old_snapshot = parse_fomod_xml(Path(old_path).read_bytes())
    candidates = build_candidates(
        snapshot,
        old_snapshot=old_snapshot,
        llm=llm,
        target_locale=target_locale,
        cancellation=cancellation,
    )
    output, report = patch_and_validate(snapshot, candidates)
    _raise_if_cancelled(cancellation)
    if output != snapshot.raw:
        _atomic_replace_bytes(target, output)
    return report


def find_fomod_xml_files(root: str | Path) -> tuple[Path, ...]:
    """Return the supported FOMOD metadata files in deterministic order."""
    base = Path(root)
    candidates = (
        base / "fomod" / "ModuleConfig.xml",
        base / "fomod" / "info.xml",
        base / "ModuleConfig.xml",
        base / "info.xml",
    )
    seen: set[Path] = set()
    found: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if path.is_file() and resolved not in seen:
            seen.add(resolved)
            found.append(path)
    return tuple(found)


def _parse_root(text: str) -> ET.Element:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    return ET.fromstring(text, parser=parser)


def _extract_namespaces(text: str) -> tuple[tuple[str, str], ...]:
    seen: dict[str, str] = {}
    for _, value in ET.iterparse(io.StringIO(text), events=("start-ns",)):
        prefix, uri = value
        seen.setdefault(prefix or "", uri)
    return tuple(seen.items())


def _extract_values(root: ET.Element) -> list[XmlTextValue]:
    values: list[XmlTextValue] = []

    def walk(element: ET.Element, path: tuple[XmlPathSegment, ...]) -> None:
        if not isinstance(element.tag, str):
            return
        local = _local_name(element.tag).casefold()
        core = (element.text or "").strip()
        if core and local in _TRANSLATABLE_TEXT_TAGS:
            values.append(XmlTextValue(XmlLocator(path, XmlValueKind.TEXT), core))
        if local in _TRANSLATABLE_NAME_ATTRIBUTE_TAGS:
            for attribute, value in element.attrib.items():
                if _local_name(attribute).casefold() == "name" and value.strip():
                    values.append(
                        XmlTextValue(
                            XmlLocator(path, XmlValueKind.ATTRIBUTE, attribute),
                            value,
                        )
                    )
        for index, child in enumerate(list(element)):
            if isinstance(child.tag, str):
                walk(child, (*path, XmlPathSegment(child.tag, index)))

    walk(root, (XmlPathSegment(str(root.tag), 0),))
    return values


def _extract_resource_references(root: ET.Element) -> set[str]:
    references: set[str] = set()
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        for value in element.attrib.values():
            normalized = _resource_reference(value)
            if normalized is not None:
                references.add(normalized)
        if _local_name(element.tag).casefold() == "image" and element.text:
            normalized = _resource_reference(element.text)
            if normalized is not None:
                references.add(normalized)
    return references


def _resource_reference(value: str) -> str | None:
    normalized = value.strip().replace("\\", "/").lstrip("./")
    if Path(normalized).suffix.casefold() not in _IMAGE_EXTENSIONS:
        return None
    return normalized


def _locate(root: ET.Element, locator: XmlLocator) -> ET.Element:
    if root.tag != locator.path[0].qname:
        raise FomodXmlError("FOMOD_XML_LOCATOR_STALE", "XML root no longer matches locator")
    current = root
    for segment in locator.path[1:]:
        children = list(current)
        if segment.child_index >= len(children):
            raise FomodXmlError("FOMOD_XML_LOCATOR_STALE", "XML child index is no longer present")
        current = children[segment.child_index]
        if current.tag != segment.qname:
            raise FomodXmlError("FOMOD_XML_LOCATOR_STALE", "XML child QName no longer matches")
    return current


def _serialize(snapshot: FomodXmlSnapshot, root: ET.Element) -> bytes:
    with _NAMESPACE_LOCK:
        for prefix, uri in snapshot.namespaces:
            if prefix not in {"xml", "xmlns"} and not re.fullmatch(r"ns\d+", prefix):
                ET.register_namespace(prefix, uri)
        body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    text = f"{snapshot.prolog}{body}{snapshot.trailing}"
    try:
        encoded = text.encode(snapshot.encoding)
    except UnicodeEncodeError as exc:
        raise FomodXmlError("FOMOD_XML_ENCODING_LOSS", str(exc)) from exc
    return snapshot.bom + encoded


def _validate_fidelity(
    source: FomodXmlSnapshot,
    output: FomodXmlSnapshot,
    changed: frozenset[str],
) -> None:
    if source.encoding != output.encoding or source.bom != output.bom:
        raise FomodXmlError("FOMOD_XML_ENCODING_FIDELITY_FAILED", "XML encoding or BOM changed")
    if source.namespaces != output.namespaces:
        raise FomodXmlError("FOMOD_XML_NAMESPACE_FIDELITY_FAILED", "XML namespaces changed")
    if source.resource_references != output.resource_references:
        raise FomodXmlError("FOMOD_XML_RESOURCE_REFERENCE_CHANGED", "XML resource references changed")
    source_root = _parse_root(_decode(source))
    output_root = _parse_root(_decode(output))

    def compare(
        before: ET.Element,
        after: ET.Element,
        path: tuple[XmlPathSegment, ...],
    ) -> None:
        if _node_tag(before) != _node_tag(after):
            raise FomodXmlError("FOMOD_XML_STRUCTURE_CHANGED", "XML node order or QName changed")
        before_attributes = dict(before.attrib)
        after_attributes = dict(after.attrib)
        if before_attributes.keys() != after_attributes.keys():
            raise FomodXmlError("FOMOD_XML_ATTRIBUTE_CHANGED", "XML attribute set changed")
        for attribute, value in before_attributes.items():
            locator = XmlLocator(path, XmlValueKind.ATTRIBUTE, attribute).serialize()
            if locator not in changed and after_attributes[attribute] != value:
                raise FomodXmlError("FOMOD_XML_ATTRIBUTE_CHANGED", "Unknown XML attribute changed")
        text_locator = XmlLocator(path, XmlValueKind.TEXT).serialize()
        if text_locator not in changed and (before.text or "") != (after.text or ""):
            raise FomodXmlError("FOMOD_XML_TEXT_CHANGED", "Non-translatable XML text changed")
        if (before.tail or "") != (after.tail or ""):
            raise FomodXmlError("FOMOD_XML_TAIL_CHANGED", "XML node tail changed")
        before_children = list(before)
        after_children = list(after)
        if len(before_children) != len(after_children):
            raise FomodXmlError("FOMOD_XML_STRUCTURE_CHANGED", "XML child count changed")
        for index, (before_child, after_child) in enumerate(zip(before_children, after_children, strict=True)):
            child_path = (*path, XmlPathSegment(str(before_child.tag), index))
            compare(before_child, after_child, child_path)

    compare(source_root, output_root, (XmlPathSegment(str(source_root.tag), 0),))


def _node_tag(element: ET.Element) -> str:
    if isinstance(element.tag, str):
        return element.tag
    if element.tag is ET.Comment:
        return "#comment"
    if element.tag is ET.ProcessingInstruction:
        return "#pi"
    return repr(element.tag)


def _detect_encoding(raw: bytes) -> tuple[str, bytes]:
    for bom, encoding in _BOMS.items():
        if raw.startswith(bom):
            return encoding, bom
    declaration = raw[:256].decode("ascii", errors="ignore")
    match = re.search(r"<\?xml[^>]*encoding=[\"']([^\"']+)", declaration, re.IGNORECASE)
    encoding = (match.group(1) if match else "utf-8").casefold().replace("_", "-")
    aliases = {"utf8": "utf-8", "utf16le": "utf-16-le", "utf16be": "utf-16-be"}
    encoding = aliases.get(encoding, encoding)
    if encoding == "utf-16":
        raise FomodXmlError("FOMOD_XML_ENCODING_AMBIGUOUS", "UTF-16 XML requires a BOM")
    if encoding not in {"utf-8", "utf-16-le", "utf-16-be"}:
        raise FomodXmlError("FOMOD_XML_ENCODING_UNSUPPORTED", f"unsupported XML encoding: {encoding}")
    return encoding, b""


def _outer_lexical_text(text: str) -> tuple[str, str]:
    root_match = re.search(r"<(?!(?:\?|!))[^\s>/]+", text)
    if root_match is None:
        raise FomodXmlError("FOMOD_XML_ROOT_MISSING", "XML root element was not found")
    trailing_match = re.search(r"\s*\Z", text)
    trailing = trailing_match.group(0) if trailing_match else ""
    return text[: root_match.start()], trailing


def _decode(snapshot: FomodXmlSnapshot) -> str:
    return snapshot.raw[len(snapshot.bom) :].decode(snapshot.encoding)


def _replace_core(value: str, replacement: str) -> str:
    prefix = value[: len(value) - len(value.lstrip())]
    suffix = value[len(value.rstrip()) :]
    return f"{prefix}{replacement}{suffix}"


def _local_name(qname: str) -> str:
    return qname.rsplit("}", 1)[-1]


def _bom_name(bom: bytes) -> str:
    return {
        b"": "none",
        b"\xef\xbb\xbf": "utf-8",
        b"\xff\xfe": "utf-16-le",
        b"\xfe\xff": "utf-16-be",
    }[bom]


def _build_prompt(text: str, target_locale: str) -> list[dict[str, str]]:
    prompt = (
        "Translate this FOMOD installer UI text. Preserve placeholders and markup. "
        f"Target locale: {target_locale}. Return only the translation.\n\n{text}"
    )
    return [{"role": "user", "content": prompt}]


def _atomic_replace_bytes(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _raise_if_cancelled(signal: object | None) -> None:
    if signal is None:
        return
    state = getattr(signal, "is_cancelled", None)
    if state is not None:
        cancelled = bool(state() if callable(state) else state)
    else:
        is_set = getattr(signal, "is_set", None)
        cancelled = bool(is_set()) if callable(is_set) else False
    if cancelled:
        raise TaskCancelled("FOMOD XML translation cancelled")
