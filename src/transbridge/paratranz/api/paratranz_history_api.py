from typing import Optional
from src.transbridge.paratranz.paratranz_client import ParatranzClient

class ParatranzHistoryAPI(ParatranzClient):

    def get_project_history(self, project_id: int, page: int = 1):
        """
        获取项目历史记录（翻译变更，审核记录等）

        返回结构示例：
        {
          "docs": [...],
          "page": 1,
          "pages": 10,
          "total": 100
        }
        """
        return self._request(
            "GET",
            f"/projects/{project_id}/history",
            params={"page": page}
        )

    def get_file_history(self, project_id: int, file_id: int, page: int = 1):
        """
        获取某文件历史记录
        包含文件级的变更，比如 key 或内容变更
        """
        return self._request(
            "GET",
            f"/projects/{project_id}/files/{file_id}/history",
            params={"page": page}
        )

    def get_term_history(self, project_id: int, term_id: int, page: int = 1):
        """
        获取术语历史记录（术语表变更）
        术语 ID 可从 terms API 获取
        """
        return self._request(
            "GET",
            f"/projects/{project_id}/terms/{term_id}/history",
            params={"page": page}
        )
