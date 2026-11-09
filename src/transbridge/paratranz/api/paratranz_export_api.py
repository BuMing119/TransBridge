# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""导出压缩包接口，依据 https://paratranz.cn/api-docs?format=json v0.5.7。

列表仅管理员可读；最近结果与下载权限由项目配置决定，两者不能混用。
HTTP/解析错误由公共客户端转换为 ExternalServiceError，取消信号原样传播。
"""

from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzExportAPI(ParatranzClient):
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
            Job 对象，包含 id、status（0=未开始，1=执行中，2=成功，-1=失败）等字段。
            返回不代表压缩包已生成；调用方需等待新的导出结果。超时不自动重发 POST。
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

        try:
            with open(save_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if cancellation is not None:
                        cancellation.raise_if_cancelled()
                    f.write(chunk)
        finally:
            response.close()

        return save_path
