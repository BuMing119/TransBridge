from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import json
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.application.translation.proofread_advisory import collect_review_findings
from transbridge.application.translation.proofread_checkpoint import (
    ProofreadCheckpoint,
    _digest,
    _input,
    proofread_settings,
)
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
            "results": [
                {
                    "entry_key": item["entry_key"],
                    "final_translation": self.text,
                    "review_status": "revised",
                    "review_reason": "结合上下文修订措辞",
                }
                for item in entries
            ]
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
        ("stage", 2),
        ("text", "当前候选已改变"),
    ],
)
def test_changed_inputs_are_not_reused(tmp_path, field, value):
    item = candidate(1)
    ProofreadStage(Client(), checkpoint=store(tmp_path)).run((item,))
    client = Client()
    ProofreadStage(client, checkpoint=store(tmp_path)).run((replace(item, **{field: value}),))
    assert len(client.calls) == 1


def test_reconstructed_revision_reuses_content_but_keeps_current_commit_guard(tmp_path):
    item = candidate(1)
    ProofreadStage(Client(), checkpoint=store(tmp_path)).run((item,))
    current = replace(item, run_id="new-execution", before_revision=EntryRevision(23))
    client = Client()
    result = ProofreadStage(client, checkpoint=store(tmp_path)).run((current,))
    assert not client.calls
    assert result.candidates[0].run_id == "new-execution"
    assert result.candidates[0].before_revision == EntryRevision(23)
    assert result.candidates[0].text == "您好"


def test_legacy_revision_input_hash_remains_readable(tmp_path):
    item = collect_review_findings((candidate(1),))[0]
    checkpoint = store(tmp_path)
    ProofreadStage(Client(), checkpoint=checkpoint).run((item,))
    payload = checkpoint.read()[_input(item)]
    with closing(sqlite3.connect(checkpoint.path)) as db, db:
        db.execute("DELETE FROM candidates")
    checkpoint.write([(_input(item, legacy_revision=True), payload)])
    client = Client()
    result = ProofreadStage(client, checkpoint=store(tmp_path)).run((item,))
    assert not client.calls and result.candidates[0].text == "您好"


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


def test_legacy_first_pass_without_review_evidence_is_rechecked_but_not_forced_to_use_terms(tmp_path):
    item = candidate(1)
    checkpoint = store(tmp_path)
    terms = {"Hello": "标准"}
    prepared = collect_review_findings((item,))[0]
    checkpoint.write([(_input(prepared), {"text": "旧断点候选", "terms": _digest(terms)})])
    assert checkpoint.saved_count() == 0

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
    assert len(client.calls) == 1
    assert refiner.calls == 0
    assert result.candidates[0].text == "您好"
    result = ProofreadStage(
        client,
        checkpoint=store(tmp_path),
        term_resolver=lambda _: terms,
        refiner=refiner,
    ).run((item,))
    assert len(client.calls) == 1
    assert refiner.calls == 0
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


def test_corrupt_checkpoint_recomputes_and_preserves_original(tmp_path, caplog):
    checkpoint = store(tmp_path)
    checkpoint.path.write_bytes(b"broken database")
    assert checkpoint.read() == {}
    assert checkpoint.saved_count() == 0
    assert checkpoint.path.read_bytes() == b"broken database"
    client = Client()
    result = ProofreadStage(client, checkpoint=checkpoint).run((candidate(1),))
    assert len(client.calls) == 1 and result.candidates[0].text == "您好"
    assert checkpoint.saved_count() == 1
    backups = tuple(tmp_path.glob("corrupt-*.sqlite3"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"broken database"
    assert str(backups[0]) in caplog.text


@pytest.mark.parametrize(
    "schema",
    [
        "CREATE TABLE candidates (input TEXT PRIMARY KEY)",
        "CREATE TABLE candidates (payload TEXT NOT NULL)",
        "CREATE TABLE candidates (input TEXT, payload TEXT NOT NULL)",
        "CREATE TABLE candidates (input TEXT PRIMARY KEY, payload TEXT NOT NULL, future_metadata TEXT)",
        "CREATE TABLE future_candidates (input TEXT PRIMARY KEY, payload TEXT NOT NULL)",
    ],
)
def test_incompatible_checkpoint_schema_recomputes_without_modifying_original(tmp_path, schema):
    checkpoint = store(tmp_path)
    with closing(sqlite3.connect(checkpoint.path)) as db, db:
        db.execute(schema)
    original = checkpoint.path.read_bytes()

    assert checkpoint.read() == {}
    assert checkpoint.saved_count() == 0
    assert checkpoint.path.read_bytes() == original
    client = Client()
    result = ProofreadStage(client, checkpoint=checkpoint).run((candidate(1),))

    assert len(client.calls) == 1 and result.candidates[0].accepted
    assert checkpoint.saved_count() == 1
    backups = tuple(tmp_path.glob("corrupt-*.sqlite3"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    resumed_client = Client()
    ProofreadStage(resumed_client, checkpoint=store(tmp_path)).run((candidate(1),))
    assert not resumed_client.calls


def test_locked_checkpoint_is_not_misclassified_as_incompatible_schema(tmp_path, monkeypatch):
    checkpoint = store(tmp_path)
    ProofreadStage(Client(), checkpoint=checkpoint).run((candidate(1),))
    original = checkpoint.path.read_bytes()
    connect = sqlite3.connect

    def short_timeout(*args, **kwargs):
        return connect(*args, **{**kwargs, "timeout": 0.01})

    with closing(connect(checkpoint.path)) as blocker, blocker:
        blocker.execute("BEGIN EXCLUSIVE")
        monkeypatch.setattr(sqlite3, "connect", short_timeout)
        with pytest.raises(RuntimeError, match="database is locked"):
            checkpoint.write([("new", {"text": "new", "terms": "a" * 64})])
        assert not tuple(tmp_path.glob("corrupt-*.sqlite3"))
    assert checkpoint.path.read_bytes() == original
    assert checkpoint.saved_count() == 1


@pytest.mark.parametrize("payload", ["{", "[]", '{"text": "", "terms": "invalid"}'])
def test_one_invalid_candidate_keeps_other_candidates_reusable(tmp_path, payload):
    checkpoint = store(tmp_path)
    inputs = (candidate(1), candidate(2))
    ProofreadStage(Client(), checkpoint=checkpoint).run(inputs)
    rows = checkpoint.read()
    with sqlite3.connect(checkpoint.path) as db:
        last_key = list(rows)[-1]
        db.execute("UPDATE candidates SET payload = ? WHERE input = ?", (payload, last_key))
    assert checkpoint.saved_count() == 1
    client = Client()
    result = ProofreadStage(client, checkpoint=store(tmp_path)).run(inputs)
    assert len(client.calls) == 1 and len(client.calls[0]) == 1
    assert all(item.accepted for item in result.candidates)
    assert store(tmp_path).saved_count() == 2
    backups = tuple(tmp_path.glob("corrupt-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT payload FROM candidates WHERE input = ?", (last_key,)).fetchone()[0] == payload


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
        return json.dumps({"results": [{"entry_key": item.entry_key.to_dict(), "final_translation": "您好",
                                       "review_status": "revised", "review_reason": "措辞修订"}]})
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
