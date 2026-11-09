# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""项目任务接口，依据 ParaTranz OpenAPI 0.5.7（2026-11-03 核对）。

官方文档：https://paratranz.cn/api-docs?format=json
项目须开启 ``extra.task``。读取要求项目成员权限，写入要求管理员以上权限；
未登录返回 401，无权限返回 403。参数、任务状态及权限最终由服务端校验。
所有方法接受可选的 ``cancellation`` 协作取消令牌；HTTP/响应错误通过
``ExternalServiceError`` 传播。写操作不自动重试，避免未知结果时重复改变远端状态。
"""

from typing import Literal

from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzTasksAPI(ParatranzClient):
    """管理项目任务、任务文件以及显式关联的任务词条。"""

    def list_tasks(self, project_id: int, page: int = 1, page_size: int = 20, *, cancellation=None):
        """GET /projects/{projectId}/tasks：获取任务分页列表，需项目成员权限。

        Args:
            project_id: 项目 ID，项目须开启任务功能。
            page: 页码，默认 1。
            page_size: 每页数量，默认 20，最小为 1。
            cancellation: 可选协作取消令牌。

        Returns:
            含 page、pageSize、rowCount、pageCount、results 的字典。
            results 为 Task 对象列表：置顶任务优先，其余按创建时间倒序。
            任务可含 assignees；列表中 taskFiles.file 仅为文件 ID，未展开文件对象。
        """
        return self._request(
            "GET",
            f"/projects/{project_id}/tasks",
            params={"page": page, "pageSize": page_size},
            cancellation=cancellation,
            expected_type=dict,
        )

    def create_task(self, project_id: int, data: dict, *, cancellation=None):
        """POST /projects/{projectId}/tasks：创建任务，需管理员以上权限。

        Args:
            project_id: 项目 ID。
            data: JSON 请求体，必填 title（标题，最多 128 字符）和 goal。
                goal 可含 translate/check/review，值为 0～1 的目标完成率，
                check 用于二次校对项目。可选 content（Markdown 说明）、
                deadline（日期时间字符串；省略或 None 表示长期有效）、
                bumped（True 置顶）以及 extra.members（仅用于展示的成员
                用户 ID 列表，最多 100 个）。按原样发送，不填充省略的字段。
            cancellation: 可选协作取消令牌。

        Returns:
            新建的 Task 字典，含 id、project、title、goal、status 等信息。
            status 为 0（进行中）或 1（已完成），html 由服务端生成。

        Notes:
            缺少标题或目标时服务端返回 400；请求结果未知时不要盲目重试创建。
        """
        return self._request(
            "POST",
            f"/projects/{project_id}/tasks",
            json=data,
            cancellation=cancellation,
            expected_type=dict,
            retryable=False,
        )

    def get_task(self, project_id: int, task_id: int, *, cancellation=None):
        """GET /projects/{projectId}/tasks/{taskId}：读取任务详情，需项目成员权限。

        Args:
            project_id: 项目 ID。
            task_id: 任务 ID。
            cancellation: 可选协作取消令牌。

        Returns:
            Task 字典，包含 taskFiles、assignees 以及 words 字数统计。
            words 的 total/translated/checked/reviewed 分别为总字数、
            已翻译、已检查、已审核字数；finishedAt 为完成时间，旧数据可能为空。
        """
        return self._request(
            "GET", f"/projects/{project_id}/tasks/{task_id}", cancellation=cancellation, expected_type=dict
        )

    def operate_task(self, project_id: int, task_id: int, op: Literal["pin", "unpin", "finish"], *, cancellation=None):
        """POST /projects/{projectId}/tasks/{taskId}：操作任务，需管理员以上权限。

        Args:
            project_id: 项目 ID。
            task_id: 任务 ID。
            op: pin 置顶、unpin 取消置顶、finish 标记完成；不支持的操作返回 400。
            cancellation: 可选协作取消令牌。

        Returns:
            操作后的 Task 字典。finish 后任务文件及词条关联写入将返回 400。
        """
        return self._request(
            "POST",
            f"/projects/{project_id}/tasks/{task_id}",
            json={"op": op},
            cancellation=cancellation,
            expected_type=dict,
            retryable=False,
        )

    def update_task(self, project_id: int, task_id: int, data: dict, *, cancellation=None):
        """PUT /projects/{projectId}/tasks/{taskId}：更新任务，需管理员以上权限。

        Args:
            project_id: 项目 ID。
            task_id: 任务 ID。
            data: 仅更新传入的字段。可含 title（最多 128 字符）、content
                （Markdown，更新时重新生成 html）、goal（translate/check/review
                的 0～1 完成率）、deadline（日期时间；None 清空为长期有效）、
                bumped（True 置顶到当前时间，False 取消置顶）及 extra.members
                （最多 100 个成员用户 ID，仅展示）。省略字段保持原值，显式的
                False、None、空字符串或空列表均原样保留。
            cancellation: 可选协作取消令牌。

        Returns:
            更新后的 Task 字典。
        """
        return self._request(
            "PUT",
            f"/projects/{project_id}/tasks/{task_id}",
            json=data,
            cancellation=cancellation,
            expected_type=dict,
            retryable=False,
        )

    def delete_task(self, project_id: int, task_id: int, *, cancellation=None):
        """DELETE /projects/{projectId}/tasks/{taskId}：删除任务，需管理员以上权限。

        Args:
            project_id: 项目 ID。
            task_id: 待删除的任务 ID。
            cancellation: 可选协作取消令牌。

        Returns:
            成功响应的 JSON；官方未定义成功响应体，空响应时返回 None。
        """
        return self._request(
            "DELETE", f"/projects/{project_id}/tasks/{task_id}", cancellation=cancellation, retryable=False
        )

    def list_task_files(self, project_id: int, task_id: int, *, cancellation=None):
        """GET /projects/{projectId}/tasks/{taskId}/files：列出任务文件，需项目成员权限。

        Args:
            project_id: 项目 ID。
            task_id: 任务 ID。
            cancellation: 可选协作取消令牌。

        Returns:
            TaskFile 字典列表，不分页。每项含 id、task、file、mode，
            词条统计 total/translated/disputed/checked/reviewed/hidden/locked、
            words 总词数及 progress（translate/check/review 的 0～1 进度）。
        """
        return self._request(
            "GET", f"/projects/{project_id}/tasks/{task_id}/files", cancellation=cancellation, expected_type=list
        )

    def add_task_file(self, project_id: int, task_id: int, file_id: int, mode: int = 0, *, cancellation=None):
        """POST /projects/{projectId}/tasks/{taskId}/files：添加文件，需管理员以上权限。

        Args:
            project_id: 项目 ID。
            task_id: 任务 ID。
            file_id: 项目文件 ID，以 JSON 字段 fileId 发送。
            mode: 默认 0（词条模式，仅追踪任务显式关联的词条）；
                1 为文件模式，追踪该文件全部词条。
            cancellation: 可选协作取消令牌。

        Returns:
            添加的 TaskFile 字典。

        Notes:
            文件已在任务中或任务已完成时服务端返回 400。
        """
        return self._request(
            "POST",
            f"/projects/{project_id}/tasks/{task_id}/files",
            json={"fileId": file_id, "mode": mode},
            cancellation=cancellation,
            expected_type=dict,
            retryable=False,
        )

    def remove_task_files(self, project_id: int, task_id: int, file_ids: list[int], *, cancellation=None):
        """DELETE /projects/{projectId}/tasks/{taskId}/files：移除文件，需管理员以上权限。

        Args:
            project_id: 项目 ID。
            task_id: 任务 ID。
            file_ids: 必填文件 ID 列表，以重复查询参数发送，如 ?file=1&file=2。
                只移除任务关联，不调用项目文件删除接口。
            cancellation: 可选协作取消令牌。

        Returns:
            成功响应的 JSON；官方未定义成功响应体，空响应时返回 None。

        Notes:
            未指定文件或任务已完成时服务端返回 400。
        """
        return self._request(
            "DELETE",
            f"/projects/{project_id}/tasks/{task_id}/files",
            params={"file": file_ids},
            cancellation=cancellation,
            retryable=False,
        )

    def update_task_strings(
        self, project_id: int, task_id: int, op: Literal["add", "remove"], items: list[int], *, cancellation=None
    ):
        """PUT /projects/{projectId}/tasks/{taskId}/strings：批量关联词条，需管理员以上权限。

        Args:
            project_id: 项目 ID。
            task_id: 任务 ID。
            op: add 加入任务；remove 移出任务。
            items: 词条 ID 列表，单次最多 1000 条。
            cancellation: 可选协作取消令牌。

        Returns:
            add 返回 added、skipped 计数；remove 返回 removed、skipped 计数。
            无效词条、文件模式来源、已关联或未关联等无需处理的项计入 skipped。

        Notes:
            仅修改词条模式的显式关联。add 遇到未关联文件时自动以词条模式添加；
            文件模式已追踪其全部词条，add/remove 均跳过这些来源，无法单独移出。
            参数错误或任务已完成返回 400，任务不存在返回 404。
            可通过词条列表接口的 task 筛选参数查看关联结果。
        """
        return self._request(
            "PUT",
            f"/projects/{project_id}/tasks/{task_id}/strings",
            json={"op": op, "items": items},
            cancellation=cancellation,
            expected_type=dict,
            retryable=False,
        )
