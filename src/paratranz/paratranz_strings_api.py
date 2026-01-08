import json
from .paratranz_client import ParatranzClient

class ParatranzStringsAPI(ParatranzClient):

    def list_strings(self, project_id: int, page: int = 1, lang: str = None):
        """词条列表，可分页及按语言过滤"""
        params = {"page": page}
        if lang:
            params["lang"] = lang
        return self._request("GET", f"/projects/{project_id}/strings", params=params)

    def create_string(self, project_id: int, data: dict):
        """
        创建词条
        data 示例:
        {
            "key": "HELLO",
            "original": "Hello",
            "translation": "你好",
            "context": "UI greeting"
        }
        """
        return self._request("POST", f"/projects/{project_id}/strings", data=json.dumps(data))

    def batch_update(self, project_id: int, data: list):
        """
        批量更新词条
        data 示例:
        [
            {"id": 1001, "translation": "新的翻译"},
            {"id": 1002, "translation": "另一个翻译"}
        ]
        """
        return self._request("POST", f"/projects/{project_id}/strings/batch", data=json.dumps(data))

    def batch_delete(self, project_id: int, ids: list):
        """
        批量删除词条
        ids 示例: [1001, 1002, 1003]
        """
        return self._request("POST", f"/projects/{project_id}/strings/batch/delete", data=json.dumps(ids))

    def get_string(self, project_id: int, string_id: int):
        """获取单个词条"""
        return self._request("GET", f"/projects/{project_id}/strings/{string_id}")

    def update_string(self, project_id: int, string_id: int, data: dict):
        """
        更新单个词条
        data 示例:
        {
            "translation": "更新后的文本",
            "context": "UI"
        }
        """
        return self._request("PUT", f"/projects/{project_id}/strings/{string_id}", data=json.dumps(data))

    def delete_string(self, project_id: int, string_id: int):
        """删除单个词条"""
        return self._request("DELETE", f"/projects/{project_id}/strings/{string_id}")
