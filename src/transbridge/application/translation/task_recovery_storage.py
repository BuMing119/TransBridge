"""Immutable recovery inputs and small, independently replaceable manifests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .task_history import atomic_json

_FIXED = frozenset({
    "sources",
    "execution_inputs",
    "execution_config",
    "reference_snapshot",
    "execution_references",
    "entries",
    "baseline_entries",
    "unmapped",
    "prepared_source",
    "candidate_rows",
    "raw_entries",
    "parse_unmapped",
    "input_snapshot",
    "applied_entries",
})
_MUTABLE = frozenset({"entries", "unmapped", "candidate_rows", "applied_entries", "entry_statuses"})
_FIXED |= _MUTABLE


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class RecoveryStorage:
    """Content references are published before their manifest; old generations remain readable."""

    def __init__(self, root: Path):
        self.root = root

    def _directory(self, task_id=None):
        if task_id is None:
            return self.root / "inputs"
        digest = hashlib.sha256(task_id.encode()).hexdigest()
        directory = self.root / "state" / digest[:16]
        owner = directory / "owner.json"
        if owner.exists() and json.loads(owner.read_text(encoding="utf-8")) != {"task_sha256": digest}:
            raise ValueError("task recovery state directory identity collision")
        return directory

    def _put(self, value, *, task_id=None):
        data = _encoded(value)
        digest = hashlib.sha256(data).hexdigest()
        directory = self._directory(task_id)
        if task_id is not None and not (directory / "owner.json").exists():
            atomic_json(directory / "owner.json", {"task_sha256": hashlib.sha256(task_id.encode()).hexdigest()})
        path = directory / f"{digest[:16]}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if hashlib.sha256(_encoded(existing)).hexdigest() != digest:
                raise ValueError("task recovery existing input checksum mismatch")
        else:
            atomic_json(path, value)
        return digest

    def _get(self, digest, loaded, *, task_id=None):
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("task recovery input reference is invalid")
        identity = (task_id, digest)
        if identity not in loaded:
            value = json.loads((self._directory(task_id) / f"{digest[:16]}.json").read_text(encoding="utf-8"))
            if hashlib.sha256(_encoded(value)).hexdigest() != digest:
                raise ValueError("task recovery fixed input checksum mismatch")
            loaded[identity] = value
        return loaded[identity]

    def pack(self, record, *, previous=None, changed=None):
        manifest = dict(record)
        references = dict((previous or {}).get("_input_refs", {}))
        written = {}

        def put_source(source):
            candidates = written.setdefault(source.get("key"), [])
            for previous_source, digest in candidates:
                if source == previous_source:
                    return digest
            digest = self._put(source)
            candidates.append((source, digest))
            return digest

        for field in _FIXED:
            if changed is not None and field not in changed:
                continue
            if field not in record:
                continue
            value = record[field]
            if field == "sources":
                references[field] = [put_source(source) for source in value]
                manifest["sources"] = [{"key": source["key"], "label": source["label"]} for source in value]
                manifest["scope_count"] = sum(
                    len(source.get(name, ()))
                    for source in value
                    for name in ("translate_keys", "polish_keys", "postprocess_keys")
                )
            elif field == "execution_inputs":
                references[field] = {run: [put_source(source) for source in sources] for run, sources in value.items()}
                manifest.pop(field, None)
            else:
                references[field] = self._put(value, task_id=record["task_id"] if field in _MUTABLE else None)
                manifest.pop(field, None)
        manifest["_recovery_storage"] = 1
        manifest["_input_refs"] = references
        return manifest

    def unpack(self, manifest, *, fields=None, loaded=None):
        if "_recovery_storage" not in manifest:
            return dict(manifest)
        if manifest.get("_recovery_storage") != 1 or not isinstance(manifest.get("_input_refs"), dict):
            raise ValueError("unsupported task recovery storage contract")
        record = {key: value for key, value in manifest.items() if key not in {"_recovery_storage", "_input_refs"}}
        loaded = {} if loaded is None else loaded
        for field, reference in manifest["_input_refs"].items():
            if field not in _FIXED:
                raise ValueError("task recovery input field is invalid")
            if fields is not None and field not in fields:
                continue
            if field == "sources":
                record[field] = [self._get(digest, loaded) for digest in reference]
            elif field == "execution_inputs":
                record[field] = {
                    run: [self._get(digest, loaded) for digest in sources] for run, sources in reference.items()
                }
            else:
                record[field] = self._get(reference, loaded, task_id=manifest["task_id"] if field in _MUTABLE else None)
        record.pop("scope_count", None) if fields is None else None
        return record

    def prune_state(self, manifest):
        """Only task-owned, superseded mutable generations are eligible for cleanup."""
        retained = {value[:16] for field, value in manifest["_input_refs"].items() if field in _MUTABLE} | {"owner"}
        for path in self._directory(manifest["task_id"]).glob("*.json"):
            if (
                len(path.stem) == 16
                and all(char in "0123456789abcdef" for char in path.stem)
                and path.stem not in retained
            ):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    # Cleanup failure must not turn a successfully published
                    # receipt into a reported save failure. Retry on next write.
                    import logging

                    logging.getLogger(__name__).warning(
                        "Cannot remove superseded recovery state %s", path, exc_info=True
                    )


def recovery_summary(record):
    """Produce detached display metadata without opening immutable inputs or results."""
    summary = {key: value for key, value in record.items() if key not in _FIXED and not key.startswith("_")}
    summary["sources"] = [{"key": source["key"], "label": source["label"]} for source in record["sources"]]
    summary["scope_count"] = record.get(
        "scope_count",
        sum(
            len(source.get(name, ()))
            for source in record["sources"]
            for name in ("translate_keys", "polish_keys", "postprocess_keys")
        ),
    )
    summary["recovery_summary"] = True
    return summary
