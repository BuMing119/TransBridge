"""Generated native records remain exact through persistence and task guards."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.generated_term_writer import GeneratedTermWriter
from transbridge.ai_translator.shared_term_pool import SharedTermPool
from transbridge.ai_translator.term_database import TermDatabaseManager
from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency


@pytest.fixture(params=["shared", "legacy"])
def generated_task(request, tmp_path, monkeypatch):
    def directory(stem):
        path = tmp_path / stem
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(directory))
    config = LLMConfig(term_priority=["dynamic"], enable_semantic_match=False)
    run = SimpleNamespace(run_id="generated-roundtrip", config=config, spec=SimpleNamespace(mode="mixed"))
    guard = TaskConsistency(run)
    pool = (
        SharedTermPool("project", "variant", "zh_CN", path=tmp_path / "shared.json")
        if request.param == "shared"
        else None
    )

    def load(source, stage="translation"):
        with guard.terminology_access():
            # Legacy sources may share one file; project sources share the pool.
            manager = TermDatabaseManager(config, "plugin.esp", shared_term_pool=pool)
            manager.load_all()
            guard.observe_terms(source, stage, manager)
        return manager

    def save(source, manager, terms, before_acknowledge=None):
        def acknowledged(current, accepted):
            if before_acknowledge is not None:
                before_acknowledge()
            guard.record_generated_terms(source, current, accepted)

        return GeneratedTermWriter(manager, access=guard.terminology_write_access).save(terms, observer=acknowledged)

    return SimpleNamespace(pool=pool, run=run, guard=guard, load=load, save=save)


@pytest.mark.parametrize(
    "term,translation,context",
    [
        (" Frost Immunity", "霜冻免疫", "MGEF:FULL"),
        ("Frost Immunity", "霜冻免疫\n", "MGEF:FULL"),
        ("Frost Immunity", "霜冻免疫", "\nMGEF:FULL\t"),
    ],
)
def test_generated_whitespace_survives_acknowledgement_reload_and_next_stage(
    generated_task, term, translation, context
):
    task = generated_task
    first, second = task.load("one"), task.load("two")
    assert task.save("one", first, [(term, translation, "auto_project_name", context)]) == 1
    assert task.save("two", second, [("Shield", "盾", "auto_name", "ARMO:FULL")]) == 1
    expected = [(term, translation, "auto_project_name", context), ("Shield", "盾", "auto_name", "ARMO:FULL")]
    for source in ("one", "two"):
        for stage in ("polish", "translation"):
            manager = task.load(source, stage)
            assert [
                (row.term, row.translation, row.source, row.context) for row in manager.get_dynamic_db().as_list()
            ] == expected
    if task.pool is not None:
        restored = SharedTermPool(*task.pool.identity, path=task.pool._path)
        assert restored.as_list() == task.pool.as_list()
    task.guard.require_current(task.run)
    assert not task.guard.error


@pytest.mark.parametrize("field", ["translation", "metadata"])
def test_native_roundtrip_preserves_external_edit_without_blocking_results(generated_task, field):
    task = generated_task
    manager = task.load("one")
    path = Path(manager.get_dynamic_db()._path)
    external = []

    def edit():
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["terms"] if task.pool is not None else payload
        rows[0][field] = "外部译文" if field == "translation" else {"external": True}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        external.append(path.read_bytes())

    assert task.save("one", manager, [(" Frost Immunity", "霜冻免疫", "auto_project_name", "")], edit) == 0
    assert path.read_bytes() == external[-1]
    if task.pool is not None:
        assert task.pool.snapshot().entries == ()
    task.guard.require_current(task.run)
