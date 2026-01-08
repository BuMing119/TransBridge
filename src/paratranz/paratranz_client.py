import json
import requests

class ParatranzClient:
    BASE_URL = "https://paratranz.cn/api"

    def __init__(self, token: str, timeout: int = 10):
        self.token = token
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json;charset=UTF-8"
        }

    def _request(self, method: str, endpoint: str, **kwargs):
        url = f"{self.BASE_URL}{endpoint}"
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self.headers,
                timeout=self.timeout,
                **kwargs
            )
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"HTTP request failed: {e}")

        if response.status_code == 204:  # No Content
            return None

        if not response.ok:
            raise RuntimeError(
                f"API Error {response.status_code}: {response.text}"
            )

        return response.json()
