# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""贡献查询接口；依据 https://paratranz.cn/api-docs?format=json（OpenAPI 0.5.7）。"""

from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzScoresAPI(ParatranzClient):
    def get_scores(
        self,
        project_id: int,
        page: int = 1,
        page_size: int = 50,
        uid: int | None = None,
        operation: str | None = None,
        start: str | None = None,
        end: str | None = None,
        *,
        begin: str | None = None,
        cancellation=None,
    ):
        """
        获取成员贡献列表：GET /projects/{projectId}/scores。

        Args:
            project_id: 项目 ID
            page: 页码
            page_size: 每页数量
            uid: 按用户 ID 筛选
            operation: 按类型筛选："translate", "edit", "review"
            start: begin 的兼容别名，保留旧位置参数和关键字调用；发送为 begin。
                新调用请使用 begin；仅在移除旧客户端公共接口时才可删除此别名。
            end: 筛选结束时间（ISO 8601）
            begin: 筛选开始时间（ISO 8601，如 "2024-01-01T00:00:00Z"）。
            cancellation: 可选取消令牌，在请求前后检查取消状态。

        Returns:
            含 page、pageSize、rowCount、pageCount、results 的分页对象。
            results 为 Score 列表，含 id、createdAt、uid、project、base、multiplier、value。

        Raises:
            ValueError: start 和 begin 同时传入且值不同，避免静默选择错误时间范围。
        """
        if start is not None and begin is not None and start != begin:
            raise ValueError("start and begin must match when both are provided")
        effective_begin = begin if begin is not None else start
        params = {"page": page, "pageSize": page_size}
        if uid is not None:
            params["uid"] = uid
        if operation is not None:
            params["operation"] = operation
        if effective_begin is not None:
            params["begin"] = effective_begin
        if end is not None:
            params["end"] = end
        return self._request("GET", f"/projects/{project_id}/scores", params=params, cancellation=cancellation)
