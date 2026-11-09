# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzStringsAPI(ParatranzClient):
    def list_strings(
        self,
        project_id: int,
        page: int = 1,
        page_size: int = 50,
        file: int | None = None,
        stage: int | None = None,
        detailed: bool = False,
        *,
        task: int | None = None,
        cancellation=None,
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
            task: 按项目任务 ID 筛选；None 不传，0 等显式值也会保留。
            cancellation: 可选协作取消信号。

        Returns:
            官方分页对象（page、pageSize、rowCount、pageCount、results），兼容裸数组响应。
        """
        params = {"page": page, "pageSize": page_size}
        if file is not None:
            params["file"] = file
        if stage is not None:
            params["stage"] = stage
        if task is not None:
            params["task"] = task
        if detailed:
            params["detailed"] = 1
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
