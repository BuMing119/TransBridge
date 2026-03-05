"""
配置管理器，用于存储和管理 Paratranz API 相关的配置信息
"""
import os
import configparser
from typing import Dict, Any, Optional


class ParatranzConfig:
    """Paratranz API 配置管理器"""

    # 默认配置
    DEFAULT_BASE_URL = "https://paratranz.cn/api"
    DEFAULT_TIMEOUT = 10
    DEFAULT_HEADERS = {
        "Content-Type": "application/json;charset=UTF-8"
    }
    # 默认配置文件名（INI格式）
    DEFAULT_CONFIG_FILE = "paratranz_config.ini"

    @staticmethod
    def get_data_dir():
        """获取数据目录路径"""
        # 获取项目根目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # 向上查找项目根目录（找到包含src的目录）
        project_root = current_dir
        while not os.path.exists(os.path.join(project_root, "src")) and project_root != os.path.dirname(project_root):
            project_root = os.path.dirname(project_root)

        # 创建data目录
        data_dir = os.path.join(project_root, "data")
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)

        return data_dir

    @staticmethod
    def get_config_file_path():
        """获取配置文件完整路径"""
        data_dir = ParatranzConfig.get_data_dir()
        return os.path.join(data_dir, ParatranzConfig.DEFAULT_CONFIG_FILE)

    def __init__(self,
                 token: str = None,
                 user_id: Optional[int] = None,
                 base_url: str = DEFAULT_BASE_URL,
                 timeout: int = DEFAULT_TIMEOUT,
                 extra_headers: Optional[Dict[str, str]] = None):
        """
        初始化配置

        Args:
            token: API 认证令牌
            user_id: 当前用户的 ParaTranz 数字 ID（需手动填写，ParaTranz 暂无自动获取接口）
            base_url: API 基础 URL，默认为官方 API 地址
            timeout: 请求超时时间（秒），默认为 10 秒
            extra_headers: 额外的 HTTP 请求头
        """
        self.token = token
        self.user_id: Optional[int] = user_id
        self.base_url = base_url
        self.timeout = timeout

        # 合并默认请求头和额外请求头
        self.headers = self.DEFAULT_HEADERS.copy()
        if extra_headers:
            self.headers.update(extra_headers)

        # 如果有 token，添加认证头
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"

    def get_headers(self) -> Dict[str, str]:
        """获取完整的请求头"""
        return self.headers

    def update_token(self, new_token: str) -> None:
        """更新认证令牌"""
        self.token = new_token
        self.headers["Authorization"] = f"Bearer {self.token}"

    def update_timeout(self, new_timeout: int) -> None:
        """更新超时时间"""
        self.timeout = new_timeout

    def add_header(self, key: str, value: str) -> None:
        """添加请求头"""
        self.headers[key] = value

    def remove_header(self, key: str) -> None:
        """移除请求头"""
        if key in self.headers:
            del self.headers[key]

    def save_to_file(self) -> None:
        """
        将配置保存到 INI 文件

        配置文件固定保存在项目的 data 目录中
        """
        config_path = self.get_config_file_path()

        # 创建配置解析器
        config = configparser.ConfigParser()

        # 添加配置节
        config.add_section('api')
        config.set('api', 'base_url', self.base_url)
        config.set('api', 'timeout', str(self.timeout))
        if self.token:
            config.set('api', 'token', self.token)
        if self.user_id is not None:
            config.set('api', 'user_id', str(self.user_id))

        # 保存除 Authorization 外的所有请求头
        headers_section = 'headers'
        config.add_section(headers_section)
        for key, value in self.headers.items():
            if key != "Authorization":
                config.set(headers_section, key, value)

        # 写入文件
        with open(config_path, 'w', encoding='utf-8') as f:
            config.write(f)

    @classmethod
    def load_from_file(cls, token: str = None) -> 'ParatranzConfig':
        """
        从 INI 文件加载配置

        Args:
            token: API 认证令牌，如果提供则覆盖文件中的令牌

        Returns:
            加载的配置对象

        Raises:
            FileNotFoundError: 如果配置文件不存在
            ValueError: 如果配置文件格式不正确
        """
        config_path = cls.get_config_file_path()

        # 检查文件是否存在
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        # 创建配置解析器并读取文件
        config = configparser.ConfigParser()
        config.read(config_path, encoding='utf-8')

        # 验证配置文件格式
        if not config.has_section('api'):
            raise ValueError("不是有效的 Paratranz 配置文件")

        # 提取配置项
        base_url = config.get('api', 'base_url', fallback=cls.DEFAULT_BASE_URL)
        timeout = config.getint('api', 'timeout', fallback=cls.DEFAULT_TIMEOUT)

        # 从文件中读取 token（如果存在且没有提供 token 参数）
        file_token = config.get('api', 'token', fallback=None)
        if token is None:
            token = file_token

        # 从文件中读取 user_id
        user_id_str = config.get('api', 'user_id', fallback=None)
        user_id = int(user_id_str) if user_id_str and user_id_str.isdigit() else None

        # 提取额外的请求头（跳过 content-type，由 _request 按需设置）
        extra_headers = {}
        if config.has_section('headers'):
            for key, value in config.items('headers'):
                if key.lower() != "content-type":
                    extra_headers[key] = value

        # 创建配置对象
        config_obj = cls(
            token=token,
            user_id=user_id,
            base_url=base_url,
            timeout=timeout,
            extra_headers=extra_headers,
        )

        return config_obj

    @classmethod
    def create_or_load(cls, token: str = None) -> 'ParatranzConfig':
        """
        尝试从文件加载配置，如果文件不存在则创建新配置

        Args:
            token: API 认证令牌

        Returns:
            配置对象
        """
        try:
            # 尝试加载配置文件
            return cls.load_from_file(token)
        except FileNotFoundError:
            # 文件不存在，创建新配置
            return cls(token=token)
