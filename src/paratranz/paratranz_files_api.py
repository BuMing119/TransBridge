import json
from typing import Optional
from .paratranz_client import ParatranzClient

class ParatranzFilesAPI(ParatranzClient):

    def list_files(self, project_id: int):
        """获取文件列表"""
        return self._request("GET", f"/projects/{project_id}/files")

    def upload_file(self, project_id: int, filepath: str, **kwargs):
        """
        上传文件
        filepath: 本地路径
        可选参数举例:
            lang="en"
            keepPath=True
        """
        files = {
            "file": open(filepath, "rb")
        }

        # 额外的 form-field 参数（非 JSON）
        data = kwargs if kwargs else None

        try:
            return self._request(
                "POST",
                f"/projects/{project_id}/files",
                files=files,
                data=data
            )
        finally:
            files["file"].close()

    def get_file_info(self, project_id: int, file_id: int):
        """获取文件信息"""
        return self._request("GET", f"/projects/{project_id}/files/{file_id}")

    def update_file_info(self, project_id: int, file_id: int, data: dict):
        """
        更新文件信息（如重命名、路径等）
        data 示例:
        {
            "name": "ui/text.json",
            "lang": "en"
        }
        """
        return self._request("PUT", f"/projects/{project_id}/files/{file_id}", data=json.dumps(data))

    def delete_file(self, project_id: int, file_id: int):
        """删除文件"""
        return self._request("DELETE", f"/projects/{project_id}/files/{file_id}")

    def get_file_translation(self, project_id: int, file_id: int, lang: str):
        """获取文件翻译内容（整个文件）"""
        return self._request(
            "GET",
            f"/projects/{project_id}/files/{file_id}/translation",
            params={"lang": lang}
        )

    def update_file_translation(self, project_id: int, file_id: int, lang: str, content: dict):
        """
        更新整个文件翻译
        content 示例 (JSON 文件):
        {
            "HELLO": "你好",
            "WELCOME": "欢迎"
        }
        """
        return self._request(
            "PUT",
            f"/projects/{project_id}/files/{file_id}/translation",
            params={"lang": lang},
            data=json.dumps(content)
        )
