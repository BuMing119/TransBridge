"""
ConfigDialog: API Token 配置对话框。
启动时若无有效 Token 则自动弹出，也可通过「设置 / API 配置」手动打开。
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QSpinBox, QLabel, QPushButton, QHBoxLayout, QMessageBox,
    QFrame,
)
from PyQt6.QtCore import pyqtSignal

from src.transbridge.paratranz.config_manager import ParatranzConfig
from src.transbridge.paratranz.api.paratranz_project_api import ParatranzProjectAPI


class ConfigDialog(QDialog):

    config_saved = pyqtSignal(object)  # ParatranzConfig

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self.setWindowTitle("API 配置")
        self.setFixedSize(460, 260)
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

        self._user_id_spin = QSpinBox()
        self._user_id_spin.setRange(0, 999999999)
        self._user_id_spin.setSpecialValueText("未设置")  # 0 显示为"未设置"
        self._user_id_spin.setToolTip(
            "你的 ParaTranz 用户数字 ID。\n"
            "获取方式：登录后访问个人主页，URL 中的数字即为 user_id。\n"
            "（ParaTranz 暂无自动获取接口，需手动填写）"
        )
        form.addRow("用户 ID:", self._user_id_spin)

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
        self._user_id_spin.setValue(cfg.user_id or 0)
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
            # 验证成功
            self._ctx.config.update_token(token)
            self._ctx.config.update_timeout(self._timeout_spin.value())
            uid = self._user_id_spin.value()
            self._ctx.config.user_id = uid if uid > 0 else None
            self._ctx.config.save_to_file()
            self._ctx.config = self._ctx.config  # 触发 config_changed 信号
            uid_hint = f"用户 ID：{self._ctx.config.user_id}" if self._ctx.config.user_id else "未设置用户 ID，将无法显示用户信息"
            self._status_lbl.setText(f"Token 验证成功，配置已保存。{uid_hint}")
            self.config_saved.emit(self._ctx.config)
            self.accept()
        except Exception as e:
            self._status_lbl.setText(f"验证失败：{e}")
        finally:
            self._verify_btn.setEnabled(True)
