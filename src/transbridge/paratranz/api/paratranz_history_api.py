"""历史查询接口；依据 https://paratranz.cn/api-docs?format=json（OpenAPI 0.5.7）。"""

from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzHistoryAPI(ParatranzClient):
    def get_project_history(
        self,
        project_id: int,
        page: int = 1,
        page_size: int = 50,
        uid: int | None = None,
        tid: int | str | list[int] | None = None,
        history_type: str | None = None,
        *,
        begin: str | None = None,
        end: str | None = None,
        keyword: str | None = None,
        cancellation=None,
    ):
        """
        获取项目历史记录：GET /projects/{projectId}/history。

        Args:
            project_id: 项目 ID
            page: 页码
            page_size: 每页数量
            uid: 按用户 ID 筛选，仅 history_type="comment" 时生效。
            tid: 指定词条的全部历史（含导入及修改），指定后分页失效。
                支持单个 ID、逗号分隔的 ID 字符串或 ID 列表；列表以重复查询参数发送。
            history_type: 省略时为词条历史，可用 translate/edit/check/review 筛选操作；
                term 为术语修改，import 为导入，comment 为评论。发送为 type 参数。
            begin: 开始时间（ISO 8601），指定后按创建时间排序。
            end: 结束时间（ISO 8601）。
            keyword: 按修改前后内容模糊搜索，仅默认词条历史类型时生效。
            cancellation: 可选取消令牌，在请求前后检查取消状态。

        Returns:
            含 page、pageSize、rowCount、pageCount、results 的历史分页对象。
            多个 tid 的结果通过各自的 tid 区分；指定 tid 后不可依赖分页筛选。
        """
        params = {"page": page, "pageSize": page_size}
        if uid is not None:
            params["uid"] = uid
        if tid is not None:
            params["tid"] = tid
        if history_type is not None:
            params["type"] = history_type
        for name, value in (("begin", begin), ("end", end), ("keyword", keyword)):
            if value is not None:
                params[name] = value
        return self._request("GET", f"/projects/{project_id}/history", params=params, cancellation=cancellation)

    def list_file_revisions(
        self,
        project_id: int,
        page: int = 1,
        page_size: int = 50,
        file: int | None = None,
        revision_type: str | None = None,
        *,
        cancellation=None,
        filename: str | None = None,
        begin: str | None = None,
        end: str | None = None,
    ):
        """
        获取文件历史：GET /projects/{projectId}/files/revisions。

        需项目成员权限；非成员返回 403，未登录返回 401，私密项目对非成员返回 404。

        Args:
            project_id: 项目 ID
            page: 页码
            page_size: 每页数量
            file: 按文件 ID 筛选
            revision_type: create（创建）、update（更新）、import（导入）、delete（删除）。
                发送为 type 参数。
            cancellation: 可选取消令牌，在请求前后检查取消状态。
            filename: 按文件名模糊筛选。
            begin: 开始时间（ISO 8601），指定后按创建时间排序。
            end: 结束时间（ISO 8601）。

        Returns:
            含 page、pageSize、rowCount、pageCount、results 的分页对象。
            results 中每项为 Revision，包含文件名、操作类型和增删改数量等信息。
        """
        params = {"page": page, "pageSize": page_size}
        if file is not None:
            params["file"] = file
        if revision_type is not None:
            params["type"] = revision_type
        for name, value in (("filename", filename), ("begin", begin), ("end", end)):
            if value is not None:
                params[name] = value
        return self._request(
            "GET",
            f"/projects/{project_id}/files/revisions",
            params=params,
            cancellation=cancellation,
            expected_type=(list, dict),
        )

    def get_term_history(self, project_id: int, term_id: int, page: int = 1, page_size: int = 50):
        """获取术语历史记录"""
        return self._request(
            "GET", f"/projects/{project_id}/terms/{term_id}/history", params={"page": page, "pageSize": page_size}
        )
