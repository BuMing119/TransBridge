"""Provider failures protect real credentials without changing model evidence."""

import json

import pytest

from transbridge.ai_translator.post_processor.llm_arbiter import ArbitrationContext, LLMArbiter
from transbridge.ai_translator.post_processor.llm_refiner import LLMRefiner
from transbridge.ai_translator.post_processor.polisher import LLMPolisher
from transbridge.ai_translator.post_processor.quality_gate import QualityGateChecker
from transbridge.application.translation.polish_report import build_polish_report_snapshot
from transbridge.converter.translation_entry import TranslationEntry

TEXT = "password: swordfish"


class FailingClient:
    def __init__(self):
        self._api_key = "opaque-key-with-no-provider-prefix"
        self.requests = []

    def chat(self, messages, max_tokens=0):
        self.requests.append(messages)
        raise RuntimeError(f"Provider rejected {self._api_key}; token=technical-credential")


@pytest.mark.parametrize("mode", ("polish", "refine", "quality", "arbitrate"))
@pytest.mark.parametrize("batch", (False, True))
def test_provider_failures_are_protected_at_their_source(mode, batch, monkeypatch):
    client = FailingClient()
    entry = TranslationEntry("entry", "entry", TEXT, TEXT, 1, "QUST:CNAM")
    if mode == "polish":
        service = LLMPolisher(client)
        result = service.polish_batch([entry])["entry"] if batch else service.polish(entry)
        message = result.note
        assert result.polished_translation == TEXT
    elif mode == "refine":
        service = LLMRefiner(client)
        result = service.refine_batch([entry], {"entry": []})["entry"] if batch else service.refine(entry, [])
        message = result.note
    elif mode == "quality":
        service = QualityGateChecker(client)
        issues = service.check_batch([entry]) if batch else service.check(entry)
        message = issues[0].message
        assert issues[0].original == TEXT
    else:
        service = LLMArbiter(client)
        monkeypatch.setattr(service, "_quick_decide", lambda _ctx: None)
        ctx = ArbitrationContext(entry=entry)
        result = service.arbitrate_batch([ctx])["entry"] if batch else service.arbitrate(ctx)
        message = result.reason
    assert client.requests
    assert TEXT in json.dumps(client.requests, ensure_ascii=False)
    assert client._api_key not in message
    assert "technical-credential" not in message
    assert "REDACTED" in message
    assert entry.translation == TEXT


def test_successful_model_explanation_remains_business_data_in_report():
    class Client:
        def chat(self, messages, max_tokens=0):
            return json.dumps({
                "polished_translation": TEXT,
                "confidence": 0.9,
                "needs_arbitration": False,
                "note": TEXT,
                "changes": [{"original": TEXT, "polished": TEXT, "reason": TEXT}],
            })

    entry = TranslationEntry("entry", "entry", TEXT, "before", 1, "QUST:CNAM")
    result = LLMPolisher(Client()).polish(entry)
    snapshot = build_polish_report_snapshot({"entry": result}, [entry], run_id="business-report")
    candidate = snapshot.candidates[0]
    assert candidate.text == TEXT
    assert dict(candidate.report_details)["note"] == TEXT
    assert TEXT in json.dumps(dict(candidate.report_details)["changes"])
