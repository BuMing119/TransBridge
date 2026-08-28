"""
ConfigDialog: API Token 配置对话框。
启动时若无有效 Token 则自动弹出，也可通过「设置 / API 配置」手动打开。
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from transbridge.paratranz.api.paratranz_project_api import ParatranzProjectAPI
from transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI
from transbridge.paratranz.config_manager import ParatranzConfig
from transbridge.ui.workers import ApiWorker


class ConfigDialog(QDialog):
    config_saved = pyqtSignal(object)  # ParatranzConfig

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._verify_worker: ApiWorker | None = None
        self._verified = False
        # 设置为独立窗口，使其在任务栏显示，并始终置顶
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowTitle("API 配置")
        self.setFixedSize(460, 280)
        self._init_ui()
        self._load_current()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        box = QGroupBox("ParaTranz API 配置")
        form = QFormLayout(box)

        self._token_input = QLineEdit()
        self._token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_input.setPlaceholderText("在 ParaTranz 个人设置页获取")
        form.addRow("API Token:", self._token_input)

        # 添加帮助链接
        help_link = QLabel(
            '<a href="https://paratranz.cn/users/my">点击打开 ParaTranz 个人设置页选择“设置”获取 Token</a>'
        )
        help_link.setOpenExternalLinks(True)
        form.addRow("", help_link)

        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(5, 60)
        self._timeout_spin.setValue(15)
        self._timeout_spin.setSuffix(" 秒")
        form.addRow("请求超时:", self._timeout_spin)

        layout.addWidget(box)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        layout.addWidget(self._status_lbl)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.hide()
        layout.addWidget(self._progress)

        layout.addStretch()

        btn_row = QHBoxLayout()
        self._verify_btn = QPushButton("验证并保存")
        self._verify_btn.setDefault(True)
        self._verify_btn.clicked.connect(self._verify_and_save)
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self._verify_btn)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

    def _load_current(self):
        cfg = self._ctx.config
        self._token_input.setText(cfg.token or "")
        self._timeout_spin.setValue(cfg.timeout or 15)

    def _verify_and_save(self):
        token = self._token_input.text().strip()
        if not token:
            self._status_lbl.setText("请输入 API Token。")
            return

        timeout = self._timeout_spin.value()
        self._verified = False
        self._set_verifying(True, "正在后台验证…")

        def _verify():
            tmp_cfg = ParatranzConfig(token=token, timeout=timeout)
            api = ParatranzProjectAPI(token=token, config=tmp_cfg)
            api.list_projects(page=1, page_size=1)

            # 自动获取当前用户 uid
            user_api = ParatranzUserAPI(token=token, config=tmp_cfg)
            me = user_api.get_my_user()
            uid = me.get("id") if isinstance(me, dict) else None
            nickname = me.get("nickname") or me.get("username") or "已登录"
            return uid, nickname

        def _on_verified(result):
            uid, nickname = result
            self._ctx.config.update_token(token)
            self._ctx.config.update_timeout(timeout)
            self._ctx.config.user_id = uid
            self._ctx.config.save_to_file()
            self._ctx.config = self._ctx.config  # 触发 config_changed 信号
            self._status_lbl.setText(f"验证成功，已登录为：{nickname}（ID: {uid}）")
            self.config_saved.emit(self._ctx.config)
            self._verified = True

        def _on_error(message: str):
            self._status_lbl.setText(f"验证失败：{message}")

        worker = ApiWorker(_verify, route_http_errors=False)
        worker.result.connect(_on_verified)
        worker.error.connect(_on_error)
        worker.finished.connect(self._finish_verification)
        worker.start()
        self._verify_worker = worker

    def _set_verifying(self, verifying: bool, message: str = "") -> None:
        self._verify_btn.setEnabled(not verifying)
        self._token_input.setEnabled(not verifying)
        self._timeout_spin.setEnabled(not verifying)
        self._cancel_btn.setEnabled(not verifying)
        self._progress.setVisible(verifying)
        if message:
            self._status_lbl.setText(message)

    def _finish_verification(self) -> None:
        self._set_verifying(False)
        if self._verified:
            self.accept()
