import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests
from urllib3.fields import RequestField
from urllib3.filepost import encode_multipart_formdata

from src.transbridge.paratranz.paratranz_client import ParatranzClient


def _make_file_field(field_name: str, filename: str, data: bytes) -> RequestField:
    """
    构建带 RFC 5987 编码文件名的 multipart 字段。

    urllib3 2.x 改为 WHATWG 标准，将文件名以原始 UTF-8 字节放入 quoted-string，
    而 ParaTranz 服务端按 Latin-1 解析 HTTP 头，导致中文文件名乱码。
    RFC 5987 格式 (filename*=UTF-8''...) 可被所有现代服务端正确识别。
    """
    rf = RequestField(name=field_name, data=data, filename=None)
    rf.make_multipart()
    encoded = quote(filename, safe="")
    rf.headers["Content-Disposition"] = (
        f'form-data; name="{field_name}"; '
        f'filename="{filename}"; '
        f"filename*=UTF-8''{encoded}"
    )
    return rf


class ParatranzFilesAPI(ParatranzClient):

    def list_files(self, project_id: int):
        """获取文件列表"""
        return self._request("GET", f"/projects/{project_id}/files")

    def upload_file(self, project_id: int, filepath: str, path: Optional[str] = None):
        """
        上传文件（创建新文件）

        Args:
            project_id: 项目 ID
            filepath: 本地文件路径，文件名由此决定
            path: 文件在项目中的目录路径，如 "path/to/"（可选）
        """
        p = Path(filepath)
        fields: list[RequestField] = [_make_file_field("file", p.name, p.read_bytes())]
        if path is not None:
            rf = RequestField(name="path", data=path)
            rf.make_multipart()
            fields.append(rf)
        body, ct = encode_multipart_formdata(fields)
        return self._request_multipart("POST", f"/projects/{project_id}/files", body, ct)

    def get_file_info(self, project_id: int, file_id: int):
        """获取文件信息"""
        return self._request("GET", f"/projects/{project_id}/files/{file_id}")

    def update_file_info(self, project_id: int, file_id: int, data: dict):
        """
        更新文件元信息（仅修改文件名等，不更新词条内容）

        data 示例:
        {
            "name": "path/to/filename.csv",
            "extra": {}
        }
        """
        return self._request("PUT", f"/projects/{project_id}/files/{file_id}", json=data)

    def reupload_file(self, project_id: int, file_id: int, filepath: str):
        """
        重新上传文件（更新原文，不改动译文）

        Args:
            project_id: 项目 ID
            file_id: 文件 ID
            filepath: 本地文件路径，格式需与创建时一致（也可上传 JSON，文件名加 .json）
        """
        p = Path(filepath)
        fields = [_make_file_field("file", p.name, p.read_bytes())]
        body, ct = encode_multipart_formdata(fields)
        return self._request_multipart("POST", f"/projects/{project_id}/files/{file_id}", body, ct)

    def delete_file(self, project_id: int, file_id: int):
        """删除文件"""
        return self._request("DELETE", f"/projects/{project_id}/files/{file_id}")

    def get_file_translation(self, project_id: int, file_id: int):
        """获取文件翻译数据（返回 JSON 原始数据）"""
        return self._request("GET", f"/projects/{project_id}/files/{file_id}/translation")

    def update_file_translation(self, project_id: int, file_id: int, filepath: str, force: bool = False):
        """
        更新文件翻译（仅更新译文，不改动原文）

        Args:
            project_id: 项目 ID
            file_id: 文件 ID
            filepath: 本地文件路径（格式同上传时）
            force: 是否强制覆盖，默认 False（仅覆盖未人工编辑过的词条）
        """
        p = Path(filepath)
        url = f"{self.config.base_url}/projects/{project_id}/files/{file_id}/translation"
        headers = self.config.get_headers()

        # 使用 requests 的标准 multipart 方式
        with open(filepath, "rb") as f:
            files = {"file": (p.name, f, "application/json")}
            data = {"force": str(force).lower()}
            response = requests.post(
                url=url,
                headers={"Authorization": headers.get("Authorization", "")},
                files=files,
                data=data,
                timeout=self.config.timeout,
            )

        if not response.ok:
            raise RuntimeError(f"API Error {response.status_code}: {response.text}")

        if response.status_code == 204 or not response.content.strip():
            return None
        try:
            return response.json()
        except ValueError:
            return None
