"""Admit only format-specific translation records in a portable database."""

from __future__ import annotations

import codecs
import re
import xml.etree.ElementTree as ET

from transbridge.application.io.contracts import FormatId
from transbridge.application.io.plugin_dialogue_order import METADATA_KEY
from transbridge.application.io.sst_export import (
    PLUGIN_SST_BLOCKER_CODES,
    PLUGIN_SST_LOCATOR,
    PLUGIN_SST_MASTERS,
    PLUGIN_SST_UNAVAILABLE,
    PluginSstLocator,
    validate_master_names,
)
from transbridge.application.io.strings_adapter import _encode_chunk
from transbridge.application.projects.native_source_formats import SST_FORMATS, _eet_record, _sst_record
from transbridge.application.projects.translation_templates import STRINGS_FORMATS, XML_FORMATS, _xml_parser
from transbridge.converter.eet_entries import EET_CODEC_VERSION
from transbridge.converter.sst_entries import SST_CODEC_VERSION
from transbridge.parser.eet_binary_codec import EetDocument
from transbridge.parser.plugin.dialogue_order_evidence import EXTRACTION_VERSION, evidence_from_payload
from transbridge.parser.xt.sst_codec import SstDocument

from .entry_codec import immutable_json

_FORMAT_FIELDS = frozenset({
    "eet_header",
    "sst_header",
    "xml_header",
    METADATA_KEY,
    "localized_language",
    "localized_lookup_detected",
    "stage_policy_version",
    PLUGIN_SST_MASTERS,
    PLUGIN_SST_UNAVAILABLE,
})
_LOCATION_FIELDS = frozenset({"path", "source_path", "source_file", "plugin_path", "location", "uri", "filename"})
_PATH = re.compile(r"(?i)^(?:file:|[a-z]:[\\/]|[\\/])")
_DIGEST = re.compile(r"[0-9a-f]{64}")


def validate_format(format_id, values, fingerprint):
    fields = {name: immutable_json(value) for name, value in values.items()}
    native = _FORMAT_FIELDS.intersection(fields)
    for name in ("source_role", "encoding", "localized_language", "stage_policy_version"):
        if name in fields and (not isinstance(fields[name], str) or not fields[name]):
            raise ValueError(f"source format field {name} must be text")
    if format_id is FormatId.BINARY_EET:
        _require_fields(native, {"eet_header"})
        version, game, phrases, type_dt = fields["eet_header"]
        EetDocument(version, game, (), phrases, type_dt).to_bytes()
    elif format_id in SST_FORMATS:
        _require_fields(native, {"sst_header"})
        magic, flag, masters, labels = fields["sst_header"]
        if magic != ("SSU8" if format_id is FormatId.SST_SSU8 else "SSU9"):
            raise ValueError("SST header does not match its selected format")
        SstDocument(magic.encode("ascii"), flag, masters, labels, ()).to_bytes()
    elif format_id in XML_FORMATS:
        _require_fields(native, {"xml_header"})
        _validate_xml_header(fields["xml_header"], fields.get("encoding") or "utf-8")
    elif format_id is FormatId.PLUGIN_SSE:
        if native & {"eet_header", "sst_header", "xml_header"}:
            raise ValueError("plugin source contains a native translation header")
        _validate_plugin_fields(fields, fingerprint)
    else:
        _require_fields(native, set())
    if "bom" in fields:
        marker = bytes.fromhex(fields["bom"])
        if marker not in {
            codecs.BOM_UTF8,
            codecs.BOM_UTF16_LE,
            codecs.BOM_UTF16_BE,
            codecs.BOM_UTF32_LE,
            codecs.BOM_UTF32_BE,
        }:
            raise ValueError("source BOM is unsupported")


def validate_native_entry(format_id, entry, metadata, fingerprint, ordinal, has_plugin_sst):
    native = {name for name in ("eet.native", "sst.native") if name in metadata}
    if format_id is FormatId.BINARY_EET:
        _require_fields(native, {"eet.native"})
        _validate_locator("eet", EET_CODEC_VERSION, metadata, fingerprint, ordinal)
        _eet_record(entry).to_bytes()
    elif format_id in SST_FORMATS:
        _require_fields(native, {"sst.native"})
        _validate_locator("sst", SST_CODEC_VERSION, metadata, fingerprint, ordinal)
        SstDocument(b"SSU8", 0, (), (), (_sst_record(entry),)).to_bytes()
    else:
        _require_fields(native, set())
    if format_id in STRINGS_FORMATS:
        identity = entry.snapshot.string_id
        if type(identity) is not int or not 0 <= identity <= 0xFFFFFFFF:
            raise ValueError("localized translation row has an invalid String ID")
        _encode_chunk(entry.snapshot.original, metadata["strings.encoding"], format_id is not FormatId.STRINGS)
    if has_plugin_sst:
        PluginSstLocator.from_values(metadata.get(PLUGIN_SST_LOCATOR))
    elif PLUGIN_SST_LOCATOR in metadata:
        raise ValueError("entry has an SST locator without a verified plugin source header")


def _validate_locator(prefix, version, metadata, fingerprint, ordinal):
    if (
        type(metadata.get(f"{prefix}.codec_version")) is not int
        or metadata[f"{prefix}.codec_version"] != version
        or metadata.get(f"{prefix}.source_sha256") != fingerprint
        or type(metadata.get(f"{prefix}.record_index")) is not int
        or metadata[f"{prefix}.record_index"] != ordinal
    ):
        raise ValueError("native entry locator does not match its source revision and physical order")
    signature = metadata.get(f"{prefix}.record_signature")
    if not isinstance(signature, str) or not _DIGEST.fullmatch(signature):
        raise ValueError("native entry locator has an invalid record signature")


def validate_xml_record(format_id, fragment, tail):
    if format_id not in XML_FORMATS:
        raise ValueError("physical XML records can only belong to an XML translation source")
    try:
        wrapper = ET.fromstring(f"<record>{fragment}</record>", parser=_xml_parser())
    except ET.ParseError as exc:
        raise ValueError("physical XML translation record is malformed") from exc
    if len(wrapper) != 1 or wrapper.text or wrapper[0].tail:
        raise ValueError("physical XML translation record does not contain one complete node")
    _validate_xml_locations(wrapper[0])
    if tail is not None and not isinstance(tail, str):
        raise ValueError("XML record tail must be text")


def _validate_xml_header(header, encoding):
    tag, attributes, text = header
    if not isinstance(tag, str) or (text is not None and not isinstance(text, str)):
        raise ValueError("XML source header must contain typed text fields")
    values = dict(attributes)
    if len(values) != len(attributes) or any(
        type(name) is not str or type(value) is not str for name, value in values.items()
    ):
        raise ValueError("XML source attributes must be distinct text fields")
    try:
        codecs.lookup(encoding)
        root = ET.fromstring(ET.tostring(ET.Element(tag, values)))
    except (ET.ParseError, LookupError) as exc:
        raise ValueError("XML source header is malformed") from exc
    _validate_xml_locations(root)


def _validate_xml_locations(root):
    for node in root.iter():
        for key, value in node.attrib.items():
            if key.rsplit("}", 1)[-1].casefold() in _LOCATION_FIELDS and _PATH.match(value):
                raise ValueError("XML metadata contains a historical source path")
        if isinstance(node.tag, str) and node.tag.rsplit("}", 1)[-1].casefold() in _LOCATION_FIELDS:
            if node.text and _PATH.match(node.text.strip()):
                raise ValueError("XML metadata contains a historical source path")


def _validate_plugin_fields(fields, fingerprint):
    if "localized_lookup_detected" in fields and type(fields["localized_lookup_detected"]) is not bool:
        raise ValueError("plugin localization indicator must be boolean")
    if METADATA_KEY in fields:
        version, source_fingerprint, payload = fields[METADATA_KEY]
        if type(version) is not int or version != EXTRACTION_VERSION or source_fingerprint != fingerprint:
            raise ValueError("plugin dialogue metadata belongs to another source revision")
        evidence_from_payload(payload)
    if PLUGIN_SST_MASTERS in fields:
        if PLUGIN_SST_UNAVAILABLE in fields:
            raise ValueError("plugin SST metadata contains conflicting capabilities")
        validate_master_names(fields[PLUGIN_SST_MASTERS])
    if PLUGIN_SST_UNAVAILABLE in fields and fields[PLUGIN_SST_UNAVAILABLE] not in PLUGIN_SST_BLOCKER_CODES:
        raise ValueError("plugin SST metadata contains an unknown capability blocker")


def _require_fields(actual, expected):
    if actual != expected:
        raise ValueError("native translation metadata does not match its format")
