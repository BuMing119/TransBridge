
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QSpinBox, QMessageBox, QGroupBox, QFormLayout,
    QLabel
)
from PyQt6.QtCore import pyqtSignal
from src.transbridge.paratranz.api.paratranz_project_api import ParatranzProjectAPI
from src.transbridge.paratranz.config_manager import ParatranzConfig

class ParatranzClientUI(QDialog):
    """
    Paratranz 客户端配置界面
    用于配置 API 令牌和基本设置
    """

    # 信号：当配置完成并点击确定时发出
    config_updated = pyqtSignal(object)  # 改为发送 ParatranzConfig 对象

    def __init__(self, parent=None, initial_config=None):
        super().__init__(parent)
        self.setWindowTitle("Paratranz API 配置")
        self.resize(500, 350)

        # 初始化配置
        if isinstance(initial_config, ParatranzConfig):
            self.config = initial_config
        elif initial_config:
            # 如果是字典格式，转换为 ParatranzConfig 对象
            self.config = ParatranzConfig(
                token=initial_config.get("token", ""),
                timeout=initial_config.get("timeout", 10)
            )
        else:
            # 尝试加载配置，如果不存在则创建新配置
            self.config = ParatranzConfig.create_or_load()

        self.init_ui()

    def init_ui(self):
        # 主布局
        layout = QVBoxLayout()

        # 配置组
        config_group = QGroupBox("API 配置")
        config_layout = QFormLayout()

        # 令牌输入
        token_value = self.config.token if self.config.token else ""
        self.token_input = QLineEdit(token_value)
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_input.setPlaceholderText("请输入 Paratranz API 令牌")
        config_layout.addRow("API 令牌:", self.token_input)

        # 超时设置
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(1, 60)
        self.timeout_input.setValue(self.config.timeout)
        self.timeout_input.setSuffix(" 秒")
        config_layout.addRow("请求超时:", self.timeout_input)

        # 基础URL设置
        # self.base_url_input = QLineEdit(self.config.base_url)
        # self.base_url_input.setPlaceholderText("API 基础 URL")
        # config_layout.addRow("API 基础 URL:", self.base_url_input)

        config_group.setLayout(config_layout)
        layout.addWidget(config_group)

        # 配置信息提示
        config_info_group = QGroupBox("配置信息")
        config_info_layout = QVBoxLayout()

        info_label = QLabel("配置将自动保存到项目的 data 目录中")
        config_info_layout.addWidget(info_label)

        config_info_group.setLayout(config_info_layout)
        layout.addWidget(config_info_group)

        # 按钮区域
        button_layout = QHBoxLayout()

        # 测试连接按钮
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self.test_connection)
        button_layout.addWidget(self.test_button)

        button_layout.addStretch()

        # 确定和取消按钮
        self.ok_button = QPushButton("确定")
        self.ok_button.clicked.connect(self.accept_config)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def test_connection(self):
        """测试 API 连接"""
        token = self.token_input.text().strip()
        if not token:
            QMessageBox.warning(self, "警告", "请先输入 API 令牌")
            return

        try:
            # 创建临时配置用于测试
            temp_config = ParatranzConfig(
                token=token,
                timeout=self.timeout_input.value(),
                base_url=ParatranzConfig.DEFAULT_BASE_URL  # 使用默认URL，因为UI中base_url_input被注释了
            )

            # 创建客户端并测试连接
            client = ParatranzProjectAPI(token=token, config=temp_config)
            # 尝试获取项目列表作为测试
            result = client.list_projects()
            QMessageBox.information(self, "成功", "API 连接测试成功！")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"连接测试失败: {str(e)}")

    def accept_config(self):
        """保存配置并关闭对话框"""
        token = self.token_input.text().strip()
        if not token:
            QMessageBox.warning(self, "警告", "请输入有效的 API 令牌")
            return

        # 更新配置对象
        self.config.update_token(token)
        self.config.update_timeout(self.timeout_input.value())

        # 基础URL设置已暂时禁用
        # base_url = self.base_url_input.text().strip()
        # if base_url:
        #     self.config.base_url = base_url

        # 保存配置到data目录
        try:
            self.config.save_to_file()
            QMessageBox.information(self, "成功", "配置已保存")
        except Exception as e:
            QMessageBox.warning(self, "警告", f"保存配置失败: {str(e)}")

        # 发出配置更新信号
        self.config_updated.emit(self.config)

        # 不自动关闭对话框，让用户手动关闭
        # self.accept()

    def get_config(self):
        """获取当前配置"""
        return self.config
