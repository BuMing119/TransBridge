"""History queries use durable receipts and do not revalidate old facts in Qt."""

import json

from tests.ui.tools.smart_assistant.test_request_control_binding import complete_work, make_binding
from transbridge.ui.tools.smart_assistant.request_history_binding import retrieve_history

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def test_history_adapter_returns_original_material_and_resumes(composed, monkeypatch):
    case = make_binding(
        composed, monkeypatch, "read_request_history", {"message_id": "input", "offset": 0, "limit": 2000}
    )
    before = case.binding.service.state(composed[2])
    retrieve_history(case.binding, case.parsed)
    complete_work(case)
    case.deliveries.pop(0)()
    assert not case.failures
    receipt = json.loads(case.binding.facade._conversation.get_transcript()[-1]["content"])
    assert receipt["material_only"] and "取消任务" in receipt["text"]
    assert case.binding.service.state(composed[2])["requests"] == before["requests"]
    case.resumes.pop()()
    assert case.starts == [True]


def test_saved_history_fact_survives_later_source_reassignment(composed, monkeypatch):
    case = make_binding(
        composed, monkeypatch, "read_request_history", {"message_id": "input", "offset": 0, "limit": 2000}
    )
    retrieve_history(case.binding, case.parsed)
    complete_work(case)
    case.binding.service.transact(
        composed[2], lambda state: state.setdefault("message_owners", {}).update(input="other")
    )
    case.binding.control_results.cancel()
    case.binding.admission = None
    case.deliveries.pop(0)()
    assert not case.failures and not case.resumes
    assert "material_only" in case.binding.facade._conversation.get_transcript()[-1]["content"]
