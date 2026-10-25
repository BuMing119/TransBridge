import json
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.post_processor.base import PostProcessIssue
from transbridge.ai_translator.shared_term_pool import SharedTermPool
from transbridge.ai_translator.term_database import TermDatabaseManager
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import STAGE_QUESTIONABLE, TranslationEntry
from transbridge.ui.tools.ai_translator.proofread_composition import build_proofread_pipeline


@pytest.fixture
def compose(tmp_path, monkeypatch):
    def directory(stem):
        path = tmp_path / "cache" / stem
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(directory))
    config = LLMConfig(
        term_priority=["dynamic"],
        enable_semantic_match=False,
        pp_strategy="strict",
        pp_enable_consistency_check=True,
        pp_enable_format_validation=False,
        pp_enable_quality_gate=False,
        pp_enable_refinement=False,
        pp_enable_polish=False,
        pp_enable_arbitration=False,
    )

    def build(pool, *, esp_path=None, source="paratranz/project/source", client=None):
        if client is not None:
            config.pp_strategy = "proofread"
            monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", lambda *_args, **_kwargs: client)
            monkeypatch.setattr("transbridge.infra.llm_reasoning.with_reasoning_intent", lambda value, *_args: value)
        observed = []
        pipeline = build_proofread_pipeline(
            config,
            esp_path,
            profile=AiExecutionProfile.from_config("polish", config),
            request_budget=AiRequestBudget(1) if client is not None else None,
            terminology_binding=SimpleNamespace(term_database_kwargs=lambda: {}),
            stop_event=None,
            pause_event=None,
            log_store=SimpleNamespace(
                write_chunk=lambda *_args: None,
                write_line=lambda *_args: None,
                close_channel=lambda *_args: None,
            ),
            term_snapshot_observer=observed.append,
            shared_term_pool=pool,
            request_lane=source,
        )
        return pipeline, observed

    return build


@pytest.mark.parametrize("esp_path", [None, ""])
def test_pathless_project_proofreading_uses_live_shared_terms(compose, tmp_path, esp_path):
    pool = SharedTermPool("project", "variant", "zh_CN", path=tmp_path / "terms.json")
    pipeline, observed = compose(pool, esp_path=esp_path)
    assert len(observed) == 1
    assert isinstance(observed[0], TermDatabaseManager)
    assert observed[0].get_dynamic_db() is pool
    entry = TranslationEntry("one", "one", "Visit Riverwood", "前往河木镇", 1, "INFO:NAM1")

    # Another source may publish a term after this source enters proofreading.
    pool.publish([("Riverwood", "溪木镇", "auto_name", "")])
    result = pipeline.process([entry])[entry.id]

    assert [(issue.issue_type, issue.suggestion) for issue in result.issues] == [
        (PostProcessIssue.TERM_MISMATCH, "溪木镇")
    ]
    assert result.accepted is True
    assert result.review_status == "needs_review"
    assert result.review_contract_version == 0
    assert result.target_stage == STAGE_QUESTIONABLE
    assert result.review_reason
    assert result.polished_translation == "前往河木镇"
    assert entry.translation == "前往河木镇"


@pytest.mark.parametrize("esp_path", [None, ""])
def test_pathless_project_sends_live_references_to_ai_and_marks_legacy_response_for_review(compose, tmp_path, esp_path):
    class LegacyResponseClient:
        def __init__(self):
            self.requests = []

        def chat(self, messages, _max_tokens):
            entries = json.loads(messages[-1]["content"])["entries"]
            self.requests.extend(entries)
            return json.dumps({
                "results": [
                    {"entry_key": entry["entry_key"], "final_translation": entry["current_translation"]}
                    for entry in entries
                ]
            })

    client = LegacyResponseClient()
    pool = SharedTermPool("project", "variant", "zh_CN", path=tmp_path / "terms.json")
    pipeline, _observed = compose(pool, esp_path=esp_path, client=client)
    entry = TranslationEntry("one", "one", "Visit Riverwood", "前往河木镇", 1, "INFO:NAM1")
    pool.publish([("Riverwood", "溪木镇", "auto_name", "")])

    result = pipeline.process([entry])[entry.id]

    assert len(client.requests) == 1
    sent = client.requests[0]
    assert sent["terms"] == {"Riverwood": "溪木镇"}
    assert sent["original"] == entry.original
    assert sent["current_translation"] == entry.translation
    assert sent["context"] == entry.context
    assert any(
        issue["term"] == "Riverwood" and issue["standard_translation"] == "溪木镇" for issue in sent["detected_issues"]
    )
    assert result.accepted is True
    assert result.review_status == "needs_review"
    assert result.review_contract_version == 0
    assert result.review_reference_terms == sent["terms"]
    assert result.detected_issues == tuple(sent["detected_issues"])
    assert result.target_stage == STAGE_QUESTIONABLE
    assert result.review_reason
    assert result.polished_translation == entry.translation == "前往河木镇"


def test_pathless_non_project_proofreading_keeps_optional_terminology(compose):
    pipeline, observed = compose(None)
    entry = TranslationEntry("one", "one", "Visit Riverwood", "前往河木镇", 1, "INFO:NAM1")

    result = pipeline.process([entry])[entry.id]

    assert observed == []
    assert result.issues == ()
    assert result.accepted is True
