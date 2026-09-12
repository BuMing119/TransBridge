"""Synthetic end-to-end Qt measurement; timing is reported, not a machine-dependent CI assertion."""

import json
from threading import get_ident
from time import monotonic

from PyQt6.QtCore import QTimer
import pytest

from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import (
    _requests,
    _seed_long_request,
    _until,
)
from transbridge.config.llm import LLMConfig

pytest_plugins = ["tests.ui.tools.smart_assistant.test_request_lifecycle_panel"]


@pytest.mark.slow
def test_ten_thousand_messages_complete_with_live_qt_heartbeat(environment, monkeypatch):
    _seed_long_request(environment)
    conversation = environment.panel.chat._conversation
    for index in range(10000):
        conversation.add_assistant(f"Synthetic explanation {index}: keep original ordering and do not write files.")
    environment.panel.chat._orchestrator._cached_llm_config = LLMConfig(assistant_context_window=4_000_000)
    main_thread = get_ident()
    gui_work = []

    def instrument(owner, name):
        original = getattr(owner, name)

        def measured(*args, **kwargs):
            begin = monotonic()
            try:
                return original(*args, **kwargs)
            finally:
                if get_ident() == main_thread:
                    gui_work.append((name, (monotonic() - begin) * 1000))

        monkeypatch.setattr(owner, name, measured)

    for name in ("save_history", "update_request", "state"):
        instrument(environment.service, name)
    for name in ("prepare_model_input", "handle_response", "refresh"):
        instrument(environment.binding, name)
    ticks = [monotonic()]
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(monotonic()))
    timer.start()
    try:
        environment.binding.wake()
        _until(lambda: bool(environment.client.calls) and environment.binding.admission is None, timeout=60)
    finally:
        timer.stop()
        ticks.append(monotonic())
    result = {
        "messages": 10000,
        "total_request_ms": (ticks[-1] - ticks[0]) * 1000,
        "maximum_heartbeat_gap_ms": max(b - a for a, b in zip(ticks, ticks[1:])) * 1000,
        "heartbeat_count": len(ticks) - 2,
        "business_calls": len(environment.client.calls),
        "summary_calls": len(environment.client.summary_calls),
        "slowest_gui_operations": sorted(gui_work, key=lambda item: item[1], reverse=True)[:5],
    }
    print("CONTEXT_QT_MEASUREMENT=" + json.dumps(result))
    assert result["heartbeat_count"] > 0
    assert _requests(environment)[0].terminal
    assert result["business_calls"] == 1
    assert "Synthetic explanation 0:" in str(environment.client.calls[0][0])
    assert "Synthetic explanation 9999:" in str(environment.client.calls[0][0])
