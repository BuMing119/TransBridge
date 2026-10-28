from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.ui.tools.ai_runtime_support import frozen_ai_request
from transbridge.ai_translator.generated_term_writer import GeneratedTermWriter
from transbridge.ai_translator.project_terminology_adapter import (
    EnabledEffectiveTerminologyGate,
    ProjectTerminologyAdapter,
)
from transbridge.ai_translator.shared_term_pool import SharedTermPool
from transbridge.ai_translator.term_database import DynamicTermDatabase, TermDatabaseManager
from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
    SnapshotEffectiveTerminologyPort,
    TerminologyLookupContext,
)
from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency


@pytest.fixture
def shared_task(tmp_path, monkeypatch):
    def directory(stem):
        path = tmp_path / stem
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(directory))
    config = LLMConfig(term_priority=["dynamic"], enable_semantic_match=False)
    request = SimpleNamespace(run_id="shared-terms", config=config, spec=SimpleNamespace(mode="mixed"))
    request = frozen_ai_request(request)
    guard = TaskConsistency(request)
    pool = SharedTermPool("project", "variant", "zh_CN", path=tmp_path / "pool.json")

    def load(source, *, stage="translation", adapter=None):
        with guard.terminology_access():
            manager = TermDatabaseManager(
                config,
                f"{source}.esp",
                shared_term_pool=pool,
                generated_term_plugin_id=f"{source}.esp",
                effective_loader=adapter,
                terminology_context=TerminologyLookupContext("project", "variant"),
            )
            manager.load_all()
            guard.observe_terms(source, stage, manager)
            return manager

    def save(source, manager, terms, *, scope_plugin_id=None, before_acknowledge=None):
        def acknowledge(current, accepted):
            if before_acknowledge is not None:
                before_acknowledge()
            guard.record_generated_terms(source, current, accepted)

        return GeneratedTermWriter(manager, access=guard.terminology_write_access).save(
            terms, scope_plugin_id=scope_plugin_id, observer=acknowledge
        )

    return SimpleNamespace(pool=pool, config=config, request=request, guard=guard, load=load, save=save)


def test_other_plugin_terms_survive_polish_retry_and_final_guard(shared_task):
    one, two = shared_task.load("one"), shared_task.load("two")
    shared_task.save("one", one, [("Sword", "剑", "auto_name", "")])
    shared_task.save("two", two, [("Shield", "盾", "auto_name", "")])

    for source in ("one", "two"):
        for stage in ("polish", "translation"):
            manager = shared_task.load(source, stage=stage)
            assert manager.exact_match(["Sword", "Shield"]) == {"Sword": "剑", "Shield": "盾"}
    shared_task.guard.require_current(shared_task.request)


def test_same_word_in_distinct_scopes_is_acknowledged_separately(shared_task):
    manager = shared_task.load("one")
    shared_task.save("one", manager, [("Sword", "剑", "auto_name", "")])
    shared_task.save("one", manager, [("Sword", "刀", "auto_name", "")], scope_plugin_id="one.esp")
    assert shared_task.pool.snapshot().records_by_term["Sword"].translation == "剑"
    assert shared_task.pool.snapshot("one.esp").records_by_term["Sword"].translation == "刀"
    assert shared_task.load("one", stage="polish").resolve_term("Sword").translation == "剑"
    shared_task.guard.require_current(shared_task.request)


def test_loaded_view_edit_cannot_replace_the_shared_pool_scope(shared_task):
    manager = shared_task.load("one")
    shared_task.save("one", manager, [("Sword", "剑", "auto_name", "")])
    manager = shared_task.load("one", stage="polish")
    shared_task.save("one", manager, [("Sword", "刀", "auto_name", "")], scope_plugin_id="one.esp")
    manager._merged_terms = list(shared_task.pool.snapshot().entries)
    manager._merged_terms[0].translation = "刀"
    shared_task.guard.observe_terms("one", "polish", manager)
    assert shared_task.pool.snapshot().records_by_term["Sword"].translation == "剑"
    shared_task.guard.require_current(shared_task.request)


def test_conflict_only_persistence_is_owned_even_without_accepted_rows(shared_task):
    one, two = shared_task.load("one"), shared_task.load("two")
    shared_task.save("one", one, [("Sword", "剑", "auto_name", "")])
    shared_task.save("two", two, [("Sword", "刀", "auto_name", "")])
    assert shared_task.pool.revision == 2
    assert len(shared_task.pool.conflicts_since(0)) == 1
    assert shared_task.load("two", stage="polish").resolve_term("Sword").translation == "剑"
    shared_task.guard.require_current(shared_task.request)


def test_generated_revision_and_vector_scope_changes_are_not_published_version_changes(shared_task):
    manager = shared_task.load("one")
    shared_task.save("one", manager, [("Sword", "剑", "auto_name", "")])
    manager._vector_snapshot_identity = "plugin:one.esp:shared:revision:1"
    shared_task.guard.observe_terms("one", "polish", manager)
    shared_task.guard.require_current(shared_task.request)


def test_published_snapshot_change_is_not_a_result_validity_condition(shared_task):
    snapshot = EffectiveTerminologySnapshot("project", "variant", EffectiveSnapshotStatus.READY, "v1", "digest")
    snapshots = SimpleNamespace(snapshot=lambda *_args: snapshot)
    adapter = ProjectTerminologyAdapter(SnapshotEffectiveTerminologyPort(snapshots), EnabledEffectiveTerminologyGate())
    shared_task.load("one", adapter=adapter)
    snapshot = replace(snapshot, version_id="v2", content_digest="changed")
    shared_task.load("one", stage="polish", adapter=adapter)
    shared_task.guard.require_current(shared_task.request)


@pytest.mark.parametrize("check", ["require", "write", "polish"])
def test_external_shared_file_edit_blocks_publication_only_and_preserves_the_file(shared_task, check):
    manager = shared_task.load("one")
    shared_task.save("one", manager, [("Sword", "剑", "auto_name", "")])
    external = SharedTermPool("project", "variant", "zh_CN", path=shared_task.pool._path)
    external.publish([("Shield", "盾", "auto_name", "")])
    original_bytes = Path(shared_task.pool._path).read_bytes()
    if check == "require":
        shared_task.guard.require_current(shared_task.request)
    elif check == "write":
        assert shared_task.save("one", manager, [("Bow", "弓", "auto_name", "")]) == 0
    else:
        shared_task.load("one", stage="polish")
    assert Path(shared_task.pool._path).read_bytes() == original_bytes
    shared_task.guard.require_current(shared_task.request)


def test_external_envelope_edit_during_acknowledgement_is_not_accepted_or_rolled_back(shared_task):
    manager = shared_task.load("one")
    path = Path(shared_task.pool._path)
    external_bytes = []

    def external_edit():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["conflicts"] = [{"reason": "external change without changing any term rows"}]
        path.write_text(json.dumps(payload), encoding="utf-8")
        external_bytes.append(path.read_bytes())

    assert shared_task.save("one", manager, [("Sword", "剑", "auto_name", "")], before_acknowledge=external_edit) == 0
    assert path.read_bytes() == external_bytes[0]
    assert shared_task.pool.revision == 0
    assert shared_task.pool.snapshot().entries == ()


def test_historical_plugin_dynamic_reference_change_does_not_block_shared_mode(shared_task):
    shared_task.load("one")
    local = DynamicTermDatabase("one.esp")
    local.add_many_and_save([("Sword", "外部剑", "manual", "")])
    shared_task.guard.require_current(shared_task.request)


def test_restored_pool_terms_form_baseline_and_new_writes_can_extend_it(shared_task):
    shared_task.pool.publish([("Sword", "剑", "auto_name", "")])
    manager = shared_task.load("one")
    shared_task.save("one", manager, [("Shield", "盾", "auto_name", "")])
    assert shared_task.load("one", stage="polish").exact_match(["Sword", "Shield"]) == {"Sword": "剑", "Shield": "盾"}
    shared_task.guard.require_current(shared_task.request)
