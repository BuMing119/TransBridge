"""
ConfigDialog: API Token 配置对话框。
启动时若无有效 Token 则自动弹出，也可通过「设置 / API 配置」手动打开。
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QSpinBox, QLabel, QPushButton, QHBoxLayout,
)
from PyQt6.QtCore import pyqtSignal, Qt

from src.transbridge.paratranz.config_manager import ParatranzConfig
from src.transbridge.paratranz.api.paratranz_project_api import ParatranzProjectAPI
from src.transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI


class ConfigDialog(QDialog):

    config_saved = pyqtSignal(object)  # ParatranzConfig

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        # 设置为独立窗口，使其在任务栏显示，并始终置顶
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowCloseButtonHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowTitle("API 配置")
        self.setFixedSize(460, 250)
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
        help_link = QLabel('<a href="https://paratranz.cn/users/my">点击打开 ParaTranz 个人设置页选择“设置”获取 Token</a>')
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

        layout.addStretch()

        btn_row = QHBoxLayout()
        self._verify_btn = QPushButton("验证并保存")
        self._verify_btn.setDefault(True)
        self._verify_btn.clicked.connect(self._verify_and_save)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self._verify_btn)
        btn_row.addWidget(cancel_btn)
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

        self._verify_btn.setEnabled(False)
        self._status_lbl.setText("正在验证…")

        # 同步验证（在主线程，因为是对话框且用户会等待）
        try:
            tmp_cfg = ParatranzConfig(token=token, timeout=self._timeout_spin.value())
            api = ParatranzProjectAPI(token=token, config=tmp_cfg)
            api.list_projects(page=1, page_size=1)

            # 自动获取当前用户 uid
            user_api = ParatranzUserAPI(token=token, config=tmp_cfg)
            me = user_api.get_my_user()
            uid = me.get("id") if isinstance(me, dict) else None
            nickname = me.get("nickname") or me.get("username") or "已登录"

            self._ctx.config.update_token(token)
            self._ctx.config.update_timeout(self._timeout_spin.value())
            self._ctx.config.user_id = uid
            self._ctx.config.save_to_file()
            self._ctx.config = self._ctx.config  # 触发 config_changed 信号
            self._status_lbl.setText(f"验证成功，已登录为：{nickname}（ID: {uid}）")
            self.config_saved.emit(self._ctx.config)
            self.accept()
        except Exception as e:
            self._status_lbl.setText(f"验证失败：{e}")
        finally:
            self._verify_btn.setEnabled(True)
