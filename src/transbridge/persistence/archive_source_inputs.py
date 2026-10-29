"""Persist source content explicitly supplied by older portable project bundles."""

import hashlib
from pathlib import Path

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectSourceRequest
from transbridge.application.projects.source_content import source_content_identity
from transbridge.application.projects.source_registry import legacy_source_role

from .project_archive_files import member_destination
from .v2.schema import parse_json_bytes


def persist_imported_source_inputs(archive, project, variants, owned, preparer, artifacts, context):
    """Parse the already-published package inputs, never an external reference.

    Captured files and their parsed results are published before the Project
    record. The caller's existing rollback owns the captured files; immutable
    artifacts may safely remain unreferenced after a failed transaction.
    """
    sources = project.envelope.data["sources"]
    pending = tuple(source for source in sources if not source.get("source_artifact_id"))
    if not pending:
        return
    manifest = parse_json_bytes(archive.read("archive.json"))
    locations = {item["source_id"]: item["member"] for item in manifest["sources"]}
    active_id = project.envelope.data.get("active_variant_id")
    fingerprints = next(
        (variant.source_fingerprints for variant in variants if variant.ref.identity.value == active_id), ()
    )
    for source in pending:
        captured = Path(member_destination(locations[source["source_id"]], owned))
        options = dict(source.get("format_options") or {})
        fingerprint = source.get("fingerprint")
        if fingerprint is None:
            with captured.open("rb") as stream:
                fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
        matches = {item.namespace.value for item in fingerprints if item.sha256 == fingerprint}
        recorded = options.get("source_namespace") or source_content_identity(source)
        namespace = recorded if recorded in matches else next(iter(matches)) if len(matches) == 1 else None
        if matches and (namespace is None or options.get("source_namespace", namespace) != namespace):
            raise DomainError(
                ErrorCategory.CONFLICT,
                "PROJECT_SOURCE_IDENTITY_AMBIGUOUS",
                "来源内容对应多个或冲突的已保存身份，无法安全恢复译文；请在恢复视图中核对来源。",
            )
        if namespace is not None:
            options["source_namespace"] = namespace
        prepared = preparer.prepare_source(
            ProjectSourceRequest(
                str(captured),
                format_hint=FormatId(source["format_id"]),
                expected_fingerprint=fingerprint,
                options=tuple(options.items()),
            ),
            context,
            role=legacy_source_role(source) or "primary",
            common_options=(),
        )
        descriptor = prepared.to_dict()
        artifact_id = descriptor.get("source_artifact_id")
        if artifact_id is None:
            if artifacts is None or prepared.hydration is None:
                raise ValueError("当前服务无法保存项目包提供的来源内容")
            artifact_id = artifacts.put(prepared)
        source["source_artifact_id"] = artifact_id
        source["fingerprint"] = prepared.baseline.fingerprint.sha256
        source["format_options"] = descriptor["format_options"]
        source.setdefault("legacy", {})["namespace"] = prepared.baseline.fingerprint.namespace.value
