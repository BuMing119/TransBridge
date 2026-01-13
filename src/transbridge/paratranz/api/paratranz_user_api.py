import json
from src.transbridge.paratranz.paratranz_client import ParatranzClient

class ParatranzUserAPI(ParatranzClient):

    def get_user(self, user_id: int):
        """获取用户信息"""
        return self._request(
            "GET",
            f"/users/{user_id}"
        )

    def update_user(self, user_id: int, data: dict):
        """
        更新用户信息

        data 示例:
        {
            "name": "NewName",
            "languages": ["zh-CN", "en"],
            "email": "new@mail.com"
        }
        """
        return self._request(
            "PUT",
            f"/users/{user_id}",
            data=json.dumps(data)
        )

    def get_user_history(self, user_id: int, page: int = 1):
        """
        获取用户近期词条相关历史记录（翻译 / 修改 / 审核）

        返回分页结构：
        {
          "docs": [...],
          "page": 1,
          "pages": 10,
          "total": 200
        }
        """
        return self._request(
            "GET",
            f"/users/{user_id}/history",
            params={"page": page}
        )
