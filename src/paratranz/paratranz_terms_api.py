import json
from typing import List
from .paratranz_client import ParatranzClient


class ParatranzTermsAPI(ParatranzClient):

    def list_terms(self, project_id: int, page: int = 1):
        """术语列表"""
        return self._request(
            "GET",
            f"/projects/{project_id}/terms",
            params={"page": page}
        )

    def create_term(self, project_id: int, data: dict):
        """
        创建术语

        data 示例:
        {
            "key": "FPS",
            "original": "First Person Shooter",
            "translation": "第一人称射击",
            "description": "游戏术语说明"
        }
        """
        return self._request(
            "POST",
            f"/projects/{project_id}/terms",
            data=json.dumps(data)
        )

    def batch_import_terms(self, project_id: int, terms: List[dict]):
        """
        批量导入术语

        terms 示例:
        [
            {"key": "NPC", "original": "Non-player character", "translation": "非玩家角色"},
            {"key": "HP", "original": "Health Point", "translation": "生命值"}
        ]
        """
        return self._request(
            "POST",
            f"/projects/{project_id}/terms/batch",
            data=json.dumps(terms)
        )

    def get_term(self, project_id: int, term_id: int):
        """获取术语信息"""
        return self._request(
            "GET",
            f"/projects/{project_id}/terms/{term_id}"
        )

    def update_term(self, project_id: int, term_id: int, data: dict):
        """
        修改术语字段

        data 示例:
        {
            "translation": "更新后的翻译",
            "description": "新的描述"
        }
        """
        return self._request(
            "PUT",
            f"/projects/{project_id}/terms/{term_id}",
            data=json.dumps(data)
        )

    def delete_term(self, project_id: int, term_id: int):
        """删除术语"""
        return self._request(
            "DELETE",
            f"/projects/{project_id}/terms/{term_id}"
        )
