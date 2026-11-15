# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""One window for pairing source files, reviewing differences and confirming an update."""

from pathlib import Path, PureWindowsPath

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.io import FormatId
from transbridge.ui.foundation.components import configure_dialog, make_primary_button
from transbridge.ui.source_update_preview import SourceUpdatePreview
from transbridge.ui.source_update_selection import SourceUpdateSelection

_FILTERS = {
    FormatId.PLUGIN_SSE: "插件 (*.esp *.esm *.esl)",
    FormatId.STRINGS: "Skyrim STRINGS (*.strings)",
    FormatId.DLSTRINGS: "Skyrim DLSTRINGS (*.dlstrings)",
    FormatId.ILSTRINGS: "Skyrim ILSTRINGS (*.ilstrings)",
    FormatId.XML_EET: "ESP-ESM Translator XML (*.xml *.eet)",
    FormatId.BINARY_EET: "ESP-ESM Translator EET (*.eet)",
    FormatId.XML_XT: "xTranslator XML (*.xml)",
}
_BATCH_FILTER = "来源文件 (*.esp *.esm *.esl *.xml *.eet *.strings *.dlstrings *.ilstrings *.json *.sst);;所有文件 (*)"


class SourceUpdateDialog(QDialog):
    discovery_requested = pyqtSignal(object, object)
    preview_requested = pyqtSignal(object)
    commit_requested = pyqtSignal(str)
    back_requested = pyqtSignal(str)

    def __init__(self, sources, project_name: str, parent=None) -> None:
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowTitle("更新源文件")
        self.resize(900, 520)
        self._busy = False
        self._preview = None
        self.selection = SourceUpdateSelection(sources)
        layout = QVBoxLayout(self)
        project = QLabel(f"工程：{project_name}", self)
        project.setTextFormat(Qt.TextFormat.PlainText)
        project.setWordWrap(True)
        layout.addWidget(project)
        self.pages = QStackedWidget(self)
        layout.addWidget(self.pages, 1)
        self.pairing_page = self._pairing_page()
        self.preview_page = SourceUpdatePreview(self)
        self.pages.addWidget(self.pairing_page)
        self.pages.addWidget(self.preview_page)

        self.feedback = QLabel(self)
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        self.feedback.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.feedback.hide()
        layout.addWidget(self.feedback)
        footer = QHBoxLayout()
        self.scope = QLabel("本次配对的来源将更新到全部翻译版本。", self)
        self.scope.setWordWrap(True)
        footer.addWidget(self.scope)
        footer.addStretch(1)
        self.back_button = QPushButton("返回改选", self)
        self.cancel_button = QPushButton("取消", self)
        self.primary_button = make_primary_button("预览差异", self)
        for button in (self.back_button, self.cancel_button, self.primary_button):
            button.setAutoDefault(False)
            footer.addWidget(button)
        self.back_button.clicked.connect(self._go_back)
        self.cancel_button.clicked.connect(self.reject)
        self.primary_button.clicked.connect(self._continue)
        layout.addLayout(footer)
        self._refresh()
        self.files_button.setFocus()

    def _pairing_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.addStretch()
        directory = QPushButton("选择文件夹…", page)
        directory.clicked.connect(self._choose_directory)
        toolbar.addWidget(directory)
        self.files_button = QPushButton("选择多个文件…", page)
        self.files_button.clicked.connect(self._choose_files)
        toolbar.addWidget(self.files_button)
        layout.addLayout(toolbar)
        self.table = QTableWidget(len(self.selection.sources), 4, page)
        self.table.setAccessibleName("工程来源与新版文件的配对")
        self.table.setHorizontalHeaderLabels(("工程来源", "新版文件", "匹配状态", "操作"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header = self.table.horizontalHeader()
        for column in (0, 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._row_buttons = []
        for index, source in enumerate(self.selection.sources):
            self.table.setItem(index, 0, _path_item(source.location, source.label))
            self.table.setItem(index, 1, QTableWidgetItem())
            self.table.setItem(index, 2, QTableWidgetItem())
            actions = QWidget(self.table)
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(4, 3, 4, 3)
            choose = QPushButton(actions)
            choose.clicked.connect(lambda _checked=False, row=index: self._row_menu(row))
            skip = QPushButton("跳过本项", actions)
            skip.setAccessibleName(f"跳过 {source.label}")
            skip.clicked.connect(lambda _checked=False, key=source.source_id: self._skip(key))
            action_layout.addWidget(choose)
            action_layout.addWidget(skip)
            self.table.setCellWidget(index, 3, actions)
            self._row_buttons.append((choose, skip))
        self.table.verticalHeader().setDefaultSectionSize(max(52, 2 * self.fontMetrics().height() + 14))
        layout.addWidget(self.table, 1)
        self.counts = QLabel(page)
        layout.addWidget(self.counts)
        return page

    def set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.pairing_page.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        self.back_button.setEnabled(not busy)
        self.set_feedback(message)
        self._refresh()

    def set_feedback(self, message: str) -> None:
        self.feedback.setText(message)
        self.feedback.setToolTip("")
        self.feedback.setVisible(bool(message))

    def show_discovery(self, result) -> None:
        self.selection.add_files(result.paths)
        self.set_busy(False)
        if result.errors:
            self.set_feedback(f"部分位置无法读取。\n{result.errors[0]}")
            self.feedback.setToolTip("\n".join(result.errors))
        elif not any(row.candidates for row in self._rows):
            self.set_feedback("没有找到同名且格式相符的文件。可以在对应行手动选择新版文件。")

    def show_preview(self, preview) -> None:
        self._preview = preview
        self.preview_page.set_preview(preview)
        self.pages.setCurrentWidget(self.preview_page)
        self.set_busy(False)
        self.preview_page.differences.setFocus()

    def show_pairing(self, message: str = "") -> None:
        self._preview = None
        self.pages.setCurrentWidget(self.pairing_page)
        self.set_busy(False, message)

    def reject(self) -> None:
        if not self._busy:
            super().reject()

    def _refresh(self) -> None:
        self._rows = self.selection.rows()
        self._pairs = tuple((row.source.source_id, row.replacement) for row in self._rows if row.replacement)
        pending = sum(bool(row.candidates and not row.replacement and not row.skipped) for row in self._rows)
        skipped = sum(row.skipped for row in self._rows)
        for index, row in enumerate(self._rows):
            replacement = self.table.item(index, 1)
            if row.replacement:
                replacement.setText(_path_text(row.replacement))
                replacement.setToolTip(row.replacement)
            else:
                replacement.setText(f"发现 {len(row.candidates)} 个同名文件" if row.candidates else "—")
                replacement.setToolTip("")
            status = (
                "已跳过" if row.skipped else "已配对" if row.replacement else "待选择" if row.candidates else "未配对"
            )
            status_item = self.table.item(index, 2)
            status_item.setText(status)
            font = status_item.font()
            font.setBold(status == "待选择")
            status_item.setFont(font)
            choose, skip = self._row_buttons[index]
            label = "更换…" if row.replacement else "选择文件…"
            choose.setText(label)
            choose.setAccessibleName(f"{label} {row.source.label}")
            skip.setEnabled(not row.skipped and bool(row.candidates or row.replacement))
        self.counts.setText(f"已配对 {len(self._pairs)} 项 · 待选择 {pending} 项 · 已跳过 {skipped} 项")
        preview = self._preview
        self.back_button.setVisible(preview is not None)
        self.scope.setVisible(preview is None)
        self.primary_button.setText(f"更新 {len(preview.differences)} 个来源" if preview else "预览差异")
        self.primary_button.setAccessibleName(self.primary_button.text())
        self.primary_button.setEnabled(not self._busy and (preview is not None or bool(self._pairs and not pending)))

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择新版文件", "", _BATCH_FILTER)
        if paths:
            self.discovery_requested.emit(tuple(paths), None)

    def _choose_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择新版文件夹（包含子文件夹）")
        if directory:
            self.discovery_requested.emit((), directory)

    def _row_menu(self, index: int) -> None:
        row = self._rows[index]
        button, _ = self._row_buttons[index]
        menu = QMenu(button)
        for path in row.candidates:
            action = menu.addAction(path)
            action.setText(path.replace("&", "&&"))
            action.triggered.connect(lambda _checked=False, selected=path: self._assign(row.source.source_id, selected))
        if row.candidates:
            menu.addSeparator()
        menu.addAction("浏览文件…", lambda: self._browse(row))
        if row.source.location:
            menu.addAction("使用原路径的文件", lambda: self._assign(row.source.source_id, row.source.location))
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        menu.deleteLater()

    def _browse(self, row) -> None:
        directory = str(Path(row.replacement or row.source.location).parent)
        file_filter = _FILTERS.get(row.source.format_id, "来源文件 (*)")
        path, _ = QFileDialog.getOpenFileName(
            self, f"为 {row.source.label} 选择新版文件", directory, f"{file_filter};;所有文件 (*)"
        )
        if path:
            self._assign(row.source.source_id, path)

    def _assign(self, key: str, path: str) -> None:
        if self.selection.choose(key, path):
            self.set_feedback("")
            self._refresh()
        else:
            self.set_feedback(f"此文件已配对到另一项。请先调整对应项，再选择此文件。\n{path}")

    def _skip(self, key: str) -> None:
        self.selection.skip(key)
        self.set_feedback("")
        self._refresh()

    def _continue(self) -> None:
        if self._preview is None:
            self.preview_requested.emit(self._pairs)
        else:
            self.commit_requested.emit(self._preview.token)

    def _go_back(self) -> None:
        if self._preview is not None:
            self.back_requested.emit(self._preview.token)


def _path_text(path: str, label: str = "") -> str:
    if not path:
        return label
    parsed = PureWindowsPath(path) if "\\" in path else Path(path)
    return f"{parsed.name}\n{parsed.parent}" if parsed.parent.parts else parsed.name


def _path_item(path: str, label: str) -> QTableWidgetItem:
    item = QTableWidgetItem(_path_text(path, label))
    item.setToolTip(path)
    return item
