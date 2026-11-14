# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Encode game text without changing the Unicode read back from the file."""

from itertools import zip_longest


def encode_game_text(text: str, encoding: str, *, allow_nul: bool = False) -> bytes:
    if not allow_nul and (nul := text.find("\x00")) >= 0:
        raise ValueError(f"NUL (U+0000) at character offset {nul}")
    payload = text.encode(encoding, errors="strict")
    # Some Windows codecs encode distinct Unicode characters to the same
    # bytes. Reject aliases that would silently change the text on reparse.
    if encoding != "utf-8":
        decoded = payload.decode(encoding, errors="strict")
        if decoded != text:
            for index, (before, after) in enumerate(zip_longest(text, decoded, fillvalue="")):
                if before != after:
                    raise ValueError(f"{_codepoint(before)} at character offset {index} decodes as {_codepoint(after)}")
    return payload


def describe_game_text_error(error: ValueError) -> str:
    if isinstance(error, UnicodeEncodeError):
        offending = error.object[error.start : min(error.end, error.start + 4)]
        codepoints = ", ".join(_codepoint(char) for char in offending)
        return f"{codepoints} at character offset {error.start}: {error.reason}"
    return str(error)


def _codepoint(char: str) -> str:
    return f"U+{ord(char):04X}" if char else "the end of the text"
