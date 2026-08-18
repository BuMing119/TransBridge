from typing import Optional
from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzScoresAPI(ParatranzClient):

    def get_scores(
        self,
        project_id: int,
        page: int = 1,
        page_size: int = 50,
        uid: Optional[int] = None,
        operation: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None
    ):
        """
        获取成员贡献列表（分页）

        Args:
            project_id: 项目 ID
            page: 页码
            page_size: 每页数量
            uid: 按用户 ID 筛选
            operation: 按类型筛选："translate", "edit", "review"
            start: 筛选开始时间（ISO 8601，如 "2024-01-01T00:00:00Z"）
            end: 筛选结束时间（ISO 8601）

        Returns:
            分页结果，每项为 Score 对象（含 id, createdAt, uid, project, base, multiplier, value）
        """
        params = {"page": page, "pageSize": page_size}
        if uid is not None:
            params["uid"] = uid
        if operation is not None:
            params["operation"] = operation
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        return self._request("GET", f"/projects/{project_id}/scores", params=params)
