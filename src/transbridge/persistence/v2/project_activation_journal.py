"""Durable publication of a Project's Variant catalog and active pointer.

Prepared manifests recover to exact preimages; committed manifests recover to
verified targets. Unknown content fences all writers instead of being replaced.
Baseline registrations are process-local projections: they participate before
the commit marker and are rebuilt from source descriptors when a process reopens.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os

from transbridge.application.contracts import DomainError, ErrorCategory

from .atomic_documents import AtomicDocumentStore
from .ids import ProjectId, ProjectRef, VariantId, VariantRef
from .models import LoadedRecord
from .project_record_guard import require_record_version, verified_preimage
from .schema import parse_json_bytes, serialize_document, validate_v2

_LOGGER = logging.getLogger(__name__)


class ProjectActivationProtocol:
    def __init__(self, root, filesystem, projects, variants):
        self._filesystem = filesystem
        self._documents = AtomicDocumentStore(root, filesystem)
        self._projects = projects
        self._variants = variants
        self._lock = projects.mutation_lock
        self._directory = self._documents.path("project-activation-journal")
        self.recover_pending()
        self._lock.add_recovery(self.recover_pending)

    def commit(self, activation, transaction_id, *, publish_baselines=None, restore_baselines=None):
        with self._lock:
            self.recover_pending()
            records = self._records(activation)
            if not records:
                return
            document = {"schema_version": 1, "transaction_id": transaction_id, "phase": "prepared", "records": records}
            path = self._documents.path(os.path.join("project-activation-journal", self._filename(transaction_id)))
            if self._filesystem.exists(path):
                raise _conflict("A transaction with this identity already has a recovery journal.")
            try:
                self._write_manifest(path, document, "prepare")
                for index, record in enumerate(records):
                    self._replace(record, target=True, token=f"{transaction_id}-publish-{index}")
                if publish_baselines is not None:
                    publish_baselines()
                document["phase"] = "committed"
                self._write_manifest(path, document, "commit")
            except Exception:
                committed = False
                if self._filesystem.exists(path):
                    # A marker replace may succeed before its readback fails.
                    try:
                        committed = self._load(path)["phase"] == "committed"
                        self._recover(path, completing_commit=True)
                    finally:
                        if not committed and restore_baselines is not None:
                            restore_baselines()
                elif restore_baselines is not None:
                    restore_baselines()
                if committed:
                    return
                raise
            self._cleanup(path, transaction_id, committed=True)

    def recover_pending(self):
        with self._lock:
            for path in self._filesystem.list_files(self._directory):
                self._recover(path)

    def _records(self, activation):
        records = []
        for ref, revision, digest in activation.checked_variants:
            self._record(ref, None, revision, digest, write=False)
        project = activation.candidate_project
        if project is not None:
            project_ref = ProjectRef(ProjectId(project.envelope.identity))
            records.append(
                self._record(
                    project_ref,
                    project if activation.write_candidate_project else None,
                    activation.expected_project_revision,
                    activation.expected_project_hash,
                    write=activation.write_candidate_project,
                )
            )
        if activation.candidate_variant_ref is not None:
            records.append(
                self._record(
                    activation.candidate_variant_ref,
                    activation.candidate_variant.to_dto() if activation.write_candidate_variant else None,
                    activation.expected_variant_revision,
                    activation.expected_variant_hash,
                    write=activation.write_candidate_variant,
                )
            )
        if activation.removed_variant is not None:
            ref, revision, digest = activation.removed_variant
            records.append(self._record(ref, None, revision, digest, write=True))
        pointer_path = self._documents.path("active-project.json")
        pointer = {
            "schema_version": 1,
            "project_id": None if project is None else project.envelope.identity,
            "variant_id": None
            if activation.candidate_variant_ref is None
            else activation.candidate_variant_ref.identity.value,
            "source_ref": activation.source_ref,
        }
        records.append(_record_data("pointer", None, None, self._read(pointer_path), serialize_document(pointer)))
        return [record for record in records if record["previous"] != record["target"]]

    def _record(self, ref, target, revision, digest, *, write):
        repository = self._projects if isinstance(ref, ProjectRef) else self._variants
        path = repository.path_for(ref)
        current = None
        previous = None
        if self._filesystem.exists(path):
            previous = self._filesystem.read_bytes(path)
            value = validate_v2(parse_json_bytes(previous), ref)
            current = LoadedRecord(ref, value, _digest(previous))
        require_record_version(current, revision, digest, identity=ref.identity.value)
        if current is not None:
            previous = verified_preimage(self._filesystem, repository, ref, current)
        payload = previous if not write else None if target is None else serialize_document(target.envelope.to_dict())
        # Unchanged payloads are the validated, hash-verified preimage above.
        if write and payload is not None:
            validate_v2(parse_json_bytes(payload), ref)
        return _record_data(
            "project" if isinstance(ref, ProjectRef) else "variant",
            ref.identity.value,
            None if isinstance(ref, ProjectRef) else ref.project_id.value,
            previous,
            payload,
        )

    def _write_manifest(self, path, document, stage):
        document["digest"] = _manifest_digest(document)
        self._documents.write_bytes(
            path,
            serialize_document(document),
            f"{document['transaction_id']}-{stage}",
            durable=True,
        )
        if self._load(path) != document:
            raise _conflict("The activation recovery journal could not be verified.")

    def _load(self, path):
        try:
            document = json.loads(self._filesystem.read_bytes(path))
            if set(document) != {"schema_version", "transaction_id", "phase", "records", "digest"}:
                raise ValueError("invalid manifest shape")
            if document["schema_version"] != 1 or document["phase"] not in {"prepared", "committed"}:
                raise ValueError("unsupported manifest")
            if _manifest_digest(document) != document["digest"]:
                raise ValueError("manifest checksum mismatch")
            if os.path.basename(path) != self._filename(document["transaction_id"]):
                raise ValueError("manifest identity mismatch")
            if not isinstance(document["records"], list) or not 1 <= len(document["records"]) <= 4:
                raise ValueError("invalid activation participants")
            paths = set()
            for record in document["records"]:
                if not isinstance(record, dict) or set(record) != {
                    "kind",
                    "identity",
                    "project_id",
                    "previous",
                    "previous_digest",
                    "target",
                    "target_digest",
                }:
                    raise ValueError("invalid record shape")
                destination = self._path(record)
                if destination in paths:
                    raise ValueError("duplicate record")
                paths.add(destination)
                for field in ("previous", "target"):
                    value = _decode(record[field])
                    if _digest(value) != record[f"{field}_digest"]:
                        raise ValueError("record checksum mismatch")
                    if value is not None:
                        parsed = parse_json_bytes(value)
                        if record["kind"] != "pointer":
                            validate_v2(parsed, self._ref(record))
                        else:
                            _validate_pointer(parsed)
            self._validate_participants(document["records"])
            return document
        except Exception as exc:
            raise _conflict("Invalid activation journal; preserve it for recovery.", path=path) from exc

    def _validate_participants(self, records):
        pointers = [record for record in records if record["kind"] == "pointer"]
        projects = [record for record in records if record["kind"] == "project"]
        variants = [record for record in records if record["kind"] == "variant"]
        if len(pointers) > 1 or len(projects) > 1:
            raise ValueError("ambiguous activation participants")
        parent_ids = {record["project_id"] for record in variants}
        parent_ids.update(record["identity"] for record in projects)
        if len(parent_ids) > 1:
            raise ValueError("activation cannot mutate multiple Projects")
        if projects:
            project = projects[0]
            if project["previous"] is None or project["target"] is None:
                raise ValueError("activation cannot create or delete a Project")
            target_project = parse_json_bytes(_decode(project["target"]))
            data = target_project["data"]
            for variant in variants:
                declared = variant["identity"] in data["variant_ids"]
                if (variant["target"] is not None) != declared:
                    raise ValueError("Variant publication disagrees with its Project catalog")
            if pointers:
                pointer = parse_json_bytes(_decode(pointers[0]["target"]))
                if (pointer["project_id"], pointer["variant_id"]) != (project["identity"], data["active_variant_id"]):
                    raise ValueError("active pointer disagrees with its Project")
        if pointers:
            if pointers[0]["target"] is None:
                raise ValueError("activation pointer must have an explicit target")
            pointer = parse_json_bytes(_decode(pointers[0]["target"]))
            if variants and pointer["project_id"] not in parent_ids:
                raise ValueError("active pointer targets another Project")
        if any(record["previous"] is None and record["target"] is None for record in records):
            raise ValueError("empty activation record")

    def _recover(self, path, *, completing_commit=False):
        document = self._load(path)
        committed = document["phase"] == "committed"
        # Validate every participant first: never partly roll back a manifest
        # that already conflicts with a later external write.
        for record in document["records"]:
            self._require_known(record)
        records = document["records"] if committed else reversed(document["records"])
        for index, record in enumerate(records):
            self._replace(record, target=committed, token=f"{document['transaction_id']}-recover-{index}")
        self._cleanup(path, document["transaction_id"], committed=committed and completing_commit)
        return document["phase"]

    def _replace(self, record, *, target, token):
        self._require_known(record)
        path = self._path(record)
        payload = _decode(record["target" if target else "previous"])
        if self._read(path) == payload:
            return
        if payload is None:
            self._documents.remove_durable(path, token)
        else:
            self._documents.write_bytes(path, payload, token, durable=True)
        if self._read(path) != payload:
            raise _conflict("Activation publication verification failed.", path=path)

    def _require_known(self, record):
        path = self._path(record)
        current = _digest(self._read(path))
        if current not in {record["previous_digest"], record["target_digest"]}:
            raise _conflict(
                "Activation recovery conflicts with newer content; automatic writes are blocked.", path=path
            )

    def _cleanup(self, path, token, *, committed):
        try:
            self._documents.remove_durable(path, token)
        except Exception:
            if not committed:
                raise
            _LOGGER.warning("Committed activation journal cleanup is pending: %s", path, exc_info=True)

    def _path(self, record):
        if record["kind"] == "pointer":
            if record["identity"] is not None or record["project_id"] is not None:
                raise ValueError("pointer cannot have a record identity")
            return self._documents.path("active-project.json")
        ref = self._ref(record)
        return (self._projects if isinstance(ref, ProjectRef) else self._variants).path_for(ref)

    @staticmethod
    def _ref(record):
        if record["kind"] == "project" and record["project_id"] is None:
            return ProjectRef(ProjectId(record["identity"]))
        if record["kind"] == "variant":
            return VariantRef(VariantId(record["identity"]), ProjectId(record["project_id"]))
        raise ValueError("invalid activation record kind")

    def _read(self, path):
        return self._filesystem.read_bytes(path) if self._filesystem.exists(path) else None

    @staticmethod
    def _filename(transaction_id):
        if not isinstance(transaction_id, str) or not transaction_id or len(transaction_id) > 256:
            raise ValueError("invalid transaction identity")
        return f"{hashlib.sha256(transaction_id.encode()).hexdigest()}.json"


def _record_data(kind, identity, project_id, previous, target):
    return {
        "kind": kind,
        "identity": identity,
        "project_id": project_id,
        "previous": _encode(previous),
        "previous_digest": _digest(previous),
        "target": _encode(target),
        "target_digest": _digest(target),
    }


def _encode(value):
    return None if value is None else base64.b64encode(value).decode("ascii")


def _decode(value):
    return None if value is None else base64.b64decode(value.encode("ascii"), validate=True)


def _digest(value):
    return None if value is None else hashlib.sha256(value).hexdigest()


def _manifest_digest(document):
    return _digest(serialize_document({key: value for key, value in document.items() if key != "digest"}))


def _conflict(message, *, path=None):
    return DomainError(ErrorCategory.CONFLICT, "PROJECT_ACTIVATION_RECOVERY_REQUIRED", message, details={"path": path})


def _validate_pointer(document):
    required = {"schema_version", "project_id", "variant_id"}
    if not required <= set(document) <= required | {"source_ref"} or document["schema_version"] != 1:
        raise ValueError("invalid active pointer shape")
    project = document["project_id"]
    variant = document["variant_id"]
    if project is None:
        if variant is not None:
            raise ValueError("detached active Variant")
    else:
        project_id = ProjectId(project)
        if variant is not None:
            VariantRef(VariantId(variant), project_id)
    if document.get("source_ref") is not None and not isinstance(document["source_ref"], str):
        raise ValueError("invalid pointer source reference")
