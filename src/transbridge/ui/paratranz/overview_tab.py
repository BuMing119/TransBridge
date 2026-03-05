"""
OverviewTab: 项目概览标签页，展示基本信息与翻译进度统计。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QProgressBar, QPushButton, QMessageBox, QFormLayout,
)
from PyQt6.QtCore import Qt

from src.transbridge.paratranz.api.paratranz_project_api import ParatranzProjectAPI
from src.transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
from ..workers import ApiWorker


def _extract_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "results", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


_PRIVACY_LABELS = {0: "公开", 1: "内部", 2: "私密"}
_REVIEW_LABELS = {0: "无须校对", 1: "一次校对", 2: "二次校对"}
_JOIN_LABELS = {0: "公开加入", 1: "申请加入", 2: "测试加入", 3: "私密"}


class OverviewTab(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._gen = 0
        self._init_ui()
        ctx.project_selected.connect(self.set_project)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 基本信息
        info_box = QGroupBox("项目信息")
        self._info_form = QFormLayout(info_box)
        self._lbl_name = QLabel("—")
        self._lbl_desc = QLabel("—")
        self._lbl_desc.setWordWrap(True)
        self._lbl_game = QLabel("—")
        self._lbl_privacy = QLabel("—")
        self._lbl_review = QLabel("—")
        self._lbl_join = QLabel("—")
        self._lbl_members = QLabel("—")
        for label_text, widget in (
            ("项目名称", self._lbl_name),
            ("项目说明", self._lbl_desc),
            ("所属游戏", self._lbl_game),
            ("隐私设置", self._lbl_privacy),
            ("校对模式", self._lbl_review),
            ("加入方式", self._lbl_join),
            ("成员数量", self._lbl_members),
        ):
            self._info_form.addRow(label_text + ":", widget)
        layout.addWidget(info_box)

        # 进度统计
        prog_box = QGroupBox("翻译进度")
        prog_layout = QVBoxLayout(prog_box)
        self._prog_bar = QProgressBar()
        self._prog_bar.setRange(0, 100)
        self._prog_label = QLabel("—")
        prog_layout.addWidget(self._prog_bar)
        prog_layout.addWidget(self._prog_label)
        layout.addWidget(prog_box)

        # 操作按钮（仅管理员可见）
        self._admin_row = QHBoxLayout()
        self._edit_btn = QPushButton("编辑项目信息")
        self._edit_btn.clicked.connect(self._edit_project)
        self._del_btn = QPushButton("删除项目")
        self._del_btn.setStyleSheet("color: red;")
        self._del_btn.clicked.connect(self._delete_project)
        self._admin_row.addWidget(self._edit_btn)
        self._admin_row.addWidget(self._del_btn)
        self._admin_row.addStretch()
        layout.addLayout(self._admin_row)

        layout.addStretch()

        self._set_empty()

    def _set_empty(self):
        for lbl in (self._lbl_name, self._lbl_desc, self._lbl_game,
                    self._lbl_privacy, self._lbl_review, self._lbl_join, self._lbl_members):
            lbl.setText("—")
        self._prog_bar.setValue(0)
        self._prog_label.setText("请先选择一个项目")
        self._edit_btn.setVisible(False)
        self._del_btn.setVisible(False)

    def set_project(self, project: dict | None):
        if not project:
            self._set_empty()
            return

        p = project
        self._lbl_name.setText(p.get("name", "—"))
        self._lbl_desc.setText(p.get("desc") or p.get("description") or "—")
        self._lbl_game.setText(p.get("game") or "—")
        self._lbl_privacy.setText(_PRIVACY_LABELS.get(p.get("privacy", 0), "—"))
        self._lbl_review.setText(_REVIEW_LABELS.get(p.get("reviewMode", 0), "—"))
        self._lbl_join.setText(_JOIN_LABELS.get(p.get("joinMode", 0), "—"))
        self._lbl_members.setText(str(p.get("total", "—")))

        is_admin = self._ctx.is_admin()
        self._edit_btn.setVisible(is_admin)
        self._del_btn.setVisible(is_admin)

        self._load_progress(p.get("id"))

    def _load_progress(self, project_id):
        if not project_id:
            return
        config = self._ctx.config
        self._gen += 1
        gen = self._gen

        def _fetch():
            api = ParatranzFilesAPI(token=config.token, config=config)
            return _extract_list(api.list_files(project_id))

        def _on_files(files: list):
            if self._gen != gen:
                return
            total = sum(f.get("total", 0) for f in files)
            translated = sum(f.get("translated", 0) for f in files)
            if total > 0:
                pct = int(translated / total * 100)
                self._prog_bar.setValue(pct)
                self._prog_label.setText(
                    f"总词条：{total}  已翻译：{translated}（{pct}%）"
                )
            else:
                self._prog_bar.setValue(0)
                self._prog_label.setText("暂无数据")

        w = ApiWorker(_fetch)
        w.result.connect(_on_files)
        w.error.connect(lambda _: None)
        w.start()
        self._workers.append(w)

    def _edit_project(self):
        QMessageBox.information(self, "提示", "项目编辑功能暂未实现")

    def _delete_project(self):
        project = self._ctx.current_project
        if not project:
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除项目「{project.get('name', '')}」吗？此操作不可撤销！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        config = self._ctx.config
        project_id = project.get("id")

        def _delete():
            api = ParatranzProjectAPI(token=config.token, config=config)
            return api.delete_project(project_id)

        def _on_done(_):
            QMessageBox.information(self, "成功", "项目已删除")
            self._ctx.current_project = None

        w = ApiWorker(_delete)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.critical(self, "删除失败", e))
        w.start()
        self._workers.append(w)
