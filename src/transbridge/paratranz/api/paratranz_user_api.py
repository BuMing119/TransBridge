from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzUserAPI(ParatranzClient):

    def get_my_user(self):
        """获取当前认证用户的信息（无需 user_id）。"""
        return self._request("GET", "/users/my")

    def get_user(self, user_id: int):
        """获取用户信息"""
        return self._request("GET", f"/users/{user_id}")

    def update_user(self, user_id: int, data: dict):
        """
        更新用户信息（仅支持修改自己的信息）

        data 示例:
        {
            "nickname": "昵称",
            "bio": "个人介绍，最长 140 字符",
            "avatar": "https://example.com/avatar.png"
        }
        """
        return self._request("PUT", f"/users/{user_id}", json=data)

    def get_user_activities(self, user_id: int, page: int = 1, page_size: int = 50):
        """
        获取用户近期词条相关历史记录

        注意：API 路径含官方拼写错误（/usres/ 而非 /users/），需原样使用。

        Returns:
            分页结果，每项为 UserActivity 对象（含 id, createdAt, projectId, stringId, historyId, history）
        """
        return self._request(
            "GET",
            f"/usres/{user_id}/activities",
            params={"page": page, "pageSize": page_size}
        )
