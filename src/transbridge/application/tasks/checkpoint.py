"""Versioned, owner-scoped checkpoint contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
from typing import Any, Protocol, runtime_checkable

from .models import JobSpec, OwnerRef

CHECKPOINT_SCHEMA_VERSION = 1


class CheckpointError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CheckpointCorruptError(CheckpointError):
    pass


class CheckpointFutureSchemaError(CheckpointError):
    pass


class CheckpointMismatchError(CheckpointError):
    pass


class CheckpointRevisionError(CheckpointError):
    pass


class LegacyCheckpointError(CheckpointError):
    pass


@dataclass(frozen=True, slots=True)
class CheckpointFrontier:
    ready: tuple[str, ...] = ()
    running: tuple[str, ...] = ()
    completed: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all(isinstance(value, tuple) for value in (self.ready, self.running, self.completed)):
            raise TypeError("frontier collections must be tuples")
        all_nodes = (*self.ready, *self.running, *self.completed)
        if any(not isinstance(node, str) or not node.strip() for node in all_nodes):
            raise ValueError("frontier node ids must not be empty")
        if len(set(all_nodes)) != len(all_nodes):
            raise ValueError("frontier ready/running/completed sets must be disjoint")


@dataclass(frozen=True, slots=True)
class CheckpointExpectation:
    run_id: str
    owner: OwnerRef
    spec_fingerprint: str
    input_fingerprint: str


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    run_id: str
    owner: OwnerRef
    spec_fingerprint: str
    input_fingerprint: str
    revision: int
    frontier: CheckpointFrontier = field(default_factory=CheckpointFrontier)
    completed_entry_keys: tuple[str, ...] = ()
    completed_actions: tuple[str, ...] = ()
    completed_commit_ids: frozenset[str] = field(default_factory=frozenset)
    candidate_refs: tuple[str, ...] = ()
    branch_decisions: tuple[tuple[str, str], ...] = ()
    loop_counters: tuple[tuple[str, int], ...] = ()
    hitl_results: tuple[tuple[str, str], ...] = ()
    graph_results: tuple[tuple[str, Any], ...] = ()
    schema_version: int = CHECKPOINT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        tuple_fields = (
            self.completed_entry_keys,
            self.completed_actions,
            self.candidate_refs,
            self.branch_decisions,
            self.loop_counters,
            self.hitl_results,
            self.graph_results,
        )
        if not all(isinstance(value, tuple) for value in tuple_fields):
            raise TypeError("checkpoint record collections must be immutable tuples")
        if not isinstance(self.completed_commit_ids, frozenset):
            raise TypeError("completed_commit_ids must be a frozenset")
        for name, pairs in (
            ("branch_decisions", self.branch_decisions),
            ("loop_counters", self.loop_counters),
            ("hitl_results", self.hitl_results),
            ("graph_results", self.graph_results),
        ):
            if any(not isinstance(pair, tuple) or len(pair) != 2 for pair in pairs):
                raise TypeError(f"{name} entries must be immutable key/value tuples")
        for name, value in (
            ("run_id", self.run_id),
            ("owner_id", self.owner.owner_id),
            ("spec_fingerprint", self.spec_fingerprint),
            ("input_fingerprint", self.input_fingerprint),
        ):
            if not value or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("CheckpointRecord can only create the current schema")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 0:
            raise ValueError("checkpoint revision must not be negative")
        _require_non_empty_unique(self.completed_entry_keys, "completed_entry_keys")
        _require_non_empty_unique(self.completed_actions, "completed_actions")
        _require_non_empty_unique(tuple(self.completed_commit_ids), "completed_commit_ids")
        _require_non_empty_unique(self.candidate_refs, "candidate_refs")
        _require_unique(tuple(key for key, _ in self.branch_decisions), "branch_decisions")
        _require_unique(tuple(key for key, _ in self.loop_counters), "loop_counters")
        _require_unique(tuple(key for key, _ in self.hitl_results), "hitl_results")
        _require_unique(tuple(key for key, _ in self.graph_results), "graph_results")
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for _, value in self.loop_counters):
            raise ValueError("loop counters must not be negative")
        _require_non_empty_unique(tuple(key for key, _ in self.loop_counters), "loop_counters")
        for name, pairs in (
            ("branch_decisions", self.branch_decisions),
            ("hitl_results", self.hitl_results),
        ):
            _require_non_empty_unique(tuple(key for key, _ in pairs), name)
            if any(not isinstance(value, str) or not value.strip() for _, value in pairs):
                raise ValueError(f"{name} values must be non-empty strings")
        normalized_results = tuple((key, _canonical_json(value)) for key, value in self.graph_results)
        _require_non_empty_unique(tuple(key for key, _ in normalized_results), "graph_results")
        object.__setattr__(self, "graph_results", normalized_results)

    def accepts_commit(self, commit_id: str) -> bool:
        if not commit_id or not commit_id.strip():
            raise ValueError("commit_id must not be empty")
        return commit_id not in self.completed_commit_ids

    def mark_committed(self, commit_id: str) -> CheckpointRecord:
        if not self.accepts_commit(commit_id):
            return self
        return replace(
            self,
            revision=self.revision + 1,
            completed_commit_ids=self.completed_commit_ids | {commit_id},
        )

    def validate(self, expected: CheckpointExpectation) -> None:
        mismatches = []
        if self.run_id != expected.run_id:
            mismatches.append("run_id")
        if not self.owner.same_scope(expected.owner):
            mismatches.append("owner")
        if self.spec_fingerprint != expected.spec_fingerprint:
            mismatches.append("spec_fingerprint")
        if self.input_fingerprint != expected.input_fingerprint:
            mismatches.append("input_fingerprint")
        if mismatches:
            raise CheckpointMismatchError(
                "checkpoint_identity_mismatch",
                f"checkpoint does not match expected {', '.join(mismatches)}",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "owner": _owner_to_dict(self.owner),
            "spec_fingerprint": self.spec_fingerprint,
            "input_fingerprint": self.input_fingerprint,
            "revision": self.revision,
            "frontier": {
                "ready": list(self.frontier.ready),
                "running": list(self.frontier.running),
                "completed": list(self.frontier.completed),
            },
            "completed_entry_keys": list(self.completed_entry_keys),
            "completed_actions": list(self.completed_actions),
            "completed_commit_ids": sorted(self.completed_commit_ids),
            "candidate_refs": list(self.candidate_refs),
            "branch_decisions": dict(self.branch_decisions),
            "loop_counters": dict(self.loop_counters),
            "hitl_results": dict(self.hitl_results),
            "graph_results": {key: json.loads(value) for key, value in self.graph_results},
        }

    def to_json_bytes(self) -> bytes:
        try:
            return json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise CheckpointCorruptError("checkpoint_not_serializable", str(exc)) from exc

    @classmethod
    def from_dict(cls, value: Any) -> CheckpointRecord:
        if not isinstance(value, dict):
            raise CheckpointCorruptError("checkpoint_invalid_root", "checkpoint root must be an object")
        if "schema_version" not in value:
            raise LegacyCheckpointError(
                "checkpoint_legacy_non_atomic",
                "legacy checkpoint has no schema/identity envelope and cannot be resumed",
            )
        schema = _integer(value.get("schema_version"), "schema_version")
        if schema > CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointFutureSchemaError(
                "checkpoint_future_schema",
                f"checkpoint schema {schema} is newer than supported {CHECKPOINT_SCHEMA_VERSION}",
            )
        if schema < CHECKPOINT_SCHEMA_VERSION:
            raise LegacyCheckpointError(
                "checkpoint_legacy_schema",
                f"checkpoint schema {schema} requires an explicit read-only importer",
            )
        try:
            frontier_value = _mapping(value.get("frontier"), "frontier")
            record = cls(
                schema_version=schema,
                run_id=_string(value.get("run_id"), "run_id"),
                owner=_owner_from_dict(_mapping(value.get("owner"), "owner")),
                spec_fingerprint=_string(value.get("spec_fingerprint"), "spec_fingerprint"),
                input_fingerprint=_string(value.get("input_fingerprint"), "input_fingerprint"),
                revision=_integer(value.get("revision"), "revision"),
                frontier=CheckpointFrontier(
                    ready=_strings(frontier_value.get("ready"), "frontier.ready"),
                    running=_strings(frontier_value.get("running"), "frontier.running"),
                    completed=_strings(frontier_value.get("completed"), "frontier.completed"),
                ),
                completed_entry_keys=_strings(value.get("completed_entry_keys", []), "completed_entry_keys"),
                completed_actions=_strings(value.get("completed_actions", []), "completed_actions"),
                completed_commit_ids=frozenset(_strings(value.get("completed_commit_ids", []), "completed_commit_ids")),
                candidate_refs=_strings(value.get("candidate_refs", []), "candidate_refs"),
                branch_decisions=_string_pairs(value.get("branch_decisions", {}), "branch_decisions"),
                loop_counters=_integer_pairs(value.get("loop_counters", {}), "loop_counters"),
                hitl_results=_string_pairs(value.get("hitl_results", {}), "hitl_results"),
                graph_results=_result_pairs(value.get("graph_results", {})),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, CheckpointError):
                raise
            raise CheckpointCorruptError("checkpoint_invalid_record", str(exc)) from exc
        return record

    @classmethod
    def from_json_bytes(cls, payload: bytes) -> CheckpointRecord:
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CheckpointCorruptError("checkpoint_invalid_json", str(exc)) from exc
        return cls.from_dict(value)


@runtime_checkable
class CheckpointPort(Protocol):
    def save(self, record: CheckpointRecord) -> None: ...

    def load(
        self,
        run_id: str,
        *,
        expected: CheckpointExpectation | None = None,
    ) -> CheckpointRecord | None: ...

    def delete(self, run_id: str) -> bool: ...


def job_spec_fingerprint(specification: JobSpec) -> str:
    value = {
        "job_type": specification.job_type,
        "input_ref": specification.input_ref,
        "input_fingerprint": specification.input_fingerprint,
        "display_name": specification.display_name,
        "config_digest": specification.config_digest,
        "capabilities": {
            "pause": specification.capabilities.supports_pause,
            "resume": specification.capabilities.supports_resume,
            "cancel": specification.capabilities.supports_cancel,
            "checkpoint": specification.capabilities.supports_checkpoint,
        },
        "metadata": list(specification.metadata),
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _owner_to_dict(owner: OwnerRef) -> dict[str, Any]:
    return {
        "owner_id": owner.owner_id,
        "entrypoint": owner.entrypoint,
        "project_id": owner.project_id,
        "variant_id": owner.variant_id,
        "session_id": owner.session_id,
        "permissions": sorted(owner.permissions),
    }


def _owner_from_dict(value: dict[str, Any]) -> OwnerRef:
    return OwnerRef(
        owner_id=_string(value.get("owner_id"), "owner.owner_id"),
        entrypoint=_string(value.get("entrypoint"), "owner.entrypoint"),
        project_id=_optional_string(value.get("project_id"), "owner.project_id"),
        variant_id=_optional_string(value.get("variant_id"), "owner.variant_id"),
        session_id=_optional_string(value.get("session_id"), "owner.session_id"),
        permissions=frozenset(_strings(value.get("permissions", []), "owner.permissions")),
    )


def _require_unique(values: tuple[str, ...], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must contain unique values")


def _require_non_empty_unique(values: tuple[str, ...], name: str) -> None:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{name} must contain non-empty strings")
    _require_unique(values, name)


def _canonical_json(value: Any) -> str:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = value
    else:
        decoded = value
    if not isinstance(decoded, dict):
        raise ValueError("graph result must be a JSON object")
    try:
        return json.dumps(
            decoded,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"graph result is not canonical JSON: {exc}") from exc


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CheckpointCorruptError("checkpoint_invalid_field", f"{name} must be an object")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CheckpointCorruptError("checkpoint_invalid_field", f"{name} must be a non-empty string")
    return value


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _string(value, name)


def _integer(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CheckpointCorruptError("checkpoint_invalid_field", f"{name} must be an integer")
    return value


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CheckpointCorruptError("checkpoint_invalid_field", f"{name} must be an array")
    return tuple(_string(item, f"{name}[]") for item in value)


def _string_pairs(value: Any, name: str) -> tuple[tuple[str, str], ...]:
    mapping = _mapping(value, name)
    return tuple(sorted((_string(key, f"{name}.key"), _string(item, f"{name}.{key}")) for key, item in mapping.items()))


def _integer_pairs(value: Any, name: str) -> tuple[tuple[str, int], ...]:
    mapping = _mapping(value, name)
    pairs = ((_string(key, f"{name}.key"), _integer(item, f"{name}.{key}")) for key, item in mapping.items())
    return tuple(sorted(pairs))


def _result_pairs(value: Any) -> tuple[tuple[str, dict[str, Any]], ...]:
    mapping = _mapping(value, "graph_results")
    result = []
    for key, item in mapping.items():
        if not isinstance(item, dict):
            raise CheckpointCorruptError(
                "checkpoint_invalid_field",
                f"graph_results.{key} must be an object",
            )
        result.append((_string(key, "graph_results.key"), item))
    return tuple(sorted(result))
