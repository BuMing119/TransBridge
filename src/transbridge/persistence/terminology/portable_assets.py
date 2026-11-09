# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Portable, validated copies of immutable project-selected profile revisions."""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3

from transbridge.application.terminology_profiles.models import PublishedTerminologyProfile, TerminologyProfileContent
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.project_store.entry_codec import encode_json

from .profiles import _dump_content, _load_content
from .project_selection import read_project_selections
from .repository import SqliteTerminologyRepository
from .schema import initialize_schema

PROFILE_ASSET_MEMBER = "terminology-assets.json"


def export_profile_assets(project_database: Path, workspace_root: Path, project_id: str) -> dict | None:
    selections = read_project_selections(project_database, project_id)
    if not selections:
        return None
    repository = SqliteTerminologyRepository.open(str(workspace_root), project_id, writable=False)
    try:
        revisions = []
        for profile_id, revision in sorted({(item.profile_id, item.revision) for item in selections}):
            profile = repository.localization_profiles.get_published(profile_id, revision)
            if profile is None or profile.project_id != project_id:
                raise ValueError("Project references a missing published terminology profile")
            revisions.append({**asdict(profile), "content": json.loads(_dump_content(profile.content))})
    finally:
        repository.close()
    return {"schema": 1, "profiles": revisions}


def validate_profile_assets(payload, project_database, project_id):
    selections = read_project_selections(project_database, project_id)
    expected = {(item.profile_id, item.revision) for item in selections}
    if not expected and payload is None:
        return selections, ()
    if (
        not isinstance(payload, dict)
        or payload.keys() != {"schema", "profiles"}
        or type(payload["schema"]) is not int
        or payload["schema"] != 1
    ):
        raise ValueError("Portable terminology asset format is invalid")
    if not isinstance(payload["profiles"], list):
        raise ValueError("Portable terminology profiles must be a list")
    profiles = {}
    for row in payload["profiles"]:
        profile = _decode_profile(row, project_id)
        key = (profile.profile_id, profile.revision)
        if key in profiles:
            raise ValueError("Portable terminology profile revision is duplicated")
        profiles[key] = profile
    if profiles.keys() != expected or not expected:
        raise ValueError("Portable terminology assets differ from the project's selection references")
    return selections, tuple(profiles.values())


def install_profile_assets(payload, destination, project_database, original_project_id, imported_project_id):
    selections, profiles = validate_profile_assets(payload, project_database, original_project_id)
    if not profiles:
        return
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("Portable terminology database staging path is already occupied")
    _write_profiles(destination, imported_project_id, profiles)
    if original_project_id != imported_project_id:
        with project_connection(project_database, write=True) as connection:
            for selection in selections:
                rebased = replace(selection, project_id=imported_project_id)
                connection.execute(
                    "UPDATE variant SET terminology_selection = ? WHERE identity = ?",
                    (encode_json(asdict(rebased)), selection.variant_id),
                )


def _decode_profile(row, project_id):
    expected = {"profile_id", "project_id", "revision", "name", "content_digest", "content", "published_at"}
    if not isinstance(row, dict) or row.keys() != expected or row["project_id"] != project_id:
        raise ValueError("Portable terminology profile identity is invalid")
    content = _load_content(json.dumps(row["content"], ensure_ascii=False))
    return PublishedTerminologyProfile(**{**row, "content": content})


def _write_profiles(destination, project_id, profiles):
    latest = {}
    for profile in profiles:
        old = latest.get(profile.profile_id)
        if old is None or old.revision < profile.revision:
            latest[profile.profile_id] = profile
    with closing(sqlite3.connect(destination)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("BEGIN IMMEDIATE")
        initialize_schema(connection)
        connection.executemany(
            "INSERT INTO terminology_profiles VALUES (?, ?, ?, 'active', 0, ?, ?, ?, ?)",
            (
                (
                    item.profile_id,
                    project_id,
                    item.name,
                    _dump_content(TerminologyProfileContent()),
                    item.revision,
                    item.published_at,
                    item.published_at,
                )
                for item in latest.values()
            ),
        )
        connection.executemany(
            "INSERT INTO terminology_profile_revisions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    item.profile_id,
                    project_id,
                    item.revision,
                    item.name,
                    item.content_digest,
                    _dump_content(item.content),
                    item.published_at,
                )
                for item in profiles
            ),
        )
