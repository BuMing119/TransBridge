"""ParaTranz API 配置。"""

import os
import configparser
from typing import Dict, Optional

from .paths import get_data_dir, get_config_file_path


class ParatranzConfig:
    """ParaTranz API 配置管理器。"""

    DEFAULT_BASE_URL = "https://paratranz.cn/api"
    DEFAULT_TIMEOUT = 30
    DEFAULT_HEADERS = {}
    DEFAULT_CONFIG_FILE = "paratranz_config.ini"

    def __init__(
        self,
        token: str = None,
        user_id: Optional[int] = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        self.token = token
        self.user_id: Optional[int] = user_id
        self.base_url = base_url
        self.timeout = timeout
        self.headers = self.DEFAULT_HEADERS.copy()
        if extra_headers:
            self.headers.update(extra_headers)
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"

    @staticmethod
    def get_data_dir() -> str:
        return get_data_dir()

    @staticmethod
    def get_config_file_path() -> str:
        return get_config_file_path()

    def get_headers(self) -> Dict[str, str]:
        return self.headers

    def update_token(self, new_token: str) -> None:
        self.token = new_token
        self.headers["Authorization"] = f"Bearer {self.token}"

    def update_timeout(self, new_timeout: int) -> None:
        self.timeout = new_timeout

    def add_header(self, key: str, value: str) -> None:
        self.headers[key] = value

    def remove_header(self, key: str) -> None:
        if key in self.headers:
            del self.headers[key]

    def save_to_file(self) -> None:
        config_path = get_config_file_path()
        config = configparser.ConfigParser()
        config.add_section("api")
        config.set("api", "base_url", self.base_url)
        config.set("api", "timeout", str(self.timeout))
        if self.token:
            config.set("api", "token", self.token)
        if self.user_id is not None:
            config.set("api", "user_id", str(self.user_id))
        headers_section = "headers"
        config.add_section(headers_section)
        for key, value in self.headers.items():
            if key != "Authorization":
                config.set(headers_section, key, value)
        with open(config_path, "w", encoding="utf-8") as f:
            config.write(f)

    @classmethod
    def load_from_file(cls, token: str = None) -> "ParatranzConfig":
        config_path = get_config_file_path()
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        config = configparser.ConfigParser()
        config.read(config_path, encoding="utf-8")
        if not config.has_section("api"):
            raise ValueError("不是有效的 Paratranz 配置文件")
        base_url = config.get("api", "base_url", fallback=cls.DEFAULT_BASE_URL)
        timeout = config.getint("api", "timeout", fallback=cls.DEFAULT_TIMEOUT)
        file_token = config.get("api", "token", fallback=None)
        if token is None:
            token = file_token
        user_id_str = config.get("api", "user_id", fallback=None)
        user_id = int(user_id_str) if user_id_str and user_id_str.isdigit() else None
        extra_headers = {}
        if config.has_section("headers"):
            for key, value in config.items("headers"):
                if key.lower() != "content-type":
                    extra_headers[key] = value
        return cls(
            token=token, user_id=user_id,
            base_url=base_url, timeout=timeout,
            extra_headers=extra_headers,
        )

    @classmethod
    def create_or_load(cls, token: str = None) -> "ParatranzConfig":
        try:
            return cls.load_from_file(token)
        except FileNotFoundError:
            return cls(token=token)
