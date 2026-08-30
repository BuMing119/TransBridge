"""Fail-closed typed JSON codec for terminology synchronization records."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import json
from typing import Any

from transbridge.application.terminology_sync import models

SYNC_PAYLOAD_SCHEMA_VERSION = 1

_DATACLASSES = {
    cls.__name__: cls
    for cls in (
        models.TerminologySyncTarget,
        models.TerminologySyncLine,
        models.TerminologySyncProfile,
        models.TerminologySyncBaseline,
        models.TerminologySyncItemLink,
        models.TerminologySyncRunRecord,
        models.TerminologySyncItemOutcomeRecord,
    )
}
_ENUMS = {
    cls.__name__: cls
    for cls in (
        models.TerminologySyncMode,
        models.TerminologyLossyPolicy,
        models.TerminologyDeletePolicy,
        models.TerminologySyncOwnership,
        models.TerminologySyncTombstone,
        models.TerminologySyncOutcome,
        models.TerminologySyncRunOutcome,
    )
}


def dumps_sync(value: object) -> str:
    if type(value) not in _DATACLASSES.values():
        raise TypeError(f"unsupported sync payload type: {type(value).__name__}")
    payload = {
        "schema_version": SYNC_PAYLOAD_SCHEMA_VERSION,
        "value": _encode(value),
    }
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def loads_sync[T](payload: str, expected_type: type[T]) -> T:
    try:
        raw = json.loads(payload)
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "value"}:
            raise ValueError("sync payload envelope is invalid")
        version = raw["schema_version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError("sync payload schema version is invalid")
        if version != SYNC_PAYLOAD_SCHEMA_VERSION:
            raise ValueError(f"unsupported sync payload schema {version}; supported {SYNC_PAYLOAD_SCHEMA_VERSION}")
        decoded = _decode(raw["value"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid terminology sync payload") from exc
    if not isinstance(decoded, expected_type):
        raise ValueError(f"expected {expected_type.__name__}, found {type(decoded).__name__}")
    return decoded


def _encode(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        type_name = type(value).__name__
        if type_name not in _DATACLASSES:
            raise TypeError(f"unsupported sync dataclass: {type_name}")
        return {
            "$type": type_name,
            "fields": {field.name: _encode(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, Enum):
        type_name = type(value).__name__
        if type_name not in _ENUMS:
            raise TypeError(f"unsupported sync enum: {type_name}")
        return {"$enum": type_name, "value": value.value}
    if isinstance(value, tuple):
        return {"$tuple": [_encode(item) for item in value]}
    if value is None or isinstance(value, str | int | bool):
        return value
    raise TypeError(f"unsupported sync payload value: {type(value).__name__}")


def _decode(value: Any) -> Any:
    if not isinstance(value, dict):
        if value is None or isinstance(value, str | int | bool):
            return value
        raise ValueError("sync payload contains an unsupported value")
    if set(value) == {"$tuple"}:
        items = value["$tuple"]
        if not isinstance(items, list):
            raise ValueError("sync tuple payload is invalid")
        return tuple(_decode(item) for item in items)
    if set(value) == {"$enum", "value"}:
        enum_type = _ENUMS.get(value["$enum"])
        if enum_type is None:
            raise ValueError("sync payload enum type is unsupported")
        return enum_type(value["value"])
    if set(value) == {"$type", "fields"}:
        cls = _DATACLASSES.get(value["$type"])
        raw_fields = value["fields"]
        if cls is None or not isinstance(raw_fields, dict):
            raise ValueError("sync payload dataclass type is unsupported")
        expected_fields = {field.name for field in fields(cls)}
        if set(raw_fields) != expected_fields:
            raise ValueError(f"sync payload fields do not match {cls.__name__}")
        return cls(**{name: _decode(item) for name, item in raw_fields.items()})
    raise ValueError("sync payload object is invalid")


__all__ = ["SYNC_PAYLOAD_SCHEMA_VERSION", "dumps_sync", "loads_sync"]
