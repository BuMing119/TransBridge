"""Offline estimates for admission, never provider usage or a guaranteed upper bound."""

import math
import re

ESTIMATOR_LABEL = "text-v2-estimated-25pct"
_PARTS = re.compile(r"[A-Za-z0-9_]+|[ \t\r\n]+|[^\x00-\x7f]|.")


def estimate_tokens(text: str) -> int:
    """Allow three ASCII word characters/token, punctuation separately, and Unicode bytes/2.

    A 25% margin covers ordinary mixed text and tool JSON. Unusual encodings can
    exceed this heuristic; only the provider can report actual token usage.
    """
    total = 0.0
    for part in _PARTS.finditer(text):
        value = part.group()
        if not value.isascii():
            total += len(value.encode("utf-8")) / 2
        elif value[0].isalnum() or value[0] == "_" or value.isspace():
            total += math.ceil(len(value) / 3)
        else:
            total += 1
    return math.ceil(total * 1.25)


def select_estimator(model: str):
    """Use only an already loaded exact-model encoding; never load/download assets."""
    try:
        from tiktoken.model import encoding_name_for_model
        from tiktoken.registry import ENCODINGS
    except ImportError:
        return estimate_tokens, ESTIMATOR_LABEL
    try:
        name = encoding_name_for_model(model)
    except KeyError:
        return estimate_tokens, ESTIMATOR_LABEL
    encoding = ENCODINGS.get(name)
    if encoding is None:
        return estimate_tokens, ESTIMATOR_LABEL
    return (
        lambda text: math.ceil(len(encoding.encode(text, disallowed_special=())) * 1.25),
        f"{name}-estimated-25pct",
    )
