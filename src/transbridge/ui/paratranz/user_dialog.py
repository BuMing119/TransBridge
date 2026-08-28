"""
UserInfoDialog: 当前用户信息查看与编辑对话框。
"""

from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI

from ..workers import ApiWorker


class UserInfoDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._user: dict | None = ctx.current_user
        self.setWindowTitle("我的信息")
        self.setMinimumSize(500, 480)
        self._init_ui()
        if self._user and self._user.get("id"):
            self._load_full_user()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 基本信息
        info_box = QGroupBox("用户信息")
        form = QFormLayout(info_box)

        self._lbl_username = QLabel("—")
        self._lbl_credit = QLabel("—")
        self._lbl_stats = QLabel("—")
        form.addRow("用户名:", self._lbl_username)
        form.addRow("信用值:", self._lbl_credit)
        form.addRow("翻译/编辑/审核:", self._lbl_stats)
        layout.addWidget(info_box)

        # 可编辑字段
        edit_box = QGroupBox("编辑资料")
        edit_form = QFormLayout(edit_box)
        self._nickname_input = QLineEdit()
        self._bio_input = QLineEdit()
        self._bio_input.setPlaceholderText("最长 140 字符")
        self._avatar_input = QLineEdit()
        self._avatar_input.setPlaceholderText("头像 URL（可选）")
        edit_form.addRow("昵称:", self._nickname_input)
        edit_form.addRow("个人介绍:", self._bio_input)
        edit_form.addRow("头像 URL:", self._avatar_input)
        layout.addWidget(edit_box)

        # 近期动态
        activity_box = QGroupBox("近期动态")
        activity_layout = QVBoxLayout(activity_box)
        self._activity_table = QTableWidget(0, 3)
        self._activity_table.setHorizontalHeaderLabels(["时间", "项目 ID", "词条 ID"])
        self._activity_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._activity_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._activity_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._activity_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._activity_table.setMaximumHeight(150)
        activity_layout.addWidget(self._activity_table)
        layout.addWidget(activity_box)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("保存资料")
        save_btn.clicked.connect(self._save_profile)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        # 填充当前已知数据
        if self._user:
            self._fill(self._user)

    def _fill(self, user: dict):
        self._lbl_username.setText(user.get("username", "—"))
        credit = user.get("credit", "—")
        credit_text = f"{credit}（账户受限）" if isinstance(credit, (int, float)) and credit < 0 else str(credit)
        self._lbl_credit.setText(credit_text)
        t = user.get("translated", 0)
        e = user.get("edited", 0)
        r = user.get("reviewed", 0)
        self._lbl_stats.setText(f"{t} / {e} / {r}")
        self._nickname_input.setText(user.get("nickname", "") or "")
        self._bio_input.setText(user.get("bio", "") or "")
        self._avatar_input.setText(user.get("avatar", "") or "")

    def _load_full_user(self):
        uid = self._user.get("id")
        if not uid:
            return
        config = self._ctx.config

        def _fetch():
            api = ParatranzUserAPI(token=config.token, config=config)
            user = api.get_user(uid)
            activities = api.get_user_activities(uid, page_size=20)
            return user, activities

        def _on_done(result):
            user, activities = result
            if user:
                self._user = user
                self._fill(user)
            acts = activities if isinstance(activities, list) else []
            self._activity_table.setRowCount(len(acts))
            for row, act in enumerate(acts):
                cells = [
                    str(act.get("createdAt", ""))[:10],
                    str(act.get("projectId", "—")),
                    str(act.get("stringId", "—")),
                ]
                for col, val in enumerate(cells):
                    self._activity_table.setItem(row, col, QTableWidgetItem(val))

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda _: None)
        w.start()
        self._workers.append(w)

    def _save_profile(self):
        uid = (self._user or {}).get("id")
        if not uid:
            QMessageBox.warning(self, "提示", "无法获取用户 ID，无法保存")
            return
        data = {
            "nickname": self._nickname_input.text().strip() or None,
            "bio": self._bio_input.text().strip() or None,
            "avatar": self._avatar_input.text().strip() or None,
        }
        data = {k: v for k, v in data.items() if v is not None}
        config = self._ctx.config

        def _save():
            api = ParatranzUserAPI(token=config.token, config=config)
            return api.update_user(uid, data)

        def _on_done(user):
            if user:
                self._ctx.current_user = user
            QMessageBox.information(self, "成功", "资料已保存")

        w = ApiWorker(_save)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.critical(self, "保存失败", e))
        w.start()
        self._workers.append(w)
