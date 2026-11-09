"""Verify an ambiguous durable save against one read transaction."""

from __future__ import annotations

import json
import sqlite3

from transbridge.application.projects.models import LifecycleSave
from transbridge.persistence.v2.ids import ProjectRef
from transbridge.persistence.v2.variant import SourceFingerprint

from .entry_codec import encode_json
from .reads import read_entries, read_sources, require_variant
from .receipts import receipt_row, request_payload
from .records import project_settings, read_project, read_variant


def save_matches(connection: sqlite3.Connection, save: LifecycleSave) -> bool:
    snapshot = save.variant
    if snapshot is None or snapshot.ref != save.formal_variant_ref:
        return False
    project = read_project(connection, ProjectRef(snapshot.ref.project_id))
    if (
        project.envelope.revision != save.project.envelope.revision
        or any(
            project.envelope.data[key] != save.project.envelope.data[key]
            for key in ("name", "variant_ids", "variant_names", "active_variant_id")
        )
        or project_settings(project) != project_settings(save.project)
    ):
        return False
    variant = require_variant(connection, snapshot.ref.identity)
    if (
        variant["revision"] != snapshot.revision
        or json.loads(variant["label_library"]) != snapshot.label_library_data()
    ):
        return False
    for operation_id, outcomes in save.applications:
        receipt = receipt_row(connection, operation_id)
        if (
            receipt is None
            or receipt["variant_id"] != snapshot.ref.identity.value
            or receipt["request"] != encode_json(request_payload(outcomes))
        ):
            return False
    if isinstance(snapshot.entries, tuple):
        return read_variant(connection, snapshot.ref)[0] == snapshot
    fingerprints = tuple(
        sorted(
            (
                SourceFingerprint(source.namespace, source.fingerprint)
                for source in read_sources(connection, native_fields=False)
            ),
            key=lambda item: item.namespace.value,
        )
    )
    if fingerprints != snapshot.source_fingerprints or save.entry_changes is None:
        return False
    keys = (entry.entry_key for entry in save.entry_changes)
    actual = {row.state.entry_key: row.state for row in read_entries(connection, snapshot.ref.identity, keys)}
    return actual == {entry.entry_key: entry for entry in save.entry_changes}
