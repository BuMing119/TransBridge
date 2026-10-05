from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor, SourceOutcome
from transbridge.ui.tools.ai_translator.task_entry_results import TaskEntryResults
from transbridge.ui.tools.ai_translator.task_scope import SourceTask
from transbridge.ui.tools.ai_translator.workflow_log_store import WorkflowLogStore


def test_new_executor_reuses_durable_results_through_real_pipeline(tmp_path, monkeypatch):
    calls = []

    class Client:
        def chat(self, messages, max_tokens=0):
            items = json.loads(messages[1]["content"])["entries"]
            calls.extend(items)
            return json.dumps({
                "results": [{"entry_key": item["entry_key"], "final_translation": "您好"} for item in items]
            })

    monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", lambda _: Client())
    monkeypatch.setattr("transbridge.infra.llm_reasoning.with_reasoning_intent", lambda client, *_: client)
    config = LLMConfig(model="fake", max_concurrent=1)
    entry = TranslationEntry("one", "one", "Hello", "你好", 1, "")
    task = SourceTask("plugin", "Plugin", None, None, (), (entry,))
    events = []

    def execute(variant="variant", reuse=True, run_id="run-one", recovery_task_id=None):
        pause = threading.Event()
        pause.set()
        request = SimpleNamespace(
            run_id=run_id,
            recovery_task_id=recovery_task_id,
            config=config,
            reuse_proofread=reuse,
            request_budget=AiRequestBudget(1),
            terminology_binding=None,
            spec=SimpleNamespace(
                owner=SimpleNamespace(project_id="project", variant_id=variant),
                execution_profile=AiExecutionProfile.from_config("polish", config),
            ),
        )
        executor = SourceExecutor(
            request,
            stop_event=threading.Event(),
            pause_event=pause,
            shared_terms={},
            terms_lock=threading.Lock(),
            progress=lambda *a: None,
            log=lambda *args: events.append(args[-1]),
            checkpoint_root=tmp_path / "resume",
        )
        logs = WorkflowLogStore(None, workflow="test", log_base=tmp_path / "logs")
        try:
            return executor._polish(task, logs)
        finally:
            logs.close()

    assert execute()["one"].accepted
    assert execute()["one"].polished_translation == "您好"
    assert len(calls) == 1
    assert any("已恢复 1 条" in message for message in events)
    assert execute(variant="other")["one"].accepted
    assert len(calls) == 2
    assert execute(reuse=False)["one"].accepted
    assert len(calls) == 3
    assert execute(run_id="fresh-task")["one"].accepted
    assert len(calls) == 4
    assert execute(run_id="continuation", recovery_task_id="run-one")["one"].accepted
    assert len(calls) == 4
    assert entry.translation == "你好"


def test_cancelled_history_retains_validated_candidate_without_publishing():
    from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadResult

    entry = TranslationEntry("one", "one", "Hello", "你好", 1, "")
    task = SourceTask("plugin", "Plugin", None, None, (), (entry,))
    candidate = ProofreadResult(
        "one",
        entry.key,
        "你好",
        "你好",
        0,
        False,
        "校对已取消",
        "failed",
        processing_status="completed",
        candidate_translation="您好",
    )
    results = TaskEntryResults((task,), "run")
    results.ingest((SourceOutcome(task, polish={"one": candidate}, cancelled=True),), {entry.identity}, cancelled=True)
    snapshot = results.snapshot(task.key, cancelled=True)
    assert snapshot.accepted_count == 1
    assert snapshot.candidates[0].text == "您好"
    assert dict(snapshot.candidates[0].report_details)["result_status"] == "not_applied"
    assert entry.translation == "你好"
