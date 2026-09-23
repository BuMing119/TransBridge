"""State-query adapter uses the durable control-operation boundary."""

from tests.ui.tools.smart_assistant.test_request_control_binding import complete_work, make_binding
from transbridge.ui.tools.smart_assistant.request_state_binding import retrieve_state

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def test_state_adapter_commits_then_delivers(composed, monkeypatch):
    case = make_binding(composed, monkeypatch)
    retrieve_state(case.binding, case.parsed)
    complete_work(case)
    case.deliveries.pop(0)()
    assert not case.failures
    assert case.binding.facade._conversation.get_transcript()[-1]["name"] == "read_request_state"
