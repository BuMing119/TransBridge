"""Deterministic shared pacing, cancellation, cooldown and safe timing evidence."""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
from threading import Event

import pytest
import requests

from tests.paratranz.test_file_publication import gateway
from transbridge.application.ports.paratranz import ExternalServiceError
from transbridge.paratranz.file_publication import RequestBudget

LOGGER = "transbridge.application.categorized_upload.diagnostics"


class Clock:
    now = 0.0

    def advance(self, seconds):
        self.now += seconds


def events(caplog, step):
    return [
        payload
        for record in caplog.records
        if record.name == LOGGER and (payload := json.loads(record.getMessage())).get("step") == step
    ]


def clocked(monkeypatch, *responses, budget=None, clock=None):
    clock = clock or Clock()
    port, session = gateway(*responses, budget=budget)
    send = session.send

    def timed_send(request, **kwargs):
        clock.advance(0.125)
        return send(request, **kwargs)

    monkeypatch.setattr("transbridge.paratranz.file_publication.monotonic", lambda: clock.now)
    monkeypatch.setattr(port.cancellation, "wait", clock.advance)
    monkeypatch.setattr(session, "send", timed_send)
    return port, session, clock


def test_default_pacing_separates_shared_budget_wait_and_http_time(monkeypatch, caplog):
    budget, clock = RequestBudget(), Clock()
    first, _, _ = clocked(monkeypatch, [], budget=budget, clock=clock)
    second, _, _ = clocked(monkeypatch, [], budget=budget, clock=clock)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert first.inventory(7) == second.inventory(7) == {}
    rows = events(caplog, "file_http_response")
    assert budget.interval == 0.5
    assert [row["wait_ms"] for row in rows] == [0, 375]
    assert [row["elapsed_ms"] for row in rows] == [125, 125]
    assert [row["total_ms"] for row in rows] == [125, 500]
    assert {row["outcome"] for row in rows} == {"success"}


@pytest.mark.parametrize("failure,status", [(requests.Timeout("private translation"), None), ((403, {}, {}), 403)])
def test_failed_http_is_timed_without_logging_exception_text(monkeypatch, caplog, failure, status):
    port, session, _ = clocked(monkeypatch, failure)
    with caplog.at_level(logging.INFO, logger=LOGGER), pytest.raises(ExternalServiceError):
        port.inventory(7)
    (row,) = events(caplog, "file_http_response")
    assert row["elapsed_ms"] == row["total_ms"] == 125
    assert row["wait_ms"] == 0 and row["http_status"] == status
    assert row["outcome"] == "failed" and len(session.calls) == 1
    assert "private translation" not in caplog.text
    assert "file-publication-test-token" not in caplog.text


def test_cancelled_budget_wait_is_timed_without_claiming_http_was_sent(monkeypatch, caplog):
    port, session, clock = clocked(monkeypatch, [], [], budget=RequestBudget())
    assert port.inventory(7) == {}

    def cancel_wait(_seconds):
        clock.advance(0.2)
        port.cancellation.event.set()

    monkeypatch.setattr(port.cancellation, "wait", cancel_wait)
    with caplog.at_level(logging.INFO, logger=LOGGER), pytest.raises(ExternalServiceError, match="取消"):
        port.inventory(7)
    (row,) = events(caplog, "file_request_wait")
    assert row["wait_ms"] == 200 and row["outcome"] == "cancelled"
    assert not events(caplog, "file_http_response") and len(session.calls) == 1


def test_rate_limit_wait_has_its_own_duration_and_preserves_retry_bound(monkeypatch, caplog):
    budget, clock = RequestBudget(), Clock()
    port, session, _ = clocked(monkeypatch, *((429, {}, {"Retry-After": "2"}),) * 3, budget=budget, clock=clock)
    with caplog.at_level(logging.INFO, logger=LOGGER), pytest.raises(ExternalServiceError):
        port.inventory(7)
    assert len(session.calls) == 3
    assert [row["attempt"] for row in events(caplog, "file_http_response")] == [1, 2, 3]
    assert [row["elapsed_ms"] for row in events(caplog, "file_http_response")] == [125] * 3
    assert [row["elapsed_ms"] for row in events(caplog, "file_rate_limit_wait")] == [2000, 2000]
    second, _, _ = clocked(monkeypatch, [], budget=budget, clock=clock)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert second.inventory(7) == {}
    (row,) = events(caplog, "file_http_response")
    assert row["wait_ms"] == 2000 and clock.now == 6.5


def test_cancelled_rate_limit_wait_is_logged_and_not_replayed(monkeypatch, caplog):
    port, session, clock = clocked(monkeypatch, (429, {}, {"Retry-After": "12"}), [])

    def cancel_wait(_seconds):
        clock.advance(0.2)
        port.cancellation.event.set()

    monkeypatch.setattr(port.cancellation, "wait", cancel_wait)
    with caplog.at_level(logging.INFO, logger=LOGGER), pytest.raises(ExternalServiceError, match="取消"):
        port.inventory(7)
    (row,) = events(caplog, "file_rate_limit_wait")
    assert row["requested_ms"] == 12000 and row["elapsed_ms"] == 200 and row["outcome"] == "cancelled"
    assert len(session.calls) == 1


def test_cancelled_rate_limit_keeps_shared_cooldown_for_other_window(monkeypatch, caplog):
    budget, clock = RequestBudget(), Clock()
    first, first_http, _ = clocked(monkeypatch, (429, {}, {"Retry-After": "12"}), budget=budget, clock=clock)
    second, second_http, _ = clocked(monkeypatch, [], budget=budget, clock=clock)

    def cancel_wait(_seconds):
        clock.advance(0.2)
        first.cancellation.event.set()

    monkeypatch.setattr(first.cancellation, "wait", cancel_wait)
    with pytest.raises(ExternalServiceError, match="取消"):
        first.inventory(7)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert second.inventory(7) == {}
    (row,) = events(caplog, "file_http_response")
    assert row["wait_ms"] == 11800
    assert row["elapsed_ms"] == 125 and clock.now == 12.25
    assert len(first_http.calls) == len(second_http.calls) == 1


def test_shared_account_cannot_send_during_another_windows_retry_after(monkeypatch):
    budget = RequestBudget(0)
    clock = Clock()
    monkeypatch.setattr("transbridge.paratranz.file_publication.monotonic", lambda: clock.now)
    first, _ = gateway((429, {}, {"Retry-After": "12"}), [], budget=budget)
    second, second_http = gateway([], budget=budget)
    cooling, release, queued = Event(), Event(), Event()

    def cool_down(_seconds):
        cooling.set()
        assert release.wait(3)
        clock.advance(12)

    def queue_wait(seconds):
        queued.set()
        assert release.wait(3)
        return False

    monkeypatch.setattr(first.cancellation, "wait", cool_down)
    monkeypatch.setattr(second.cancellation, "wait", queue_wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(first.inventory, 7)
        assert cooling.wait(3)
        waiting = pool.submit(second.inventory, 7)
        try:
            assert queued.wait(3)
            assert second_http.calls == []
        finally:
            release.set()
        assert active.result(timeout=3) == waiting.result(timeout=3) == {}


def test_server_cooldown_does_not_delay_another_account_or_endpoint(monkeypatch):
    budget, clock = RequestBudget(), Clock()
    port, _, _ = clocked(monkeypatch, [], budget=budget, clock=clock)
    endpoint, account = port._budget_key
    budget.defer((endpoint, account), 12)
    budget.acquire((endpoint, "another-account"), port.cancellation)
    budget.acquire(("another-endpoint", account), port.cancellation)
    assert clock.now == 0


def test_slow_http_does_not_add_pacing_delay_after_response(monkeypatch):
    budget, clock = RequestBudget(), Clock()
    port, _, _ = clocked(monkeypatch, [], [], budget=budget, clock=clock)
    assert port.inventory(7) == {}
    clock.advance(0.625)
    assert port.inventory(7) == {}
    assert clock.now == 0.875


@pytest.mark.parametrize("response", [[], (403, {}, {}), (429, {}, {"Retry-After": "2"})])
def test_timing_log_failure_preserves_success_error_and_rate_limit_retry(monkeypatch, caplog, response):
    from transbridge.application.categorized_upload import diagnostics

    port, session, clock = clocked(monkeypatch, response, [])

    def fail_log(*_args, **_kwargs):
        raise ValueError("private diagnostic payload")

    monkeypatch.setattr(diagnostics, "log_event", fail_log)
    if isinstance(response, tuple) and response[0] == 403:
        with pytest.raises(ExternalServiceError) as caught:
            port.inventory(7)
        assert caught.value.status == 403
    else:
        assert port.inventory(7) == {}
    retried = isinstance(response, tuple) and response[0] == 429
    assert len(session.calls) == (2 if retried else 1)
    if retried:
        assert clock.now == 2.25
    assert "timing diagnostics unavailable" in caplog.text
    assert "private diagnostic payload" not in caplog.text


def test_failed_timing_handler_uses_fixed_stderr_fallback(monkeypatch):
    from transbridge.application.categorized_upload import diagnostics

    output = []

    def fail(*_args, **_kwargs):
        raise RuntimeError("private diagnostic payload")

    monkeypatch.setattr(diagnostics, "log_event", fail)
    monkeypatch.setattr(diagnostics._logger, "warning", fail)
    monkeypatch.setattr(diagnostics.os, "write", lambda fd, message: output.append((fd, message)))
    diagnostics.log_timing("phase_timing", private="sensitive translation")
    assert output == [(2, b"ParaTranz timing diagnostics unavailable; operation result is preserved.\n")]
