"""Minimal, deterministic protection for translation program syntax.

This module deliberately does not attempt to validate natural language.  It
only recognizes placeholders and explicit program/markup tokens whose loss
would make a translated value unusable by its consumer.
"""

from __future__ import annotations

import re

_PROTECTED_SYNTAX = re.compile(
    # A space after '%' is ambiguous with percentage prose, so do not treat it as a printf flag.
    r"%(?:\([^)]+\))?[-+#0']*(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:hh|h|ll|l|L|z|j|t)?[diuoxXfFeEgGaAcspn]"
    r"|\{(?:[A-Za-z_][\w.-]*|\d+)(?:![rsa])?(?::[^{}]*)?\}"
    r"|\$\{[A-Za-z_][\w.-]*\}"
    r"|</?[A-Za-z][^<>\r\n]*?/?>"
    r"|\[(?:pagebreak|br)\]"
    r"|\\(?:r\\n|[rn])",
    re.IGNORECASE,
)


def extract_protected_syntax(text: str) -> tuple[str, ...]:
    """Return a sorted multiset representation of protected syntax tokens."""

    return tuple(sorted(match.group(0) for match in _PROTECTED_SYNTAX.finditer(text or "")))


def protected_syntax_matches(source: str, translated: str) -> bool:
    """Return whether ``translated`` preserves every protected source token."""

    return extract_protected_syntax(source) == extract_protected_syntax(translated)


__all__ = ["extract_protected_syntax", "protected_syntax_matches"]
