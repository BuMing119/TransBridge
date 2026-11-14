# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Apply the selected codec to editable plugin fields at their byte boundary."""

from sse_plugin_interface.datatypes import RawString
from sse_plugin_interface.record import Record
from sse_plugin_interface.subrecord import StringSubrecord, Subrecord

from transbridge.config.text_encoding import LEGACY_READ_POLICY, TextReadOptions, normalize_text_encoding
from transbridge.parser.game_text import describe_game_text_error, encode_game_text
from transbridge.parser.text_reader import decode_game_text


class PluginTextEncodingError(ValueError):
    """An editable plugin field cannot be represented in its selected encoding."""


class EncodedStringSubrecord(StringSubrecord):
    """Keep vendor field semantics, while bypassing RawString's fallback codecs."""

    @classmethod
    def from_parsed(cls, field: StringSubrecord, options: TextReadOptions, locator: str):
        result = cls(field.type)
        result.index = field.index
        result.size = field.size
        result.data = field.data
        result._locator = locator
        payload, terminator, tail = field.data.partition(b"\x00")
        # Keep trailing bytes from the parsed subrecord, including existing padding.
        result._suffix = terminator + tail
        result._source_replaced = False
        try:
            if options.text_read_policy == LEGACY_READ_POLICY:
                text = str(field.string)
                encoding = normalize_text_encoding(field.string.encoding)
                if encoding == "utf-8" and "\ufffd" in text and text.encode(encoding) != payload:
                    # RawString reports UTF-8 after its last codec uses replacement.
                    encoding = normalize_text_encoding(RawString.SUPPORTED_ENCODINGS[-1])
                    result._source_replaced = True
            else:
                text, encoding = decode_game_text(payload, options)
        except UnicodeDecodeError as exc:
            raise PluginTextEncodingError(f"{locator}: cannot decode as {exc.encoding} at byte {exc.start}") from exc
        result.string = result._encoded = RawString.from_str(text, encoding)
        return result

    def encode_text(self, text: str, encoding: str) -> bytes:
        """Check encoding and inline field size before making any changes."""
        if not self._source_replaced and text == self._encoded and encoding == self._encoded.encoding:
            return self.data
        try:
            data = encode_game_text(text, encoding) + self._suffix
        except ValueError as exc:
            reason = describe_game_text_error(exc)
            raise PluginTextEncodingError(f"{self._locator}: cannot encode as {encoding}: {reason}") from exc
        if len(data) > 0xFFFF:
            raise PluginTextEncodingError(f"{self._locator}: encoded field exceeds 65535 bytes in {encoding}")
        return data

    def set_encoded_text(self, text: str, encoding: str, data: bytes) -> None:
        self.string = self._encoded = RawString.from_str(text, encoding)
        self.data = data
        self._source_replaced = False

    def dump(self) -> bytes:
        # The dependency exposes mutable strings through set_string/replace_strings.
        # Those callers inherit the field's codec and use the same strict byte writer.
        if self.string is not self._encoded:
            encoding = self.string.encoding
            self.set_encoded_text(str(self.string), encoding, self.encode_text(str(self.string), encoding))
        return Subrecord.dump(self)


def apply_input_encoding(record: Record, options: TextReadOptions, plugin_name: str) -> int:
    """Read editable text from its original payload, leaving localized IDs intact."""
    fallback_count = 0
    for index, field in enumerate(record.subrecords):
        if isinstance(field, StringSubrecord) and isinstance(field.string, RawString):
            locator = f"{plugin_name} {record.type} {field.type} ({record.formid}, subrecord {index})"
            decoded = EncodedStringSubrecord.from_parsed(field, options, locator)
            record.subrecords[index] = decoded
            fallback_count += decoded.string.encoding != options.input_encoding
    return fallback_count
