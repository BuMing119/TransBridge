# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""ParaTranz file operations (official OpenAPI 0.7.0).

Source: https://paratranz.cn/api-docs?format=json

Use file endpoints for uploads, rather than creating/updating individual strings:
* POST /files creates a file; a path/name collision is rejected.
* POST /files/{id} updates source content only. incremental=False (API default)
  DELETES missing strings; pass incremental=True to retain them. skip=False lets
  the server return hashMatched for unchanged content. It is not a concurrency lock.
* POST /files/{id}/translation matches existing keys, accepts translation/stage,
  never creates strings, and ignores submitted original/context. force=False
  protects human translations and skips empty/identity/redundant translations.
  Import requires administrator permission; 423 means the file is busy.
* GET /files/{id}/translation returns ALL strings, without pagination; its legacy
  name below is retained for callers. No upload needs a per-string readback.

Treat hashMatched/unchanged as successful no-ops, empty as no imported content.
File IDs are resolved from the current complete path, not persisted string IDs.
Do not blindly retry a POST after a timeout: it may already have been applied.
"""

from pathlib import Path
from typing import Literal
from urllib.parse import quote

from urllib3.fields import RequestField
from urllib3.filepost import encode_multipart_formdata

from transbridge.paratranz.paratranz_client import ParatranzClient


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
        f'form-data; name="{field_name}"; filename="{filename}"; filename*=UTF-8\'\'{encoded}'
    )
    return rf


class ParatranzFilesAPI(ParatranzClient):
    def list_files(
        self,
        project_id: int,
        *,
        order_by: str | None = None,
        name: str | None = None,
        size: int | None = None,
        lightweight: bool = False,
        task: int | None = None,
        modified_at: str | None = None,
        created_at: str | None = None,
        cancellation=None,
    ):
        """获取文件列表，可筛选、排序、限制数量，或使用轻量模式。"""
        params = {
            "orderBy": order_by,
            "name": name,
            "size": size,
            "list": 1 if lightweight else None,
            "task": task,
            "modifiedAt": modified_at,
            "createdAt": created_at,
        }
        return self._request("GET", f"/projects/{project_id}/files", params=params, cancellation=cancellation)

    def list_files_with_path(self, project_id: int) -> dict[str, int]:
        """
        获取文件列表，返回 完整路径 -> file_id 映射。

        注意：ParaTranz API 返回的 name 可能包含路径（如 "path/to/filename.csv"）
        也可能分开为 name 和 folder 字段。

        完整路径格式：folder/name 或 name
        例如："上古卷轴5/人物/人名.json" 或 "人名.json"

        Returns:
            dict[full_path, file_id]
        """
        file_list = self.list_files(project_id) or []
        result = {}
        for f in file_list:
            full_name = f.get("name", "")
            folder = f.get("folder", "")
            file_id = f.get("id")

            if file_id is not None:
                # 如果 name 包含路径，直接使用
                if "/" in full_name:
                    full_path = full_name
                else:
                    # 否则拼接 folder 和 name
                    full_path = f"{folder}/{full_name}" if folder else full_name
                result[full_path] = file_id
        return result

    def find_file_by_name(self, project_id: int, filename: str) -> list[dict]:
        """
        根据文件名查找项目中的所有匹配文件（支持同名文件在不同路径）。

        注意：ParaTranz API 返回的 name 可能包含路径（如 "path/to/filename.csv"）
        也可能分开为 name 和 folder 字段。

        Args:
            project_id: 项目 ID
            filename: 文件名（不含路径）

        Returns:
            list of file info dict，每个包含 id, name, folder 等字段
        """
        file_list = self.list_files(project_id) or []
        result = []
        for f in file_list:
            full_name = f.get("name", "")

            # 如果 name 包含路径，分离出文件名
            if "/" in full_name:
                parts = full_name.rsplit("/", 1)
                actual_name = parts[1] if len(parts) > 1 else full_name
                actual_folder = parts[0] if len(parts) > 1 else ""
                # 更新文件记录以便返回一致的格式
                f["name"] = actual_name
                f["folder"] = actual_folder
            else:
                actual_name = full_name

            if actual_name == filename:
                result.append(f)
        return result

    def upload_file(
        self, project_id: int, filepath: str, path: str | None = None, *, task: int | None = None, cancellation=None
    ):
        """
        上传文件（创建新文件）

        Args:
            project_id: 项目 ID
            filepath: 本地文件路径，文件名由此决定
            path: 文件在项目中的目录路径，如 "path/to/"（可选）
            task: 上传时关联的项目任务 ID。
        """
        p = Path(filepath)
        return self.upload_file_bytes(
            project_id, p.name, p.read_bytes(), path=path, task=task, cancellation=cancellation
        )

    def upload_file_bytes(
        self, project_id, filename, content, *, path=None, task: int | None = None, cancellation=None
    ):
        """通过 multipart 创建文件。返回 {file, revision} 或空响应。"""
        fields: list[RequestField] = [_make_file_field("file", filename, content)]
        for name, value in (("path", path), ("task", task)):
            if value is not None:
                field = RequestField(name=name, data=str(value))
                field.make_multipart()
                fields.append(field)
        body, ct = encode_multipart_formdata(fields)
        return self._request_multipart("POST", f"/projects/{project_id}/files", body, ct, cancellation=cancellation)

    def create_file_from_items(
        self,
        project_id: int,
        filename: str,
        items: list[dict],
        *,
        file_format: str | None = None,
        file_hash: str | None = None,
        task: int | None = None,
        extra: dict | None = None,
        cancellation=None,
    ):
        """用 JSON 词条数组创建文件，filename 带格式扩展名。"""
        options = {"format": file_format, "hash": file_hash, "task": task, "extra": extra}
        data = {
            "filename": filename,
            "items": items,
            **{key: value for key, value in options.items() if value is not None},
        }
        return self._request(
            "POST", f"/projects/{project_id}/files", json=data, expected_type=dict, cancellation=cancellation
        )

    def rename_folder(self, project_id: int, source: str, target: str, *, cancellation=None):
        """按路径前缀批量重命名目录。成功时无响应体。"""
        return self._request(
            "PUT",
            f"/projects/{project_id}/files",
            json={"op": "renameFolder", "source": source, "target": target},
            cancellation=cancellation,
            retryable=False,
        )

    def merge_files(
        self, project_id: int, source_file_id: int, target_file_id: int, mode: Literal[0, 1], *, cancellation=None
    ):
        """合并同格式文件。冲突时 mode=0 保留目标词条，mode=1 使用源词条。"""
        return self._request(
            "PUT",
            f"/projects/{project_id}/files",
            json={"op": "mergeFiles", "source": source_file_id, "target": target_file_id, "mode": mode},
            expected_type=dict,
            cancellation=cancellation,
            retryable=False,
        )

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

    def reupload_file(
        self,
        project_id: int,
        file_id: int,
        filepath: str,
        *,
        incremental=False,
        skip=False,
        task: int | None = None,
        cancellation=None,
    ):
        """
        重新上传文件（更新原文，不改动译文）

        Args:
            project_id: 项目 ID
            file_id: 文件 ID
            filepath: 本地文件路径，格式需与创建时一致（也可上传 JSON，文件名加 .json）
            incremental: True 保留上传内容中缺失的词条；False 会删除缺失词条（官方默认）。
            skip: True 跳过服务端哈希检查；通常应保持 False。
            task: 更新时关联的项目任务 ID。
        """
        p = Path(filepath)
        return self.update_file_bytes(
            project_id,
            file_id,
            p.name,
            p.read_bytes(),
            incremental=incremental,
            skip=skip,
            task=task,
            cancellation=cancellation,
        )

    def update_file_bytes(
        self,
        project_id,
        file_id,
        filename,
        content,
        *,
        incremental=True,
        skip=False,
        task: int | None = None,
        cancellation=None,
    ):
        """更新原文，不改译文；应用上传默认增量，避免隐式删除。"""
        fields = [_make_file_field("file", filename, content)]
        for name, value in (("incremental", incremental), ("skip", skip), ("task", task)):
            if value is not None:
                field = RequestField(name=name, data=str(value).lower())
                field.make_multipart()
                fields.append(field)
        body, ct = encode_multipart_formdata(fields)
        return self._request_multipart(
            "POST", f"/projects/{project_id}/files/{file_id}", body, ct, cancellation=cancellation
        )

    def update_file_from_items(
        self,
        project_id: int,
        file_id: int,
        filename: str,
        items: list[dict],
        *,
        file_hash: str | None = None,
        incremental: bool = True,
        skip: bool = False,
        task: int | None = None,
        cancellation=None,
    ):
        """用 JSON 词条数组更新原文。默认保留缺失词条，与 update_file_bytes 一致。"""
        data = {"filename": filename, "items": items, "incremental": incremental, "skip": skip}
        if file_hash is not None:
            data["hash"] = file_hash
        if task is not None:
            data["task"] = task
        return self._request(
            "POST",
            f"/projects/{project_id}/files/{file_id}",
            json=data,
            expected_type=dict,
            cancellation=cancellation,
        )

    def delete_file(self, project_id: int, file_id: int):
        """删除文件"""
        return self._request("DELETE", f"/projects/{project_id}/files/{file_id}")

    def get_file_translation(self, project_id: int, file_id: int, *, cancellation=None):
        """一次获取全部词条（原文、译文和状态，不分页）；需项目成员权限。"""
        return self._request("GET", f"/projects/{project_id}/files/{file_id}/translation", cancellation=cancellation)

    def get_file_content(
        self,
        project_id: int,
        file_id: int,
        *,
        rev: int | None = None,
        tid: int | None = None,
        dl: bool = False,
        cancellation=None,
    ):
        """GET /projects/{projectId}/files/{fileId}/content：获取归档原文或下载原文。

        需项目成员权限：未登录为 401，非成员为 403，私密项目对非成员为 404。
        默认读取最近一次 create/update 的原文归档，不是当前译文导出。

        Args:
            project_id: 项目 ID。
            file_id: 文件 ID。
            rev: 文件历史接口返回的归档 ID；省略时使用服务端默认版本。
            tid: 词条 ID，读取其导入时所属原文版本，并返回 highlight 定位信息。
                与 rev 同传时的选择规则由服务端决定，客户端不自行推断。
            dl: True 时请求附件，返回原始 bytes（保留 UTF-8 BOM）；False 时读取 JSON。
            cancellation: 可选协作取消信号。

        Returns:
            dl=False：Revision 字典，附 content 原文字符串；指定 tid 时附 highlight
            （key、original）。dl=True：完整原文附件字节，不执行解码或自动写盘。

        Raises:
            ExternalServiceError: HTTP 失败或 JSON 响应无效；保留公共客户端错误上下文。
        """
        params = {}
        if rev is not None:
            params["rev"] = rev
        if tid is not None:
            params["tid"] = tid
        if dl:
            params["dl"] = 1
            response = self._request(
                "GET",
                f"/projects/{project_id}/files/{file_id}/content",
                params=params,
                raw_response=True,
                cancellation=cancellation,
            )
            try:
                return response.content
            finally:
                response.close()
        return self._request(
            "GET",
            f"/projects/{project_id}/files/{file_id}/content",
            params=params,
            expected_type=dict,
            cancellation=cancellation,
        )

    def update_file_translation(
        self,
        project_id: int,
        file_id: int,
        filepath: str,
        force: bool = False,
        *,
        cancellation=None,
        skip: bool = False,
        filename: str | None = None,
        path: str | None = None,
    ):
        """
        更新文件翻译（仅更新译文，不改动原文）

        Args:
            project_id: 项目 ID
            file_id: 文件 ID
            filepath: 本地文件路径（格式同上传时）
            force: 是否强制覆盖，默认 False（仅覆盖未人工编辑过的词条）
            skip: 跳过内容哈希检查，默认 False。按 key 匹配，不新增词条，不更新原文/上下文。
            filename, path: 记录到导入历史的文件名及目录。
        """
        p = Path(filepath)
        endpoint = f"/projects/{project_id}/files/{file_id}/translation"

        # 使用 requests 的标准 multipart 方式
        with open(filepath, "rb") as f:
            files = {"file": (p.name, f, "application/json")}
            data = {"force": str(force).lower(), "skip": str(skip).lower()}
            if filename is not None:
                data["filename"] = filename
            if path is not None:
                data["path"] = path
            return self._request(
                "POST",
                endpoint,
                files=files,
                data=data,
                cancellation=cancellation,
                expected_type=dict,
            )

    def import_translations(
        self,
        project_id,
        file_id,
        filename,
        items,
        *,
        force=False,
        skip=False,
        file_hash: str | None = None,
        cancellation=None,
    ):
        """整批导入 key/translation/stage；无需 entry_id、原文或上下文。

        filename 必须带扩展名。force=False 使用服务端人工译文保护；
        hashMatched/unchanged 是正常无变更结果，empty 表示未解析到词条。
        file_hash 可传已有内容哈希，用于服务端对重复导入去重。
        """
        data = {"filename": filename, "items": list(items), "force": force, "skip": skip}
        if file_hash is not None:
            data["hash"] = file_hash
        return self._request(
            "POST",
            f"/projects/{project_id}/files/{file_id}/translation",
            json=data,
            cancellation=cancellation,
            expected_type=dict,
        )
