# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""A readable conflict workbook with related summary and evidence tabs."""

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from transbridge.application.terminology.conflict_usage import conflict_usage
from transbridge.application.terminology.renderers.spreadsheet_safety import expanded_spreadsheet_rows

SUMMARY = ("原名", "候选译名及使用词条数", "少数译名", "处理状态", "归并译名", "判断说明", "查看证据", "冲突编号")
EVIDENCE = ("原名", "候选译名", "使用词条数", "证据原文", "证据译文", "插件", "来源", "条目", "上下文", "冲突编号")


def write_conflict_workbook(path, snapshot, summary_rows, evidence_rows, *, cancellation=None):
    workbook = Workbook()
    workbook.remove(workbook.active)
    main = _Sheet(workbook, "冲突主表", SUMMARY, (28, 40, 24, 22, 24, 60, 18, 24), 60)
    minority = _Sheet(workbook, "少数译名证据", EVIDENCE, (28, 24, 14, 65, 65, 24, 24, 24, 20, 24), 90)
    all_evidence = _Sheet(workbook, "全部证据", EVIDENCE, (28, 24, 14, 65, 65, 24, 24, 24, 20, 24), 90)
    evidence = {item.evidence_id: item for item in snapshot.evidence}
    counts = {}
    minority_records = set()
    for conflict in snapshot.items:
        _check(cancellation)
        for usage in conflict_usage(conflict, evidence):
            key = (conflict.conflict_group_id, usage.translation)
            counts[key] = usage.count
            if usage.minority:
                minority_records.update(
                    (*key, " → ".join(item.source_chain), item.entry_key) for item in usage.evidence
                )
    links = {}
    seen = set()
    try:
        for row in evidence_rows:
            _check(cancellation)
            key = (row["冲突编号"], row["候选译名"])
            row["使用词条数"] = counts.get(key, 0)
            all_evidence.append(row)
            identity = (*key, row["来源"], row["条目"])
            if identity in minority_records and identity not in seen:
                links.setdefault(row["冲突编号"], minority.next_row)
                minority.append(row)
                seen.add(identity)
        for row in summary_rows:
            _check(cancellation)
            target = links.get(row["冲突编号"])
            row["查看证据"] = "查看少数证据" if target is not None else "无可用证据"
            main.append(row, link=None if target is None else f"#'少数译名证据'!A{target}")
        for sheet in (main, minority, all_evidence):
            sheet.finish()
        _check(cancellation)
        workbook.save(path)
    finally:
        workbook.close()


def _check(cancellation):
    if cancellation is not None:
        cancellation.raise_if_cancelled()


class WorkbookSheet:
    def __init__(self, workbook, name, headers, widths, height, *, description=None, repeat_headers=("冲突编号",)):
        self.sheet = workbook.create_sheet(name)
        self.headers = headers
        self.height = height
        self.repeat_headers = repeat_headers
        self.next_row = 2
        self.sheet.freeze_panes = "C2"
        self.sheet.sheet_view.showGridLines = False
        for index, width in enumerate((*widths, 12), 1):
            self.sheet.column_dimensions[get_column_letter(index)].width = width
        self.sheet.row_dimensions[1].height = 30
        cells = []
        for value in (*headers, "文本分段"):
            cell = WriteOnlyCell(self.sheet, value)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="234B70")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cells.append(cell)
        cells[-1].comment = Comment(
            description
            or "超出 Excel 单元格长度上限的文本按顺序分段，不截断；行高固定便于浏览，完整文本可在编辑栏查看。"
            "使用词条数按同一来源的唯一词条去重，数量并列时少数表展示全部，次数不代表正确性。",
            "TransBridge",
        )
        self.sheet.append(cells)

    def append(self, row, *, link=None):
        chunks = expanded_spreadsheet_rows(tuple(row.get(key, "") for key in self.headers))
        for index, values in enumerate(chunks):
            if self.next_row > 1_048_576:
                raise ValueError("数据超过 Excel 单表行数上限，请缩小导出范围。")
            cells = []
            for column, value in enumerate((*values, f"{index + 1}/{len(chunks)}")):
                if index and column < len(self.headers) and self.headers[column] in self.repeat_headers:
                    value = chunks[0][column]
                cell = WriteOnlyCell(self.sheet, value)
                cell.row = self.next_row
                cell.column = column + 1
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if self.next_row % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="EFF4F8")
                if link and self.headers[column : column + 1] == ("查看证据",):
                    cell.hyperlink = link
                    cell.font = Font(color="0563C1", underline="single")
                cells.append(cell)
            self.sheet.row_dimensions[self.next_row].height = self.height
            self.sheet.append(cells)
            self.next_row += 1

    def finish(self):
        self.sheet.auto_filter.ref = f"A1:{get_column_letter(len(self.headers) + 1)}{self.next_row - 1}"


_Sheet = WorkbookSheet
