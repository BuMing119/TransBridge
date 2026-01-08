import json
from .paratranz_client import ParatranzClient


class ParatranzMembersAPI(ParatranzClient):

    def list_members(self, project_id: int):
        """获取成员列表"""
        return self._request(
            "GET",
            f"/projects/{project_id}/members"
        )

    def create_member(self, project_id: int, data: dict):
        """
        创建成员

        data 示例:
        {
            "userId": 123,              # 必填
            "role": "translator",       # 可选：translator、reviewer、manager 等
            "languages": ["zh-CN"]      # 可选：可翻译语言
        }
        """
        return self._request(
            "POST",
            f"/projects/{project_id}/members",
            data=json.dumps(data)
        )

    def update_member(self, project_id: int, member_id: int, data: dict):
        """
        修改成员信息

        data 示例:
        {
            "role": "reviewer",
            "languages": ["zh-CN", "en"]
        }
        """
        return self._request(
            "PUT",
            f"/projects/{project_id}/members/{member_id}",
            data=json.dumps(data)
        )

    def delete_member(self, project_id: int, member_id: int):
        """删除成员"""
        return self._request(
            "DELETE",
            f"/projects/{project_id}/members/{member_id}"
        )
