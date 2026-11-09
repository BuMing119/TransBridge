# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""用户接口；依据 https://paratranz.cn/api-docs?format=json（OpenAPI 0.5.7）。"""

from urllib.parse import urljoin, urlsplit, urlunsplit

from transbridge.paratranz.paratranz_client import ParatranzClient


def _public_origin(api_base_url: str) -> str | None:
    parsed = urlsplit(api_base_url)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


def resolve_paratranz_media_url(value: str | None, api_base_url: str) -> str | None:
    """Resolve API-returned media paths against the ParaTranz public origin."""

    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return ""
    parsed = urlsplit(normalized)
    if parsed.scheme.casefold() in {"http", "https"} and parsed.netloc:
        return normalized
    if parsed.scheme or parsed.netloc:
        return normalized
    origin = _public_origin(api_base_url)
    return urljoin(origin, normalized) if origin is not None else normalized


def prepare_paratranz_media_url(value: str | None, api_base_url: str) -> str | None:
    """Restore same-origin ParaTranz media URLs to the relative API representation."""

    if value is None:
        return None
    normalized = value.strip()
    parsed = urlsplit(normalized)
    origin = _public_origin(api_base_url)
    if origin is None:
        return normalized
    public = urlsplit(origin)
    if (
        parsed.scheme.casefold() == public.scheme.casefold()
        and parsed.netloc.casefold() == public.netloc.casefold()
        and parsed.path.startswith("/media/")
    ):
        return urlunsplit(("", "", parsed.path, parsed.query, parsed.fragment))
    return normalized


class ParatranzUserAPI(ParatranzClient):
    def _normalize_user(self, user):
        if not isinstance(user, dict):
            return user
        normalized = user.copy()
        if "avatar" in normalized:
            normalized["avatar"] = resolve_paratranz_media_url(user.get("avatar"), self.config.base_url)
        return normalized

    def get_my_user(self):
        """获取当前认证用户的信息（无需 user_id）。"""
        return self._normalize_user(self._request("GET", "/users/my"))

    def get_user(self, user_id: int):
        """获取用户信息"""
        return self._normalize_user(self._request("GET", f"/users/{user_id}"))

    def update_user(self, user_id: int, data: dict):
        """
        更新用户信息（仅支持修改自己的信息）

        data 示例:
        {
            "nickname": "昵称",
            "bio": "个人介绍，最长 140 字符",
            "avatar": "https://example.com/avatar.png"
        }
        """
        payload = data.copy()
        if "avatar" in payload:
            payload["avatar"] = prepare_paratranz_media_url(payload["avatar"], self.config.base_url)
        return self._normalize_user(self._request("PUT", f"/users/{user_id}", json=payload))

    def with_avatar_payload(self, user):
        """Download avatar bytes through the requests adapter used by API workers."""

        normalized = self._normalize_user(user)
        if not isinstance(normalized, dict):
            return normalized
        avatar_url = str(normalized.get("avatar") or "").strip()
        if not avatar_url:
            return normalized
        parsed = urlsplit(avatar_url)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("ParaTranz avatar URL must use HTTP or HTTPS")
        response = self._session.get(
            avatar_url,
            headers={"Accept": "image/*"},
            timeout=min(float(self.config.timeout), 8.0),
            allow_redirects=True,
        )
        response.raise_for_status()
        final_url = urlsplit(response.url)
        if final_url.scheme.casefold() not in {"http", "https"} or not final_url.netloc:
            raise ValueError("ParaTranz avatar redirect left the HTTP boundary")
        enriched = normalized.copy()
        enriched["_avatar_bytes"] = response.content
        return enriched

    def get_user_projects(self, user_id: int, *, cancellation=None):
        """获取用户参与的项目：GET /users/{userId}/projects。

        私密项目由服务端按访问者权限过滤；此接口没有分页参数。

        Args:
            user_id: 要查询的用户 ID。
            cancellation: 可选取消令牌，在请求前后检查取消状态。

        Returns:
            成员关系对象列表，每项含 id、uid、permission、privacy 和 project。
            project 是含 id、name、logo、stage、privacy 的项目简略信息，
            不能把外层成员关系 id 当作项目 ID。
        """
        return self._request("GET", f"/users/{user_id}/projects", cancellation=cancellation, expected_type=list)

    def get_user_activities(
        self,
        user_id: int,
        page: int = 1,
        page_size: int = 50,
        *,
        operation: str | None = None,
        begin: str | None = None,
        end: str | None = None,
        cancellation=None,
    ):
        """获取用户近期词条动态：GET /users/{userId}/activities。

        私密项目的动态由服务端按访问者权限过滤。

        Args:
            user_id: 要查询的用户 ID。
            page: 页码，从 1 开始。
            page_size: 每页数量；保留本客户端默认 50（服务端省略参数时默认 12）。
            operation: 按操作类型筛选，如 translate；comment 返回评论记录。
            begin: 筛选开始时间，ISO 8601 日期时间字符串。
            end: 筛选结束时间，ISO 8601 日期时间字符串。
            cancellation: 可选取消令牌，在请求前后检查取消状态。

        Returns:
            含 page、pageSize、rowCount、pageCount、results 的分页对象。
            results 为 UserActivity 列表，包含 id、createdAt、projectId、uid、
            operation、stringId、historyId、isPrivate、history 等动态字段。
        """
        params = {"page": page, "pageSize": page_size}
        for name, value in (("operation", operation), ("begin", begin), ("end", end)):
            if value is not None:
                params[name] = value
        return self._request(
            "GET", f"/users/{user_id}/activities", params=params, cancellation=cancellation, expected_type=dict
        )
