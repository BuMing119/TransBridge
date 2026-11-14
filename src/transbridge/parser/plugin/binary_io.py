# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Adapt the vendor binary plugin tree at load and serialization boundaries."""

from collections.abc import Iterator
from copy import copy
from pathlib import Path
from typing import Self

from sse_plugin_interface.group import Group
from sse_plugin_interface.plugin import SSEPlugin
from sse_plugin_interface.record import Record
from sse_plugin_interface.subrecord import XXXX, StringSubrecord, Subrecord
from sse_plugin_interface.utilities import Stream

from transbridge.config.text_encoding import (
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TEXT_ENCODING,
    XTRANSLATOR_READ_POLICY,
    TextReadOptions,
)

from .text_subrecords import EncodedStringSubrecord, apply_input_encoding


class PluginBinaryIO(SSEPlugin):
    fallback_count: int = 0

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        input_encoding: str = DEFAULT_TEXT_ENCODING,
        source_language: str = DEFAULT_SOURCE_LANGUAGE,
        text_read_policy: str = XTRANSLATOR_READ_POLICY,
    ) -> Self:
        with path.open("rb") as stream:
            return cls.from_stream(
                stream,
                path.name,
                input_encoding=input_encoding,
                source_language=source_language,
                text_read_policy=text_read_policy,
            )

    @classmethod
    def from_stream(
        cls,
        stream: Stream,
        name: str,
        *,
        input_encoding: str = DEFAULT_TEXT_ENCODING,
        source_language: str = DEFAULT_SOURCE_LANGUAGE,
        text_read_policy: str = XTRANSLATOR_READ_POLICY,
    ) -> Self:
        options = TextReadOptions(input_encoding, source_language, text_read_policy)
        plugin = super().from_stream(stream, name)
        for record in plugin._records():
            plugin.fallback_count += apply_input_encoding(record, options, name)
            for field in record.subrecords:
                if _has_special_payload(field):
                    field._transbridge_parsed_attributes = vars(field).copy()
        return plugin

    def _records(self) -> Iterator[Record]:
        yield self._SSEPlugin__header
        for group in self._SSEPlugin__groups:
            yield from self.extract_group_records(group)

    def inline_text_fields(self) -> Iterator[EncodedStringSubrecord]:
        for record in self._records():
            for field in record.subrecords:
                if isinstance(field, EncodedStringSubrecord):
                    yield field

    def dump(self) -> bytes:
        return b"".join([
            _dump_preserving_payloads(self._SSEPlugin__header),
            *(_dump_preserving_payloads(group) for group in self._SSEPlugin__groups),
        ])


def _has_special_payload(field: Subrecord) -> bool:
    # The vendor serializers for structured metadata reconstruct data from
    # typed attributes and can discard uninterpreted trailing bytes.
    return type(field).dump is not Subrecord.dump and not isinstance(field, (StringSubrecord, XXXX))


def _preserved_field(field: Subrecord) -> Subrecord:
    original = getattr(field, "_transbridge_parsed_attributes", None)
    if isinstance(field, StringSubrecord) and isinstance(field.string, int):
        unchanged = field.string == int.from_bytes(field.data[:4], "little")
    else:
        unchanged = original is not None and all(
            (current := getattr(field, key)) is value or current == value
            for key, value in original.items()
            if key != "data"
        )
    if not unchanged:
        return field
    raw = Subrecord(field.type)
    raw.data = field.data
    return raw


def _dump_preserving_payloads(node: Record | Group) -> bytes:
    # Reuse vendor header, group sizing and compression without modifying the
    # working tree or asking metadata serializers to normalize opaque payloads.
    if isinstance(node, Group):
        group = copy(node)
        group.children = [_SerializedNode(_dump_preserving_payloads(child)) for child in node.children]
        return group.dump()
    record = copy(node)
    record.subrecords = [_preserved_field(field) for field in node.subrecords]
    return record.dump()


class _SerializedNode:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def dump(self) -> bytes:
        return self.data
