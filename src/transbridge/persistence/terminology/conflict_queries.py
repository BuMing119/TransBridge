"""SQLite conflict filtering before keyset pagination and row counting."""

import sqlite3

from transbridge.application.terminology.conflict_queries import ConflictFilter
from transbridge.application.terminology.models import BuildResultRef, ConflictGroup
from transbridge.application.terminology.ports import Page, PageRequest

from .codec import loads
from .queries import keyset_page


def conflict_page(
    connection: sqlite3.Connection, ref: BuildResultRef, request: PageRequest, filters: ConflictFilter
) -> Page[ConflictGroup]:
    conditions = []
    parameters: list[str] = []
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
        snapshot_digest=ref.content_digest,
        request=filters.bind_request(request),
        decode=lambda payload: loads(payload, ConflictGroup),
        filter_sql=" AND ".join(conditions),
        filter_parameters=tuple(parameters),
    )
