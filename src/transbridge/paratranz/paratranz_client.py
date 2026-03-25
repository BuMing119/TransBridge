import ssl
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from .config_manager import ParatranzConfig


class _SSLAdapter(HTTPAdapter):
    """自定义 SSL 适配器，忽略服务器异常关闭 TLS 连接的错误（UNEXPECTED_EOF_WHILE_READING）"""

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)


class ParatranzClient:
    """Paratranz API 客户端"""

    def __init__(self, token: str, timeout: int = 10, config: ParatranzConfig = None):
        """
        初始化 Paratranz 客户端

        Args:
            token: API 认证令牌
            timeout: 请求超时时间（秒），默认为 10 秒
            config: 配置管理器实例，如果不提供则会使用默认配置创建一个
        """
        if config is None:
            self.config = ParatranzConfig(token=token, timeout=timeout)
        else:
            self.config = config
            if token:
                self.config.update_token(token)
            if timeout != 10:
                self.config.update_timeout(timeout)
        self._session = requests.Session()
        self._session.mount("https://", _SSLAdapter())

    def _request(self, method: str, endpoint: str, **kwargs):
        """
        发送 HTTP 请求

        Args:
            method: HTTP 方法 (GET, POST, PUT, DELETE 等)
            endpoint: API 端点路径
            **kwargs: 其他 requests.request 参数

        Returns:
            API 响应的 JSON 数据

        Raises:
            RuntimeError: 当请求失败或 API 返回错误时
        """
        url = f"{self.config.base_url}{endpoint}"
        headers = self.config.get_headers().copy()
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    timeout=self.config.timeout,
                    **kwargs
                )
            except requests.exceptions.RequestException as e:
                raise RuntimeError(f"HTTP request failed: {e}")

            if response.status_code == 429:
                if attempt < max_retries:
                    wait = int(response.headers.get("Retry-After", "5"))
                    time.sleep(wait)
                    continue
                else:
                    raise RuntimeError(
                        f"API Error 429: 请求过于频繁，已重试 {max_retries} 次"
                    )

            if response.status_code == 204:  # No Content
                return None

            if not response.ok:
                if response.status_code == 401:
                    print(f"认证失败，当前使用的token: {self.config.token}")
                raise RuntimeError(
                    f"API Error {response.status_code}: {response.text}"
                )

            if not response.content.strip():
                return None

            try:
                return response.json()
            except ValueError:
                return None
        return None  # 不可达，满足静态分析器

    def _request_multipart(self, method: str, endpoint: str, body: bytes, content_type: str):
        """
        发送预先编码好的 multipart/form-data 请求。
        用于需要 RFC 5987 文件名编码的上传操作，绕过 requests 自带的 files= 参数处理。
        """
        url = f"{self.config.base_url}{endpoint}"
        base_headers = self.config.get_headers()
        auth = base_headers.get("Authorization") or base_headers.get("authorization", "")
        headers = {"Authorization": auth, "Content-Type": content_type}

        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=body,
                    timeout=self.config.timeout,
                )
            except requests.exceptions.RequestException as e:
                raise RuntimeError(f"HTTP request failed: {e}")

            if response.status_code == 429:
                if attempt < max_retries:
                    wait = int(response.headers.get("Retry-After", "5"))
                    time.sleep(wait)
                    continue
                else:
                    raise RuntimeError(
                        f"API Error 429: 请求过于频繁，已重试 {max_retries} 次"
                    )

            if response.status_code == 204:
                return None

            if not response.ok:
                if response.status_code == 401:
                    print(f"认证失败，当前使用的token: {self.config.token}")
                raise RuntimeError(f"API Error {response.status_code}: {response.text}")

            if not response.content.strip():
                return None

            try:
                return response.json()
            except ValueError:
                return None
        return None  # 不可达，满足静态分析器