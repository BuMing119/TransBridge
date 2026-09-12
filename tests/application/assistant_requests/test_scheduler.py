from concurrent.futures import ThreadPoolExecutor

import pytest

from transbridge.application.assistant_requests.models import RequestError, RequestItem, UserRequest
from transbridge.application.assistant_requests.reducer import RequestEvent, reduce_request
from transbridge.application.assistant_requests.resource_admission import ResourceAdmission
from transbridge.application.assistant_requests.scheduler import RequestScheduler, commit_answer


def request(request_id):
    return UserRequest(request_id, "session", request_id, (RequestItem("i", request_id),))


def test_foreground_b_keeps_slot_until_answer_finishes_then_a_continues_once():
    scheduler = RequestScheduler()
    scheduler.activate("session", "view")
    a, b = request("a"), request("b")
    selected = scheduler.select_next_turn("session", "view", (a, b), priority_request_ids=("b",))
    assert selected.request.request_id == "b"
    assert scheduler.select_next_turn("session", "view", (a, b)) is None
    b = commit_answer(
        selected.request,
        selected.admission,
        "B的回答",
        {"i": "answered"},
        message_id="m",
        finish_reason="stop",
        scheduler=scheduler,
    )
    assert scheduler.release(selected.admission)
    resumed = scheduler.select_next_turn("session", "view", (a, b))
    assert resumed.request.request_id == "a"
    assert scheduler.select_next_turn("session", "view", (a, b)) is None


def test_concurrent_wakes_start_only_one_turn_and_handoff_fences_old_answer():
    scheduler = RequestScheduler()
    scheduler.activate("session", "one")
    r = request("a")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: scheduler.select_next_turn("session", "one", (r,)), range(2)))
    selected = next(result for result in results if result)
    assert sum(result is not None for result in results) == 1
    scheduler.activate("session", "two")
    with pytest.raises(RequestError, match="TURN_LEASE_STALE"):
        commit_answer(
            r, selected.admission, "stale", {"i": "answered"}, message_id="m", finish_reason="stop", scheduler=scheduler
        )
    assert not scheduler.release(selected.admission)
    assert scheduler.select_next_turn("session", "two", (r,)) is not None


def test_inactive_and_user_interrupted_requests_do_not_restart():
    scheduler = RequestScheduler()
    scheduler.activate("session", "view")
    r = reduce_request(request("r"), RequestEvent("stop-generation", "interrupt", 1))
    assert scheduler.select_next_turn("session", "view", (r,)) is None
    scheduler.deactivate("session", "view")
    assert scheduler.select_next_turn("session", "view", (request("other"),)) is None


def test_automatic_budget_survives_saved_request_and_routing_shares_slot():
    scheduler = RequestScheduler(automatic_turn_limit=1)
    scheduler.activate("session", "view")
    turn = scheduler.acquire_routing("session", "view")
    assert scheduler.select_next_turn("session", "view", (request("r"),)) is None
    scheduler.release(turn)
    selected = scheduler.select_next_turn("session", "view", (request("r"),))
    scheduler.release(selected.admission)
    restored = UserRequest.from_dict(selected.request.to_dict())
    assert scheduler.select_next_turn("session", "view", (restored,)) is None


def test_resources_exclude_other_session_but_distinct_targets_progress():
    resources = ResourceAdmission()
    assert resources.acquire("session-a-effect", ("project:one/variant:main",))
    assert not resources.acquire("session-b-effect", ("project:one/variant:main",))
    assert resources.acquire("session-c-effect", ("project:two/variant:main",))
    resources.release("session-a-effect")
    assert resources.acquire("session-b-effect", ("project:one/variant:main",))


def test_anthropic_tool_use_completion_commits_coverage():
    scheduler = RequestScheduler()
    scheduler.activate("session", "view")
    selected = scheduler.select_next_turn("session", "view", (request("r"),))
    completed = commit_answer(
        selected.request,
        selected.admission,
        "完整回答",
        {"i": "answered"},
        message_id="answer",
        finish_reason="tool_use",
        scheduler=scheduler,
    )
    assert completed.status == "completed"
