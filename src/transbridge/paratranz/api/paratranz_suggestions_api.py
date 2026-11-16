# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""翻译记忆和机器翻译建议接口，依据 ParaTranz OpenAPI 0.7.0。

文档：https://paratranz.cn/api-docs?format=json。
建议接口开销高，调用方须串行调用并复用缓存。
"""

from typing import Literal

from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzSuggestionsAPI(ParatranzClient):
    """查询翻译建议，保留远端词条内容。"""

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

    def get_machine_translation(
        self,
        project_id: int,
        string_id: int,
        *,
        service: Literal["google", "youdao", "baidu", "deepl"] = "google",
        api_key: str | None = None,
        app_key: str | None = None,
        app_secret: str | None = None,
        force: bool = False,
        model: str | None = None,
        is_pro: bool | None = None,
        action: str | None = None,
        cancellation=None,
    ):
        """翻译指定词条原文。目标语言为 other 时可返回 None。

        参数按机翻服务选择。Google/DeepL 用 api_key，有道/百度用 app_key
        和 app_secret。结果缓存 7 天，调用方需串行调用。
        """
        params = {
            "service": service,
            "apiKey": api_key,
            "appKey": app_key,
            "appSecret": app_secret,
            "force": "true" if force else None,
            "model": model,
            "isPro": str(is_pro).lower() if is_pro is not None else None,
            "action": action,
        }
        return self._request(
            "GET",
            f"/projects/{project_id}/strings/{string_id}/suggestions/mt",
            params=params,
            expected_type=(dict, type(None)),
            cancellation=cancellation,
            retryable=False,
            allow_redirects=False,
            sensitive_values=tuple(value for value in (api_key, app_key, app_secret) if value),
        )
