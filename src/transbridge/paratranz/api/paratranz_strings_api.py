# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
from collections.abc import Mapping
from typing import Literal

from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzStringsAPI(ParatranzClient):
    def list_strings(
        self,
        project_id: int,
        page: int | None = None,
        page_size: int = 50,
        file: int | list[int] | None = None,
        stage: int | None = None,
        detailed: bool = False,
        *,
        task: int | None = None,
        uid: int | str | list[int | str] | None = None,
        exclude_uid: list[int] | None = None,
        filename: str | None = None,
        file_label: list[int] | None = None,
        exclude_file_label: list[int] | None = None,
        translate_by: list[int] | None = None,
        exclude_translate_by: list[int] | None = None,
        edit_by: list[int] | None = None,
        exclude_edit_by: list[int] | None = None,
        review_by: list[int] | None = None,
        exclude_review_by: list[int] | None = None,
        anchor: int | None = None,
        mode: int | None = None,
        ids: list[int] | None = None,
        order_by: str | None = None,
        words: int | None = None,
        words_less_than: int | None = None,
        length_gt: Literal["1", "char"] | None = None,
        filters: Mapping[str, str] | None = None,
        cancellation=None,
    ):
        """
        获取词条列表（分页）

        Args:
            project_id: 项目 ID
            page: 页码，省略时默认第 1 页；与 anchor 搭配时省略可定位词条所在页。
            page_size: 每页数量
            file: 单个文件 ID 或重复发送的文件 ID 列表。指定 filename 时服务端忽略 file。
            stage: 按词条状态筛选（0=未翻译, 1=已翻译, 2=有疑问, 3=已检查, 5=已审核, 9=已锁定, -1=已隐藏）
            detailed: 是否返回历史记录等详细内容
            task: 按项目任务 ID 筛选；None 不传，0 等显式值也会保留。
            uid: 最近编辑者，支持多个 ID 或字符串 "null"（未编辑过）。
            file_label, translate_by, edit_by, review_by: 标签和各操作用户 ID。exclude_ 对应排除条件。
            mode: 搜索位掩码，1 忽略大小写，4 包含隐藏词条。
            words, words_less_than: 原文字数下限（含）和上限（不含）。
            length_gt: "1" 按字节数、"char" 按字符数筛选长于原文的译文。
            filters: 文本和时间过滤条件，支持 key、original、translation、context、text、
                createdAt、updatedAt 及官方操作符，例如 {"original^=": "Hello", "updatedAt>=": "..."}。
            cancellation: 可选协作取消信号。

        Returns:
            官方分页对象（page、pageSize、rowCount、pageCount、results），兼容裸数组响应。
        """
        params = {
            **(filters or {}),
            "page": 1 if page is None and anchor is None else page,
            "pageSize": page_size,
            "file": file,
            "stage": stage,
            "task": task,
            "detailed": 1 if detailed else None,
            "uid": uid,
            "uid!": exclude_uid,
            "filename": filename,
            "fileLabel": file_label,
            "fileLabel!": exclude_file_label,
            "translateBy": translate_by,
            "translateBy!": exclude_translate_by,
            "editBy": edit_by,
            "editBy!": exclude_edit_by,
            "reviewBy": review_by,
            "reviewBy!": exclude_review_by,
            "anchor": anchor,
            "mode": mode,
            "id": ids,
            "orderBy": order_by,
            "words": words,
            "words<": words_less_than,
            "lengthGt": length_gt,
        }
        return self._request(
            "GET",
            f"/projects/{project_id}/strings",
            params=params,
            cancellation=cancellation,
            expected_type=(list, dict),
        )

    def create_string(self, project_id: int, data: dict, *, cancellation=None):
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
        return self._request(
            "POST",
            f"/projects/{project_id}/strings",
            json=data,
            cancellation=cancellation,
            expected_type=dict,
        )

    def batch_strings(
        self,
        project_id: int,
        op: str,
        ids: list | None = None,
        stage: int | None = None,
        translation: str | None = None,
        *,
        items: list[dict] | None = None,
        cancellation=None,
    ):
        """
        PUT /projects/{projectId}/strings：批量编辑或删除词条。

        修改 key/original/context、隐藏或锁定词条及删除操作仅管理员可用；
        标记已审核或修改已审核词条需校对以上权限，翻译人员只能修改未审核词条译文/状态。

        Args:
            project_id: 项目 ID
            op: 官方值 "edit" 或 "delete"；保留旧 "update" 别名并转换为 "edit"。
            ids: 删除的 ID 数组；编辑时可与 stage/translation 一起生成逐条 items。
            stage: 使用 ids 编辑时的共同状态，0 会原样保留。
            translation: 使用 ids 编辑时的共同译文，空字符串会原样保留。
            items: edit 的逐条编辑对象，每项含 id，可含 key/original/translation/stage/context。
                不可与 ids、stage、translation 混用；未指定 stage 时服务端依据译文自动设置状态。
            cancellation: 可选协作取消信号。

        Returns:
            edit 返回成功更新的词条 ID 数组；delete 成功无响应体时返回 None。

        Raises:
            ValueError: 操作不支持、混用两种编辑参数或 items 缺少 id。
            ExternalServiceError: 服务端拒绝或请求失败；写操作不自动重试，避免重复修改。
        """
        if op == "update":
            op = "edit"
        if op not in {"edit", "delete"}:
            raise ValueError("op must be edit, update (legacy alias), or delete")
        if op == "delete":
            if ids is None or items is not None or stage is not None or translation is not None:
                raise ValueError("delete requires ids and does not accept edit fields")
            data = {"op": "delete", "id": list(ids)}
        else:
            if items is not None:
                if ids is not None or stage is not None or translation is not None:
                    raise ValueError("items cannot be combined with ids, stage, or translation")
                if any("id" not in item for item in items):
                    raise ValueError("each edit item must contain id")
                edits = [dict(item) for item in items]
            else:
                if ids is None:
                    raise ValueError("edit requires items or ids")
                edits = [{"id": string_id} for string_id in ids]
                for item in edits:
                    if stage is not None:
                        item["stage"] = stage
                    if translation is not None:
                        item["translation"] = translation
            data = {"op": "edit", "items": edits}
        return self._request(
            "PUT",
            f"/projects/{project_id}/strings",
            json=data,
            cancellation=cancellation,
            retryable=False,
        )

    def get_string(self, project_id: int, string_id: int, *, cancellation=None):
        """获取单个词条"""
        return self._request("GET", f"/projects/{project_id}/strings/{string_id}", cancellation=cancellation)

    def get_string_terms(self, project_id: int, string_id: int, *, force: bool = False, cancellation=None):
        """查找原文命中的项目术语。分词缓存仅在排查问题时用 force 重建。"""
        return self._request(
            "GET",
            f"/projects/{project_id}/strings/{string_id}/terms",
            params={"force": "true"} if force else None,
            expected_type=list,
            cancellation=cancellation,
        )

    def update_string(self, project_id: int, string_id: int, data: dict, *, cancellation=None):
        """
        更新单个词条

        data 示例:
        {
            "translation": "更新后的文本",
            "stage": 1,
            "context": "UI"
        }
        """
        return self._request(
            "PUT",
            f"/projects/{project_id}/strings/{string_id}",
            json=data,
            cancellation=cancellation,
            expected_type=dict,
        )

    def delete_string(self, project_id: int, string_id: int, *, cancellation=None):
        """删除单个词条（仅管理员可用）"""
        return self._request(
            "DELETE",
            f"/projects/{project_id}/strings/{string_id}",
            cancellation=cancellation,
        )
