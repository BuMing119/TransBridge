"""Safe JSON codec for application terminology dataclasses."""

from __future__ import annotations

from dataclasses import MISSING, fields, is_dataclass
from enum import Enum
import json
from typing import Any

from transbridge.application.terminology import models

_DATACLASSES = {
    name: value
    for name in models.__all__
    if is_dataclass(value := getattr(models, name, None)) and isinstance(value, type)
}
_ENUMS = {
    name: value
    for name in models.__all__
    if isinstance((value := getattr(models, name, None)), type) and issubclass(value, Enum)
}


def dumps(value: Any) -> str:
    return json.dumps(_encode(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads[T](payload: str, expected_type: type[T]) -> T:
    try:
        value = _decode(json.loads(payload))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid terminology persistence payload") from exc
    if not isinstance(value, expected_type):
        raise ValueError(f"expected {expected_type.__name__}, found {type(value).__name__}")
    return value


def _encode(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "$type": type(value).__name__,
            "fields": {field.name: _encode(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, Enum):
        return {"$enum": type(value).__name__, "value": value.value}
    if isinstance(value, tuple):
        return {"$tuple": [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported terminology payload value: {type(value).__name__}")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "$tuple" in value:
        return tuple(_decode(item) for item in value["$tuple"])
    if "$enum" in value:
        return _ENUMS[value["$enum"]](value["value"])
    if "$type" in value:
        cls = _DATACLASSES[value["$type"]]
        raw_fields = value["fields"]
        decoded = {}
        for field in fields(cls):
            if field.name in raw_fields:
                decoded[field.name] = _decode(raw_fields[field.name])
            elif field.default is MISSING and field.default_factory is MISSING:
                raise KeyError(field.name)
        return cls(**decoded)
    return {key: _decode(item) for key, item in value.items()}


__all__ = ["dumps", "loads"]
