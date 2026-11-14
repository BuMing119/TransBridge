# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Write complete, frozen terminology snapshots through existing file adapters."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

from transbridge.ai_translator.term_formats import TermEntry, dump_terms_csv, dump_terms_json
from transbridge.application.terminology.conflict_usage import conflict_usage
from transbridge.application.terminology.models import BuildResult, TerminologyDraft
from transbridge.application.terminology.query_control import QueryCancellation


@dataclass(frozen=True, slots=True)
class TerminologyExport:
    kind: str
    items: tuple
    evidence: tuple = ()
    decisions: tuple = ()


def conflict_export(build: BuildResult, draft: TerminologyDraft | None, version=None) -> TerminologyExport:
    published = version if version is not None and version.build_ref == build.ref else None
    resolutions = (
        draft.conflict_resolutions if draft is not None else published.conflicts if published is not None else ()
    )
    resolved = {item.conflict_group_id: item for item in resolutions}
    return TerminologyExport(
        "conflicts",
        tuple(resolved.get(item.conflict_group_id, item) for item in build.conflicts),
        build.evidence,
        draft.decisions if draft is not None else published.decisions if published is not None else (),
    )


def write_terminology_export(
    path: Path, snapshot: TerminologyExport, *, cancellation: QueryCancellation | None = None
) -> int:
    """Stage beside the target and replace only after the complete file is written."""
    suffix = path.suffix.lower()
    workbook_kind = snapshot.kind in {"conflict_workbook", "scheme_comparison"}
    allowed = {".xlsx"} if workbook_kind else {".json", ".csv"}
    if suffix not in allowed:
        raise ValueError("请选择 Excel（.xlsx）文件。" if workbook_kind else "请选择 JSON 或 CSV 文件格式。")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=suffix, dir=path.parent)
    os.close(fd)
    staged = Path(temporary)
    try:
        if snapshot.kind == "scheme_comparison":
            from .scheme_comparison_workbook import write_scheme_comparison_workbook

            write_scheme_comparison_workbook(staged, snapshot, cancellation=cancellation)
        elif snapshot.kind == "conflict_workbook":
            from .terminology_workbook import write_conflict_workbook

            write_conflict_workbook(
                staged, snapshot, _summary_rows(snapshot), _conflict_rows(snapshot), cancellation=cancellation
            )
        elif snapshot.kind == "terms":
            entries = (_term_entry(item) for item in snapshot.items)
            (dump_terms_json if suffix == ".json" else dump_terms_csv)(staged, entries)
        elif snapshot.kind in {"conflicts", "conflict_summary"}:
            summary = snapshot.kind == "conflict_summary"
            rows = _summary_rows(snapshot) if summary else _conflict_rows(snapshot)
            if suffix == ".json":
                with staged.open("w", encoding="utf-8") as stream:
                    json.dump(list(rows), stream, ensure_ascii=False, indent=2)
            else:
                with staged.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=_SUMMARY_HEADERS if summary else _CONFLICT_HEADERS)
                    writer.writeheader()
                    writer.writerows(rows)
        else:
            raise ValueError(f"未知术语导出类型：{snapshot.kind}")
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
    return len(snapshot.items)


def _term_entry(item):
    return TermEntry(
        term=item.original,
        translation=item.translation,
        source="project-terminology",
        context=item.scope.plugin_id or "",
        variants=list(item.variants),
        note=item.notes,
        metadata={"scope_kind": item.scope.kind.value, "plugin_id": item.scope.plugin_id},
    )


_CONFLICT_HEADERS = (
    "冲突编号",
    "原名",
    "候选译名",
    "处理状态",
    "处理优先级",
    "当前采用译名",
    "归并译名",
    "判断说明",
    "证据原文",
    "证据译文",
    "插件",
    "来源",
    "条目",
    "上下文",
)

_SUMMARY_HEADERS = (
    "冲突编号",
    "原名",
    "候选译名及使用词条数",
    "少数译名",
    "少数译名代表证据",
    "处理状态",
    "归并译名",
    "判断说明",
    "统计口径",
)


def _summary_rows(snapshot):
    evidence = {item.evidence_id: item for item in snapshot.evidence}
    for conflict in snapshot.items:
        usages = conflict_usage(conflict, evidence)
        examples = []
        for usage in usages:
            if usage.minority and usage.evidence:
                item = usage.evidence[0]
                examples.append(
                    f"{usage.translation}：原文摘录 {_excerpt(item.original, conflict.normalized_original)}\n"
                    f"译文摘录 {_excerpt(item.translation, usage.translation)}\n"
                    f"来源 {' → '.join(item.source_chain)}"
                )
        yield {
            "冲突编号": conflict.conflict_group_id,
            "原名": conflict.normalized_original,
            "候选译名及使用词条数": "；".join(f"{item.translation}：{item.count}" for item in usages),
            "少数译名": "；".join(item.translation for item in usages if item.minority),
            "少数译名代表证据": "\n\n".join(examples),
            "处理状态": {
                "unresolved": "待决定",
                "unified": "已统一",
                "plugin_exception": "按插件分别使用",
                "ignored": "已排除",
                "equivalent": "同一术语 · 无需处理",
            }.get(str(conflict.status), str(conflict.status)),
            "归并译名": getattr(conflict, "recommended_translation", None) or "",
            "判断说明": getattr(conflict, "notes", ""),
            "统计口径": "同一来源及唯一词条去重；数量并列时展示全部译名；次数不代表正确性",
        }


def _excerpt(text, term):
    start = max(0, text.casefold().find(term.casefold()) - 60)
    end = min(len(text), start + 240)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def _conflict_rows(snapshot):
    evidence = {item.evidence_id: item for item in snapshot.evidence}
    decisions = {}
    for item in snapshot.decisions:
        if item.is_effective:
            decisions.setdefault(item.normalized_original, []).append(
                f"{item.scope.plugin_id or '整个项目'}：{item.translation}"
            )
    for conflict in snapshot.items:
        for variant in conflict.variants:
            for evidence_id in variant.evidence_ids:
                item = evidence.get(evidence_id)
                yield {
                    "冲突编号": conflict.conflict_group_id,
                    "原名": conflict.normalized_original,
                    "候选译名": variant.normalized_translation,
                    "处理状态": {
                        "unresolved": "待决定",
                        "unified": "已统一",
                        "plugin_exception": "按插件分别使用",
                        "ignored": "已排除",
                        "equivalent": "同一术语 · 无需处理",
                    }.get(str(conflict.status), str(conflict.status)),
                    "处理优先级": {"high": "优先处理", "medium": "一般", "low": "较低"}.get(
                        str(conflict.risk), str(conflict.risk)
                    ),
                    "当前采用译名": "；".join(decisions.get(conflict.normalized_original, ())),
                    "归并译名": getattr(conflict, "recommended_translation", None) or "",
                    "判断说明": getattr(conflict, "notes", ""),
                    "证据原文": "" if item is None else item.original,
                    "证据译文": "" if item is None else item.translation,
                    "插件": "来源未识别" if item is None else item.plugin_scope or "来源插件未识别",
                    "来源": "" if item is None else " → ".join(item.source_chain),
                    "条目": "" if item is None else item.entry_key,
                    "上下文": "" if item is None else item.context,
                }
