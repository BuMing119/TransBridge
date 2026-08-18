"""
OverviewTab: 项目概览标签页，展示基本信息与翻译进度统计。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QProgressBar, QPushButton, QMessageBox, QFormLayout,
    QDialog, QLineEdit, QTextEdit, QComboBox,
)
from PyQt6.QtCore import Qt

from transbridge.paratranz.api.paratranz_project_api import ParatranzProjectAPI
from transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
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


class EditProjectDialog(QDialog):

    def __init__(self, project: dict, parent=None):
        super().__init__(parent)
        self._project = project
        self.setWindowTitle("编辑项目信息")
        self.setMinimumWidth(400)
        self._init_ui()
        self._populate(project)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name = QLineEdit()
        self._name.setPlaceholderText("必填")
        form.addRow("项目名称 *", self._name)

        self._desc = QTextEdit()
        self._desc.setMaximumHeight(70)
        form.addRow("项目说明", self._desc)

        self._game = QLineEdit()
        form.addRow("所属游戏", self._game)

        def _combo(items):
            cb = QComboBox()
            cb.addItems(items)
            return cb

        self._privacy = _combo(["公开 (0)", "内部 (1)", "私密 (2)"])
        form.addRow("隐私设置", self._privacy)
        self._download = _combo(["公开 (0)", "内部 (1)", "私密 (2)"])
        form.addRow("下载权限", self._download)
        self._issue_mode = _combo(["公开 (0)", "内部 (1)", "私密 (2)"])
        form.addRow("讨论权限", self._issue_mode)
        self._review_mode = _combo(["无须 (0)", "一次 (1)", "二次 (2)"])
        form.addRow("校对等级", self._review_mode)
        self._join_mode = _combo(["公开 (0)", "申请 (1)", "测试 (2)", "私密 (3)"])
        form.addRow("加入方式", self._join_mode)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("保存")
        ok_btn.clicked.connect(self._validate)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _populate(self, p: dict):
        self._name.setText(p.get("name", ""))
        self._desc.setPlainText(p.get("desc") or p.get("description") or "")
        self._game.setText(p.get("game") or "")
        self._privacy.setCurrentIndex(p.get("privacy", 0))
        self._download.setCurrentIndex(p.get("download", 0))
        self._issue_mode.setCurrentIndex(p.get("issueMode", 0))
        self._review_mode.setCurrentIndex(p.get("reviewMode", 0))
        self._join_mode.setCurrentIndex(p.get("joinMode", 0))

    def _validate(self):
        if not self._name.text().strip():
            QMessageBox.warning(self, "提示", "项目名称不能为空")
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "name": self._name.text().strip(),
            "desc": self._desc.toPlainText().strip(),
            "game": self._game.text().strip() or None,
            "privacy": self._privacy.currentIndex(),
            "download": self._download.currentIndex(),
            "issueMode": self._issue_mode.currentIndex(),
            "reviewMode": self._review_mode.currentIndex(),
            "joinMode": self._join_mode.currentIndex(),
        }


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
        self._prog_bar.setTextVisible(True)
        prog_layout.addWidget(self._prog_bar)

        stats_grid = QHBoxLayout()
        self._stat_labels: dict[str, QLabel] = {}
        for key, text in (
            ("total", "总词条"),
            ("translated", "已翻译"),
            ("disputed", "有疑问"),
            ("checked", "已检查"),
            ("reviewed", "已审核"),
        ):
            col = QVBoxLayout()
            col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            val_lbl = QLabel("—")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_lbl = QLabel(text)
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_lbl.setStyleSheet("color: gray; font-size: 11px;")
            col.addWidget(val_lbl)
            col.addWidget(name_lbl)
            stats_grid.addLayout(col)
            self._stat_labels[key] = val_lbl

        prog_layout.addLayout(stats_grid)
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
        self._prog_bar.setFormat("请先选择项目")
        for lbl in self._stat_labels.values():
            lbl.setText("—")
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

        self._prog_bar.setFormat("加载中…")
        for lbl in self._stat_labels.values():
            lbl.setText("…")

        def _fetch():
            api = ParatranzFilesAPI(token=config.token, config=config)
            return _extract_list(api.list_files(project_id))

        def _on_files(files: list):
            if self._gen != gen:
                return
            total = sum(f.get("total", 0) for f in files)
            translated = sum(f.get("translated", 0) for f in files)
            disputed = sum(f.get("disputed", 0) for f in files)
            checked = sum(f.get("checked", 0) for f in files)
            reviewed = sum(f.get("reviewed", 0) for f in files)

            if total > 0:
                pct = int(translated / total * 100)
                self._prog_bar.setValue(pct)
                self._prog_bar.setFormat(f"{pct}%  （已翻译 {translated} / 共 {total}）")
            else:
                self._prog_bar.setValue(0)
                self._prog_bar.setFormat("暂无数据")

            def _fmt(n, t):
                if t > 0:
                    return f"{n}\n({int(n/t*100)}%)"
                return str(n)

            self._stat_labels["total"].setText(str(total))
            self._stat_labels["translated"].setText(_fmt(translated, total))
            self._stat_labels["disputed"].setText(_fmt(disputed, total))
            self._stat_labels["checked"].setText(_fmt(checked, total))
            self._stat_labels["reviewed"].setText(_fmt(reviewed, total))

        def _on_error(_):
            if self._gen != gen:
                return
            self._prog_bar.setFormat("加载失败")
            for lbl in self._stat_labels.values():
                lbl.setText("—")

        w = ApiWorker(_fetch)
        w.result.connect(_on_files)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _edit_project(self):
        project = self._ctx.current_project
        if not project:
            return

        dlg = EditProjectDialog(project, self)
        if not dlg.exec():
            return

        data = dlg.get_data()
        config = self._ctx.config
        project_id = project.get("id")

        def _update():
            api = ParatranzProjectAPI(token=config.token, config=config)
            return api.update_project(project_id, data)

        def _on_done(updated: dict):
            # 合并更新字段到上下文中的项目对象（API 可能返回 None）
            merged = {**project, **updated} if isinstance(updated, dict) else {**project, **data}
            self._ctx.current_project = merged
            QMessageBox.information(self, "成功", "项目信息已更新")

        w = ApiWorker(_update)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.critical(self, "更新失败", e))
        w.start()
        self._workers.append(w)

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
            self._ctx.current_project = None
            self._ctx.project_list_changed.emit()
            QMessageBox.information(self, "成功", "项目已删除")

        w = ApiWorker(_delete)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.critical(self, "删除失败", e))
        w.start()
        self._workers.append(w)
