from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.ui.tools.test_ai_task_session import _Context
from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator.task_recovery_binding import TaskRecoveryBinding, recovery_sources, signature
from transbridge.ui.tools.ai_translator.task_scope import SourceTask


def setup_binding(tmp_path):
    ctx = _Context()
    tasks = tuple(
        SourceTask(key, slot.label, None, slot.collection, (), tuple(slot.collection))
        for key, slot in ctx.slots.items()
    )
    request = SimpleNamespace(
        run_id="run",
        recovery_task_id=None,
        config=LLMConfig(model="test"),
        spec=SimpleNamespace(mode="polish", execution_profile=SimpleNamespace(enable_proofread=True)),
    )
    session = SimpleNamespace(
        project_dir=tmp_path,
        version_identity=ctx.active_version_identity,
        tasks=tasks,
        completed=False,
        project_saved=False,
    )
    binding = TaskRecoveryBinding(request, session)
    window = SimpleNamespace(
        _ctx=ctx, _view_port=SimpleNamespace(mode="polish"), _recovery_selection=(binding.record, False)
    )
    return binding, request, session, window


@pytest.mark.parametrize(
    "field,value,changed",
    [
        ("max_output_tokens", 200, True),
        ("max_tokens_per_batch", 4000, True),
        ("model", "other", True),
        ("target_lang", "en", True),
        ("api_key", "credential", False),
        ("max_concurrent", 10, False),
        ("llm_max_retries", 8, False),
        ("assistant_context_window", 8000, False),
        ("mcp_auth_token", "token", False),
    ],
)
def test_signature(field, value, changed):
    original = LLMConfig(model="test", max_output_tokens=100)
    updated = deepcopy(original)
    setattr(updated, field, value)
    assert (signature(original) != signature(updated)) == changed


def test_exact_identities_and_object_ownership(tmp_path):
    _, request, _, window = setup_binding(tmp_path)
    tasks = recovery_sources(window, request.config)
    assert [task.entries[0].identity.namespace.value for task in tasks] == ["first", "second", "third"]
    assert all(task.entries[0] is next(iter(task.collection)) for task in tasks)


@pytest.mark.parametrize("change", ["project", "source", "key", "duplicate", "locked", "empty", "mode"])
def test_invalid_scope(tmp_path, change):
    _, request, _, window = setup_binding(tmp_path)
    record = window._recovery_selection[0]
    if change == "project":
        window._ctx.active_version_identity = ("other", "variant")
    elif change == "source":
        record["sources"][0]["key"] = "missing"
    elif change == "key":
        record["sources"][0]["polish_keys"][0]["local_key"] = "missing"
    elif change == "duplicate":
        record["sources"].append(deepcopy(record["sources"][0]))
    elif change == "locked":
        next(iter(window._ctx.collection)).stage = -1
    elif change == "empty":
        next(iter(window._ctx.collection)).original = " "
    else:
        window._view_port.mode = "mixed"
    with pytest.raises(ValueError):
        recovery_sources(window, request.config)


def test_changed_config_requires_restart(tmp_path):
    _, request, _, window = setup_binding(tmp_path)
    request.config.model = "new"
    with pytest.raises(ValueError, match="重新开始"):
        recovery_sources(window, request.config)
    window._recovery_selection = (window._recovery_selection[0], True)
    assert len(recovery_sources(window, request.config)) == 3


def test_skip_requires_exact_saved_result(tmp_path):
    binding, request, _, window = setup_binding(tmp_path)
    entry = next(iter(window._ctx.slots["second"].collection))
    binding.record["applied_entries"] = [
        {
            "entry_key": entry.identity.to_dict(),
            "original": entry.original,
            "text": entry.translation,
            "stage": entry.stage,
        }
    ]
    assert not recovery_sources(window, request.config)[1].entries
    binding.record["applied_entries"][0]["text"] = "未保存"
    assert recovery_sources(window, request.config)[1].entries


def test_continuation_does_not_overwrite_manifest(tmp_path):
    binding, request, session, _ = setup_binding(tmp_path)
    request.recovery_task_id = binding.record["task_id"]
    session.tasks = session.tasks[1:]
    resumed = TaskRecoveryBinding(request, session)
    assert len(resumed.record["sources"]) == 3
    request.config.model = "changed"
    with pytest.raises(ValueError, match="不匹配"):
        TaskRecoveryBinding(request, session)


def test_lifecycle_records_saved_evidence_only(tmp_path):
    binding, _, session, window = setup_binding(tmp_path)
    entry = next(iter(window._ctx.collection))
    results = SimpleNamespace(
        applied_keys={entry.identity}, entries={entry.identity: SimpleNamespace(before=entry, text="候选", stage=1)}
    )
    run = SimpleNamespace(state="completed", user_cancelled=False, shutting_down=False, entries=results)
    session.completed = True
    binding.update(run)
    assert binding.record["state"] == "applying" and not binding.record["applied_entries"]
    assert binding.record["applied"] and not binding.record["project_saved"]
    run.shutting_down = True
    binding.update(run)
    assert binding.record["state"] == "interrupted"
    run.shutting_down = False
    session.project_saved = True
    binding.update(run)
    assert binding.record["state"] == "completed" and binding.record["applied_entries"]
    assert binding.record["applied"] and binding.record["project_saved"]
    run.user_cancelled = run.shutting_down = True
    binding.update(run)
    assert binding.record["state"] == "cancelled"


def test_translation_is_not_supported(tmp_path):
    _, request, session, _ = setup_binding(tmp_path)
    request.run_id = "translation"
    task = session.tasks[0]
    session.tasks = (replace(task, translate_entries=task.polish_entries, polish_entries=()),)
    binding = TaskRecoveryBinding(request, session)
    assert not binding.supported and binding.store is None


def test_update_does_not_swallow_storage_errors(tmp_path, monkeypatch):
    binding, _, _, _ = setup_binding(tmp_path)

    def failed(*args, **kwargs):
        raise OSError("磁盘写入失败")

    monkeypatch.setattr(binding.store, "update", failed)
    run = SimpleNamespace(
        state="running", user_cancelled=False, shutting_down=False, entries=SimpleNamespace(applied_keys=set())
    )
    with pytest.raises(OSError, match="磁盘写入失败"):
        binding.update(run)
    run.state = "unrecognized"
    with pytest.raises(ValueError, match="未知任务状态"):
        binding.update(run)


_application = None


def test_configure_recovery_locks_scope_and_displays_original_task(tmp_path):
    from PyQt6.QtWidgets import QApplication, QCheckBox, QPushButton, QRadioButton, QVBoxLayout, QWidget

    from transbridge.ui.tools.ai_translator.task_recovery_binding import configure_recovery
    from transbridge.ui.tools.ai_translator.task_sources_view import TaskSourcesView

    global _application
    _application = QApplication.instance() or QApplication([])
    binding, request, _, fake = setup_binding(tmp_path)
    window = QWidget()
    window._config_presenter = SimpleNamespace(restore_task_config=lambda *_: request.config)
    QVBoxLayout(window)
    controls = SimpleNamespace(
        **{f"mode_{name}": QRadioButton(window) for name in ("translate", "polish", "mixed", "custom")},
        scope_stack=QWidget(window),
        start_btn=QPushButton(window),
    )
    window._view = SimpleNamespace(
        controls=controls,
        sources_panel=TaskSourcesView(fake._ctx, window),
        _scope_filter_box=QWidget(window),
        resume_proofread=QCheckBox(window),
    )
    configure_recovery(window, binding.record)
    assert controls.mode_polish.isChecked()
    assert not controls.mode_polish.isEnabled() and not window._view.sources_panel.isEnabled()
    assert not window._view._scope_filter_box.isEnabled() and not controls.scope_stack.isEnabled()
    assert controls.start_btn.text() == "继续任务"
    assert binding.record["created_at"] in window._view.recovery_notice.text()
    assert "原范围 3 条" in window._view.recovery_notice.text()
    configure_recovery(window, binding.record, restart=True)
    assert controls.start_btn.text() == "重新开始"
    window.close()


def test_temporary_configuration_restores_with_current_secrets_without_global_write(monkeypatch):
    import json

    from tests.ui.tools.test_unified_task_config import View
    from transbridge.ui.tools.ai_translator.config_presenter import ConfigPresenter
    from transbridge.ui.tools.ai_translator.task_config_snapshot import execution_snapshot

    global_config = LLMConfig(model="global", api_key="old-secret")
    global_config.embedding.api_key = "old-embedding-secret"
    monkeypatch.setattr(LLMConfig, "load_from_file", lambda: global_config)
    writes = []
    monkeypatch.setattr(LLMConfig, "save_to_file", lambda self: writes.append(self))
    first_view = View()
    first = ConfigPresenter(first_view, task_draft=True)
    first.load()
    first.switch_preset("polish")
    first_view.config.model = "temporary-model"
    first_view.config.max_output_tokens = 700
    first_view.config.max_tokens_per_batch = 1700
    first_view.config.max_concurrent = 7
    frozen = first.build()
    snapshot = execution_snapshot(frozen)
    assert "old-secret" not in json.dumps(snapshot)
    assert "old-embedding-secret" not in json.dumps(snapshot)
    global_config.api_key = "current-secret"
    global_config.embedding.api_key = "current-embedding-secret"
    second_view = View()
    second = ConfigPresenter(second_view, task_draft=True)
    second.load()
    second.switch_preset("polish")
    second.restore_task_config(snapshot, "polish")
    restored = second.build()
    assert signature(restored) == signature(frozen)
    assert restored.workflow_profiles == frozen.workflow_profiles
    assert restored.max_output_tokens == 700 and restored.max_tokens_per_batch == 1700
    assert restored.max_concurrent == 7
    assert restored.api_key == "current-secret"
    assert restored.embedding.api_key == "current-embedding-secret"
    assert global_config.model == "global" and not writes


@pytest.mark.parametrize("restart", [False, True])
def test_real_window_recovery_hydration_and_deferred_refresh(tmp_path, monkeypatch, restart):
    from PyQt6.QtWidgets import QApplication

    from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
    from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
    from transbridge.ui.tools.ai_translator.task_recovery_binding import configure_recovery

    global _application
    _application = QApplication.instance() or QApplication([])
    global_config = LLMConfig(model="global-model", api_key="current-key")
    monkeypatch.setattr(LLMConfig, "load_from_file", lambda: global_config)
    writes = []
    monkeypatch.setattr(LLMConfig, "save_to_file", lambda self: writes.append(self))
    original_context = _Context()
    slot = original_context.slots["second"]
    context = SimpleNamespace(
        slots={"second": slot},
        active_slot=slot,
        collection=slot.collection,
        active_version_identity=("project", "variant"),
        esp_path=None,
        current_project=None,
        entry_labels={},
        label_library={},
    )
    workbench = SimpleNamespace(filtered_entries=lambda: tuple(slot.collection), locate_entry=lambda _: None)
    original = AITranslatorWindow(context, workbench)
    reopened = None
    try:
        original._view.controls.mode_polish.setChecked(True)
        original._view.controls.model_edit.setText("temporary-model")
        original._view.controls.tokens_spin.setValue(1700)
        original._view.controls.output_tokens_spin.setValue(700)
        _application.processEvents()
        config = original._config_presenter.build()
        task = SourceTask("second", slot.label, None, slot.collection, (), tuple(slot.collection))
        request = SimpleNamespace(
            run_id="real-window",
            recovery_task_id=None,
            config=config,
            spec=SimpleNamespace(mode="polish", execution_profile=AiExecutionProfile.from_config("polish", config)),
        )
        session = SimpleNamespace(project_dir=tmp_path, version_identity=("project", "variant"), tasks=(task,))
        record = TaskRecoveryBinding(request, session).record
        original.close()
        reopened = AITranslatorWindow(context, workbench)
        configure_recovery(reopened, record, restart=restart)
        reopened.request_task_refresh()
        _application.processEvents()
        reopened.update_estimate()
        _application.processEvents()
        actual = reopened._config_presenter.build()
        if restart:
            assert actual.model == "global-model"
        else:
            assert signature(actual) == record["config_digest"]
            assert actual.model == "temporary-model"
            assert actual.max_output_tokens == 700 and actual.max_tokens_per_batch == 1700
        assert actual.api_key == "current-key"
        assert reopened._view.controls.start_btn.text() == ("重新开始" if restart else "继续任务")
        assert reopened._view.controls.start_btn.isEnabled(), reopened._view.controls.preflight_label.full_text
        assert len(recovery_sources(reopened, actual)[0].entries) == 1
        assert not writes
    finally:
        original.close()
        if reopened is not None:
            reopened.close()
        _application.processEvents()
