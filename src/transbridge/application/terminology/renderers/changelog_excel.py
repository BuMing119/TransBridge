"""Write-only Excel layout for a frozen ChangeLogDocumentRef."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from ..changelog_queries import ChangeLogQueryService
from ..models import ChangeLogDocumentRef
from ._artifact import ArtifactPublishPolicy, publish_staged
from ._changelog_rows import ChangeLogDocumentReader, change_row, message_row
from ._manifest import RenderedArtifact, changelog_semantic_manifest
from .quality_excel import EXCEL_MAX_ROWS, _LogicalSheetWriter

RENDERER_VERSION = "terminology-changelog-excel.v1"

USER_SHEET = "最终用户摘要"
MAINTAINER_SHEET = "维护者完整明细"
FACTS_SHEET = "发布绑定事实"


class ChangeLogExcelRenderer:
    format = "xlsx"
    renderer_version = RENDERER_VERSION

    def __init__(
        self,
        documents: ChangeLogDocumentReader,
        *,
        max_sheet_rows: int = EXCEL_MAX_ROWS,
    ) -> None:
        self._queries = ChangeLogQueryService(documents)
        self._max_sheet_rows = max_sheet_rows

    def render(
        self,
        ref: ChangeLogDocumentRef,
        target: str | Path,
        *,
        policy: ArtifactPublishPolicy = ArtifactPublishPolicy.FAIL_IF_EXISTS,
        page_size: int = 1000,
    ) -> RenderedArtifact:
        document = self._queries.manifest(ref)
        manifest = changelog_semantic_manifest(document)
        workbook = Workbook(write_only=True)
        users = _LogicalSheetWriter(
            workbook,
            USER_SHEET,
            ("message_key", "argument_count", "message_arguments"),
            max_rows=self._max_sheet_rows,
        )
        changes = _LogicalSheetWriter(
            workbook,
            MAINTAINER_SHEET,
            (
                "change_id",
                "change_type",
                "term_id",
                "manual",
                "before_digest",
                "after_digest",
                "before",
                "after",
                "details",
            ),
            max_rows=self._max_sheet_rows,
        )
        facts = _LogicalSheetWriter(
            workbook,
            FACTS_SHEET,
            ("fact_type", "stable_id", "value"),
            max_rows=self._max_sheet_rows,
        )
        for index, message in enumerate(self._queries.messages(ref, page_size=page_size)):
            users.append(message_row(message), stable_id=f"message:{index:08d}")
        for change in self._queries.changes(ref, page_size=page_size):
            changes.append(change_row(change), stable_id=change.change_id)
        metadata = (
            ("metadata", "version_id", document.version_ref.version_id),
            ("metadata", "locale", document.locale),
            ("metadata", "schema_version", document.schema_version),
            ("metadata", "template_digest", document.template_digest),
        )
        for fact_type, stable_id, value in metadata:
            facts.append((fact_type, stable_id, value), stable_id=f"{fact_type}:{stable_id}")
        for fact_type, values in (
            ("conflict_group_id", self._queries.conflict_group_ids(ref, page_size=page_size)),
            ("no_evidence_term_id", self._queries.no_evidence_term_ids(ref, page_size=page_size)),
            ("manual_action_id", self._queries.manual_action_ids(ref, page_size=page_size)),
            ("diagnostic", self._queries.diagnostics(ref, page_size=page_size)),
        ):
            for index, value in enumerate(values):
                stable_id = value if fact_type != "diagnostic" else f"{index:08d}"
                facts.append((fact_type, stable_id, value), stable_id=f"{fact_type}:{stable_id}")
        sheet_names = tuple(sheet.title for sheet in workbook.worksheets)
        published = publish_staged(target, workbook.save, policy=policy)
        return RenderedArtifact(
            self.format,
            self.renderer_version,
            published.path,
            published.size,
            published.sha256,
            manifest,
            sheet_names,
        )


__all__ = [
    "ChangeLogExcelRenderer",
    "FACTS_SHEET",
    "MAINTAINER_SHEET",
    "RENDERER_VERSION",
    "USER_SHEET",
]
