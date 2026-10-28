from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.ui.tools.ai_runtime_support import ai_task_runtime, frozen_ai_request
from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency


def _request(**config):
    return SimpleNamespace(run_id="run", spec=SimpleNamespace(mode="mixed"), config=SimpleNamespace(**config))


@pytest.mark.parametrize("field,new", [("model", "new"), ("max_output_tokens", 400), ("api_key", "changed")])
def test_changed_preferences_do_not_replace_or_block_frozen_execution_config(field, new):
    preferences = SimpleNamespace(model="stored", max_output_tokens=200, api_key="old")
    request = _request(model="draft", max_output_tokens=100, api_key="draft-key")
    request = frozen_ai_request(request)
    guard = TaskConsistency(request)
    guard.require_current(request)
    setattr(preferences, field, new)
    guard.require_current(request)
    assert request.config.model == "draft" and request.config.max_output_tokens == 100


def test_mutated_execution_request_is_rejected_even_when_preferences_unchanged():
    request = _request(model="initial")
    request = frozen_ai_request(request)
    guard = TaskConsistency(request)
    from transbridge.ui.tools.ai_translator.run_spec import FrozenExecutionConfig

    request = replace(
        request, config_snapshot=FrozenExecutionConfig(SimpleNamespace(model="changed", max_concurrent=1))
    )
    with pytest.raises(RuntimeError, match="任务配置已变化"):
        guard.require_current(request)


def test_live_terminology_version_is_not_a_task_validity_condition():
    ref = SimpleNamespace(snapshot_identity="version-1", captured_at="first")
    request = _request()
    request = frozen_ai_request(request)
    guard = TaskConsistency(request)
    ref.captured_at = "second"
    guard.require_current(request)
    ref.snapshot_identity = "version-2"
    guard.require_current(request)
    assert guard.error == ""


def test_published_version_changes_do_not_invalidate_the_frozen_request():
    request = _request()
    request.terminology_binding = SimpleNamespace(snapshot_ref=SimpleNamespace(snapshot_identity="old"))
    request = frozen_ai_request(request)
    guard = TaskConsistency(request)
    guard.require_current(request)
    assert request.terminology_binding.snapshot_ref.snapshot_identity == "old"


def test_term_file_changes_do_not_block_applying_existing_results(tmp_path):
    path = tmp_path / "terms.json"
    path.write_text("{}", encoding="utf-8")
    request = _request(local_json_path=str(path))
    request = frozen_ai_request(request)
    guard = TaskConsistency(request)
    path.write_text('{"term":"新值"}', encoding="utf-8")
    guard.require_current(request)


def test_loaded_remote_reference_differences_do_not_latch_a_task_error():
    request = _request()
    request = frozen_ai_request(request)
    guard = TaskConsistency(request)
    manager = SimpleNamespace(_merged_terms=[{"term": "A", "translation": "甲"}])
    guard.observe_terms("source", "translation", manager)
    guard.observe_terms("source", "translation", manager)
    manager._merged_terms[0]["translation"] = "乙"
    guard.observe_terms("source", "translation", manager)
    guard.require_current(request)
    assert guard.error == ""


def test_polish_reference_differences_are_not_execution_failures():
    guard = TaskConsistency(frozen_ai_request(_request()))
    manager = SimpleNamespace(_merged_terms=[{"term": "A", "translation": "甲"}])
    guard.observe_terms("source", "translation", manager)
    manager._merged_terms[0]["translation"] = "乙"
    guard.observe_terms("source", "polish", manager)
    assert guard.error == ""


def test_real_frozen_request_and_config_repository_handles_are_stable():
    controller = RunController(task_runtime=ai_task_runtime())
    config = LLMConfig()
    request = controller.begin("translate", config, [])
    request = frozen_ai_request(request)
    guard = TaskConsistency(request)
    guard.require_current(request)
    # A new attempt must keep the frozen parent specification, not swap profiles.
    changed = replace(request, spec=replace(request.spec, config_digest="different"))
    with pytest.raises(RuntimeError, match="任务配置已变化"):
        guard.require_current(changed)
    controller.finish(request.run_id)


def test_generated_terms_and_external_edits_do_not_block_retry_but_conflicting_writes_are_protected(
    tmp_path, monkeypatch
):
    from transbridge.ai_translator.term_database import TermDatabaseManager

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))
    config = LLMConfig(term_priority=["dynamic"], enable_semantic_match=False)
    request = _request()
    request = frozen_ai_request(request)
    guard = TaskConsistency(request)
    first = TermDatabaseManager(config, "test.esp")
    first.load_all()
    guard.observe_terms("source", "translation", first)
    generated = [("New Name", "新名字", "auto_name", "NPC_:FULL")]
    first.get_dynamic_db().add_many_and_save(generated)
    guard.record_generated_terms("source", first, generated)

    retried = TermDatabaseManager(config, "test.esp")
    retried.load_all()
    guard.observe_terms("source", "translation", retried)
    guard.observe_terms("source", "polish", retried)
    guard.require_current(request)
    retried.get_dynamic_db().add_many_and_save([("New Name", "外部修改", "manual", "NPC_:FULL")])
    changed = TermDatabaseManager(config, "test.esp")
    changed.load_all()
    guard.observe_terms("source", "translation", changed)
    guard.require_current(request)
    with pytest.raises(RuntimeError, match="跳过共享写入"):
        with guard.terminology_write_access():
            pytest.fail("must not overwrite the external file")
    guard.require_current(request)


def test_external_dynamic_file_edit_does_not_block_apply_before_another_stage_load(tmp_path, monkeypatch):
    from transbridge.ai_translator.term_database import TermDatabaseManager

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))
    request = _request()
    request = frozen_ai_request(request)
    guard = TaskConsistency(request)
    config = LLMConfig(term_priority=["dynamic"], enable_semantic_match=False)
    manager = TermDatabaseManager(config, "test.esp")
    manager.load_all()
    guard.observe_terms("source", "translation", manager)
    manager.get_dynamic_db().add_many_and_save([("External", "外部", "manual", "")])
    guard.require_current(request)
    with pytest.raises(RuntimeError, match="跳过共享写入"):
        with guard.terminology_write_access():
            pytest.fail("must not overwrite the external file")


def test_apply_check_does_not_read_or_stat_reference_files(monkeypatch):
    request = frozen_ai_request(_request())
    guard = TaskConsistency(request)
    monkeypatch.setattr(guard, "_file_version", lambda *_: pytest.fail("reference was statted"))
    guard.require_current(request)
