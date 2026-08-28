"""Deterministic Markdown layout for a frozen ChangeLogDocumentRef."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

from ..changelog_queries import ChangeLogQueryService
from ..models import ChangeLogDocumentRef
from ._artifact import ArtifactPublishPolicy, publish_staged
from ._changelog_rows import ChangeLogDocumentReader, change_payload
from ._manifest import RenderedArtifact, changelog_semantic_manifest

RENDERER_VERSION = "terminology-changelog-markdown.v1"


class ChangeLogMarkdownRenderer:
    format = "markdown"
    renderer_version = RENDERER_VERSION

    def __init__(self, documents: ChangeLogDocumentReader) -> None:
        self._queries = ChangeLogQueryService(documents)

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

        def write(staging: Path) -> None:
            with staging.open("w", encoding="utf-8", newline="\n") as stream:
                _write_document(stream, self._queries, document, page_size=page_size)

        published = publish_staged(target, write, policy=policy)
        return RenderedArtifact(
            self.format,
            self.renderer_version,
            published.path,
            published.size,
            published.sha256,
            manifest,
        )


def _write_document(stream: TextIO, queries: ChangeLogQueryService, document, *, page_size: int) -> None:
    stream.write("# 术语库更新说明\n\n")
    stream.write(f"- 版本：`{document.version_ref.version_id}`\n")
    stream.write(f"- 语言：`{document.locale}`\n")
    stream.write(f"- 文档摘要：`{document.ref.content_digest}`\n\n")
    stream.write("## 最终用户摘要\n\n")
    if document.section_count("messages") == 0:
        stream.write("- 本版本没有术语事实变化。\n")
    else:
        for key, arguments in queries.messages(document.ref, page_size=page_size):
            payload = json.dumps({"message": key, "args": arguments}, ensure_ascii=False, sort_keys=True)
            stream.write(f"- {payload}\n")
    stream.write("\n## 翻译者/维护者完整明细\n\n")
    if document.section_count("changes") == 0:
        stream.write("没有 typed change rows。\n\n")
    else:
        for change in queries.changes(document.ref, page_size=page_size):
            stream.write(f"### {change.change_id}\n\n```json\n")
            stream.write(json.dumps(change_payload(change), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            stream.write("\n```\n\n")
    stream.write("## 发布绑定事实\n\n```json\n{")
    fields = (
        ("conflict_group_ids", queries.conflict_group_ids(document.ref, page_size=page_size)),
        ("diagnostics", queries.diagnostics(document.ref, page_size=page_size)),
        ("manual_action_ids", queries.manual_action_ids(document.ref, page_size=page_size)),
        ("no_evidence_term_ids", queries.no_evidence_term_ids(document.ref, page_size=page_size)),
    )
    for field_index, (name, values) in enumerate(fields):
        if field_index:
            stream.write(",")
        stream.write(f"{json.dumps(name)}:[")
        for value_index, value in enumerate(values):
            if value_index:
                stream.write(",")
            stream.write(json.dumps(value, ensure_ascii=False))
        stream.write("]")
    stream.write(f',"schema_version":{json.dumps(document.schema_version, ensure_ascii=False)}')
    stream.write(f',"template_digest":{json.dumps(document.template_digest, ensure_ascii=False)}}}')
    stream.write("\n```\n")


__all__ = ["ChangeLogMarkdownRenderer", "RENDERER_VERSION"]
