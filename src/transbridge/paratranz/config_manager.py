"""
配置管理器，用于存储和管理 Paratranz API 相关的配置信息
"""
import os
import configparser
from dataclasses import dataclass, field
from typing import Dict, Any, Optional


# ─────────────────────────────── LLMConfig ───────────────────────────────────

@dataclass
class LLMConfig:
    """LLM 翻译功能配置，独立于 ParatranzConfig，共享同一 INI 文件的 [llm] 节。"""

    provider: str = "openai_compatible"        # openai_compatible | anthropic
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    max_concurrent: int = 3
    max_tokens_per_batch: int = 2000   # 每批输入 token 估算上限（用于拆批）
    max_output_tokens: int = 0         # 0 = 不限制（由模型默认），Anthropic fallback 到 8192
    term_priority: list = field(default_factory=lambda: ["dynamic", "paratranz", "json", "excel"])
    local_json_path: str = ""
    local_excel_path: str = ""
    excel_original_col: str = "A"
    excel_translation_col: str = "B"
    game_profile: str = "skyrim_se"   # data/prompts/games/{game_profile}.toml
    target_lang: str = "zh_CN"        # data/prompts/langs/{target_lang}.toml

    def save_to_file(self) -> None:
        """将 [llm] 节写入共享 INI 文件（不覆盖其他节）。"""
        config_path = ParatranzConfig.get_config_file_path()
        config = configparser.ConfigParser()
        if os.path.exists(config_path):
            config.read(config_path, encoding="utf-8")
        if not config.has_section("llm"):
            config.add_section("llm")
        config.set("llm", "provider", self.provider)
        config.set("llm", "api_key", self.api_key)
        config.set("llm", "base_url", self.base_url)
        config.set("llm", "model", self.model)
        config.set("llm", "max_concurrent", str(self.max_concurrent))
        config.set("llm", "max_tokens_per_batch", str(self.max_tokens_per_batch))
        config.set("llm", "max_output_tokens", str(self.max_output_tokens))
        config.set("llm", "term_priority", ",".join(self.term_priority))
        config.set("llm", "local_json_path", self.local_json_path)
        config.set("llm", "local_excel_path", self.local_excel_path)
        config.set("llm", "excel_original_col", self.excel_original_col)
        config.set("llm", "excel_translation_col", self.excel_translation_col)
        config.set("llm", "game_profile", self.game_profile)
        config.set("llm", "target_lang", self.target_lang)
        with open(config_path, "w", encoding="utf-8") as f:
            config.write(f)

    @classmethod
    def load_from_file(cls) -> "LLMConfig":
        """从共享 INI 文件加载 [llm] 节，文件不存在或缺少节时返回默认值。"""
        config_path = ParatranzConfig.get_config_file_path()
        obj = cls()
        if not os.path.exists(config_path):
            return obj
        config = configparser.ConfigParser()
        config.read(config_path, encoding="utf-8")
        if not config.has_section("llm"):
            return obj
        obj.provider = config.get("llm", "provider", fallback=obj.provider)
        obj.api_key = config.get("llm", "api_key", fallback=obj.api_key)
        obj.base_url = config.get("llm", "base_url", fallback=obj.base_url)
        obj.model = config.get("llm", "model", fallback=obj.model)
        obj.max_concurrent = config.getint("llm", "max_concurrent", fallback=obj.max_concurrent)
        obj.max_tokens_per_batch = config.getint("llm", "max_tokens_per_batch", fallback=obj.max_tokens_per_batch)
        obj.max_output_tokens = config.getint("llm", "max_output_tokens", fallback=obj.max_output_tokens)
        priority_str = config.get("llm", "term_priority", fallback="")
        if priority_str:
            obj.term_priority = [p.strip() for p in priority_str.split(",") if p.strip()]
        obj.local_json_path = config.get("llm", "local_json_path", fallback=obj.local_json_path)
        obj.local_excel_path = config.get("llm", "local_excel_path", fallback=obj.local_excel_path)
        obj.excel_original_col = config.get("llm", "excel_original_col", fallback=obj.excel_original_col)
        obj.excel_translation_col = config.get("llm", "excel_translation_col", fallback=obj.excel_translation_col)
        obj.game_profile = config.get("llm", "game_profile", fallback=obj.game_profile)
        obj.target_lang = config.get("llm", "target_lang", fallback=obj.target_lang)
        return obj


class ParatranzConfig:
    """Paratranz API 配置管理器"""

    # 默认配置
    DEFAULT_BASE_URL = "https://paratranz.cn/api"
    DEFAULT_TIMEOUT = 10
    DEFAULT_HEADERS = {}
    # 默认配置文件名（INI格式）
    DEFAULT_CONFIG_FILE = "paratranz_config.ini"

    @staticmethod
    def get_data_dir():
        """获取数据目录路径"""
        import sys
        if getattr(sys, "frozen", False):
            # PyInstaller 打包环境：data 目录与 exe 同级
            base_dir = os.path.dirname(sys.executable)
        else:
            # 开发环境：向上查找包含 src 的项目根目录
            current_dir = os.path.dirname(os.path.abspath(__file__))
            base_dir = current_dir
            while not os.path.exists(os.path.join(base_dir, "src")) and base_dir != os.path.dirname(base_dir):
                base_dir = os.path.dirname(base_dir)

        data_dir = os.path.join(base_dir, "data")
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
            user_id: 当前用户的 ParaTranz 数字 ID（通过 GET /users/my 自动获取后缓存）
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

        # 提取额外的请求头（跳过 content-type，兼容旧配置文件中可能残留的该字段）
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
