# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Show the impact of every source in the batch before committing it."""

from pathlib import PureWindowsPath

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHeaderView, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget


class SourceUpdatePreview(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAccessibleName("源文件更新预览")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.differences = QTreeWidget(self)
        self.differences.setAccessibleName("逐来源的更新差异")
        self.differences.setHeaderLabels(("工程来源与路径", "更新影响"))
        self.differences.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.differences.setAlternatingRowColors(True)
        self.differences.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.differences, 1)
        note = QLabel(
            "匹配的译文会保留。原文变化或无法核验的已有译文需要复核。\n"
            "更新前会备份全部翻译版本，移除条目的译文可从备份找回。",
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)

    def set_preview(self, preview) -> None:
        count = len(preview.differences)
        self.summary.setText(
            f"将更新 {count} 个来源，涉及全部 {preview.variant_count} 个翻译版本。\n"
            f"原文变化 {sum(item.changed for item in preview.differences)} · "
            f"无法核验 {sum(item.unverified for item in preview.differences)} · "
            f"移除 {sum(item.removed for item in preview.differences)}"
        )
        self.differences.clear()
        for item in preview.differences:
            directory = PureWindowsPath(item.current_path).parent
            source = f"{item.source_name} · {directory}" if directory.parts else item.source_name
            row = QTreeWidgetItem([
                source,
                f"原文变化 {item.changed} · 无法核验 {item.unverified} · 移除 {item.removed}",
            ])
            row.setToolTip(0, item.current_path or item.source_name)
            for label, value in (
                ("当前来源", item.current_path or "无已登记的路径"),
                ("新版文件", item.replacement_path),
                ("其他差异", f"新增 {item.added} · 未变 {item.unchanged} · 顺序变化 {item.reordered}"),
            ):
                detail = QTreeWidgetItem(row, [label, value])
                detail.setToolTip(1, value)
            self.differences.addTopLevelItem(row)
            row.setExpanded(count == 1)
