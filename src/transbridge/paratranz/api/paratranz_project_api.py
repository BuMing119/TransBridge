# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzProjectAPI(ParatranzClient):
    def list_projects(
        self,
        page: int = 1,
        page_size: int = 50,
        uid=None,
        *,
        game: str | None = None,
        stage: int | None = None,
        public: bool = False,
        filter_text: str | None = None,
        source: str | None = None,
        target: str | None = None,
        cancellation=None,
    ):
        """获取项目列表（分页）。

        ``uid="my"`` 是 TransBridge 的当前账号别名。ParaTranz 线上接口会
        静默忽略这个字符串，因此在发送请求前必须解析为已验证的数字用户 ID。
        """
        if uid == "my":
            uid = getattr(self.config, "user_id", None)
            if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
                raise ValueError("uid='my' requires a verified ParaTranz user ID")
        params = {
            "page": page,
            "pageSize": page_size,
            "uid": uid,
            "game": game,
            "stage": stage,
            "public": 1 if public else None,
            "filter": filter_text,
            "source": source,
            "target": target,
        }
        return self._request(
            "GET",
            "/projects",
            params={key: value for key, value in params.items() if value is not None},
            cancellation=cancellation,
            expected_type=(list, dict),
        )

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

    def transfer_project(self, project_id: int, target_user_id: int, *, keep: bool = False, cancellation=None):
        """将项目转让给目标用户。仅所有者可操作，keep 保留原所有者的管理员权限。"""
        return self._request(
            "POST",
            f"/projects/{project_id}",
            json={"op": "transfer", "params": {"target": target_user_id, "keep": keep}},
            cancellation=cancellation,
        )

    def get_tag_rules(self, project_id: int, *, predefined: bool = False, cancellation=None):
        """获取富文本标签规则及配置，可附带游戏内置规则。"""
        return self._request(
            "GET",
            f"/projects/{project_id}/tag-rules",
            params={"predefined": 1} if predefined else None,
            cancellation=cancellation,
            expected_type=dict,
        )

    def save_tag_rules(self, project_id: int, rules: list[dict], *, config: dict | None = None, cancellation=None):
        """保存项目标签规则。空规则列表及空配置均可显式传入。"""
        data = {"rules": rules}
        if config is not None:
            data["config"] = config
        return self._request("POST", f"/projects/{project_id}/tag-rules", json=data, cancellation=cancellation)
