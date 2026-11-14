from threading import Event, Thread
from unittest.mock import MagicMock

import pytest

from tests.infra.test_llm_client_prompt_cache import _make_anthropic_client, _make_openai_client
from tests.infra.test_terminology_conflict_judge import Client, _inputs, _judge
from transbridge.application.terminology.conflict_judgment import ConflictJudgmentCancelled
from transbridge.application.terminology.query_control import QueryCancellation


@pytest.mark.parametrize("factory", [_make_openai_client, _make_anthropic_client])
def test_transport_cancel_interrupts_active_request_without_three_second_grace(factory, monkeypatch):
    client = factory()
    old = client._http_client
    closed = Event()
    old.close.side_effect = closed.set
    replacement = MagicMock()
    monkeypatch.setattr("transbridge.infra.llm_client.httpx.Client", lambda **_: replacement)
    monkeypatch.setattr(client, "_make_client", MagicMock())
    client._active_requests = 1
    worker = Thread(target=client.cancel)
    worker.start()
    try:
        assert closed.wait(0.75), "cancellation must close the transport while a request is still active"
        assert client._http_client is replacement
        replacement.close.assert_not_called()
    finally:
        client._active_requests = 0
        worker.join(4)
    assert not worker.is_alive()


@pytest.mark.parametrize("cancel_raises", [False, True])
def test_judgment_cancellation_retains_resources_until_request_finishes(cancel_raises):
    started, release, finished, cancelled = Event(), Event(), Event(), Event()
    cancellation = QueryCancellation()
    errors = []

    class BlockedClient(Client):
        cancelled = False

        def chat(self, messages, *, max_tokens):
            started.set()
            release.wait(5)
            return super().chat(messages, max_tokens=max_tokens)

        def cancel(self):
            self.cancelled = True
            cancelled.set()
            if cancel_raises:
                raise RuntimeError("provider cancellation failed")

    client = BlockedClient()
    conflict, evidence = _inputs()

    def run():
        try:
            _judge(client).judge((conflict,), evidence, cancellation=cancellation)
        except ConflictJudgmentCancelled as exc:
            errors.append(exc)
        finally:
            finished.set()

    worker = Thread(target=run)
    worker.start()
    try:
        assert started.wait(1)
        cancellation.cancel()
        assert cancelled.wait(0.75)
        assert not finished.wait(0.2), "request owner must stay alive until callbacks and leases drain"
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert client.cancelled and len(errors) == 1
