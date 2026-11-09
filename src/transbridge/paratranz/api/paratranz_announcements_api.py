# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""项目公告接口，依据 ParaTranz OpenAPI 0.5.7（2026-11-03 核对）。

文档：https://paratranz.cn/api-docs?format=json 。所有路径均相对于 /api。
认证、错误分类与取消由 ParatranzClient 处理；HTTP 错误抛出 ExternalServiceError。
公告写操作不会自动重试，避免请求结果未知时重复发布通知或改变置顶时间。
"""

from typing import Any

from transbridge.application.ports.paratranz import CancellationPort
from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzAnnouncementsAPI(ParatranzClient):
    """公告列表、详情及管理员写操作；保留服务端响应中的全部字段。"""

    def list_announcements(
        self,
        project_id: int,
        page: int = 1,
        page_size: int = 50,
        *,
        bumped: bool | None = None,
        category: str | None = None,
        cancellation: CancellationPort | None = None,
    ) -> dict[str, Any]:
        """GET /projects/{projectId}/announcements：分页查询项目公告。

        按置顶时间及创建时间倒序排列。非成员仅能看到 internal=False 的公告；
        私密项目对非成员返回 404。此接口与要求管理员权限的单条详情接口不同。

        Args:
            project_id: 项目 ID。
            page: 页码，默认 1。
            page_size: 每页数量，默认 50，官方范围为 1–800。
            bumped: True 时仅返回已置顶公告；False 不限置顶，None 不发送该参数。
            category: 公告分类筛选；None 不发送，空字符串仍原样发送。
            cancellation: 可选协作式取消令牌，在发送请求及处理响应时检查。

        Returns:
            分页对象，含 page、pageSize、rowCount、pageCount 及 results 公告数组。
            公告包含 id、title、content、html、internal、category、bumpedAt 等字段。
        """
        params: dict[str, Any] = {"page": page, "pageSize": page_size}
        if bumped is not None:
            params["bumped"] = str(bumped).lower()
        if category is not None:
            params["category"] = category
        return self._request(
            "GET",
            f"/projects/{project_id}/announcements",
            params=params,
            cancellation=cancellation,
            expected_type=dict,
        )

    def create_announcement(
        self, project_id: int, data: dict[str, Any], *, cancellation: CancellationPort | None = None
    ) -> dict[str, Any]:
        """POST /projects/{projectId}/announcements：发布公告，需要项目管理员权限。

        创建成功会向项目成员发送站内通知。未登录返回 401，无发布权限返回 403；
        超时或其他结果未知的失败不会自动重试，应先核对公告列表以免重复通知。

        Args:
            project_id: 项目 ID。
            data: JSON 请求体：title（标题）、content（Markdown 原文）、
                category（分类，服务端默认 announcement）、bumped（是否置顶，
                True 将 bumpedAt 设为当前时间）、internal（是否仅成员可见，默认 False）。
                html 由服务端生成，不应作为可写字段提交。字典不经修改即传给服务端。
            cancellation: 可选协作式取消令牌；取消不代表服务端已撤销写入。

        Returns:
            新建的 Announcement 对象，含 id、content、服务端生成的 html 等字段。
        """
        return self._request(
            "POST",
            f"/projects/{project_id}/announcements",
            json=data,
            cancellation=cancellation,
            retryable=False,
            expected_type=dict,
        )

    def get_announcement(
        self, project_id: int, announcement_id: int, *, cancellation: CancellationPort | None = None
    ) -> dict[str, Any]:
        """GET /projects/{projectId}/announcements/{announcementId}：获取公告详情。

        当前官方接口要求项目管理员权限：未登录返回 401，无权限返回 403，
        公告不存在返回 404；列表可见不意味着有权调用此接口。

        Args:
            project_id: 项目 ID。
            announcement_id: 公告 ID。
            cancellation: 可选协作式取消令牌。

        Returns:
            Announcement 对象，含标题、Markdown 原文、HTML、发布者、时间、
            置顶时间 bumpedAt（为空表示未置顶）、成员可见性及统计信息。
        """
        return self._request(
            "GET",
            f"/projects/{project_id}/announcements/{announcement_id}",
            cancellation=cancellation,
            expected_type=dict,
        )

    def update_announcement(
        self,
        project_id: int,
        announcement_id: int,
        data: dict[str, Any],
        *,
        cancellation: CancellationPort | None = None,
    ) -> dict[str, Any]:
        """PUT /projects/{projectId}/announcements/{announcementId}：管理员更新公告。

        Args:
            project_id: 项目 ID。
            announcement_id: 公告 ID。
            data: JSON 请求体支持 title、content、bumped、internal。title/content
                只有提供非空新值才更新；content 更新时重新生成 html。bumped=True
                置顶，False 取消置顶并清空 bumpedAt；internal=False 恢复非成员可见。
                未提供的字段不由客户端补默认值；category 不在官方更新字段中。
            cancellation: 可选取消令牌；取消不保证服务端未执行写入。

        Returns:
            更新后的 Announcement 对象。

        Notes:
            需要项目管理员权限；空请求体返回 400，未登录/无权限/不存在分别为
            401/403/404。不会自动重试，以免重复更新置顶时间。
        """
        return self._request(
            "PUT",
            f"/projects/{project_id}/announcements/{announcement_id}",
            json=data,
            cancellation=cancellation,
            retryable=False,
            expected_type=dict,
        )

    def delete_announcement(
        self, project_id: int, announcement_id: int, *, cancellation: CancellationPort | None = None
    ) -> Any:
        """DELETE /projects/{projectId}/announcements/{announcementId}：管理员删除公告。

        Args:
            project_id: 项目 ID。
            announcement_id: 要删除的公告 ID。
            cancellation: 可选取消令牌；取消不保证远端删除被撤销。

        Returns:
            官方仅规定成功状态 200，未规定响应结构；返回服务端 JSON，空响应为 None。

        Notes:
            未登录、无权限、公告不存在分别返回 401、403、404。结果未知时不自动
            重试，避免把已经成功的删除误报为后续请求的 404。
        """
        return self._request(
            "DELETE",
            f"/projects/{project_id}/announcements/{announcement_id}",
            cancellation=cancellation,
            retryable=False,
        )
