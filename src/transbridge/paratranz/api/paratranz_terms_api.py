# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzTermsAPI(ParatranzClient):
    def list_terms(
        self,
        project_id: int,
        page: int = 1,
        page_size: int = 50,
        *,
        text: str | None = None,
        term: str | None = None,
        term_id: int | None = None,
        order_by: str | None = None,
        cancellation=None,
        raw_response: bool = False,
    ):
        """分页查询术语，支持模糊搜索、精确术语或 ID 筛选及排序。"""
        return self._request(
            "GET",
            f"/projects/{project_id}/terms",
            params={
                "page": page,
                "pageSize": page_size,
                "text": text,
                "term": term,
                "id": term_id,
                "orderBy": order_by,
            },
            cancellation=cancellation,
            expected_type=(list, dict),
            raw_response=raw_response,
        )

    def create_term(self, project_id: int, data: dict, *, cancellation=None, raw_response: bool = False):
        """
        创建术语

        data 示例:
        {
            "term": "apple",
            "translation": "苹果",
            "pos": "noun",
            "note": "水果",
            "variants": ["apples"],
            "caseSensitive": false
        }
        """
        return self._request(
            "POST",
            f"/projects/{project_id}/terms",
            json=data,
            cancellation=cancellation,
            expected_type=dict,
            raw_response=raw_response,
        )

    def import_terms(self, project_id: int, filepath: str, *, overwrite: bool = False, cancellation=None):
        """
        批量导入术语（JSON 或 CSV）。

        Args:
            project_id: 项目 ID
            filepath: 本地 JSON 或 CSV 文件路径。
            overwrite: 删除导入文件中未出现的现有术语。默认仅新增与更新。

        JSON 文件格式:
        [
          {
            "term": "apple",
            "translation": "苹果",
            "pos": "noun",
            "note": "注释",
            "variants": ["apples"]
          }
        ]

        Returns:
            返回 inserted 和 updated。覆盖导入时另含 deleted。
        """
        with open(filepath, "rb") as f:
            return self._request(
                "PUT",
                f"/projects/{project_id}/terms",
                params={"overwrite": 1} if overwrite else None,
                files={"file": f},
                cancellation=cancellation,
                retryable=False,
            )

    def get_term(self, project_id: int, term_id: int):
        """获取术语信息"""
        return self._request("GET", f"/projects/{project_id}/terms/{term_id}")

    def update_term(
        self,
        project_id: int,
        term_id: int,
        data: dict,
        *,
        cancellation=None,
        raw_response: bool = False,
    ):
        """
        修改术语，data 字段同 create_term
        """
        return self._request(
            "PUT",
            f"/projects/{project_id}/terms/{term_id}",
            json=data,
            cancellation=cancellation,
            retryable=False,
            expected_type=dict,
            raw_response=raw_response,
        )

    def delete_term(self, project_id: int, term_id: int, *, cancellation=None, raw_response: bool = False):
        """删除术语（仅创建者及管理员可用）"""
        return self._request(
            "DELETE",
            f"/projects/{project_id}/terms/{term_id}",
            cancellation=cancellation,
            retryable=False,
            raw_response=raw_response,
        )
