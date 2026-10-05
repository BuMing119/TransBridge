"""Value-free, complete JSON Schema diagnostics ordered by actionable cause."""

from collections.abc import Iterable
import re
from typing import Any

from jsonschema.exceptions import ValidationError


def schema_validation_details(errors: Iterable[ValidationError], *, schema_name: str) -> dict[str, Any]:
    """Keep every rule failure without copying model text into error messages."""
    records = []
    for error in errors:
        record: dict[str, Any] = {
            "path": "/".join(str(part) for part in error.absolute_path) or "<root>",
            "validator": error.validator,
        }
        if error.validator == "required" and isinstance(error.instance, dict):
            record["missing_fields"] = sorted(key for key in error.validator_value if key not in error.instance)
        elif error.validator == "additionalProperties" and isinstance(error.instance, dict):
            properties = error.schema.get("properties", {})
            patterns = error.schema.get("patternProperties", {})
            record["unexpected_fields"] = sorted(
                key for key in error.instance if key not in properties and not any(re.search(p, key) for p in patterns)
            )
        if record not in records:
            records.append(record)
    priority = {"required": 0, "type": 1, "additionalProperties": 3}
    records.sort(key=lambda item: (priority.get(item["validator"], 2), item["path"]))
    if not records:
        return {"schema": schema_name, "errors": []}
    return {"schema": schema_name, **records[0], "errors": records}
