"""Optional round undo is never implied by stopping or hiding a request."""

import json
from types import SimpleNamespace

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.smart_assistant.context_budget import ContextBudget
from transbridge.smart_assistant.conversation_manager import ConversationManager
from transbridge.smart_assistant.request_model_input import RequestModelInput
from transbridge.ui.tools.smart_assistant.request_undo_binding import QMessageBox, RequestUndoBinding


def _setup(*, limitations=(), available=True):
    jobs, messages, mutations, previews = [], [], [], []
    saved = []
    result = {"available": available, "limitations": limitations, "reason": "没有可恢复修改"}

    def preview(context, identity):
        previews.append((context, identity))
        return result

    def undo(context, identity, *, allow_partial):
        mutations.append((context, identity, allow_partial))
        saved.append({
            "message_id": "undo-receipt",
            "role": "user",
            "content": json.dumps({
                "material_only": True,
                "kind": "round_undo_receipt",
                "round_id": identity,
                "partial": allow_partial,
                "limitations": limitations,
            }),
        })
        return {"partial": allow_partial, "limitations": limitations}

    binding = SimpleNamespace(
        context=RequestContext("owner", session_id="session"),
        _turn_generation=1,
        _closed=False,
        facade=SimpleNamespace(_conversation=ConversationManager()),
        service=SimpleNamespace(
            undo=SimpleNamespace(preview=preview, undo=undo),
            lifecycle=SimpleNamespace(read_session=lambda *_: SimpleNamespace(backend_messages=lambda: list(saved))),
        ),
        view=SimpleNamespace(show_undo=lambda text, *, available: messages.append((text, available))),
        background=SimpleNamespace(submit=lambda work, received=None, **kwargs: jobs.append((work, received, kwargs))),
        refresh=lambda: None,
    )
    adapter = RequestUndoBinding(binding)
    request = SimpleNamespace(
        request_id="request", work_round_id="round", unsettled=False, terminal=False, pause_reasons=("paused",)
    )
    return SimpleNamespace(
        adapter=adapter,
        binding=binding,
        request=request,
        jobs=jobs,
        messages=messages,
        mutations=mutations,
        previews=previews,
    )


def _run(case):
    work, received, options = case.jobs.pop(0)
    try:
        result = work()
    except Exception as exc:
        options["failed"](exc)
    else:
        received(result)


def test_stop_waits_for_settlement_and_only_offers_optional_button(monkeypatch):
    case = _setup()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("stop must not ask for undo"))
    case.adapter.stopped("request")
    case.request.unsettled = True
    case.adapter.observe([case.request])
    assert not case.jobs and not case.mutations
    case.request.unsettled = False
    case.adapter.observe([case.request])
    _run(case)
    assert case.messages[-1][1] and not case.mutations
    assert case.previews == [(case.binding.context, "round")]


@pytest.mark.parametrize("accepted", [False, True])
def test_partial_undo_requires_explicit_confirmation_after_click(monkeypatch, accepted):
    case = _setup(limitations=[{"tool": "upload", "reason": "远端上传不能自动撤销"}])
    confirmations = []

    def confirm(*args):
        confirmations.append(args)
        return QMessageBox.StandardButton.Yes if accepted else QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", confirm)
    case.adapter.stopped("request")
    case.adapter.observe([case.request])
    _run(case)
    assert not confirmations and not case.mutations
    case.adapter.request()
    _run(case)
    assert len(confirmations) == 1
    if accepted:
        _run(case)
        assert case.mutations == [(case.binding.context, "round", True)]
    else:
        assert not case.jobs and not case.mutations


def test_switch_after_click_cancels_queued_inverse_without_changing_new_view():
    case = _setup()
    case.adapter.stopped("request")
    case.adapter.observe([case.request])
    _run(case)
    case.adapter.request()
    _run(case)
    case.adapter.invalidate()
    case.binding.context = "other"
    _run(case)
    assert not case.mutations
    assert case.messages[-1] == ("", False)


def test_unavailable_and_failed_undo_expose_reason_without_retry():
    case = _setup(available=False)
    case.adapter.stopped("request")
    case.adapter.observe([case.request])
    _run(case)
    assert "没有可恢复修改" in case.messages[-1][0]
    assert not case.messages[-1][1] and not case.mutations
    case = _setup()

    def fail(*args, **kwargs):
        raise RuntimeError("版本已变化")

    case.binding.service.undo.undo = fail
    case.adapter.stopped("request")
    case.adapter.observe([case.request])
    _run(case)
    case.adapter.request()
    _run(case)
    _run(case)
    assert "版本已变化" in case.messages[-1][0]
    assert not case.messages[-1][1] and not case.jobs


def test_successful_undo_receipt_enters_next_model_input():
    case = _setup()
    case.adapter.stopped("request")
    case.adapter.observe([case.request])
    _run(case)
    case.adapter.request()
    _run(case)
    _run(case)
    records = case.binding.facade._conversation.get_transcript()
    messages, _ = RequestModelInput(tuple(records), (), ContextBudget()).assemble()
    receipt = json.loads(messages[-1]["content"])
    assert receipt["kind"] == "round_undo_receipt" and receipt["material_only"]
    assert receipt["round_id"] == "round" and not receipt["partial"]
