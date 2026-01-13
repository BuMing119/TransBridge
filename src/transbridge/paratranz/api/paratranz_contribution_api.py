from typing import Optional
from datetime import datetime
from src.transbridge.paratranz.paratranz_client import ParatranzClient

class ParatranzContributionAPI(ParatranzClient):

    def get_contributions(
        self,
        project_id: int,
        user_id: Optional[int] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        lang: Optional[str] = None
    ):
        """
        获取成员贡献统计

        参数说明：
        user_id:    过滤某个用户
        since:      毫秒时间戳 (起始)
        until:      毫秒时间戳 (结束)
        lang:       筛选语言

        返回: 列表结构
        """
        params = {}

        if user_id:
            params["userId"] = user_id
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        if lang:
            params["lang"] = lang

        return self._request(
            "GET",
            f"/projects/{project_id}/contributions",
            params=params if params else None
        )
