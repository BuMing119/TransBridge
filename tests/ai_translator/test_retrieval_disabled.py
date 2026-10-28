from __future__ import annotations

import json
import re
import threading
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.project_terminology_adapter import (
    EnabledEffectiveTerminologyGate,
    ProjectTerminologyAdapter,
)
from transbridge.ai_translator.term_database import DynamicTermDatabase, TermDatabaseManager
from transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig
from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
    SnapshotEffectiveTerminologyPort,
    TerminologyLookupContext,
)
from transbridge.application.terminology.models import DecisionStatus, TermDecision
from transbridge.application.translation import InMemoryTranslationCheckpointPort
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.infra.llm_structured_outputs import LlmStructuredOutputTruncatedError


def _project_binding():
    snapshot = EffectiveTerminologySnapshot(
        "project",
        "variant",
        EffectiveSnapshotStatus.READY,
        "v1",
        "digest",
        (TermDecision("sword", "project", "variant", "Sword", "sword", "项目剑", status=DecisionStatus.ADOPTED),),
    )
    adapter = ProjectTerminologyAdapter(
        SnapshotEffectiveTerminologyPort(SimpleNamespace(snapshot=lambda *_args: snapshot)),
        EnabledEffectiveTerminologyGate(),
    )
    return adapter, TerminologyLookupContext("project", "variant")


@pytest.mark.parametrize("project_terms", [False, True])
def test_disabled_retrieval_excludes_project_and_existing_dynamic_terms(tmp_path, monkeypatch, project_terms):
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))
    dynamic = DynamicTermDatabase("fixture.esp")
    dynamic.add_many_and_save([("Sword", "旧剑", "manual", "")])
    adapter, context = _project_binding() if project_terms else (None, None)
    manager = TermDatabaseManager(
        LLMConfig(retrieval_enabled=False),
        "fixture.esp",
        effective_loader=adapter,
        terminology_context=context,
    )
    assert manager.load_all() == {}
    entry = TranslationEntry("entry", "entry", "Sword", "", 0, "")

    assert manager.resolve_term("Sword") is None
    assert not manager.has_term("Sword")
    assert manager.exact_match(["Sword"]) == {}
    assert manager.match_terms(["Sword"]) == {}
    assert manager.match_terms_for_entry(entry) == {}
    assert manager.match_terms_scoped([entry], in_flight_terms={"Sword": "临时剑"}).flat_terms == {}
    assert manager._dynamic_db is None


def test_disabled_retrieval_does_not_query_or_rebuild_an_existing_vector_index(tmp_path, monkeypatch):
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))
    manager = TermDatabaseManager(LLMConfig(retrieval_enabled=False), "fixture.esp")

    class UnavailableIndex:
        @property
        def available(self):
            raise AssertionError("disabled retrieval must not touch the vector backend")

        def build_index(self, *_args, **_kwargs):
            raise AssertionError("disabled retrieval must not build a vector index")

    manager._vector_index = UnavailableIndex()
    assert manager.semantic_match(["Sword"]) == {}
    assert manager.rebuild_vector_index() is False


@pytest.mark.parametrize("entry_context", ["NPC_:FULL", "INFO:NAM1"])
@pytest.mark.parametrize("response_shape", ["complete", "partial", "truncated"])
def test_disabled_retrieval_translates_with_model_instead_of_project_direct_fill(
    tmp_path, monkeypatch, entry_context, response_shape
):
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))
    adapter, context = _project_binding()
    config = LLMConfig(retrieval_enabled=False, enable_post_process=False)
    entries = [TranslationEntry(word, word, word, "", 0, entry_context) for word in ("Sword", "Shield")]
    collection = TranslationEntryCollection(entries)

    class Client:
        def __init__(self):
            self.sources = []
            self.calls = 0

        def chat_stream(self, messages, _max_tokens, callback):
            match = re.search(r"<translation_entries>\s*(.*?)\s*</translation_entries>", messages[-1]["content"], re.S)
            assert match is not None
            payload = json.loads(match.group(1))
            self.calls += 1
            self.sources.extend(item["source"] for item in payload.values())
            assert all(not item.get("terms") for item in payload.values())
            partial = self.calls == 1 and response_shape != "complete"
            keys = list(payload)[:1] if partial else payload
            response = json.dumps({
                "results": [{"entry_id": key, "translation": "模型译文"} for key in keys],
            })
            callback(response)
            if partial and response_shape == "truncated":
                raise LlmStructuredOutputTruncatedError("test response truncated")
            return response

        def cancel(self):
            pass

    client = Client()
    translator = AutoTranslator(
        TranslatorConfig(config, "fixture.esp"),
        llm_client=client,
        effective_terminology=adapter,
        terminology_context=context,
        candidate_checkpoint=InMemoryTranslationCheckpointPort(),
    )

    def reject_term_updates(*_args, **_kwargs):
        pytest.fail("disabled retrieval must not update or extract terms")

    monkeypatch.setattr(translator, "_update_dynamic_terms", reject_term_updates)
    monkeypatch.setattr(translator, "_extract_dialogue_terms", reject_term_updates)
    result = translator.translate(collection, None, lambda *_args: None, threading.Event())

    assert result.success_count == 2 and result.failed_count == 0
    assert set(client.sources) == {"Sword", "Shield"}
    assert client.calls == (1 if response_shape == "complete" else 2)
    assert all(collection.get(entry.identity).translation == "模型译文" for entry in entries)
    assert translator._term_mgr._dynamic_db is None
    assert translator._in_flight_terms == {}
