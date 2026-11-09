"""Fields that may cross the parser-to-project boundary."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

_ENTRY_FIELDS = frozenset({
    "io.format",
    "io.locator",
    "terminology_plugin_id",
    "plugin.parent_dial_formid",
    "plugin.source_order",
    "plugin.record_type",
    "plugin.field_type",
    "plugin.form_id",
    "plugin.editor_id",
    "plugin.string_table",
    "plugin.speaker_form_id",
    "plugin.quest_form_id",
    "plugin.dialogue_topic",
    "plugin.sst_locator",
    "eet.codec_version",
    "eet.source_sha256",
    "eet.record_index",
    "eet.record_signature",
    "eet.form_id",
    "eet.editor_id",
    "eet.index",
    "eet.group",
    "eet.field",
    "sst.codec_version",
    "sst.source_sha256",
    "sst.record_index",
    "sst.record_signature",
    "sst.form_id",
    "sst.list_index",
    "sst.index",
    "strings.directory_index",
    "strings.encoding",
    "eet.native",
    "sst.native",
    "transbridge.io.paratranz.extensions",
    "transbridge.io.paratranz.context_order",
    "transbridge.io.dsd.locator",
})
_OPTION_FIELDS = frozenset({
    "language",
    "skip_empty",
    "source_namespace",
    "invalid_record_policy",
    "original_language",
    "translation_language",
    "encoding",
    "discover_sibling_strings",
    "external_scope",
})
_FORMAT_FIELDS = frozenset({
    "source_role",
    "encoding",
    "bom",
    "eet_header",
    "sst_header",
    "xml_header",
    "plugin_dialogue_order",
    "localized_language",
    "localized_lookup_detected",
    "stage_policy_version",
    "plugin_sst_masters",
    "plugin_sst_unavailable",
})
_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|file:)", re.IGNORECASE)


def source_name(value: str) -> str:
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    if not name.strip() or name in {".", ".."}:
        raise ValueError("source name must identify a file or display name")
    return name


def entry_fields(values: Iterable[tuple[str, Any]], *, ignore_unknown: bool = False) -> dict[str, Any]:
    fields = ((key, value) for key, value in values if key in _ENTRY_FIELDS) if ignore_unknown else values
    return _fields(fields, _ENTRY_FIELDS, "source entry")


def parse_options(values: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    return _fields(values, _OPTION_FIELDS, "source option")


def format_fields(values: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    return _fields(values, _FORMAT_FIELDS, "source format")


def business_fields(values: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    """Validate small scalar metadata carried by an external reference or provenance."""
    result = {}
    for name, value in values:
        if name in result or not name or any(part in name.lower() for part in ("path", "uri", "blob", "content")):
            raise ValueError(f"unsupported business metadata field {name!r}")
        if type(value) not in {bool, int, float, str, type(None)}:
            raise ValueError(f"business metadata field {name!r} requires a scalar")
        result[name] = _value(value)
    return result


def _fields(values: Iterable[tuple[str, Any]], allowed: frozenset[str], label: str) -> dict[str, Any]:
    result = {}
    for key, value in values:
        if key not in allowed or key in result:
            raise ValueError(f"unsupported or repeated {label} field {key!r}")
        result[key] = (
            _extension_fields(value)
            if key == "transbridge.io.paratranz.extensions"
            else _value(value, text_fields=key in {"eet.native", "sst.native", "xml_header", "eet_header"})
        )
    return result


def _value(value: Any, *, text_fields: bool = False, depth: int = 0) -> Any:
    if type(value) in {bool, int, float, type(None)}:
        return value
    if type(value) is str and (text_fields or not _PATH.match(value)):
        return value
    if isinstance(value, (tuple, list)) and depth < 10:
        return tuple(_value(item, text_fields=text_fields, depth=depth + 1) for item in value)
    raise ValueError("persistent source metadata requires approved scalar or sequence values without file paths")


def _extension_fields(value: Any, depth: int = 0) -> Any:
    if depth > 10:
        raise ValueError("ParaTranz extension nesting exceeds the supported depth")
    if isinstance(value, dict):
        return {
            key: _extension_fields(item, depth + 1)
            for key, item in value.items()
            if isinstance(key, str) and key.casefold() not in {"path", "uri", "content", "source_path"}
        }
    if isinstance(value, (tuple, list)):
        return [_extension_fields(item, depth + 1) for item in value]
    return _value(value)
