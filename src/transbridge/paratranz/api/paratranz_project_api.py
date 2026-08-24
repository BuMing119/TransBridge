from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzProjectAPI(ParatranzClient):
    def list_projects(self, page: int = 1, page_size: int = 50, uid=None, *, cancellation=None):
        """获取项目列表（分页）。

        ``uid="my"`` 是 TransBridge 的当前账号别名。ParaTranz 线上接口会
        静默忽略这个字符串，因此在发送请求前必须解析为已验证的数字用户 ID。
        """
        params = {"page": page, "pageSize": page_size}
        if uid == "my":
            uid = getattr(self.config, "user_id", None)
            if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
                raise ValueError("uid='my' requires a verified ParaTranz user ID")
        if uid is not None:
            params["uid"] = uid
        return self._request("GET", "/projects", params=params, cancellation=cancellation, expected_type=(list, dict))

    def create_project(self, data: dict):
        """
        创建项目

        data 示例:
        {
            "name": "My Project",
            "desc": "项目说明",
            "source": "en",
            "dest": "zh-CN",
            "privacy": 0,
            "download": 0,
            "issueMode": 0,
            "reviewMode": 1,
            "joinMode": 1
        }
        """
        return self._request("POST", "/projects", json=data)

    def get_project(self, project_id: int, *, cancellation=None):
        """获取项目信息"""
        return self._request("GET", f"/projects/{project_id}", cancellation=cancellation, expected_type=dict)

    def update_project(self, project_id: int, data: dict):
        """更新项目信息，字段同 create_project"""
        return self._request("PUT", f"/projects/{project_id}", json=data)

    def delete_project(self, project_id: int):
        """删除项目"""
        return self._request("DELETE", f"/projects/{project_id}")
