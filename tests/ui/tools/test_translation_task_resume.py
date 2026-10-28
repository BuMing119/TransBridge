"""Cross-process translation seeds must skip model work and still finish the pipeline."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
from threading import Event

import pytest

from tests.ui.tools import test_ai_project_name_preparation as support
from transbridge.application.translation import FilesystemTranslationCheckpointPort, LegacyTranslationCandidateSession
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.ui.tools.ai_translator.run_spec import FrozenExecutionConfig
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor
from transbridge.ui.tools.ai_translator.source_terms import source_run_id
from transbridge.ui.tools.ai_translator.task_translation_resume import prepare_translation_recovery

name_run = support.name_run


def _record(run, task, candidates, *, config=None):
    root = Path(run.request.config.get_ai_translator_dir(Path(task.esp_path).stem)) / "runtime-v2"
    session = LegacyTranslationCandidateSession(
        run_id=source_run_id("old-task-attempt-1", task.key),
        owner_id="legacy-auto-translator",
        spec_fingerprint="a" * 64,
        input_fingerprint="b" * 64,
        checkpoint=FilesystemTranslationCheckpointPort(root),
        provider="fixture",
        model="fixture",
    )
    session.accept({ai_entry_key(entry): text for entry, text in candidates}, task.collection)
    record = {
        "task_id": "old-task",
        "execution_runs": ["old-task"],
        "sources": [
            {
                "key": task.key,
                "checkpoint_stem": Path(task.esp_path).stem,
                "translate_keys": [entry.identity.to_dict() for entry in task.translate_entries],
                "inputs": [
                    {
                        "entry_key": entry.identity.to_dict(),
                        "revision": entry.revision.value,
                        "original": entry.original,
                        "translation": entry.translation,
                        "stage": entry.stage,
                        "context": entry.context or "",
                    }
                    for entry in task.translate_entries
                ],
            }
        ],
    }
    execution = (
        {}
        if config is None
        else {
            "spec": replace(run.request.spec, execution_profile=AiExecutionProfile.from_config("translate", config)),
            "config_snapshot": FrozenExecutionConfig(config),
        }
    )
    run.request = replace(
        run.request,
        **execution,
        recovery_record=record,
        recovery_task_id="old-task",
        recovery_directory=run.root / "p",
    )
    run.executor.request = run.request
    run.executor.config = run.request.config
    return root


@pytest.mark.parametrize("name_run", [False, True], indirect=True)
def test_fresh_executor_reuses_durable_candidates_and_only_translates_remaining(name_run):
    run = name_run
    task = run.task("A", [("known", "Whiterun", "LCTN:FULL"), ("pending", "Continue.", "BOOK:DESC")])
    root = _record(run, task, [(task.entries[0], "恢复雪漫")])
    originals = {path.name: path.read_bytes() for path in root.glob("*.json*")}
    run.executor.prepare_names((task,))
    assert run.provider.calls == []
    assert {item.term: item.translation for item in run.executor.shared_term_pool.snapshot().entries}[
        "Whiterun"
    ] == "恢复雪漫"
    assert all(entry.translation == "" for entry in task.collection)
    outcome = run.executor.execute(task)
    assert outcome.successful, outcome.error
    assert run.provider.counts() == {"Continue.": 1}
    assert outcome.translation.success_count == 2
    assert task.collection.get(task.entries[0].identity).translation == "恢复雪漫"
    assert all(item.status == "succeeded" for item in outcome.translation.entry_outcomes.values())
    assert all((root / name).read_bytes() == payload for name, payload in originals.items())
    if run.request.config.enable_post_process:
        assert outcome.translation.post_process_result.candidates
        assert any(event[1] == "format" for event in run.events)


def test_changed_current_input_does_not_receive_old_candidate(name_run):
    run = name_run
    task = run.task("A", [("known", "Whiterun", "LCTN:FULL")])
    _record(run, task, [(task.entries[0], "旧候选")])
    changed = replace(task.entries[0], context="different context")
    task = replace(task, translate_entries=(changed,))
    assert prepare_translation_recovery(run.executor, (task,)) == {"A": {}}
    assert task.collection.get(task.entries[0].identity).translation == ""


def test_paused_recovery_can_be_cancelled_without_loading_or_mutating(name_run, monkeypatch):
    run = name_run
    task = run.task("A", [("known", "Whiterun", "LCTN:FULL")])
    _record(run, task, [(task.entries[0], "恢复雪漫")])
    monkeypatch.setattr(
        "transbridge.application.translation.task_candidate_recovery.load_recovery_candidates",
        lambda *_: pytest.fail("paused recovery must not read candidate storage"),
    )
    run.executor.pause.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        started = Event()

        def recover():
            started.set()
            return prepare_translation_recovery(run.executor, (task,))

        future = pool.submit(recover)
        assert started.wait(3)
        assert not future.done()
        run.executor.stop.set()
        assert future.result(timeout=3) == {}
    assert run.provider.calls == []
    assert all(entry.translation == "" for entry in task.collection)


def test_completed_postprocess_is_restored_into_new_private_draft(name_run, monkeypatch):
    run = name_run
    config = run.request.config
    config.enable_post_process = True
    run.executor.consistency = None
    task = run.task("A", [("known", "Whiterun", "LCTN:FULL")])
    _record(run, task, [(task.entries[0], "恢复雪漫")], config=config)
    run.executor.prepare_names((task,))
    first = run.executor.execute(task)
    assert first.successful, first.error
    assert any(event[1] == "format" for event in run.events)
    fresh_task = run.task("A", [("known", "Whiterun", "LCTN:FULL")])
    pause = Event()
    pause.set()
    fresh = SourceExecutor(
        run.request,
        stop_event=Event(),
        pause_event=pause,
        shared_terms={},
        terms_lock=run.executor.terms_lock,
        progress=lambda *_: None,
        log=lambda *_: None,
        attempt_id="continued-attempt-1",
    )
    fresh.prepare_names((fresh_task,))
    monkeypatch.setattr(
        "transbridge.application.translation.postprocess_stages.CheckerStage.__call__",
        lambda *_: pytest.fail("a completed post-processing stage must not run again"),
    )
    resumed = fresh.execute(fresh_task)
    assert resumed.successful, resumed.error
    assert resumed.translation.success_count == 1
    assert fresh_task.collection.get("known").translation == "恢复雪漫"
    assert run.provider.calls == []


def test_interrupted_comprehensive_review_reuses_completed_batches(name_run, monkeypatch):
    run = name_run
    config = run.request.config
    config.enable_post_process = True
    config.pp_strategy = "proofread"
    config.max_concurrent = 1
    config.max_tokens_per_batch = 600
    run.executor.consistency = None
    rows = [(str(index), f"Sentence {index}: " + "word " * 180, "BOOK:DESC") for index in range(3)]
    task = run.task("A", rows)
    _record(run, task, [(entry, f"已翻译 {index}") for index, entry in enumerate(task.entries)], config=config)
    reviewed = []
    interrupt = True

    def review(messages, max_tokens=0):
        nonlocal interrupt
        entries = json.loads(messages[-1]["content"])["entries"]
        reviewed.extend(item["entry_key"] for item in entries)
        if interrupt:
            interrupt = False
            run.executor.stop.set()
        return json.dumps({
            "results": [
                {
                    "entry_key": item["entry_key"],
                    "final_translation": item["current_translation"] + "（校对）",
                    "review_status": "revised",
                    "review_reason": "根据上下文改进表达",
                }
                for item in entries
            ],
        })

    monkeypatch.setattr(run.provider, "chat", review)
    run.executor.prepare_names((task,))
    interrupted = run.executor.execute(task)
    assert interrupted.cancelled
    assert len(reviewed) == 1
    fresh_task = run.task("A", rows)
    pause = Event()
    pause.set()
    fresh = SourceExecutor(
        run.request,
        stop_event=Event(),
        pause_event=pause,
        shared_terms={},
        terms_lock=run.executor.terms_lock,
        progress=lambda *_: None,
        log=lambda *_: None,
        attempt_id="continued-attempt-1",
    )
    fresh.prepare_names((fresh_task,))
    resumed = fresh.execute(fresh_task)
    assert resumed.successful, resumed.error
    assert len(reviewed) == 3
    assert len({json.dumps(key, sort_keys=True) for key in reviewed}) == 3
    assert all(entry.translation.endswith("（校对）") for entry in fresh_task.collection)
