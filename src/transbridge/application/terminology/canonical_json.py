"""Encode normalized canonical values without one unbounded sequence C call."""

from __future__ import annotations

import json
from typing import Any

_SEQUENCE_CHUNK_SIZE = 128
_SCAN_NODE_BUDGET = 512
_ENCODER = json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def encode_normalized_json(value: Any) -> str:
    """Preserve JSONEncoder bytes, batching large normalized lists.

    Normalization must finish before entering this function: custom canonical
    payloads retain their original evaluation order. The batch size bounds the
    usual flat record sequences, not an arbitrary giant individual value.
    """
    return "".join(_parts(value))


def _requires_chunking(value: Any) -> bool:
    remaining, pending = _SCAN_NODE_BUDGET, [value]
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            if len(item) > _SEQUENCE_CHUNK_SIZE:
                return True
            children = item
        elif isinstance(item, dict):
            children = item.values()
        else:
            continue
        remaining -= len(children)
        if remaining < 0:
            return True
        pending.extend(children)
    return False


def _parts(value: Any):
    if not _requires_chunking(value):
        yield _ENCODER.encode(value)
    elif isinstance(value, dict):
        yield "{"
        for index, key in enumerate(sorted(value)):
            if index:
                yield ","
            yield _ENCODER.encode(key)
            yield ":"
            yield from _parts(value[key])
        yield "}"
    else:
        yield "["
        for start in range(0, len(value), _SEQUENCE_CHUNK_SIZE):
            if start:
                yield ","
            chunk = value[start : start + _SEQUENCE_CHUNK_SIZE]
            if len(value) <= _SEQUENCE_CHUNK_SIZE:
                for index, item in enumerate(chunk):
                    if index:
                        yield ","
                    yield from _parts(item)
            else:
                yield _ENCODER.encode(chunk)[1:-1]
        yield "]"
