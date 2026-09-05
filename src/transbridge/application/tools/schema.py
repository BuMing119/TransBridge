"""Canonical JSON Schema conversion and argument validation."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import re
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
    code: str = "SCHEMA_CONSTRAINT_FAILED"
    keyword: str = ""
    schema_pointer: str = "/"
    expected: Any = None
    actual_type: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        """Return the stable, JSON-serializable diagnostic sent to tool consumers."""
        return {
            "path": self.pointer,
            "schema_path": self.schema_pointer,
            "keyword": self.keyword,
            "code": self.code,
            "expected": deepcopy(self.expected),
            "actual_type": self.actual_type,
            "message": self.message,
        }


_VALIDATION_CODES = {
    "required": "REQUIRED_FIELD_MISSING",
    "type": "TYPE_MISMATCH",
    "enum": "VALUE_NOT_ALLOWED",
    "const": "VALUE_NOT_ALLOWED",
    "minimum": "VALUE_BELOW_MINIMUM",
    "exclusiveMinimum": "VALUE_BELOW_MINIMUM",
    "maximum": "VALUE_ABOVE_MAXIMUM",
    "exclusiveMaximum": "VALUE_ABOVE_MAXIMUM",
    "minLength": "STRING_TOO_SHORT",
    "maxLength": "STRING_TOO_LONG",
    "pattern": "STRING_PATTERN_MISMATCH",
    "minItems": "ARRAY_TOO_SHORT",
    "maxItems": "ARRAY_TOO_LONG",
    "uniqueItems": "ARRAY_ITEMS_NOT_UNIQUE",
    "minProperties": "OBJECT_TOO_SMALL",
    "maxProperties": "OBJECT_TOO_LARGE",
    "additionalProperties": "UNKNOWN_FIELD",
    "multipleOf": "NUMBER_NOT_MULTIPLE",
}

_EXPECTED_VALUE_KEYWORDS = {
    "type",
    "enum",
    "const",
    "minimum",
    "exclusiveMinimum",
    "maximum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    "additionalProperties",
    "multipleOf",
}


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


def _append_json_pointer(pointer: str, part: Any) -> str:
    escaped = str(part).replace("~", "~0").replace("/", "~1")
    return f"/{escaped}" if pointer == "/" else f"{pointer}/{escaped}"


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return type(value).__name__


def _expected_value(keyword: str, validator_value: Any) -> Any:
    if keyword not in _EXPECTED_VALUE_KEYWORDS:
        return None
    return deepcopy(validator_value)


def _validation_message(keyword: str, validator_value: Any, actual_type: str) -> str:
    messages = {
        "type": f"参数类型错误: 期望 {validator_value}，实际 {actual_type}",
        "enum": "参数值不在允许范围内",
        "const": "参数值不等于要求值",
        "minimum": f"参数值小于最小值: {validator_value}",
        "exclusiveMinimum": f"参数值必须大于: {validator_value}",
        "maximum": f"参数值大于最大值: {validator_value}",
        "exclusiveMaximum": f"参数值必须小于: {validator_value}",
        "minLength": f"字符串长度小于最小值: {validator_value}",
        "maxLength": f"字符串长度大于最大值: {validator_value}",
        "pattern": "字符串不符合要求的格式",
        "minItems": f"数组元素少于最小数量: {validator_value}",
        "maxItems": f"数组元素超过最大数量: {validator_value}",
        "uniqueItems": "数组包含重复元素",
        "minProperties": f"对象字段少于最小数量: {validator_value}",
        "maxProperties": f"对象字段超过最大数量: {validator_value}",
        "additionalProperties": "包含未声明的参数字段",
        "multipleOf": f"参数值必须是 {validator_value} 的倍数",
    }
    return messages.get(keyword, f"参数不符合 schema 约束: {keyword}")


def _unexpected_properties(instance: Any, schema: Any) -> list[str]:
    if not isinstance(instance, Mapping) or not isinstance(schema, Mapping):
        return []
    properties = schema.get("properties", {})
    declared = set(properties) if isinstance(properties, Mapping) else set()
    patterns = schema.get("patternProperties", {})
    pattern_names = tuple(patterns) if isinstance(patterns, Mapping) else ()
    return sorted(
        str(name)
        for name in instance
        if name not in declared and not any(re.search(pattern, str(name)) for pattern in pattern_names)
    )


def validate_arguments(schema: Mapping[str, Any], arguments: Any) -> list[ArgumentValidationError]:
    """Return deterministic JSON Pointer diagnostics for invalid arguments."""

    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(arguments),
        key=lambda item: (
            _json_pointer(item.absolute_path),
            _json_pointer(item.absolute_schema_path),
            str(item.validator or ""),
        ),
    )
    results: list[ArgumentValidationError] = []
    for error in errors:
        pointer = _json_pointer(error.absolute_path)
        schema_pointer = _json_pointer(error.absolute_schema_path)
        keyword = str(error.validator or "schema")
        if error.validator == "required" and isinstance(error.instance, Mapping):
            missing = sorted(name for name in error.validator_value if name not in error.instance)
            for name in missing:
                results.append(
                    ArgumentValidationError(
                        pointer=_append_json_pointer(pointer, name),
                        message=f"缺少必需参数: {name}",
                        code="REQUIRED_FIELD_MISSING",
                        keyword=keyword,
                        schema_pointer=schema_pointer,
                        expected="present",
                        actual_type="missing",
                    )
                )
            continue
        if error.validator == "additionalProperties":
            unexpected = _unexpected_properties(error.instance, error.schema)
            for name in unexpected:
                results.append(
                    ArgumentValidationError(
                        pointer=_append_json_pointer(pointer, name),
                        message=f"未声明的参数字段: {name}",
                        code="UNKNOWN_FIELD",
                        keyword=keyword,
                        schema_pointer=schema_pointer,
                        expected=False,
                        actual_type=_json_type(error.instance[name]),
                    )
                )
            if unexpected:
                continue
        actual_type = _json_type(error.instance)
        results.append(
            ArgumentValidationError(
                pointer=pointer,
                message=_validation_message(keyword, error.validator_value, actual_type),
                code=_VALIDATION_CODES.get(keyword, "SCHEMA_CONSTRAINT_FAILED"),
                keyword=keyword,
                schema_pointer=schema_pointer,
                expected=_expected_value(keyword, error.validator_value),
                actual_type=actual_type,
            )
        )
    return sorted(results, key=lambda issue: (issue.pointer, issue.schema_pointer, issue.code))
