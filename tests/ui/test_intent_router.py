from __future__ import annotations

from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.shell.intent_router import IntentRouter


def test_router_has_one_handler_owner_and_forwards_immutable_payload_once() -> None:
    router = IntentRouter()
    payloads = []

    def handler(payload):
        payloads.append(payload)
        return "done"

    router.register(IntentId.TRANSLATION_AI, handler)

    result = router.dispatch(IntentId.TRANSLATION_AI, {"content_id": "main"})

    assert result.accepted and result.value == "done"
    assert payloads == [{"content_id": "main"}]
    try:
        payloads[0]["content_id"] = "changed"
    except TypeError:
        pass
    else:
        raise AssertionError("intent payload must be immutable")


def test_router_exposes_disabled_reason_and_never_calls_disabled_handler() -> None:
    called = []
    router = IntentRouter()
    router.register(
        IntentId.TRANSLATION_AI,
        lambda _payload: called.append(True),
        availability=lambda: (False, "当前没有可翻译内容"),
    )

    availability = router.availability(IntentId.TRANSLATION_AI)
    result = router.dispatch(IntentId.TRANSLATION_AI)

    assert not availability.enabled and availability.reason == "当前没有可翻译内容"
    assert not result.accepted and result.reason == availability.reason
    assert called == []


def test_router_requires_explicit_confirmation_for_caution_actions() -> None:
    calls = []
    router = IntentRouter()
    router.register(IntentId.PUBLISH_WRITE, lambda payload: calls.append(payload))

    pending = router.dispatch(IntentId.PUBLISH_WRITE)
    accepted = router.dispatch(IntentId.PUBLISH_WRITE, confirmed=True)

    assert pending.requires_confirmation and not pending.accepted
    assert accepted.accepted and len(calls) == 1


def test_router_reports_unregistered_and_closed_actions() -> None:
    router = IntentRouter()
    assert not router.availability(IntentId.HELP_ABOUT).enabled
    router.register(IntentId.HELP_ABOUT, lambda _payload: None)
    router.close()
    assert router.availability(IntentId.HELP_ABOUT).reason == "当前窗口已关闭"
