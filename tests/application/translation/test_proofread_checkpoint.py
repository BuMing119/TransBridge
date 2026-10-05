from __future__ import annotations

from dataclasses import replace
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.application.translation.proofread_checkpoint import ProofreadCheckpoint, proofread_settings
from transbridge.application.translation.proofread_stage import ProofreadStage


def candidate(key):
    return PostProcessCandidate(
        "run",
        EntryKey(SourceNamespace("plugin"), str(key)),
        EntryRevision(1),
        "Hello",
        "你好",
        "你好",
        1,
    )


def store(root, **overrides):
    return ProofreadCheckpoint(
        root,
        **{
            "project": "project",
            "variant": "variant",
            "source": "source",
            "settings": {"model": "test"},
            **overrides,
        },
    )


class Client:
    def __init__(self, text="您好"):
        self.calls = []
        self.text = text

    def chat(self, messages, _max_tokens):
        entries = json.loads(messages[1]["content"])["entries"]
        self.calls.append(entries)
        return json.dumps({
            "results": [{"entry_key": item["entry_key"], "final_translation": self.text} for item in entries]
        })


def test_reopen_after_cancel_skips_completed_entries_even_with_different_batches(tmp_path):
    inputs = tuple(candidate(i) for i in range(4))
    first_client = Client()
    first = ProofreadStage(first_client, checkpoint=store(tmp_path), max_items=1)

    def stop_after_first(completed, total, message):
        if completed == 1:
            first.cancel()

    first.run(inputs, progress_callback=stop_after_first)
    assert len(first_client.calls) == 1
    second_client = Client()
    events = []
    second = ProofreadStage(second_client, checkpoint=store(tmp_path))
    result = second.run(inputs, event_callback=events.append)
    assert [e["entry_key"]["local_key"] for e in second_client.calls[0]] == ["1", "2", "3"]
    assert all(item.text == "您好" and item.accepted for item in result.candidates)
    assert any("已恢复 1 条" in event for event in events)


@pytest.mark.parametrize(
    "field,value",
    [
        ("original", "New source"),
        ("before_text", "新译文"),
        ("context", "changed"),
        ("before_revision", EntryRevision(2)),
        ("stage", 2),
    ],
)
def test_changed_inputs_are_not_reused(tmp_path, field, value):
    item = candidate(1)
    ProofreadStage(Client(), checkpoint=store(tmp_path)).run((item,))
    client = Client()
    ProofreadStage(client, checkpoint=store(tmp_path)).run((replace(item, **{field: value}),))
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "scope",
    [
        {"project": "other"},
        {"variant": "other"},
        {"source": "other"},
        {"settings": {"model": "other"}},
    ],
)
def test_scope_isolation(tmp_path, scope):
    ProofreadStage(Client(), checkpoint=store(tmp_path)).run((candidate(1),))
    client = Client()
    ProofreadStage(client, checkpoint=store(tmp_path, **scope)).run((candidate(1),))
    assert len(client.calls) == 1


def test_changed_terms_invalidate_cached_candidate(tmp_path):
    item = candidate(1)
    ProofreadStage(Client(), checkpoint=store(tmp_path), term_resolver=lambda _: {}).run((item,))
    client = Client()
    ProofreadStage(client, checkpoint=store(tmp_path), term_resolver=lambda _: {"Hello": "您好"}).run((item,))
    assert len(client.calls) == 1


def test_cached_first_pass_still_runs_terminology_closure_and_saves_repair(tmp_path):
    item = candidate(1)
    checkpoint = store(tmp_path)
    terms = {"Hello": "标准"}
    session = checkpoint.session((item,))
    session.observe(item, terms)
    session.save((item.with_text("您好", "proofread"),))

    class Refiner:
        calls = 0

        def refine_batch(self, entries, issues, **kwargs):
            self.calls += 1
            from transbridge.ai_translator.post_processor.llm_refiner import RefineResult

            return {entry.id: RefineResult(entry.id, entry.translation, "标准", []) for entry in entries}

    refiner = Refiner()
    client = Client()
    result = ProofreadStage(
        client,
        checkpoint=checkpoint,
        term_resolver=lambda _: terms,
        refiner=refiner,
    ).run((item,))
    assert not client.calls
    assert refiner.calls == 1
    assert result.candidates[0].text == "标准"
    result = ProofreadStage(
        client,
        checkpoint=store(tmp_path),
        term_resolver=lambda _: terms,
        refiner=refiner,
    ).run((item,))
    assert refiner.calls == 1
    assert result.candidates[0].accepted


def test_invalid_response_is_never_saved(tmp_path):
    item = replace(candidate(1), original="Hello {name}")
    ProofreadStage(Client(), checkpoint=store(tmp_path)).run((item,))
    assert store(tmp_path).read() == {}


def test_concurrent_batches_are_durable_without_replaying_calls(tmp_path):
    items = tuple(candidate(i) for i in range(20))
    ProofreadStage(Client(), checkpoint=store(tmp_path), max_items=1).run(items, max_workers=3)
    client = Client()
    result = ProofreadStage(client, checkpoint=store(tmp_path)).run(items, max_workers=3)
    assert not client.calls
    assert [c.entry_key for c in result.candidates] == [c.entry_key for c in items]


def test_write_failure_does_not_report_saved_progress(tmp_path, monkeypatch):
    checkpoint = store(tmp_path)

    def fail(rows):
        raise RuntimeError("无法保存校对断点")

    monkeypatch.setattr(checkpoint, "write", fail)
    progress = []
    with pytest.raises(RuntimeError, match="无法保存校对断点"):
        ProofreadStage(Client(), checkpoint=checkpoint).run(
            (candidate(1),), progress_callback=lambda *a: progress.append(a)
        )
    assert not progress


def test_corrupt_checkpoint_fails_explicitly(tmp_path):
    checkpoint = store(tmp_path)
    checkpoint.path.write_bytes(b"broken database")
    with pytest.raises(RuntimeError, match="无法读取校对断点"):
        checkpoint.read()


def test_settings_exclude_secrets_and_operational_limits():
    settings = proofread_settings(SimpleNamespace(model="model", api_key="secret", max_concurrent=7))
    assert settings["model"] == "model"
    assert "api_key" not in settings and "max_concurrent" not in settings


def test_process_exit_after_completed_batch_keeps_committed_results(tmp_path):
    code = """
import json, os, sys
from pathlib import Path
from transbridge.application.translation.proofread_checkpoint import ProofreadCheckpoint
from transbridge.application.translation.proofread_stage import ProofreadStage
from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace
item = PostProcessCandidate("run", EntryKey(SourceNamespace("plugin"), "1"), EntryRevision(1),
                            "Hello", "你好", "你好", 1)
class Client:
    def chat(self, messages, tokens):
        return json.dumps({"results": [{"entry_key": item.entry_key.to_dict(), "final_translation": "您好"}]})
checkpoint = ProofreadCheckpoint(Path(sys.argv[1]), project="project", variant="variant",
                                source="source", settings={"model": "test"})
ProofreadStage(Client(), checkpoint=checkpoint).run((item,), progress_callback=lambda *args: os._exit(23))
"""
    completed = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, timeout=30)
    assert completed.returncode == 23, completed.stderr.decode(errors="replace")
    client = Client()
    result = ProofreadStage(client, checkpoint=store(tmp_path)).run((candidate(1),))
    assert not client.calls
    assert result.candidates[0].text == "您好"


def test_force_rerun_can_replace_saved_candidate(tmp_path):
    item = candidate(1)
    ProofreadStage(Client(), checkpoint=store(tmp_path)).run((item,))
    client = Client("新的校对")
    ProofreadStage(client, checkpoint=store(tmp_path, reuse=False)).run((item,))
    assert len(client.calls) == 1
    result = ProofreadStage(Client(), checkpoint=store(tmp_path)).run((item,))
    assert result.candidates[0].text == "新的校对"


def test_explicit_task_isolated_from_legacy_and_other_tasks(tmp_path):
    item = candidate(1)
    legacy = store(tmp_path)
    ProofreadStage(Client(), checkpoint=legacy).run((item,))
    first = store(tmp_path, task_id="first-task")
    assert first.saved_count() == 0
    client = Client("第一任务")
    ProofreadStage(client, checkpoint=first).run((item,))
    assert len(client.calls) == 1 and first.saved_count() == 1
    continued = Client()
    result = ProofreadStage(continued, checkpoint=store(tmp_path, task_id="first-task")).run((item,))
    assert not continued.calls and result.candidates[0].text == "第一任务"
    new_client = Client("新任务")
    second = store(tmp_path, task_id="second-task")
    ProofreadStage(new_client, checkpoint=second).run((item,))
    assert len(new_client.calls) == 1
    assert first.path != second.path != legacy.path
    assert legacy.saved_count() == first.saved_count() == second.saved_count() == 1


def test_saved_count_is_historical_and_excludes_invalid_payload_shapes(tmp_path):
    checkpoint = store(tmp_path, task_id="task")
    ProofreadStage(Client(), checkpoint=checkpoint).run((candidate(1),))
    checkpoint.write([("bad-text", {"text": "", "terms": "a" * 64}), ("bad-terms", {"text": "x"})])
    assert checkpoint.saved_count() == 1
    assert checkpoint.session((replace(candidate(1), original="changed"),)).restore(lambda _: {}) == {}


@pytest.mark.parametrize("task_id", ["", " ", 7])
def test_task_id_must_be_nonempty_string(tmp_path, task_id):
    with pytest.raises(ValueError, match="task_id"):
        store(tmp_path, task_id=task_id)
