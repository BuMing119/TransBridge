import requests
from src.transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzExportAPI(ParatranzClient):

    def trigger_export(self, project_id: int, lang: str = None):
        """
        触发导出任务
        lang 可选，例如 "zh-CN"
        """
        params = {"lang": lang} if lang else None
        return self._request(
            "POST",
            f"/projects/{project_id}/export",
            params=params
        )

    def get_export_result(self, project_id: int, task_id: str):
        """
        查询导出任务结果（异步任务）
        返回可能包含: status, url, progress 等
        """
        return self._request(
            "GET",
            f"/projects/{project_id}/export/{task_id}"
        )

    def download_export(self, download_url: str, save_path: str):
        """
        下载导出后的zip压缩包
        download_url 通常来自接口返回的 url 字段
        """
        full_url = self.BASE_URL + download_url
        response = requests.get(full_url, stream=True)

        if response.status_code != 200:
            raise RuntimeError(f"Download failed: {response.status_code}")

        with open(save_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return save_path
