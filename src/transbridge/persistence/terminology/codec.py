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
_FIELD_LAYOUTS = {
    cls: tuple((field.name, field.default is MISSING and field.default_factory is MISSING) for field in fields(cls))
    for cls in _DATACLASSES.values()
}
_FIELD_NAMES = {cls: tuple(name for name, _ in layout) for cls, layout in _FIELD_LAYOUTS.items()}
_SCALAR_TYPES = frozenset({str, int, float, bool, type(None)})
_OBJECT_BOUNDARY_MIN_CHARS = 1_000_000


def dumps(value: Any) -> str:
    return json.dumps(_encode(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads[T](payload: str, expected_type: type[T]) -> T:
    try:
        # A Python boundary per object lets other threads run during large C
        # scans. Keep the raw tree intact here so unknown fields stay ignored.
        hook = _object_boundary if len(payload) >= _OBJECT_BOUNDARY_MIN_CHARS else None
        value = _decode(json.loads(payload, object_hook=hook))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid terminology persistence payload") from exc
    if not isinstance(value, expected_type):
        raise ValueError(f"expected {expected_type.__name__}, found {type(value).__name__}")
    return value


def _object_boundary(value: dict) -> dict:
    return value


def _encode(value: Any) -> Any:
    value_type = type(value)
    # Exact builtin types only: StrEnum and IntEnum must retain their tags.
    if value_type in _SCALAR_TYPES:
        return value
    if is_dataclass(value) and not isinstance(value, type):
        names = _FIELD_NAMES.get(value_type)
        if names is None:
            names = tuple(field.name for field in fields(value))
        return {
            "$type": value_type.__name__,
            "fields": {name: _encode(getattr(value, name)) for name in names},
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
    """Consume the private parsed tree as typed values replace its objects."""
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _decode(item)
        return value
    if not isinstance(value, dict):
        return value
    if "$tuple" in value:
        items = value["$tuple"]
        value.clear()
        return tuple(_consume_items(items))
    if "$enum" in value:
        cls, item = _ENUMS[value["$enum"]], value["value"]
        value.clear()
        return cls(item)
    if "$type" in value:
        cls = _DATACLASSES[value["$type"]]
        raw_fields = value["fields"]
        value.clear()
        decoded = {}
        for name, required in _FIELD_LAYOUTS[cls]:
            if name in raw_fields:
                item = raw_fields.pop(name) if isinstance(raw_fields, dict) else raw_fields[name]
                decoded[name] = _decode(item)
            elif required:
                raise KeyError(name)
        if isinstance(raw_fields, dict):
            raw_fields.clear()
        return cls(**decoded)
    result = {key: _decode(item) for key, item in value.items()}
    value.clear()
    return result


def _consume_items(items: Any):
    if isinstance(items, list):
        for index, item in enumerate(items):
            items[index] = None
            yield _decode(item)
        items.clear()
    else:
        # Preserve the existing tag behavior for non-list JSON values too.
        yield from (_decode(item) for item in items)


__all__ = ["dumps", "loads"]
