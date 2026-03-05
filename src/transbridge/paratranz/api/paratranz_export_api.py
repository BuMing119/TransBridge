import requests
from src.transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzExportAPI(ParatranzClient):

    def get_artifacts(self, project_id: int):
        """获取最近一次导出结果（Artifact 对象）"""
        return self._request("GET", f"/projects/{project_id}/artifacts")

    def trigger_export(self, project_id: int):
        """
        触发导出操作（仅管理员可用）

        Returns:
            Job 对象，包含 id, status（0=未开始, 1=执行中, 2=成功, -1=失败）等字段
        """
        return self._request("POST", f"/projects/{project_id}/artifacts")

    def download_artifacts(self, project_id: int, save_path: str) -> str:
        """
        下载最新导出的压缩包

        Args:
            project_id: 项目 ID
            save_path: 本地保存路径

        Returns:
            保存文件的路径
        """
        url = f"{self.config.base_url}/projects/{project_id}/artifacts/download"
        response = requests.get(
            url,
            headers=self.config.get_headers(),
            timeout=self.config.timeout,
            stream=True
        )
        if not response.ok:
            raise RuntimeError(f"Download failed {response.status_code}: {response.text}")

        with open(save_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return save_path
