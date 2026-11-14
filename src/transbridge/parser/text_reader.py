# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Decode one game text payload and report the codec actually used."""

from transbridge.config.text_encoding import (
    DEFAULT_TEXT_ENCODING,
    LEGACY_READ_POLICY,
    XTRANSLATOR_READ_POLICY,
    TextReadOptions,
    fallback_encoding,
)


def decode_game_text(payload: bytes, options: TextReadOptions) -> tuple[str, str]:
    primary = options.input_encoding
    try:
        return payload.decode(primary), primary
    except UnicodeDecodeError:
        fallback = (
            fallback_encoding(options.source_language)
            if primary == DEFAULT_TEXT_ENCODING and options.text_read_policy == XTRANSLATOR_READ_POLICY
            else None
        )
        if fallback is None:
            raise
    return payload.decode(fallback), fallback


def decode_strings_text(
    payload: bytes, options: TextReadOptions, *, legacy_linked: bool, length_prefixed: bool
) -> tuple[str, str]:
    if options.text_read_policy != LEGACY_READ_POLICY:
        return decode_game_text(payload, options)
    # The old standalone adapter and the old plugin lookup had separate
    # decoders. Preserve their Unicode when reproducing a saved source.
    if legacy_linked and length_prefixed:
        payload = payload.rstrip(b"\x00")
    if not legacy_linked and payload.startswith(b"\xef\xbb\xbf"):
        return payload.decode("utf-8-sig"), "utf-8-sig"
    try:
        return payload.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return payload.decode("cp1252", errors="replace" if legacy_linked else "strict"), "cp1252"
