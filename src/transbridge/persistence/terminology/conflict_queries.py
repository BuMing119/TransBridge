# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""SQLite conflict filtering before keyset pagination and row counting."""

from dataclasses import replace
import json
import sqlite3

from transbridge.application.terminology.conflict_queries import ConflictFilter
from transbridge.application.terminology.identity import canonical_digest
from transbridge.application.terminology.models import BuildResultRef, ConflictGroup
from transbridge.application.terminology.ports import Page, PageRequest

from .codec import loads
from .queries import keyset_page


def conflict_page(
    connection: sqlite3.Connection,
    ref: BuildResultRef,
    request: PageRequest,
    filters: ConflictFilter,
    *,
    snapshot_digest: str | None = None,
    resolutions: tuple[ConflictGroup, ...] = (),
) -> Page[ConflictGroup]:
    conditions = []
    parameters: list[str] = []
    if filters.start_at is not None:
        conditions.append("stable_id >= ?")
        parameters.append(filters.start_at)
    status = "json_extract(payload_json, '$.fields.status.value')"
    status_parameters = []
    if resolutions:
        status = f"COALESCE((SELECT value FROM json_each(?) WHERE key = stable_id), {status})"
        status_parameters = [json.dumps({item.conflict_group_id: item.status.value for item in resolutions})]
    if not filters.include_equivalent:
        conditions.append(f"{status} != 'equivalent'")
        parameters.extend(status_parameters)
    if filters.review_state != "all":
        predicate = {
            "pending": "= 'unresolved'",
            "equivalent": "= 'equivalent'",
            "resolved": "IN ('unified', 'plugin_exception', 'ignored')",
        }[filters.review_state]
        conditions.append(f"{status} {predicate}")
        parameters.extend(status_parameters)
    if filters.risk is not None:
        conditions.append("json_extract(payload_json, '$.fields.risk.value') = ?")
        parameters.append(filters.risk.value)
    if filters.search:
        connection.create_function("tb_casefold", 1, str.casefold, deterministic=True)
        conditions.append(
            "(instr(tb_casefold(json_extract(payload_json, '$.fields.normalized_original')), ?) > 0 "
            "OR EXISTS (SELECT 1 FROM json_each(payload_json, '$.fields.variants.\"$tuple\"') AS variant "
            "WHERE instr(tb_casefold(json_extract(variant.value, '$.fields.normalized_translation')), ?) > 0))"
        )
        parameters.extend((filters.search, filters.search))
    return keyset_page(
        connection,
        table="build_conflicts",
        owner_column="build_key",
        owner_value=ref.build_key,
        snapshot_digest=snapshot_digest or ref.content_digest,
        request=filters.bind_request(request),
        decode=lambda payload: loads(payload, ConflictGroup),
        filter_sql=" AND ".join(conditions),
        filter_parameters=tuple(parameters),
    )


def review_conflict_page(connection, build, draft, effective, request, filters):
    """Overlay reviewed decisions without changing immutable build membership."""

    resolutions = ()
    owner_ref = None
    if draft is not None:
        resolutions, owner_ref = draft.conflict_resolutions, draft.ref
    elif effective is not None and effective.build_ref == build.ref:
        resolutions, owner_ref = effective.conflicts, effective.ref
    snapshot_digest = canonical_digest((build.ref, owner_ref), namespace="terminology.review-conflicts.v1")
    page = conflict_page(
        connection, build.ref, request, filters, snapshot_digest=snapshot_digest, resolutions=resolutions
    )
    by_id = {item.conflict_group_id: item for item in resolutions}
    return replace(page, items=tuple(by_id.get(item.conflict_group_id, item) for item in page.items))
