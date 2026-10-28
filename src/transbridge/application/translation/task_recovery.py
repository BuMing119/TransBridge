"""Project-local task identities and explicit continuation metadata, without credentials."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
from threading import RLock
import uuid

from transbridge.application.io.identity import EntryKey

from .task_history import atomic_json
from .task_recovery_storage import _FIXED, _MUTABLE, RecoveryStorage, recovery_summary
from .task_recovery_transaction import recovery_transaction

logger = logging.getLogger(__name__)
_RECOVERY_LOCK = RLock()


def _nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"task recovery {name} must be a non-empty string")
    return value


def _validate_fields(value):
    """Reject credential-shaped fields at any depth before writing extensible metadata."""
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("task recovery field names must be strings")
            normalized = key.lower().replace("-", "_")
            if any(
                part in normalized for part in ("password", "secret", "credential", "api_key", "apikey")
            ) or normalized in {"token", "access_token", "refresh_token", "authorization", "cookie", "headers"}:
                raise ValueError(f"task recovery must not store credential field {key!r}")
            _validate_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _validate_fields(child)


def _validate(record):
    if not isinstance(record, dict):
        raise ValueError("task recovery record must be an object")
    # Entry-result payloads are explicit business-data boundaries. Metadata may
    # legitimately contain fields such as a game's "token" or "secret"; only
    # execution/configuration metadata is checked for credential-shaped names.
    business_fields = (
        {
            "entries",
            "baseline_entries",
            "unmapped",
            "prepared_source",
            "candidate_rows",
            "raw_entries",
            "parse_unmapped",
            "input_snapshot",
        }
        if (
            record.get("result_kind") in {"download", "dictionary", "import", "source"}
            and record.get("results_only") is True
        )
        else set()
    )
    metadata = {key: value for key, value in record.items() if key not in business_fields}

    def source_configuration(sources):
        if not isinstance(sources, list):
            return sources
        return [
            {
                **source,
                "inputs": [
                    {key: value for key, value in item.items() if key != "entry"} if isinstance(item, dict) else item
                    for item in source.get("inputs", ())
                ],
            }
            if isinstance(source, dict)
            else source
            for source in sources
        ]

    # Only the serialized TranslationEntry envelope is business data. Adjacent
    # input metadata and every execution/configuration field remain protected.
    metadata["sources"] = source_configuration(record.get("sources"))
    if isinstance(record.get("execution_inputs"), dict):
        metadata["execution_inputs"] = {
            run: source_configuration(sources) for run, sources in record["execution_inputs"].items()
        }
    _validate_fields(metadata)
    for field in ("task_id", "project_id", "variant_id", "mode", "config_digest", "state", "created_at"):
        _nonempty(record.get(field), field)
    try:
        created = datetime.fromisoformat(record["created_at"])
    except ValueError as exc:
        raise ValueError("task recovery created_at must be an ISO timestamp") from exc
    if created.tzinfo is None:
        raise ValueError("task recovery created_at must include a timezone")
    sources = record.get("sources")
    if not isinstance(sources, list):
        raise ValueError("task recovery sources must be a list")
    seen_sources, seen_entries = set(), set()
    strict = record.get("recovery_contract") == 2
    if "recovery_contract" in record and not strict:
        raise ValueError("unsupported task recovery contract")
    if strict:
        runs = record.get("execution_runs")
        if (
            type(record.get("overwrite")) is not bool
            or not isinstance(runs, list)
            or not runs
            or any(not isinstance(run, str) or not run.strip() for run in runs)
            or len(runs) != len(set(runs))
        ):
            raise ValueError("task recovery execution chain is invalid")
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("task recovery source must be an object")
        key = _nonempty(source.get("key"), "source key")
        _nonempty(source.get("label"), "source label")
        if key in seen_sources:
            raise ValueError("task recovery source keys must be unique")
        seen_sources.add(key)
        source_entries = set()
        fields = ("translate_keys", "polish_keys") + (("postprocess_keys",) if "postprocess_keys" in source else ())
        for field in fields:
            if not isinstance(source.get(field), list):
                raise ValueError(f"task recovery {field} must be a list")
            for value in source[field]:
                if not isinstance(value, dict):
                    raise ValueError("task recovery entry identity must be an object")
                _nonempty(value.get("namespace"), "entry namespace")
                _nonempty(value.get("local_key"), "entry local_key")
                if value.get("original") is not None and not isinstance(value["original"], str):
                    raise ValueError("task recovery entry original must be a string")
                identity = EntryKey.from_dict(value)
                if identity in seen_entries:
                    raise ValueError("task recovery entry identities must be unique")
                seen_entries.add(identity)
                source_entries.add(identity)
        if strict:
            inputs = source.get("inputs")
            if not isinstance(inputs, list):
                raise ValueError("task recovery source inputs must be a list")
            input_keys = set()
            for item in inputs:
                if not isinstance(item, dict):
                    raise ValueError("task recovery input must be an object")
                try:
                    identity = EntryKey.from_dict(item["entry_key"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("task recovery input identity is invalid") from exc
                if identity in input_keys:
                    raise ValueError("task recovery inputs must be unique")
                input_keys.add(identity)
                if (
                    any(not isinstance(item.get(field), str) for field in ("original", "translation", "context"))
                    or type(item.get("stage")) is not int
                    or type(item.get("revision")) is not int
                    or item["revision"] < 0
                ):
                    raise ValueError("task recovery input content is invalid")
            if input_keys != source_entries:
                raise ValueError("task recovery inputs do not match source scope")
    if "checkpoint_settings" in record and not isinstance(record["checkpoint_settings"], dict):
        raise ValueError("task recovery checkpoint_settings must be an object")
    _validate_applied(record.get("applied_entries", []), seen_entries)
    # Normalize to detached JSON values and reject non-JSON objects/non-finite numbers.
    try:
        return json.loads(json.dumps(record, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("task recovery fields must contain valid JSON values") from exc


def _validate_applied(applied, seen_entries):
    if not isinstance(applied, list):
        raise ValueError("task recovery applied_entries must be a list")
    seen_applied = set()
    for row in applied:
        if not isinstance(row, dict) or not isinstance(row.get("entry_key"), dict):
            raise ValueError("task recovery applied entry must include an entry identity")
        try:
            key = EntryKey.from_dict(row["entry_key"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("task recovery applied entry identity is invalid") from exc
        if key not in seen_entries or key in seen_applied:
            raise ValueError("task recovery applied entries must be unique and belong to its scope")
        seen_applied.add(key)
        if (
            not all(isinstance(row.get(field), str) for field in ("original", "text"))
            or type(row.get("stage")) is not int
        ):
            raise ValueError("task recovery applied entry result is invalid")


class TaskRecoveryStore:
    """Atomic metadata per task; creating a new task never overwrites an old task."""

    def __init__(self, root: Path):
        self.root = Path(root)
        # Live execution, result review and history readers may hold separate
        # store instances for the same project. Serialize their read/merge/write.
        self._lock = _RECOVERY_LOCK
        self._lease = None
        self._storage = RecoveryStorage(self.root)
        self._saved_ledgers = {}
        self._scopes = {}

    def _remember_scope(self, record, manifest):
        references = tuple(manifest.get("_input_refs", {}).get("sources", ()))
        keys = frozenset(
            EntryKey.from_dict(key)
            for source in record["sources"]
            for name in ("translate_keys", "polish_keys", "postprocess_keys")
            for key in source.get(name, ())
        )
        self._scopes[record["task_id"]] = references, keys
        return keys

    def _scope(self, manifest, loaded):
        task_id = manifest["task_id"]
        references = tuple(manifest.get("_input_refs", {}).get("sources", ()))
        previous = self._scopes.get(task_id)
        if previous is not None and previous[0] == references:
            return previous[1]
        record = self._storage.unpack(manifest, fields={"sources"}, loaded=loaded)
        return self._remember_scope(record, manifest)

    def acquire(self, task_id: str):
        """Hold execution ownership until the run and all persistence workers stop."""
        from .task_recovery_lease import TaskRecoveryLease

        _nonempty(task_id, "task_id")
        if self._lease is not None:
            if self._lease.task_id != task_id:
                raise RuntimeError("恢复记录已属于其他执行任务")
            return
        self._lease = TaskRecoveryLease(self.root, task_id)

    def release(self):
        if self._lease is not None:
            self._lease.close()
            self._lease = None

    def save_results(self, task_id, rows):
        from .task_recovery_results import TaskRecoveryResults

        with self._lock, recovery_transaction(self.root, task_id):
            TaskRecoveryResults(self.root).merge(task_id, rows)

    def save_ledger_results(self, task_id, ledger):
        from .task_result_recovery import recovery_result_rows

        # Frozen result rows are immutable. Also capture their mutable entry's
        # relevant fields so direct ledger replacements/entry edits remain visible.
        evidence = {
            key: (
                row,
                (
                    row.before.original,
                    row.before.translation,
                    row.before.stage,
                    row.before.context,
                    row.before.revision.value,
                ),
            )
            for key, row in getattr(ledger, "entries", {}).items()
            if row.status == "succeeded"
        }
        with self._lock:
            previous = self._saved_ledgers.get(task_id)
            if (
                previous is not None
                and previous.keys() == evidence.keys()
                and all(
                    previous[key][0] is value[0] and previous[key][1] == value[1] for key, value in evidence.items()
                )
            ):
                return
            self.save_results(task_id, recovery_result_rows(ledger))
            self._saved_ledgers[task_id] = evidence

    def load_results(self, task_id):
        from .task_recovery_results import TaskRecoveryResults

        with self._lock, recovery_transaction(self.root, task_id):
            return TaskRecoveryResults(self.root).load(task_id)

    def _path(self, task_id):
        _nonempty(task_id, "task_id")
        return self.root / f"{hashlib.sha256(task_id.encode('utf-8')).hexdigest()}.json"

    def create(self, record: dict) -> dict:
        record = _validate(record)
        with self._lock, recovery_transaction(self.root, record["task_id"]):
            path = self._path(record["task_id"])
            if path.exists():
                raise FileExistsError(f"Task recovery record already exists: {record['task_id']}")
            temporary = path.parent / f".create-{uuid.uuid4().hex}.tmp"
            try:
                manifest = self._storage.pack(record)
                atomic_json(temporary, manifest)
                # Publish without replacing another process's record of the same task.
                os.link(temporary, path)
                self._remember_scope(record, manifest)
            finally:
                temporary.unlink(missing_ok=True)
        return record

    def load(self, task_id: str) -> dict:
        path = self._path(task_id)
        try:
            with self._lock, recovery_transaction(self.root, task_id):
                for attempt in range(2):
                    try:
                        manifest = json.loads(path.read_text(encoding="utf-8"))
                        record = _validate(self._storage.unpack(manifest))
                        break
                    except FileNotFoundError:
                        if attempt:
                            raise
            if record["task_id"] != task_id:
                raise ValueError("task identity does not match filename")
            if "_recovery_storage" in manifest:
                self._remember_scope(record, manifest)
            return record
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Cannot read task recovery {path}: {exc}") from exc

    def list_records(self) -> tuple[dict, ...]:
        records = []
        for path in self.root.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                record = self.load(record["task_id"])
                if self._path(record["task_id"]) != path:
                    raise ValueError("task identity does not match filename")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                logger.warning("Skipping unreadable task recovery %s; original retained: %s", path, exc, exc_info=True)
                continue
            records.append(record)
        return tuple(sorted(records, key=lambda item: item["created_at"], reverse=True))

    def update(self, task_id: str, **fields) -> dict:
        with self._lock:
            self.update_metadata(task_id, **fields)
            return self.load(task_id)

    def list_summaries(self, *, project_id=None, variant_id=None, states=None) -> tuple[dict, ...]:
        records = []
        for path in self.root.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict) or self._path(record.get("task_id")) != path:
                    raise ValueError("task identity does not match filename")
                for field in ("project_id", "variant_id", "state", "created_at"):
                    _nonempty(record.get(field), field)
                if (
                    (project_id is not None and str(record["project_id"]) != str(project_id))
                    or (variant_id is not None and str(record["variant_id"]) != str(variant_id))
                    or (states is not None and record["state"] not in states)
                ):
                    continue
                records.append(recovery_summary(record))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                logger.warning("Skipping unreadable task recovery %s; original retained: %s", path, exc)
        return tuple(sorted(records, key=lambda item: item["created_at"], reverse=True))

    def mutate_metadata(self, task_id, change, *, load_fields=()):
        """Serialize a read/merge/write using small state, without rehydrating fixed inputs."""
        with self._lock, recovery_transaction(self.root, task_id):
            path = self._path(task_id)
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("task_id") != task_id:
                raise ValueError("task identity does not match filename")
            if "_recovery_storage" not in manifest:
                # Compatibility conversion only at the storage boundary. The
                # original inline bytes remain available after atomic migration.
                legacy = _validate(manifest)
                backup = self.root / "legacy" / path.name
                if not backup.exists():
                    atomic_json(backup, manifest)
                manifest = self._storage.pack(legacy)
                self._remember_scope(legacy, manifest)
                migrated = True
            else:
                migrated = False
            loaded = {}
            metadata = self._storage.unpack(manifest, fields=set(load_fields), loaded=loaded)
            fields = change(metadata)
            if not isinstance(fields, dict):
                raise ValueError("task recovery metadata change must be an object")
            if any(not isinstance(key, str) for key in fields):
                raise ValueError("task recovery field names must be strings")
            if any(
                key.startswith("_")
                or (key == "task_id" and value != task_id)
                or (key == "created_at" and value != manifest["created_at"])
                for key, value in fields.items()
            ):
                raise ValueError("task recovery identity/storage fields are immutable")
            changed = {key for key, value in fields.items() if key not in metadata or metadata[key] != value}
            normalized = (
                json.loads(json.dumps({key: fields[key] for key in changed}, ensure_ascii=False, allow_nan=False))
                if changed
                else {}
            )
            fields.update(normalized)
            # Fixed inputs require the full contract validation when explicitly
            # changed; ordinary state transactions never traverse them.
            fixed_changes = changed & (_FIXED - _MUTABLE)
            if fixed_changes:
                full = self._storage.unpack(manifest, loaded=loaded)
                full.update(fields)
                _validate(full)
            else:
                business = (
                    {"entries", "unmapped", "candidate_rows"}
                    if (
                        manifest.get("results_only") is True
                        and manifest.get("result_kind") in {"download", "dictionary", "import", "source"}
                    )
                    else set()
                )
                _validate_fields({
                    key: value for key, value in fields.items() if key not in business | (_FIXED - _MUTABLE)
                })
                if "applied_entries" in changed:
                    _validate_applied(fields["applied_entries"], self._scope(manifest, loaded))
                for field in ("project_id", "variant_id", "mode", "state", "config_digest"):
                    if field in fields:
                        _nonempty(fields[field], field)
            merged = {**metadata, **fields}
            if changed:
                merged["updated_at"] = datetime.now(UTC).isoformat()
                manifest = self._storage.pack(merged, previous=manifest, changed=changed)
            if changed or migrated:
                atomic_json(path, manifest)
                self._storage.prune_state(manifest)
            summary = {
                key: value
                for key, value in recovery_summary(manifest).items()
                if key not in {"sources", "scope_count", "recovery_summary"}
            }
            return {**summary, **fields, **({"updated_at": merged["updated_at"]} if "updated_at" in merged else {})}

    def update_metadata(self, task_id, **fields):
        return self.mutate_metadata(task_id, lambda _previous: fields, load_fields=tuple(fields))
