# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""ParaTranz OpenAPI 0.7.0 的项目导出、构建任务和下载记录。

列表仅管理员可读；最近结果与下载权限由项目配置决定，两者不能混用。
HTTP/解析错误由公共客户端转换为 ExternalServiceError，取消信号原样传播。
"""

from transbridge.paratranz.paratranz_client import ParatranzClient

from .artifact_response import save_artifact_response


class ParatranzExportAPI(ParatranzClient):
    def list_jobs(
        self,
        project_id: int,
        *,
        status: int | None = None,
        limit: int | None = None,
        job_type: str | None = None,
        cancellation=None,
    ):
        """获取项目的构建和导出任务，按创建时间倒序。"""
        return self._request(
            "GET",
            f"/projects/{project_id}/jobs",
            params={"status": status, "limit": limit, "type": job_type},
            cancellation=cancellation,
            expected_type=list,
        )

    def get_job(self, project_id: int, job_id: int, *, cancellation=None):
        """获取单个构建任务的状态和结果。"""
        return self._request(
            "GET", f"/projects/{project_id}/jobs/{job_id}", cancellation=cancellation, expected_type=dict
        )

    def get_download_records(
        self,
        project_id: int,
        *,
        page: int = 1,
        page_size: int = 50,
        begin: str | None = None,
        end: str | None = None,
        cancellation=None,
    ):
        """分页读取项目压缩包下载记录，需项目管理员权限。"""
        return self._request(
            "GET",
            f"/projects/{project_id}/artifacts/records",
            params={"page": page, "pageSize": page_size, "begin": begin, "end": end},
            cancellation=cancellation,
            expected_type=dict,
        )

    def get_artifacts(self, project_id: int, *, page: int = 1, page_size: int = 50, cancellation=None):
        """GET /projects/{projectId}/artifacts：获取导出历史列表，仅管理员可用。

        Args:
            project_id: 项目 ID。
            page: 页码，从 1 开始。
            page_size: 每页数量，发送为 pageSize。
            cancellation: 可选协作取消信号。

        Returns:
            分页对象，含 page、pageSize、rowCount、pageCount、results（Artifact 数组）。
            为兼容已有部署仍接受裸数组；不在客户端选取最近一条。
        """
        return self._request(
            "GET",
            f"/projects/{project_id}/artifacts",
            params={"page": page, "pageSize": page_size},
            cancellation=cancellation,
            expected_type=(list, dict),
        )

    def get_latest_artifact(self, project_id: int, *, cancellation=None):
        """GET /projects/{projectId}/artifacts/latest：获取最近一次导出结果。

        Args:
            project_id: 项目 ID；访问权限由项目 extra.downloadMode 决定，拒绝时返回 403。
            cancellation: 可选协作取消信号。

        Returns:
            Artifact 字典（id、createdAt、total、translated、reviewed、duration 等）；
            尚无导出结果时为 None（服务端 JSON null）。不触发新导出。
        """
        return self._request(
            "GET",
            f"/projects/{project_id}/artifacts/latest",
            cancellation=cancellation,
            expected_type=(dict, type(None)),
        )

    def trigger_export(self, project_id: int, *, cancellation=None):
        """
        POST /projects/{projectId}/artifacts：手动触发导出，仅管理员可用。

        Args:
            project_id: 项目 ID。
            cancellation: 可选协作取消信号。

        Returns:
            Job 对象，包含 id、status（0=等待或执行中，1=成功，2=失败）等字段。
            调用方可用 get_job 查询完成状态。超时不自动重发 POST。
        """
        return self._request(
            "POST",
            f"/projects/{project_id}/artifacts",
            cancellation=cancellation,
            expected_type=dict,
        )

    def download_artifacts(self, project_id: int, save_path: str, *, cancellation=None) -> str:
        """
        GET /projects/{projectId}/artifacts/download：下载最新导出的压缩包。

        服务端返回 302，HTTP 客户端跟随跳转并按 8192 字节块写入文件；不触发导出。

        Args:
            project_id: 项目 ID
            save_path: 本地保存路径，父目录需存在；已有文件会覆盖。
            cancellation: 可选协作取消信号，在写入各数据块前检查。

        Returns:
            保存文件的路径。下载失败或取消时可能留下部分文件，由调用方处理。
        """
        endpoint = f"/projects/{project_id}/artifacts/download"
        response = self._request(
            "GET",
            endpoint,
            cancellation=cancellation,
            raw_response=True,
            stream=True,
        )

        return save_artifact_response(response, save_path, cancellation=cancellation)
