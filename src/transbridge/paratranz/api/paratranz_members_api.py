# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzMembersAPI(ParatranzClient):
    def list_members(self, project_id: int, *, begin: str | None = None, end: str | None = None, cancellation=None):
        """获取成员列表，可指定贡献统计的时间段。"""
        return self._request(
            "GET",
            f"/projects/{project_id}/members",
            params={"begin": begin, "end": end},
            cancellation=cancellation,
        )

    def add_member(self, project_id: int, uid: int, permission: int, *, note: str | None = None, cancellation=None):
        """
        添加成员（需管理员以上权限）

        Args:
            project_id: 项目 ID
            uid: 用户 ID
            permission: 权限等级（1=翻译者, 2=校对者, 3=管理员, 10=所有者）
        """
        data = {"uid": uid, "permission": permission}
        if note is not None:
            data["note"] = note
        return self._request("POST", f"/projects/{project_id}/members", json=data, cancellation=cancellation)

    def update_member(self, project_id: int, member_id: int, data: dict):
        """
        修改成员信息

        data 示例:
        {
            "permission": 2,
            "note": "校对人员"
        }

        注意：仅管理员及所有者可修改权限，仅所有者可设置管理员。
        """
        return self._request("PUT", f"/projects/{project_id}/members/{member_id}", json=data)

    def delete_member(self, project_id: int, member_id: int, *, clean: bool = False, cancellation=None):
        """移除成员。clean 使用「清理」通知文案。"""
        return self._request(
            "DELETE",
            f"/projects/{project_id}/members/{member_id}",
            params={"clean": 1} if clean else None,
            cancellation=cancellation,
            retryable=False,
        )

    def leave_project(self, project_id: int, *, cancellation=None):
        """以当前账号退出项目。"""
        return self._request(
            "DELETE", f"/projects/{project_id}/members/self", cancellation=cancellation, retryable=False
        )
