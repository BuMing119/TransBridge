from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from tests.application.assistant_requests.test_round_undo import _setup as _base_setup
from tests.contracts.config.test_unified_repository import _repository
from transbridge.application.assistant_requests.config_undo import (
    apply_config_undo,
    capture_config_undo,
    combine_config_undo,
    preflight_config_undo,
)
from transbridge.application.assistant_requests.config_undo_capture import capture_config_commit
from transbridge.application.assistant_requests.models import EffectStatus, RequestError, RequestStatus
from transbridge.config.llm import LLMConfig
from transbridge.config.repository import ConfigRepositoryError

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def _setup(composed):
    case = _base_setup(composed)
    _, requests, context, _, _, _, execution, _ = case
    requests.update_request(
        context,
        execution.request_id,
        lambda request: replace(
            request, status=RequestStatus.OPEN, effects=(replace(request.effects[0], status=EffectStatus.RUNNING),)
        ),
    )
    return case


def _settle(requests, context, execution):
    requests.update_request(
        context,
        execution.request_id,
        lambda request: replace(
            request,
            status=RequestStatus.CANCELLED,
            effects=(replace(request.effects[0], status=EffectStatus.SUCCEEDED),),
        ),
    )


def test_config_inverse_preserves_unselected_fields_and_increases_revision(tmp_path):
    repo = _repository(tmp_path)
    before = repo.update_sections({"llm": {"temperature": "0.1"}, "other": {"value": "keep"}})
    after = repo.update_sections({"llm": {"temperature": "0.8"}}, expected_revision=before.revision)
    receipt = capture_config_undo(before, after, {"temperature"})
    restored = apply_config_undo(repo, receipt)
    assert restored.value("llm", "temperature") == "0.1"
    assert restored.value("other", "value") == "keep"
    assert restored.revision == after.revision + 1
    assert set(receipt["before"]) == {"temperature"}


def test_config_cas_rejects_stale_write_and_aba_undo(tmp_path):
    repo = _repository(tmp_path)
    before = repo.update_sections({"llm": {"temperature": "0.1"}})
    after = repo.update_sections({"llm": {"temperature": "0.8"}})
    receipt = capture_config_undo(before, after, {"temperature"})
    with pytest.raises(ConfigRepositoryError, match="changed"):
        repo.update_sections({"llm": {"temperature": "0.3"}}, expected_revision=before.revision)
    repo.update_sections({"llm": {"temperature": "0.2"}})
    repo.update_sections({"llm": {"temperature": "0.8"}})
    with pytest.raises(ConfigRepositoryError, match="changed"):
        apply_config_undo(repo, receipt)
    assert repo.load().value("llm", "temperature") == "0.8"


def test_config_chain_restores_absent_keys_and_rejects_secret_fields(tmp_path):
    repo = _repository(tmp_path)
    before = repo.load()
    after = repo.update_sections({"llm": {"temperature": "0.8"}})
    last = repo.update_sections({"llm": {"temperature": "0.9", "target_lang": "zh_CN"}})
    combined = combine_config_undo([
        capture_config_undo(before, after, {"temperature"}),
        capture_config_undo(after, last, {"temperature", "target_lang"}),
    ])
    result = apply_config_undo(repo, combined)
    assert result.value("llm", "temperature") is None
    assert result.value("llm", "target_lang") is None
    with pytest.raises(ConfigRepositoryError):
        capture_config_undo(before, after, {"api_key"})


def test_config_round_durable_receipt_and_explicit_undo(composed, tmp_path):
    _, requests, context, _, _, _, execution, undo = _setup(composed)
    undo.config_repository = _repository(tmp_path)
    before = undo.configuration().update_sections({"llm": {"temperature": "0.1"}})

    def rename(state):
        state["undo_rounds"][execution.work_round_id]["effects"]["effect"]["tool"] = "set_translation_config"

    requests.transact(context, rename)
    capture_config_commit(undo, context, execution, "effect", {"temperature": "0.7"})
    _settle(requests, context, execution)
    assert undo.preview(context, execution.work_round_id)["available"]
    result = undo.undo(context, execution.work_round_id)
    assert not result["partial"]
    assert undo.configuration().load().value("llm", "temperature") == "0.1"
    assert undo.configuration().load().revision == before.revision + 2


def test_config_persistence_failure_leaves_pending_without_replay(composed, tmp_path, monkeypatch):
    _, requests, context, _, _, _, execution, undo = _setup(composed)
    undo.config_repository = _repository(tmp_path)
    original = undo._finish

    def fail(*args, **kwargs):
        raise OSError("receipt save failed")

    monkeypatch.setattr(undo, "_finish", fail)
    with pytest.raises(OSError, match="receipt save"):
        capture_config_commit(undo, context, execution, "effect", {"temperature": "0.7"})
    assert undo.configuration().load().value("llm", "temperature") == "0.7"
    assert not undo.preview(context, execution.work_round_id)["available"]
    monkeypatch.setattr(undo, "_finish", original)


def test_endpoint_undo_restores_absent_members_without_materializing_defaults(tmp_path):
    repository = _repository(tmp_path)
    repository.path.write_text("[meta]\nschema_version=2\nrevision=1\n[llm]\nprovider=openai\n", encoding="utf-8")
    before = repository.load()
    after = repository.update_sections({
        "llm": {"provider": "custom", "base_url": "https://example.test/v1", "model": "new"}
    })
    restored = apply_config_undo(repository, capture_config_undo(before, after, {"provider", "base_url", "model"}))
    assert dict(restored.section("llm").values) == {"provider": "openai"}


def test_endpoint_legacy_empty_inverse_is_rejected_before_any_write(tmp_path):
    repository = _repository(tmp_path)
    repository.path.write_text(
        "[meta]\nschema_version=2\nrevision=1\n[llm]\nprovider=openai\nbase_url=\nmodel=\n",
        encoding="utf-8",
    )
    before = repository.load()
    after = repository.update_sections({
        "llm": {"provider": "custom", "base_url": "https://example.test/v1", "model": "new"}
    })
    receipt = capture_config_undo(before, after, {"provider", "base_url", "model"})
    with pytest.raises(ConfigRepositoryError, match="previous endpoint") as error:
        preflight_config_undo(repository, receipt)
    assert error.value.code == "config_undo_endpoint_unsupported"
    with pytest.raises(ConfigRepositoryError, match="previous endpoint"):
        apply_config_undo(repository, receipt)
    assert repository.load() == after


def _tool_context(composed, tmp_path, monkeypatch):
    case = _setup(composed)
    _, requests, context, _, _, _, execution, undo = case
    repository = _repository(tmp_path)
    undo.config_repository = repository
    gate = SimpleNamespace(
        context=context,
        service=SimpleNamespace(undo=undo),
        current_request=lambda: next(
            r for r in requests.requests(requests.state(context)) if r.request_id == execution.request_id
        ),
    )
    tool_context = SimpleNamespace(assistant_gate=gate, assistant_effect_id="effect")
    monkeypatch.setattr(
        "transbridge.smart_assistant.tools._common.load_llm_config",
        lambda: LLMConfig.load_from_file(repository=repository, environment={}),
    )
    return case, repository, tool_context


def test_translation_tool_sparse_save_does_not_materialize_workflow_overlay_or_credentials(
    composed, tmp_path, monkeypatch
):
    from transbridge.smart_assistant.tools.tool_translator import _tool_set_translation_config

    case, repository, tool_context = _tool_context(composed, tmp_path, monkeypatch)
    before = repository.update_sections({
        "llm": {
            "pp_strategy": "strict",
            "workflow_profiles": json.dumps({"translate": {"pp_strategy": "proofread"}}),
            "temperature": "0.1",
        },
        "other": {"value": "keep"},
    })
    loaded = LLMConfig.load_from_file(repository=repository, environment={})
    assert loaded.pp_strategy != before.value("llm", "pp_strategy")
    secrets_before = dict(repository.credential_store.values)
    monkeypatch.setattr(repository.credential_store, "set", lambda *args: pytest.fail("unexpected credential write"))
    result = _tool_set_translation_config({"temperature": 0.8, "max_tokens": 1000}, tool_context)
    assert result.success, result.message
    after = repository.load()
    expected = dict(before.section("llm").values)
    expected.update(temperature="0.8", max_output_tokens="1000")
    assert dict(after.section("llm").values) == expected
    assert after.value("other", "value") == "keep"
    assert repository.credential_store.values == secrets_before
    _, requests, context, _, _, _, execution, undo = case
    commit = requests.state(context)["undo_rounds"][execution.work_round_id]["commits"][0]
    receipt = undo._read(context, commit["receipt"])
    assert set(receipt["before"]) == {"temperature", "max_output_tokens"}
    assert "credential" not in json.dumps(receipt) and "api_key" not in json.dumps(receipt)


def test_term_tool_maps_only_explicit_arguments_to_sparse_persisted_fields(composed, tmp_path, monkeypatch):
    from transbridge.smart_assistant.tools.tool_translator import _tool_set_term_config

    _, repository, tool_context = _tool_context(composed, tmp_path, monkeypatch)
    repository.update_sections({"llm": {"local_csv_path": "unchanged.csv", "temperature": "0.4"}})
    result = _tool_set_term_config({"term_sources": ["json", "csv"], "json_path": "terms.json"}, tool_context)
    assert result.success, result.message
    after = repository.load()
    assert dict(after.section("llm").values) == {
        "local_csv_path": "unchanged.csv",
        "temperature": "0.4",
        "term_priority": "json,csv",
        "local_json_path": "terms.json",
    }


def test_cancelled_effect_cannot_save_configuration_or_add_intent(composed, tmp_path):
    _, requests, context, _, _, _, execution, undo = _setup(composed)
    undo.config_repository = _repository(tmp_path)
    before = undo.configuration().load()
    _settle(requests, context, execution)
    state_before = requests.state(context)
    with pytest.raises(RequestError):
        capture_config_commit(undo, context, execution, "effect", {"temperature": "0.7"})
    assert undo.configuration().load() == before
    assert requests.state(context) == state_before
