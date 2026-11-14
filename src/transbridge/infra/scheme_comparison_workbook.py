# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Side-by-side scheme differences using the shared readable workbook sheet."""

from openpyxl import Workbook

from .terminology_workbook import WorkbookSheet


def write_scheme_comparison_workbook(path, comparison, *, cancellation=None):
    workbook = Workbook()
    workbook.remove(workbook.active)
    names = tuple(
        f"方案 {index}：{scheme.name}（r{scheme.revision}）" for index, scheme in enumerate(comparison.schemes, 1)
    )
    headers = ("来源", "词条标识", "原文", "当前译文", *names, "提示")
    description = "超长文本按顺序分段，不截断；来源与词条标识在续行重复。这里只记录方案间译文不同的词条。"
    rows = WorkbookSheet(
        workbook,
        "译文差异",
        headers,
        (28, 26, 60, 60, *(60 for _ in names), 40),
        72,
        description=description,
        repeat_headers=("来源", "词条标识"),
    )
    meta = WorkbookSheet(
        workbook, "方案信息", ("项目", "内容"), (28, 95), 36, description=description, repeat_headers=("项目",)
    )
    try:
        for row in comparison.items:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            rows.append(
                dict(
                    zip(
                        headers,
                        (row.source, row.entry_key, row.original, row.current, *row.translations, row.notes),
                        strict=True,
                    )
                )
            )
        information = [
            ("比较时间（UTC）", comparison.created_at),
            ("比较词条数", comparison.total_count),
            ("差异词条数", len(comparison.items)),
            ("带提示词条数", comparison.warning_count),
            ("比较口径", "当前来源词条按各方案转换后逐条比较；相同译文不列出，原译文不修改。"),
        ]
        for index, scheme in enumerate(comparison.schemes, 1):
            information.extend((
                (f"方案 {index}", scheme.name),
                (f"方案 {index} 标识", scheme.profile_id),
                (f"方案 {index} 版本", scheme.revision),
                (f"方案 {index} 内容标识", scheme.content_digest),
            ))
        for key, value in information:
            meta.append({"项目": key, "内容": value})
        rows.finish()
        meta.finish()
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        workbook.save(path)
    finally:
        workbook.close()
