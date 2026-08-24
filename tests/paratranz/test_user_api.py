from __future__ import annotations

from types import SimpleNamespace

from transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI


def _api(response, calls: list[tuple[str, str, dict]]) -> ParatranzUserAPI:
    api = object.__new__(ParatranzUserAPI)
    api.config = SimpleNamespace(base_url="https://paratranz.cn/api", timeout=30)

    def request(method: str, endpoint: str, **kwargs):
        calls.append((method, endpoint, kwargs))
        return response

    api._request = request
    return api


def test_user_avatar_relative_media_path_is_resolved_against_public_origin() -> None:
    calls: list[tuple[str, str, dict]] = []
    api = _api({"id": 119, "avatar": "/media/avatar.jpeg!320"}, calls)

    user = api.get_my_user()

    assert user["avatar"] == "https://paratranz.cn/media/avatar.jpeg!320"
    assert calls == [("GET", "/users/my", {})]


def test_absolute_external_avatar_url_is_preserved() -> None:
    calls: list[tuple[str, str, dict]] = []
    api = _api({"id": 119, "avatar": "https://cdn.example/avatar.png"}, calls)

    user = api.get_user(119)

    assert user["avatar"] == "https://cdn.example/avatar.png"


def test_profile_update_restores_same_origin_media_path_and_normalizes_response() -> None:
    calls: list[tuple[str, str, dict]] = []
    api = _api({"id": 119, "avatar": "/media/new.jpeg!320"}, calls)

    user = api.update_user(
        119,
        {"nickname": "望山", "avatar": "https://paratranz.cn/media/new.jpeg!320"},
    )

    assert calls == [
        (
            "PUT",
            "/users/119",
            {"json": {"nickname": "望山", "avatar": "/media/new.jpeg!320"}},
        )
    ]
    assert user["avatar"] == "https://paratranz.cn/media/new.jpeg!320"


class _AvatarResponse:
    def __init__(self, payload: bytes) -> None:
        self.content = payload
        self.url = "https://paratranz.cn/media/avatar.jpeg!320"
        self.raise_calls = 0

    def raise_for_status(self) -> None:
        self.raise_calls += 1


class _AvatarSession:
    def __init__(self, response: _AvatarResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_avatar_download_uses_requests_adapter_without_a_two_megabyte_limit() -> None:
    payload = b"x" * (2 * 1024 * 1024 + 1)
    calls: list[tuple[str, str, dict]] = []
    api = _api(None, calls)
    response = _AvatarResponse(payload)
    api._session = _AvatarSession(response)

    user = api.with_avatar_payload({"id": 119, "avatar": "/media/avatar.jpeg!320"})

    assert user["_avatar_bytes"] == payload
    assert api._session.calls == [
        (
            "https://paratranz.cn/media/avatar.jpeg!320",
            {
                "headers": {"Accept": "image/*"},
                "timeout": 8.0,
                "allow_redirects": True,
            },
        )
    ]
    assert response.raise_calls == 1
