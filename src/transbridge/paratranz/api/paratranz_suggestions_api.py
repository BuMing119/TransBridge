"""翻译记忆建议接口，依据官方 OpenAPI v0.5.7（2026-11-03 核对）。

文档：https://paratranz.cn/api-docs?format=json。
该接口开销高，调用方必须串行调用并复用缓存，不应批量并发或高频轮询。
"""

from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzSuggestionsAPI(ParatranzClient):
    """读取翻译建议；不创建翻译、不触发机器翻译。"""

    def get_suggestions(
        self,
        project_id: int,
        string_id: int,
        *,
        scope: str = "self",
        rate: int = 70,
        ignore: str | None = None,
        force: bool = False,
        cancellation=None,
        raw_response: bool = False,
    ):
        """GET /projects/{projectId}/strings/{stringId}/suggestions：查询翻译记忆。

        Args:
            project_id: 当前项目 ID。
            string_id: 需查询建议的词条 ID。
            scope: self=当前项目；my=我加入的项目；all=当前项目的所有关联项目，含未加入的。
            rate: 最低匹配率百分比，0–100，默认 70；0 不过滤，包含关系不受此值限制。
            ignore: 忽略的项目 ID 字符串，以非数字字符分隔，例如 "12,34"。
            force: 跳过结果及 Elasticsearch 请求缓存，仅用于排障；正常调用保持 False。
            cancellation: 可选协作取消信号。
            raw_response: True 时返回 requests.Response，由调用方读取并关闭，以检查检索响应头。

        Returns:
            默认返回最多 30 条建议的数组，按匹配率与得分排序。matching 表示匹配率；
            contains=1 表示当前原文包含建议，其 matching 不可直接与其他匹配率比较。
            原始响应头 X-Es-Took 为耗时毫秒，X-Es-Hits 为命中数；X-Es-Timedout=true
            表示检索超时，只返回部分结果。

        Notes:
            同参数结果在生产环境缓存 30 秒。请等待上一请求结束再发下一请求，大量查询应留间隔。
            本方法禁用自动重试，429/超时交由调用方降低频率后处理，不反复 force 刷新。
            HTTP/解析错误以 ExternalServiceError 传播；权限由服务端校验。
        """
        params = {"scope": scope, "rate": rate, "force": str(force).lower()}
        if ignore is not None:
            params["ignore"] = ignore
        return self._request(
            "GET",
            f"/projects/{project_id}/strings/{string_id}/suggestions",
            params=params,
            expected_type=list,
            raw_response=raw_response,
            cancellation=cancellation,
            retryable=False,
        )
