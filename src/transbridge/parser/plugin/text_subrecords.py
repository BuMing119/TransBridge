# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Apply the selected codec to editable plugin fields at their byte boundary."""

import logging

from sse_plugin_interface.datatypes import RawString
from sse_plugin_interface.record import Record
from sse_plugin_interface.subrecord import StringSubrecord, Subrecord

from transbridge.config.text_encoding import (
    DEFAULT_TEXT_ENCODING,
    LEGACY_READ_POLICY,
    XTRANSLATOR_READ_POLICY,
    XTRANSLATOR_WINDOWS_READ_POLICY,
    TextReadOptions,
    normalize_text_encoding,
)
from transbridge.parser.game_text import describe_game_text_error, encode_game_text
from transbridge.parser.text_reader import decode_game_text

log = logging.getLogger(__name__)


def _historical_text(string: RawString, payload: bytes) -> tuple[str, str, bool]:
    text = str(string)
    encoding = normalize_text_encoding(string.encoding)
    replaced = encoding == "utf-8" and "\ufffd" in text and text.encode(encoding) != payload
    if replaced:
        # RawString labels replacement decoding as UTF-8, even though it used
        # the final historical codec. Keep the real codec for strict writing.
        encoding = normalize_text_encoding(RawString.SUPPORTED_ENCODINGS[-1])
    return text, encoding, replaced


class PluginTextEncodingError(ValueError):
    """An editable plugin field cannot be represented in its selected encoding."""


class EncodedStringSubrecord(StringSubrecord):
    """Prefer the selected codec, retaining historical decoding as a final fallback."""

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
                text, encoding, result._source_replaced = _historical_text(field.string, payload)
            else:
                text, encoding = decode_game_text(payload, options)
                # Retain native replacement information for diagnostics; output
                # is always encoded strictly, including unchanged fields.
                result._source_replaced = encoding != "utf-8" and "\ufffd" in text
                if result._source_replaced:
                    log.warning(
                        "%s: Windows %s decoding used replacement characters; source bytes retained.", locator, encoding
                    )
        except (UnicodeDecodeError, LookupError, OSError) as exc:
            failure = (
                f"cannot decode as {exc.encoding} at byte {exc.start}"
                if isinstance(exc, UnicodeDecodeError)
                else str(exc)
            )
            if (
                options.text_read_policy not in {XTRANSLATOR_READ_POLICY, XTRANSLATOR_WINDOWS_READ_POLICY}
                or options.input_encoding != DEFAULT_TEXT_ENCODING
            ):
                raise PluginTextEncodingError(f"{locator}: {failure}") from exc
            # The vendor has already decoded this field with the old plugin
            # fallback sequence. Use it only after the current rules fail, so
            # successfully decoded sources retain their saved interpretation.
            text, encoding, result._source_replaced = _historical_text(field.string, payload)
            log.warning(
                "%s: %s; used historical %s decoding%s. Select an explicit input encoding if the text is incorrect.",
                locator,
                failure,
                encoding,
                " with replacement characters" if result._source_replaced else "",
            )
        result.string = result._encoded = RawString.from_str(text, encoding)
        return result

    def encode_text(self, text: str, encoding: str) -> bytes:
        """Check encoding and inline field size before making any changes."""
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
