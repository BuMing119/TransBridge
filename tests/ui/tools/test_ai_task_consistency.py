from dataclasses import replace
from types import SimpleNamespace

import pytest

from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency


def _request(**config):
    return SimpleNamespace(run_id="run", spec=SimpleNamespace(mode="mixed"), config=SimpleNamespace(**config))


@pytest.mark.parametrize("field,new", [("model", "new"), ("max_output_tokens", 400), ("api_key", "changed")])
def test_changed_preferences_block_without_replacing_draft_execution_config(field, new):
    preferences = SimpleNamespace(model="stored", max_output_tokens=200, api_key="old")
    request = _request(model="draft", max_output_tokens=100, api_key="draft-key")
    guard = TaskConsistency(request, config_provider=lambda: preferences)
    guard.require_current(request)
    setattr(preferences, field, new)
    with pytest.raises(RuntimeError, match="配置已变化"):
        guard.require_current(request)
    assert request.config.model == "draft" and request.config.max_output_tokens == 100


def test_mutated_execution_request_is_rejected_even_when_preferences_unchanged():
    request = _request(model="initial")
    guard = TaskConsistency(request)
    request.config.model = "changed"
    with pytest.raises(RuntimeError, match="任务配置已变化"):
        guard.require_current(request)


def test_terminology_identity_ignores_capture_time_but_rejects_changed_version():
    ref = SimpleNamespace(snapshot_identity="version-1", captured_at="first")
    request = _request()
    guard = TaskConsistency(request, terminology_provider=lambda: ref)
    ref.captured_at = "second"
    guard.require_current(request)
    ref.snapshot_identity = "version-2"
    with pytest.raises(RuntimeError, match="术语版本已变化"):
        guard.require_current(request)


def test_initial_term_version_race_does_not_validate_new_version_against_old_request():
    request = _request()
    request.terminology_binding = SimpleNamespace(snapshot_ref=SimpleNamespace(snapshot_identity="old"))
    guard = TaskConsistency(request, terminology_provider=lambda: SimpleNamespace(snapshot_identity="new"))
    with pytest.raises(RuntimeError, match="术语版本已变化"):
        guard.require_current(request)


def test_term_file_changes_require_new_task(tmp_path):
    path = tmp_path / "terms.json"
    path.write_text("{}", encoding="utf-8")
    request = _request(local_json_path=str(path))
    guard = TaskConsistency(request)
    path.write_text('{"term":"新值"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="术语文件已变化"):
        guard.require_current(request)


def test_loaded_remote_terms_are_checked_before_retry_calls_and_latch_task_error():
    request = _request()
    guard = TaskConsistency(request)
    manager = SimpleNamespace(_merged_terms=[{"term": "A", "translation": "甲"}])
    guard.observe_terms("source", "translation", manager)
    guard.observe_terms("source", "translation", manager)
    manager._merged_terms[0]["translation"] = "乙"
    with pytest.raises(RuntimeError, match="术语内容已变化"):
        guard.observe_terms("source", "translation", manager)
    with pytest.raises(RuntimeError, match="术语内容已变化"):
        guard.require_current(request)


def test_first_polish_stage_on_retry_cannot_adopt_changed_translation_terms():
    guard = TaskConsistency(_request())
    manager = SimpleNamespace(_merged_terms=[{"term": "A", "translation": "甲"}])
    guard.observe_terms("source", "translation", manager)
    manager._merged_terms[0]["translation"] = "乙"
    with pytest.raises(RuntimeError, match="术语内容已变化"):
        guard.observe_terms("source", "polish", manager)


def test_real_frozen_request_and_config_repository_handles_are_stable():
    controller = RunController()
    config = LLMConfig()
    request = controller.begin("translate", config, [])
    guard = TaskConsistency(request, config_provider=lambda: config)
    guard.require_current(request)
    # A new attempt must keep the frozen parent specification, not swap profiles.
    changed = replace(request, spec=replace(request.spec, config_digest="different"))
    with pytest.raises(RuntimeError, match="任务配置已变化"):
        guard.require_current(changed)
    controller.finish(request.run_id)


def test_own_generated_terms_allow_retry_but_external_dynamic_edits_do_not(tmp_path, monkeypatch):
    from transbridge.ai_translator.term_database import TermDatabaseManager

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))
    config = LLMConfig(term_priority=["dynamic"], enable_semantic_match=False)
    request = _request()
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
    with pytest.raises(RuntimeError, match="术语.*已变化"):
        guard.observe_terms("source", "translation", changed)


def test_external_dynamic_file_edit_blocks_apply_before_another_stage_load(tmp_path, monkeypatch):
    from transbridge.ai_translator.term_database import TermDatabaseManager

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))
    request = _request()
    guard = TaskConsistency(request)
    config = LLMConfig(term_priority=["dynamic"], enable_semantic_match=False)
    manager = TermDatabaseManager(config, "test.esp")
    manager.load_all()
    guard.observe_terms("source", "translation", manager)
    manager.get_dynamic_db().add_many_and_save([("External", "外部", "manual", "")])
    with pytest.raises(RuntimeError, match="动态术语已变化"):
        guard.require_current(request)
