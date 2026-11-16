# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzIssuesAPI(ParatranzClient):
    def list_issues(
        self,
        project_id: int,
        status: int | None = None,
        *,
        page: int | None = None,
        page_size: int | None = None,
        uid: int | None = None,
        cancellation=None,
    ):
        """
        获取讨论列表

        Args:
            project_id: 项目 ID
            status: 筛选状态，0=讨论中，1=已关闭（不传则返回全部）
            page, page_size: 页码及每页条数。省略使用服务端默认值。
            uid: 按发帖用户筛选。

        Returns:
            分页结果，含额外字段 closedCount（已关闭数）和 openCount（讨论中数）
        """
        return self._request(
            "GET",
            f"/projects/{project_id}/issues",
            params={"status": status, "page": page, "pageSize": page_size, "uid": uid},
            cancellation=cancellation,
        )

    def create_issue(self, project_id: int, title: str, content: str):
        """
        发起讨论

        Args:
            project_id: 项目 ID
            title: 讨论标题
            content: 讨论内容（支持 Markdown）
        """
        return self._request("POST", f"/projects/{project_id}/issues", json={"title": title, "content": content})

    def get_issue(self, project_id: int, issue_id: int):
        """
        获取讨论详情

        Returns:
            Issue 对象 + activities（回复列表）+ subscribers（订阅用户列表）
        """
        return self._request("GET", f"/projects/{project_id}/issues/{issue_id}")

    def reply_issue(self, project_id: int, issue_id: int, content: str):
        """
        回复讨论

        Args:
            project_id: 项目 ID
            issue_id: 讨论 ID
            content: 回复内容（支持 Markdown）
        """
        return self._request(
            "POST", f"/projects/{project_id}/issues/{issue_id}", json={"op": "reply", "content": content}
        )

    def subscribe_issue(self, project_id: int, issue_id: int):
        """订阅讨论"""
        return self._request("POST", f"/projects/{project_id}/issues/{issue_id}", json={"op": "subscribe"})

    def unsubscribe_issue(self, project_id: int, issue_id: int):
        """取消订阅讨论"""
        return self._request("POST", f"/projects/{project_id}/issues/{issue_id}", json={"op": "unsubscribe"})

    def update_issue(self, project_id: int, issue_id: int, data: dict):
        """
        修改讨论（标题、内容、状态等）

        data 示例:
        {
            "title": "新标题",
            "content": "新内容",
            "status": 1
        }
        """
        return self._request("PUT", f"/projects/{project_id}/issues/{issue_id}", json=data)

    def delete_issue(self, project_id: int, issue_id: int):
        """删除讨论"""
        return self._request("DELETE", f"/projects/{project_id}/issues/{issue_id}")
