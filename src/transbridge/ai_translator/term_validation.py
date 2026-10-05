"""Lexical validity for terminology admission and runtime use, without judging meaning."""

from functools import lru_cache
import re

from transbridge.application.translation.protected_syntax import extract_protected_syntax


def _visible_text(text: str) -> str:
    for token in extract_protected_syntax(text):
        text = text.replace(token, " ")
    return text


@lru_cache(maxsize=4096)
def valid_term_text(text: str) -> bool:
    """Reject punctuation or syntax alone; retain names, digits and unchanged abbreviations."""
    return any(character.isalnum() for character in _visible_text(text))


def valid_term_pair(term: str, translation: str) -> bool:
    return valid_term_text(term) and valid_term_text(translation)


def contains_term(text: str, term: str) -> bool:
    """Require verbatim evidence and complete English words within visible text."""
    if not valid_term_text(term):
        return False
    left = r"(?<!\w)" if term[0].isascii() and (term[0].isalnum() or term[0] == "_") else ""
    right = r"(?!\w)" if term[-1].isascii() and (term[-1].isalnum() or term[-1] == "_") else ""
    return re.search(left + re.escape(term) + right, _visible_text(text)) is not None
