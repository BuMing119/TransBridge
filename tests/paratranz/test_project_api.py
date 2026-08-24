from __future__ import annotations

from types import SimpleNamespace

import pytest

from transbridge.paratranz.api.paratranz_project_api import ParatranzProjectAPI


def _api(user_id: int | None, calls: list[tuple[str, str, dict]]) -> ParatranzProjectAPI:
    api = object.__new__(ParatranzProjectAPI)
    api.config = SimpleNamespace(user_id=user_id)

    def request(method: str, endpoint: str, **kwargs):
        calls.append((method, endpoint, kwargs))
        return []

    api._request = request
    return api


def test_my_project_alias_is_sent_as_verified_numeric_user_id() -> None:
    calls: list[tuple[str, str, dict]] = []
    api = _api(52466, calls)

    api.list_projects(page=2, page_size=200, uid="my", cancellation="token")

    assert calls == [
        (
            "GET",
            "/projects",
            {
                "params": {"page": 2, "pageSize": 200, "uid": 52466},
                "cancellation": "token",
                "expected_type": (list, dict),
            },
        )
    ]


@pytest.mark.parametrize("user_id", [None, 0, -1, True])
def test_my_project_alias_requires_a_verified_user_id(user_id) -> None:
    calls: list[tuple[str, str, dict]] = []
    api = _api(user_id, calls)

    with pytest.raises(ValueError, match="verified ParaTranz user ID"):
        api.list_projects(uid="my")

    assert calls == []


def test_explicit_numeric_user_id_is_preserved() -> None:
    calls: list[tuple[str, str, dict]] = []
    api = _api(7, calls)

    api.list_projects(uid=19)

    assert calls[0][2]["params"]["uid"] == 19
