from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from transbridge.paratranz.workflow.downloader import ParaTranzDownloader

from ...workers import ApiWorker
from .base import OpCard
from .presenter import OperationCardPresenter


@dataclass
class BatchDownloadResult:
    """批量下载结果汇总。"""

    success_count: int = 0
    skipped_count: int = 0  # 未找到同名文件
    failed_count: int = 0
    merged_total: int = 0
    details: list[str] = field(default_factory=list)


class _SlotSelectDialog(QDialog):
    """插件槽位选择对话框，用于批量操作时选择要操作的插件。"""

    def __init__(self, title: str, slots: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        self.setMinimumHeight(200)
        self.setMaximumHeight(400)

        layout = QVBoxLayout(self)

        hint = QLabel("选择要操作的插件：")
        hint.setStyleSheet("color: #555;")
        layout.addWidget(hint)

        # 全选/全不选 按钮
        btn_row = QHBoxLayout()
        self._btn_all = QPushButton("全选")
        self._btn_none = QPushButton("全不选")
        btn_row.addWidget(self._btn_all)
        btn_row.addWidget(self._btn_none)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #ddd; border-radius: 3px; }")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.setSpacing(4)

        self._checkboxes: dict[str, QCheckBox] = {}
        for key, slot in slots.items():
            label = slot.label or Path(key).stem
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet("QCheckBox { spacing: 4px; }")
            cb._slot_key = key
            container_layout.addWidget(cb)
            self._checkboxes[key] = cb
        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # 状态标签
        self._status_label = QLabel(f"已选 {len(slots)} 个插件")
        self._status_label.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self._status_label)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("确认")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        # 连接信号
        self._btn_all.clicked.connect(self._select_all)
        self._btn_none.clicked.connect(self._select_none)
        for cb in self._checkboxes.values():
            cb.stateChanged.connect(self._update_status)

    def _select_all(self):
        for cb in self._checkboxes.values():
            cb.setChecked(True)

    def _select_none(self):
        for cb in self._checkboxes.values():
            cb.setChecked(False)

    def _update_status(self):
        count = sum(1 for cb in self._checkboxes.values() if cb.isChecked())
        self._status_label.setText(f"已选 {count} 个插件")
        self._ok_btn.setEnabled(count > 0)

    def selected_slots(self) -> list:
        """返回选中的 slot key 列表。"""
        return [cb._slot_key for cb in self._checkboxes.values() if cb.isChecked()]


class _BatchResultDialog(QDialog):
    """批量操作结果对话框，带滚动区域。"""

    def __init__(self, title: str, header: str, items: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self.setMinimumHeight(200)
        self.setMaximumHeight(400)

        layout = QVBoxLayout(self)

        # 标题说明
        header_lbl = QLabel(header)
        header_lbl.setWordWrap(True)
        layout.addWidget(header_lbl)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #ddd; border-radius: 3px; }")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.setSpacing(2)

        for item in items:
            lbl = QLabel(item)
            lbl.setStyleSheet("color: #333;")
            container_layout.addWidget(lbl)
        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # 提示信息
        footer = QLabel(f"共 {len(items)} 个项目")
        footer.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(footer)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btn_box.accepted.connect(self.accept)
        layout.addWidget(btn_box)


class _BatchConfirmDialog(QDialog):
    """批量操作确认对话框，带滚动区域。"""

    def __init__(self, title: str, header: str, items: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self.setMinimumHeight(200)
        self.setMaximumHeight(400)

        layout = QVBoxLayout(self)

        # 标题说明
        header_lbl = QLabel(header)
        header_lbl.setWordWrap(True)
        layout.addWidget(header_lbl)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #ddd; border-radius: 3px; }")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.setSpacing(2)

        for item in items:
            lbl = QLabel(item)
            lbl.setStyleSheet("color: #333;")
            container_layout.addWidget(lbl)
        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # 提示信息
        footer = QLabel(f"共 {len(items)} 个项目")
        footer.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(footer)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No)
        btn_box.button(QDialogButtonBox.StandardButton.Yes).setText("确认")
        btn_box.button(QDialogButtonBox.StandardButton.No).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)


class _FileSelectDialog(QDialog):
    """下载合并文件选择对话框：从项目文件列表中勾选要参与合并的文件。"""

    def __init__(self, config, project_id: int, parent=None):
        super().__init__(parent)
        self._config = config
        self._project_id = project_id
        self._workers = []
        self._file_data: list[dict] = []

        self.setWindowTitle("选择要合并的文件")
        self.setMinimumSize(520, 380)

        layout = QVBoxLayout(self)

        tool_row = QHBoxLayout()
        self._btn_all = QPushButton("全选")
        self._btn_none = QPushButton("全不选")
        self._btn_all.setEnabled(False)
        self._btn_none.setEnabled(False)
        self._btn_all.clicked.connect(self._select_all)
        self._btn_none.clicked.connect(self._select_none)
        tool_row.addWidget(self._btn_all)
        tool_row.addWidget(self._btn_none)
        tool_row.addStretch()
        layout.addLayout(tool_row)

        self._stack = QStackedWidget()

        loading_widget = QWidget()
        loading_layout = QVBoxLayout(loading_widget)
        loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label = QLabel("正在获取文件列表…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(self._loading_label)
        self._stack.addWidget(loading_widget)  # index 0

        self._list_widget = QListWidget()
        self._list_widget.itemChanged.connect(self._on_item_changed)
        self._stack.addWidget(self._list_widget)  # index 1

        layout.addWidget(self._stack)

        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("确认合并")
        self._ok_btn.setEnabled(False)
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._load_files()

    def _load_files(self):
        from transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI

        config = self._config
        project_id = self._project_id

        def _fetch():
            api = ParatranzFilesAPI(token=config.token, config=config)
            return api.list_files(project_id) or []

        w = ApiWorker(_fetch)
        w.result.connect(self._on_files_loaded)
        w.error.connect(self._on_load_error)
        w.start()
        self._workers.append(w)

    def _on_files_loaded(self, files: list):
        self._file_data = files
        self._list_widget.blockSignals(True)
        for f in files:
            name = f.get("name", str(f.get("id", "")))
            total = f.get("total", 0)
            translated = f.get("translated", 0)
            label = f"{name}    {total} 条  已译 {translated} 条"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, f["id"])
            self._list_widget.addItem(item)
        self._list_widget.blockSignals(False)
        self._stack.setCurrentIndex(1)
        self._btn_all.setEnabled(True)
        self._btn_none.setEnabled(True)
        self._update_status()

    def _on_load_error(self, msg: str):
        self._loading_label.setText(f"获取文件列表失败：{msg}")

    def _select_all(self):
        self._list_widget.blockSignals(True)
        for i in range(self._list_widget.count()):
            self._list_widget.item(i).setCheckState(Qt.CheckState.Checked)
        self._list_widget.blockSignals(False)
        self._update_status()

    def _select_none(self):
        self._list_widget.blockSignals(True)
        for i in range(self._list_widget.count()):
            self._list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)
        self._list_widget.blockSignals(False)
        self._update_status()

    def _on_item_changed(self, _item):
        self._update_status()

    def _update_status(self):
        count = 0
        total_strings = 0
        file_totals = {f["id"]: f.get("total", 0) for f in self._file_data}
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                count += 1
                total_strings += file_totals.get(item.data(Qt.ItemDataRole.UserRole), 0)
        self._status_label.setText(f"已选 {count} 个文件，共 {total_strings} 条词条")
        self._ok_btn.setEnabled(count > 0)

    def selected_file_ids(self) -> list[int]:
        result = []
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result


class DownloadCard(OpCard):
    def __init__(self, ctx, run_worker, parent=None):
        super().__init__(
            "从 ParaTranz 下载合并",
            "从当前已选项目拉取译文，按 key 合并到本地集合（stage >= 1 的词条）。",
            "下载合并",
            parent,
        )
        self._ctx = ctx
        self._presenter = OperationCardPresenter(ctx)
        self._run_worker = run_worker
        self.btn.clicked.connect(self.download)
        self.batch_btn.clicked.connect(self.batch_download)

    def update_batch_visibility(self):
        """更新批量按钮可见性（由 step3 调用）。"""
        self.set_batch_visible(self._presenter.batch_available)

    def batch_download(self):
        """批量下载入口。"""
        if self._dispatch_planned("download", self._ctx, batch=True):
            return
        slots = self._ctx.slots
        if len(slots) <= 1:
            return

        project = self._ctx.current_project
        if not project:
            QMessageBox.warning(self, "未选择项目", "请先在 ParaTranz 管理面板中选择目标项目。")
            return

        # 弹出插件选择对话框
        dlg = _SlotSelectDialog("批量下载 - 选择插件", slots, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected_keys = dlg.selected_slots()
        if not selected_keys:
            QMessageBox.warning(self, "未选择插件", "请至少选择一个插件进行批量下载。")
            return

        selected_slots = [slots[k] for k in selected_keys]
        self.do_batch_download(selected_slots, project)

    def _find_split_files(self, file_map: dict, slot_name: str) -> list[tuple[str, int]]:
        """
        查找插件对应的所有分割文件。
        返回 [(文件名, file_id), ...] 列表，按原始文件、_1、_2...排序。
        """
        result = []
        # 先找原始文件 Plugin.json
        main_name = slot_name + ".json"
        if main_name in file_map:
            result.append((main_name, file_map[main_name]))

        # 再找分割文件 Plugin_1.json, Plugin_2.json...
        suffix = 1
        while True:
            split_name = f"{slot_name}_{suffix}.json"
            if split_name in file_map:
                result.append((split_name, file_map[split_name]))
                suffix += 1
            else:
                break

        return result

    def do_batch_download(self, selected_slots: list, project: dict):
        """执行批量下载（由 step3 调用）。"""
        from transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI

        project_id = project.get("id")
        project_name = project.get("name", "?")
        config = self._ctx.config

        # 确认对话框
        slot_names = [s.label or Path(s.esp_path).stem for s in selected_slots]
        items = [f"• {name}.json (含分割文件)" for name in slot_names]
        header = (
            f"即将从项目「{project_name}」下载\n"
            "每个插件将下载同名 JSON 文件及其分割文件（如 _1, _2）并合并。\n"
            "未找到同名文件的插件将被跳过。"
        )

        dlg = _BatchConfirmDialog("确认批量下载", header, items, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        def _batch_download_factory(progress_cb):
            results = BatchDownloadResult()
            downloader = ParaTranzDownloader(config)
            api = ParatranzFilesAPI(token=config.token, config=config)

            # 获取项目文件列表
            try:
                files = api.list_files(project_id) or []
                file_map = {f["name"]: f["id"] for f in files}
            except Exception as e:
                raise RuntimeError(f"获取文件列表失败：{e}")

            total = len(selected_slots)

            for i, slot in enumerate(selected_slots):
                slot_name = slot.label or Path(slot.esp_path).stem

                if progress_cb:
                    progress_cb(i, total, f"正在下载 {slot_name}…")

                # 查找所有分割文件
                split_files = self._find_split_files(file_map, slot_name)
                if not split_files:
                    results.skipped_count += 1
                    results.details.append(f"⊘ {slot_name}.json: 未找到同名文件")
                    continue

                # 收集所有 file_id
                file_ids = [fid for _, fid in split_files]
                file_names_str = ", ".join([name for name, _ in split_files])

                try:
                    result = downloader.download_to_collection(
                        project_id,
                        slot.collection,
                        file_ids=file_ids,
                    )
                    results.success_count += 1
                    results.merged_total += result.merged
                    results.details.append(f"✓ {slot_name}: 合并 {result.merged} 条 ({file_names_str})")
                except Exception as e:
                    results.failed_count += 1
                    results.details.append(f"✗ {slot_name}: {e}")

            if progress_cb:
                progress_cb(total, total, "下载完成")
            return results

        def _on_done(result: BatchDownloadResult):
            # 触发 collection_changed 更新 UI
            self._ctx.collection_changed.emit(self._ctx.collection)

            header = (
                f"成功：{result.success_count} 个\n"
                f"跳过：{result.skipped_count} 个\n"
                f"失败：{result.failed_count} 个\n"
                f"合并词条：{result.merged_total} 条"
            )
            dlg = _BatchResultDialog("批量下载完成", header, result.details, parent=self)
            dlg.exec()

        self._run_worker(
            fn_factory=_batch_download_factory,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "批量下载失败", e),
            progress_total=len(selected_slots),
            progress_msg="正在批量下载…",
        )

    def download(self):
        if self._dispatch_planned("download", self._ctx):
            return
        collection = self._ctx.collection
        project = self._ctx.current_project
        if not collection or not project:
            return
        project_id = project.get("id")
        config = self._ctx.config

        dlg = _FileSelectDialog(config, project_id, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        file_ids = dlg.selected_file_ids()

        def _download_factory(progress_cb):
            downloader = ParaTranzDownloader(config)
            return downloader.download_to_collection(
                project_id, collection, file_ids=file_ids, progress_callback=progress_cb
            )

        def _on_done(result):
            self._ctx.collection_changed.emit(self._ctx.collection)
            QMessageBox.information(
                self,
                "下载合并完成",
                f"已合并：{result.merged} 条\n"
                f"未匹配：{result.skipped_no_match} 条\n"
                f"跳过（stage 不足）：{result.skipped_low_stage} 条",
            )

        self._run_worker(
            fn_factory=_download_factory,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "下载失败", e),
            progress_total=len(file_ids),
            progress_msg="正在从 ParaTranz 下载合并…",
        )
