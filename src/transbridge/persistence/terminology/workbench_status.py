# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Project status projections without loading full evidence or domain snapshots."""

import json

from transbridge.application.terminology.conflict_review import TerminologyReviewCounts
from transbridge.application.terminology.models import (
    BuildCompleteness,
    BuildFreshness,
    BuildResultRef,
    BuildSummary,
    DraftRef,
    LlmExtractionStatus,
    TerminologyVersionRef,
)
from transbridge.application.terminology.workbench_status import BuildStatus, DraftStatus, WorkbenchStatus

from .codec import loads


def read_workbench_status(connection, project_id, variant_id):
    build = _build_status(connection, project_id, variant_id)
    version = connection.execute(
        "SELECT v.version_key, v.version_id, v.content_digest, v.build_key FROM effective_versions e "
        "JOIN versions v ON v.version_key=e.version_key WHERE e.project_id=? AND e.variant_id=?",
        (project_id, variant_id),
    ).fetchone()
    version_ref = (
        None
        if version is None
        else TerminologyVersionRef(version["version_id"], project_id, variant_id, version["content_digest"])
    )
    draft = connection.execute(
        "SELECT draft_id, base_version_id, base_content_digest, revision, decision_set_digest "
        "FROM drafts WHERE project_id=? AND variant_id=?",
        (project_id, variant_id),
    ).fetchone()
    rows = _decision_rows(connection, project_id, variant_id, version, draft is not None)
    review = sum(not row[2] and row[6] == "review_required" for row in rows)
    draft_status = None
    if draft is not None:
        previous = (
            {}
            if version is None
            else {
                (row[0], row[1]): row[2:6] for row in _decision_rows(connection, project_id, variant_id, version, False)
            }
        )
        added = sum((row[0], row[1]) not in previous and not row[2] for row in rows)
        unchanged = sum(previous.get((row[0], row[1])) == row[2:6] for row in rows)
        draft_status = DraftStatus(
            DraftRef(
                draft["draft_id"],
                project_id,
                variant_id,
                draft["base_version_id"],
                draft["base_content_digest"],
                draft["revision"],
                draft["decision_set_digest"],
            ),
            len(rows),
            added,
            unchanged,
        )
    unresolved = _conflict_count(connection, project_id, variant_id, build, draft, version)
    return WorkbenchStatus(build, draft_status, version_ref, TerminologyReviewCounts(review, unresolved))


def _build_status(connection, project_id, variant_id):
    row = connection.execute(
        "SELECT build_key,content_digest, json_extract(payload_json, '$.fields.summary') AS summary, "
        "json_extract(payload_json, '$.fields.completeness.value') AS completeness, "
        "json_extract(payload_json, '$.fields.freshness.value') AS freshness, "
        "json_extract(payload_json, '$.fields.llm_status.value') AS llm_status, "
        "json_extract(payload_json, '$.fields.diagnostics.\"$tuple\"') AS diagnostics "
        "FROM builds WHERE project_id=? AND variant_id=? ORDER BY rowid DESC LIMIT 1",
        (project_id, variant_id),
    ).fetchone()
    if row is None:
        return None
    counts = dict(
        connection.execute(
            "SELECT json_extract(payload_json,'$.fields.extraction_method.value'), COUNT(*) FROM build_candidates "
            "WHERE build_key=? GROUP BY json_extract(payload_json,'$.fields.extraction_method.value')",
            (row["build_key"],),
        ).fetchall()
    )
    return BuildStatus(
        BuildResultRef(row["build_key"], row["content_digest"]),
        loads(row["summary"], BuildSummary),
        BuildCompleteness(row["completeness"] or "full"),
        BuildFreshness(row["freshness"] or "current"),
        LlmExtractionStatus(row["llm_status"] or "skipped"),
        tuple(json.loads(row["diagnostics"] or "[]")),
        counts.get("deterministic_name", 0),
        counts.get("llm_text", 0),
    )


def _decision_rows(connection, project_id, variant_id, version, draft):
    if not draft and version is None:
        return []
    source = (
        "SELECT j.value AS payload FROM drafts d, json_each(d.payload_json,'$.fields.decisions.\"$tuple\"') j "
        "WHERE d.project_id=? AND d.variant_id=?"
        if draft
        else "SELECT payload_json AS payload FROM version_terms WHERE version_key=?"
    )
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT json_extract(payload,'$.fields.normalized_original'), "
            "json_extract(payload,'$.fields.scope'), COALESCE(json_extract(payload,'$.fields.suppressed'),0), "
            "json_extract(payload,'$.fields.translation'), json_extract(payload,'$.fields.variants'), "
            "COALESCE(json_extract(payload,'$.fields.notes'),''), json_extract(payload,'$.fields.status.value') "
            f"FROM ({source})",
            (project_id, variant_id) if draft else (version["version_key"],),
        ).fetchall()
    ]


def _conflict_count(connection, project_id, variant_id, build, draft, version):
    if build is None:
        return 0
    resolutions = {}
    if draft is not None:
        resolutions = dict(
            connection.execute(
                "SELECT json_extract(j.value,'$.fields.conflict_group_id'),"
                "json_extract(j.value,'$.fields.status.value') "
                "FROM drafts d,json_each(d.payload_json,'$.fields.conflict_resolutions.\"$tuple\"') j "
                "WHERE d.project_id=? AND d.variant_id=?",
                (project_id, variant_id),
            ).fetchall()
        )
    elif version is not None and version["build_key"] == build.ref.build_key:
        resolutions = dict(
            connection.execute(
                "SELECT json_extract(j.value,'$.fields.conflict_group_id'),"
                "json_extract(j.value,'$.fields.status.value') FROM versions v, "
                "json_each(v.payload_json,'$.fields.conflicts.\"$tuple\"') j WHERE v.version_key=?",
                (version["version_key"],),
            ).fetchall()
        )
    return sum(
        resolutions.get(row[0], row[1]) == "unresolved"
        for row in connection.execute(
            "SELECT stable_id,json_extract(payload_json,'$.fields.status.value') "
            "FROM build_conflicts WHERE build_key=?",
            (build.ref.build_key,),
        )
    )
