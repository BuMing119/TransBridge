from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzMembersAPI(ParatranzClient):

    def list_members(self, project_id: int):
        """获取成员列表"""
        return self._request("GET", f"/projects/{project_id}/members")

    def add_member(self, project_id: int, uid: int, permission: int):
        """
        添加成员（需管理员以上权限）

        Args:
            project_id: 项目 ID
            uid: 用户 ID
            permission: 权限等级（1=翻译者, 2=校对者, 3=管理员, 10=所有者）
        """
        return self._request(
            "POST",
            f"/projects/{project_id}/members",
            json={"uid": uid, "permission": permission}
        )

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
        return self._request(
            "PUT",
            f"/projects/{project_id}/members/{member_id}",
            json=data
        )

    def delete_member(self, project_id: int, member_id: int):
        """删除成员"""
        return self._request("DELETE", f"/projects/{project_id}/members/{member_id}")
