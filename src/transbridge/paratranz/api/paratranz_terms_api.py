from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzTermsAPI(ParatranzClient):

    def list_terms(self, project_id: int, page: int = 1, page_size: int = 50):
        """获取术语列表（分页）"""
        return self._request(
            "GET",
            f"/projects/{project_id}/terms",
            params={"page": page, "pageSize": page_size}
        )

    def create_term(self, project_id: int, data: dict):
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
        return self._request("POST", f"/projects/{project_id}/terms", json=data)

    def import_terms(self, project_id: int, filepath: str):
        """
        批量导入术语（上传 JSON 文件）

        Args:
            project_id: 项目 ID
            filepath: 本地 JSON 文件路径

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
            {"inserted": int, "updated": int, "deleted": int}
        """
        with open(filepath, "rb") as f:
            return self._request(
                "PUT",
                f"/projects/{project_id}/terms",
                files={"file": f}
            )

    def get_term(self, project_id: int, term_id: int):
        """获取术语信息"""
        return self._request("GET", f"/projects/{project_id}/terms/{term_id}")

    def update_term(self, project_id: int, term_id: int, data: dict):
        """
        修改术语，data 字段同 create_term
        """
        return self._request("PUT", f"/projects/{project_id}/terms/{term_id}", json=data)

    def delete_term(self, project_id: int, term_id: int):
        """删除术语（仅创建者及管理员可用）"""
        return self._request("DELETE", f"/projects/{project_id}/terms/{term_id}")
