from typing import Optional
from src.transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzHistoryAPI(ParatranzClient):

    def get_project_history(
        self,
        project_id: int,
        page: int = 1,
        page_size: int = 50,
        uid: Optional[int] = None,
        tid: Optional[int] = None,
        history_type: Optional[str] = None
    ):
        """
        获取项目历史记录

        Args:
            project_id: 项目 ID
            page: 页码
            page_size: 每页数量
            uid: 按用户 ID 筛选
            tid: 按词条 ID 筛选（仅 history_type="text" 时有效，指定后分页失效）
            history_type: 记录类型："text"（词条，默认）, "term"（术语）, "import"（导入）, "comment"（评论）
        """
        params = {"page": page, "pageSize": page_size}
        if uid is not None:
            params["uid"] = uid
        if tid is not None:
            params["tid"] = tid
        if history_type is not None:
            params["type"] = history_type
        return self._request("GET", f"/projects/{project_id}/history", params=params)

    def list_file_revisions(
        self,
        project_id: int,
        page: int = 1,
        page_size: int = 50,
        file: Optional[int] = None,
        revision_type: Optional[str] = None
    ):
        """
        获取文件上传历史（Revision 记录）

        Args:
            project_id: 项目 ID
            page: 页码
            page_size: 每页数量
            file: 按文件 ID 筛选
            revision_type: 类型："create", "update", "import"
        """
        params = {"page": page, "pageSize": page_size}
        if file is not None:
            params["file"] = file
        if revision_type is not None:
            params["type"] = revision_type
        return self._request("GET", f"/projects/{project_id}/files/revisions", params=params)

    def get_term_history(self, project_id: int, term_id: int, page: int = 1, page_size: int = 50):
        """获取术语历史记录"""
        return self._request(
            "GET",
            f"/projects/{project_id}/terms/{term_id}/history",
            params={"page": page, "pageSize": page_size}
        )
