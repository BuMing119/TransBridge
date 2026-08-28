"""Canonical JSON Schema conversion and argument validation."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


class ToolSchemaError(ValueError):
    """A canonical tool schema is invalid and application startup must fail."""


class LegacySchemaConversionError(ValueError):
    """A legacy per-parameter schema cannot be converted safely."""


_LEGACY_TYPES = {
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "list": "array",
    "array": "array",
    "dict": "object",
    "object": "object",
}
_LEGACY_ALLOWED_KEYS = {
    "default",
    "description",
    "enum",
    "items",
    "max",
    "max_length",
    "min",
    "min_length",
    "required",
    "type",
}


@dataclass(frozen=True, slots=True)
class ArgumentValidationError:
    pointer: str
    message: str


def _is_canonical_schema(raw: Mapping[str, Any]) -> bool:
    """Distinguish root schema keywords from identically named legacy params."""

    if isinstance(raw.get("type"), (str, list)):
        return True
    if isinstance(raw.get("required"), list):
        return True
    if isinstance(raw.get("description"), str) or isinstance(raw.get("title"), str):
        return True
    if any(key in raw for key in ("$id", "$schema", "$ref", "const", "not")):
        return True
    if any(isinstance(raw.get(key), list) for key in ("allOf", "anyOf", "oneOf")):
        return True
    if isinstance(raw.get("properties"), Mapping):
        return True
    return any(key in raw for key in ("additionalProperties", "unevaluatedProperties"))


def _validate_schema(schema: Mapping[str, Any]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ToolSchemaError(str(exc)) from exc


def canonicalize_parameters(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Convert legacy per-parameter declarations into an object JSON Schema.

    Canonical input is validated strictly. Unsupported legacy extensions are not
    guessed; callers can mark that tool capability unavailable.
    """

    if raw is None or not raw:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    if not isinstance(raw, Mapping):
        raise LegacySchemaConversionError("parameters must be a mapping")

    if _is_canonical_schema(raw):
        schema = deepcopy(dict(raw))
        _validate_schema(schema)
        root_type = schema.get("type", "object")
        if root_type != "object":
            raise ToolSchemaError("tool parameter schema root type must be object")
        schema.setdefault("type", "object")
        return schema

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, declaration in raw.items():
        if not isinstance(name, str) or not isinstance(declaration, Mapping):
            raise LegacySchemaConversionError(f"invalid declaration for {name!r}")
        unknown = set(declaration) - _LEGACY_ALLOWED_KEYS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise LegacySchemaConversionError(f"unsupported legacy keywords for {name}: {names}")
        legacy_type = declaration.get("type", "str")
        json_type = _LEGACY_TYPES.get(str(legacy_type).lower())
        if json_type is None:
            raise LegacySchemaConversionError(f"unsupported legacy type for {name}: {legacy_type}")
        prop: dict[str, Any] = {"type": json_type}
        if description := declaration.get("description"):
            prop["description"] = description
        if "default" in declaration:
            prop["default"] = deepcopy(declaration["default"])
        if "enum" in declaration:
            prop["enum"] = deepcopy(declaration["enum"])
        if "items" in declaration:
            items = declaration["items"]
            if isinstance(items, str):
                item_type = _LEGACY_TYPES.get(items.lower())
                if item_type is None:
                    raise LegacySchemaConversionError(f"unsupported legacy item type for {name}: {items}")
                prop["items"] = {"type": item_type}
            elif isinstance(items, Mapping):
                prop["items"] = deepcopy(dict(items))
            else:
                raise LegacySchemaConversionError(f"invalid items schema for {name}")
        if "min" in declaration:
            prop["minimum"] = declaration["min"]
        if "max" in declaration:
            prop["maximum"] = declaration["max"]
        if "min_length" in declaration:
            prop["minLength"] = declaration["min_length"]
        if "max_length" in declaration:
            prop["maxLength"] = declaration["max_length"]
        properties[name] = prop
        if declaration.get("required", True):
            required.append(name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    _validate_schema(schema)
    return schema


def _json_pointer(path: Any) -> str:
    parts = [str(part).replace("~", "~0").replace("/", "~1") for part in path]
    return "/" + "/".join(parts) if parts else "/"


def validate_arguments(schema: Mapping[str, Any], arguments: Any) -> list[ArgumentValidationError]:
    """Return deterministic JSON Pointer diagnostics for invalid arguments."""

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(arguments), key=lambda item: list(item.path))
    results: list[ArgumentValidationError] = []
    for error in errors:
        message = error.message
        if error.validator == "required" and isinstance(error.instance, Mapping):
            missing = [name for name in error.validator_value if name not in error.instance]
            if missing:
                message = f"缺少必需参数: {missing[0]}"
        elif error.validator == "type":
            message = f"参数类型错误: 期望 {error.validator_value}，实际 {type(error.instance).__name__}"
        results.append(
            ArgumentValidationError(
                pointer=_json_pointer(error.absolute_path),
                message=message,
            )
        )
    return results
