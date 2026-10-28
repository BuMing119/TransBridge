"""Reuse local schema executors within one validation, never input results."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator, validators
from referencing.jsonschema import DRAFT202012

_LOCAL_KEYWORDS = frozenset({
    "$schema",
    "type",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "minLength",
    "maxLength",
    "minimum",
    "enum",
    "const",
    "pattern",
    "uniqueItems",
    "format",
})
_DIALECT = Draft202012Validator.META_SCHEMA["$id"]


def _local_schemas(schema) -> dict[int, dict] | None:
    """Inspect schema positions only; enum/const objects are instance data."""
    nodes = {}
    visiting = set()

    def visit(node):
        if type(node) is bool:
            # Boolean schemas need the library's distinct descend diagnostics.
            return True
        if type(node) is not dict or id(node) in visiting:
            return False
        if id(node) in nodes:
            return True
        if not node.keys() <= _LOCAL_KEYWORDS or node.get("$schema", _DIALECT) != _DIALECT:
            return False
        visiting.add(id(node))
        properties = node.get("properties", {})
        if type(properties) is not dict or not all(visit(child) for child in properties.values()):
            return False
        for keyword in ("additionalProperties", "items"):
            if keyword in node and not visit(node[keyword]):
                return False
        visiting.remove(id(node))
        nodes[id(node)] = node
        return True

    return nodes if type(schema) is dict and visit(schema) else None


@dataclass(slots=True)
class _Execution:
    validator_type: type
    schemas: dict[int, dict]
    owners: dict[int, Any]
    children: dict[int, Any] = field(default_factory=dict)


_EXECUTION: ContextVar[_Execution | None] = ContextVar("persistence_schema_execution", default=None)


@contextmanager
def schema_execution(validator_type, schema, **configuration) -> Iterator[Any]:
    """Create one executor scope, restoring an outer scope even after failure.

    The constructor boundary distinguishes explicitly supplied resolvers from
    the resolver jsonschema normally creates itself. Unsupported schemas and
    resolver configurations retain standard execution for the entire call.
    """
    validator = validator_type(schema, **configuration)
    schemas = _local_schemas(schema)
    enabled = schemas is not None and not {"resolver", "_resolver"}.intersection(configuration)
    execution = (
        _Execution(validator_type, schemas, {id(validator): validator})
        if enabled and validator._ref_resolver is None
        else None
    )
    token = _EXECUTION.set(execution)
    try:
        yield validator
    finally:
        _EXECUTION.reset(token)


def local_schema_validator(handlers):
    """Extend only our own validator class; global Draft behavior is untouched."""
    validator_type = validators.extend(Draft202012Validator, handlers)
    standard_descend = validator_type.descend

    def descend(self, instance, schema, path=None, schema_path=None, resolver=None):
        execution = _EXECUTION.get()
        if (
            execution is None
            or type(schema) is not dict
            or type(self) is not execution.validator_type
            or execution.owners.get(id(self)) is not self
            or execution.schemas.get(id(schema)) is not schema
            or resolver is not None
            or self._ref_resolver is not None
        ):
            yield from standard_descend(self, instance, schema, path=path, schema_path=schema_path, resolver=resolver)
            return
        evolved = execution.children.get(id(schema))
        if evolved is None:
            child_resolver = self._resolver.in_subresource(DRAFT202012.create_resource(schema))
            evolved = self.evolve(schema=schema, _resolver=child_resolver)
            if type(evolved) is not execution.validator_type:
                yield from standard_descend(
                    self, instance, schema, path=path, schema_path=schema_path, resolver=resolver
                )
                return
            execution.children[id(schema)] = evolved
            execution.owners[id(evolved)] = evolved
        # iter_errors already supplies keyword, schema, instance and type-checker
        # details. Only the caller's relative path components remain to prepend.
        for error in evolved.iter_errors(instance):
            if path is not None:
                error.path.appendleft(path)
            if schema_path is not None:
                error.schema_path.appendleft(schema_path)
            yield error

    validator_type.descend = descend
    return validator_type
