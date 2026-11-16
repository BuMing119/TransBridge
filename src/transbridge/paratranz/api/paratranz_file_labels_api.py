# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Project file label operations in ParaTranz OpenAPI 0.7.0."""

from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzFileLabelsAPI(ParatranzClient):
    def list_file_labels(self, project_id: int, *, cancellation=None):
        """列出项目文件标签。"""
        return self._request(
            "GET", f"/projects/{project_id}/files/labels", expected_type=list, cancellation=cancellation
        )

    def create_file_label(
        self,
        project_id: int,
        name: str,
        *,
        color: str | None = None,
        file_ids: list[int] | None = None,
        cancellation=None,
    ):
        """创建文件标签，可指定颜色和关联文件。"""
        data = {"name": name}
        if color is not None:
            data["color"] = color
        if file_ids is not None:
            data["fileIds"] = file_ids
        return self._request(
            "POST", f"/projects/{project_id}/files/labels", json=data, expected_type=dict, cancellation=cancellation
        )

    def get_file_label(self, project_id: int, label_id: int, *, cancellation=None):
        """读取单个文件标签。"""
        return self._request(
            "GET", f"/projects/{project_id}/files/labels/{label_id}", expected_type=dict, cancellation=cancellation
        )

    def update_file_label(
        self,
        project_id: int,
        label_id: int,
        *,
        name: str | None = None,
        color: str | None = None,
        file_ids: list[int] | None = None,
        cancellation=None,
    ):
        """修改文件标签，file_ids=[] 可清空关联文件。"""
        options = {"name": name, "color": color, "fileIds": file_ids}
        return self._request(
            "PUT",
            f"/projects/{project_id}/files/labels/{label_id}",
            json={key: value for key, value in options.items() if value is not None},
            expected_type=dict,
            cancellation=cancellation,
            retryable=False,
        )

    def delete_file_label(self, project_id: int, label_id: int, *, cancellation=None):
        """删除文件标签。"""
        return self._request(
            "DELETE", f"/projects/{project_id}/files/labels/{label_id}", cancellation=cancellation, retryable=False
        )
