import json
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
            # 如果没有提供配置管理器，则使用默认配置创建一个
            self.config = ParatranzConfig(token=token, timeout=timeout)
        else:
            # 使用提供的配置管理器
            self.config = config
            # 如果提供了 token 参数，更新配置中的 token
            if token:
                self.config.update_token(token)
            # 如果提供了 timeout 参数，更新配置中的 timeout
            if timeout != 10:  # 只有当 timeout 不是默认值时才更新
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
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self.config.get_headers(),
                timeout=self.config.timeout,
                **kwargs
            )
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"HTTP request failed: {e}")

        if response.status_code == 204:  # No Content
            return None

        if not response.ok:
            # 如果是401未授权错误，输出当前使用的token以便调试
            if response.status_code == 401:
                print(f"认证失败，当前使用的token: {self.config.token}")
            raise RuntimeError(
                f"API Error {response.status_code}: {response.text}"
            )

        return response.json()
