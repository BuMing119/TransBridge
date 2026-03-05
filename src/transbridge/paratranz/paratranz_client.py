import requests
from .config_manager import ParatranzConfig


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
        if "files" in kwargs:
            # 文件上传时只保留 Authorization，让 requests 自动生成
            # multipart/form-data boundary。不能用 pop 因为 headers dict
            # 可能同时含有 "Content-Type" 和 "content-type" 两个键。
            auth = headers.get("Authorization") or headers.get("authorization", "")
            headers = {"Authorization": auth} if auth else {}

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                timeout=self.config.timeout,
                **kwargs
            )
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"HTTP request failed: {e}")

        if response.status_code == 204:  # No Content
            return None

        if not response.ok:
            if response.status_code == 401:
                print(f"认证失败，当前使用的token: {self.config.token}")
            raise RuntimeError(
                f"API Error {response.status_code}: {response.text}"
            )

        return response.json()

    def _request_multipart(self, method: str, endpoint: str, body: bytes, content_type: str):
        """
        发送预先编码好的 multipart/form-data 请求。
        用于需要 RFC 5987 文件名编码的上传操作，绕过 requests 自带的 files= 参数处理。
        """
        url = f"{self.config.base_url}{endpoint}"
        base_headers = self.config.get_headers()
        auth = base_headers.get("Authorization") or base_headers.get("authorization", "")
        headers = {"Authorization": auth, "Content-Type": content_type}
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                data=body,
                timeout=self.config.timeout,
            )
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"HTTP request failed: {e}")

        if response.status_code == 204:
            return None

        if not response.ok:
            if response.status_code == 401:
                print(f"认证失败，当前使用的token: {self.config.token}")
            raise RuntimeError(f"API Error {response.status_code}: {response.text}")

        return response.json()