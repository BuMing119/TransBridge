from typing import Optional
from src.transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzStringsAPI(ParatranzClient):

    def list_strings(
        self,
        project_id: int,
        page: int = 1,
        page_size: int = 50,
        file: Optional[int] = None,
        stage: Optional[int] = None,
        detailed: bool = False
    ):
        """
        获取词条列表（分页）

        Args:
            project_id: 项目 ID
            page: 页码
            page_size: 每页数量
            file: 按文件 ID 筛选
            stage: 按词条状态筛选（0=未翻译, 1=已翻译, 2=有疑问, 3=已检查, 5=已审核, 9=已锁定, -1=已隐藏）
            detailed: 是否返回历史记录等详细内容
        """
        params = {"page": page, "pageSize": page_size}
        if file is not None:
            params["file"] = file
        if stage is not None:
            params["stage"] = stage
        if detailed:
            params["detailed"] = 1
        return self._request("GET", f"/projects/{project_id}/strings", params=params)

    def create_string(self, project_id: int, data: dict):
        """
        创建词条

        data 示例:
        {
            "key": "HELLO",
            "original": "Hello",
            "translation": "你好",
            "file": 101,
            "stage": 1,
            "context": "UI greeting"
        }
        """
        return self._request("POST", f"/projects/{project_id}/strings", json=data)

    def batch_strings(self, project_id: int, op: str, ids: list, stage: Optional[int] = None, translation: Optional[str] = None):
        """
        批量操作词条

        Args:
            project_id: 项目 ID
            op: 操作类型，"update" 或 "delete"
            ids: 要操作的词条 ID 列表
            stage: 目标状态（op="update" 时可用）
            translation: 目标译文（op="update" 时可用）
        """
        data = {"op": op, "id": ids}
        if stage is not None:
            data["stage"] = stage
        if translation is not None:
            data["translation"] = translation
        return self._request("PUT", f"/projects/{project_id}/strings", json=data)

    def get_string(self, project_id: int, string_id: int):
        """获取单个词条"""
        return self._request("GET", f"/projects/{project_id}/strings/{string_id}")

    def update_string(self, project_id: int, string_id: int, data: dict):
        """
        更新单个词条

        data 示例:
        {
            "translation": "更新后的文本",
            "stage": 1,
            "context": "UI"
        }
        """
        return self._request("PUT", f"/projects/{project_id}/strings/{string_id}", json=data)

    def delete_string(self, project_id: int, string_id: int):
        """删除单个词条（仅管理员可用）"""
        return self._request("DELETE", f"/projects/{project_id}/strings/{string_id}")
