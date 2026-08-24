from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from types import SimpleNamespace

from transbridge.ui.tools.ai_translator.run_controller import RunController


@dataclass(slots=True)
class Entry:
    id: str


def test_10k_scope_freeze_stays_off_the_interactive_hot_path() -> None:
    entries = [Entry(str(index)) for index in range(10_000)]
    config = SimpleNamespace(api_key="secret", model="model", provider="openai_compatible")
    controller = RunController(owner_id="performance")

    started = perf_counter()
    request = controller.begin("translate", config, entries, esp_path="plugin.esp")
    elapsed = perf_counter() - started

    assert len(request.spec.entry_keys) == 10_000
    assert elapsed < 2.0


def test_100_run_owner_lifecycles_are_bounded() -> None:
    config = SimpleNamespace(api_key="secret", model="model")
    started = perf_counter()
    for index in range(100):
        controller = RunController(owner_id=f"owner-{index}")
        request = controller.begin("polish", config, [Entry(str(index))])
        controller.finish(request.run_id)
        controller.close()

    assert perf_counter() - started < 2.0
