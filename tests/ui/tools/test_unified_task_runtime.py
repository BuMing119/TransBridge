from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import time
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools.ai_runtime_support import ai_task_runtime
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation.task_scope import SourceTask
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator import task_launch, task_progress, task_runtime, task_session
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.run_spec import AiPreflightCode, AiPreflightIssue, AiPreflightResult
from transbridge.ui.tools.ai_translator.term_source_inspector import TermSourceInspector


@pytest.fixture(scope="session")
def qapp():
    # Lifecycle tests import this fixture too. Retain one QApplication across
    # module boundaries so deferred QObject deletions never outlive its owner.
    return QApplication.instance() or QApplication([])


def wait_until(qapp, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.002)
    qapp.processEvents()
    assert condition(), "AI launch did not reach its expected state"


def _task(key):
    entry = TranslationEntry(
        key,
        "local-key",
        f"Source {key}",
        "",
        0,
        "INFO:NAM1",
        entry_key=EntryKey(SourceNamespace(key), "local-key"),
    )
    return SourceTask(key, key, f"{key}.esp", TranslationEntryCollection([entry]), (entry,), ())


@pytest.fixture
def launch(monkeypatch, qapp):
    events = []
    messages = []
    progress_instances = []
    activities = []
    preflighted = []
    config = LLMConfig(api_key="test-key", model="test-model", max_concurrent=2)
    context = SimpleNamespace(
        active_project_id="project",
        active_variant_id="variant",
        active_version_identity=("project", "variant"),
        config=object(),
    )
    controller = RunController(task_runtime=ai_task_runtime())
    window = SimpleNamespace(
        _ctx=context,
        _config_presenter=SimpleNamespace(build=lambda: config),
        _view_port=SimpleNamespace(mode="translate", overwrite=False),
        _theme_view=None,
        _run_controller=controller,
        _task_sources=lambda **_: (_task("first"),),
        progress_window_created=SimpleNamespace(emit=lambda progress: events.append(("created", progress))),
    )

    def close():
        events.append(("window-close", None))
        active = getattr(window, "_task_launch", None)
        if active is not None:
            active.cancel()
        controller.close()

    window.close = close

    create_activity = controller.create_activity

    def activity(request):
        value = create_activity(request)
        fail = value.fail
        value.fail = lambda message: (events.append(("failed", message)), fail(message))[-1]
        activities.append(value)
        return value

    controller.create_activity = activity

    class Session:
        def __init__(self, ctx, tasks, spec, *, project_dir=None):
            self.tasks = tasks
            self.spec = spec
            self.ctx = ctx
            self.project_dir = project_dir
            self.identity = ctx.active_version_identity
            from transbridge.application.translation.task_entry_results import TaskEntryResults

            self.initial_entry_results = TaskEntryResults(tasks, "launching")

        @classmethod
        def create_async(cls, ctx, tasks, spec, *, project_dir=None, on_success, on_error, parent=None, **kwargs):
            from PyQt6.QtCore import QTimer

            capture = SimpleNamespace(busy=True, cancelled=False)
            capture.cancel = lambda: setattr(capture, "cancelled", True)

            def deliver():
                capture.busy = False
                if capture.cancelled:
                    on_error("AI 任务准备已取消。")
                else:
                    on_success(cls(ctx, tasks, spec, project_dir=project_dir))

            QTimer.singleShot(0, deliver)
            return capture

        def bind_run_identity(self, run_id):
            self.initial_entry_results.run_id = run_id

        def require_current(self):
            if self.ctx.active_version_identity != self.identity:
                raise RuntimeError("活动项目或版本已变化")

    class Progress:
        def __init__(self, request, session, activity, **kwargs):
            self.request = request
            self.session = session
            self.activity = activity
            self.options = kwargs
            self.client = kwargs.get("client")
            self.running = False
            self.closed = False
            progress_instances.append(self)

        def prepare(self):
            self.running = True
            events.append(("prepared", self))

        def close(self):
            if self.running:
                return False
            self.closed = True
            if self.client is not None:
                client, self.client = self.client, None
                client.close()
            return True

        def _prepare_failed(self, message):
            self.running = False
            self.activity.fail(message)

    def preflight(mode, cfg, entries, **kwargs):
        preflighted.append((mode, cfg, entries, kwargs))
        return AiPreflightResult()

    monkeypatch.setattr(task_session, "TaskSession", Session)
    monkeypatch.setattr(task_progress, "AiTaskProgressWindow", Progress)
    monkeypatch.setattr(task_launch, "show_and_activate", lambda progress: events.append(("shown", progress)))
    monkeypatch.setattr(task_launch, "preflight_ai_run", preflight)
    monkeypatch.setattr(
        task_runtime.QMessageBox, "warning", lambda _w, title, message: messages.append((title, message))
    )
    monkeypatch.setattr(task_runtime.QMessageBox, "question", lambda *_: task_runtime.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(TermSourceInspector, "all_empty", lambda *_: False)
    monkeypatch.setattr("transbridge.ui.paratranz.target_context.bound_paratranz_project", lambda _: None)

    def start():
        task_runtime.start_task(window)
        wait_until(qapp, lambda: getattr(window, "_task_launch", None) is None)

    return SimpleNamespace(
        window=window,
        config=config,
        controller=controller,
        events=events,
        messages=messages,
        progress=progress_instances,
        activities=activities,
        preflighted=preflighted,
        start=start,
        wait=lambda condition: wait_until(qapp, condition),
    )


@pytest.mark.parametrize("count", [1, 3])
def test_sources_launch_one_frozen_task_and_outlive_closed_configuration_window(launch, count):
    tasks = tuple(_task(f"source-{i}") for i in range(count))
    launch.window._task_sources = lambda **_: tasks
    launch.start()

    (progress,) = launch.progress
    assert progress.session.tasks == tasks
    assert [call[3]["esp_path"] for call in launch.preflighted] == [task.esp_path for task in tasks]
    assert [call[2] for call in launch.preflighted] == [task.entries for task in tasks]
    assert all(call[3]["mixed_has_translation"] for call in launch.preflighted)
    assert progress.request.spec.owner.project_id == "project"
    assert progress.request.spec.owner.variant_id == "variant"
    assert len(progress.request.entries) == count
    assert progress.running and not progress.closed
    assert [event[0] for event in launch.events] == ["created", "shown", "prepared", "window-close"]
    assert not launch.controller.is_running
    launch.config.model = "later-model"
    assert progress.request.config.model == "test-model"
    with pytest.raises(FrozenInstanceError):
        progress.request.spec.overwrite = True
    assert not launch.messages


def test_preflight_failure_in_later_source_blocks_entire_task(launch, monkeypatch):
    tasks = (_task("first"), _task("bad"), _task("never-started"))
    launch.window._task_sources = lambda **_: tasks
    checked = []

    def preflight(_mode, _config, entries, **kwargs):
        checked.append(kwargs["esp_path"])
        if kwargs["esp_path"] == "bad.esp":
            from transbridge.ui.shell.action_catalog import IntentId

            return AiPreflightResult((
                AiPreflightIssue(AiPreflightCode.MISSING_DEPENDENCY, "缺少运行依赖", IntentId.SETTINGS_SERVICES),
            ))
        return AiPreflightResult()

    monkeypatch.setattr(task_launch, "preflight_ai_run", preflight)
    launch.start()
    assert checked == ["first.esp", "bad.esp"]
    assert launch.messages == [("AI 运行条件未满足", "bad：缺少运行依赖")]
    assert not launch.controller.is_running and not launch.progress and not launch.activities
    assert not launch.events


@pytest.mark.parametrize("reuse", [True, False])
def test_new_launch_ignores_legacy_reuse_checkbox(launch, reuse):
    launch.window._view = SimpleNamespace(resume_proofread=SimpleNamespace(isChecked=lambda: reuse))
    launch.start()
    assert launch.progress[0].request.reuse_proofread is False
    assert launch.progress[0].request.recovery_task_id is None


@pytest.mark.parametrize("restart", [False, True])
def test_explicit_recovery_launch_binds_only_continue_to_original_task(launch, monkeypatch, restart):
    from transbridge.ui.tools.ai_translator import task_recovery_binding

    launch.window._recovery_selection = ({"task_id": "original-task", "mode": "translate"}, restart)
    monkeypatch.setattr(task_recovery_binding, "recovery_sources", lambda *_: (_task("first"),))
    launch.start()
    (progress,) = launch.progress
    assert progress.request.run_id != "original-task"
    assert progress.request.reuse_proofread is (not restart)
    assert progress.request.recovery_task_id == (None if restart else "original-task")
    assert not launch.messages


def test_declining_empty_terms_does_not_admit_or_start_task(launch, monkeypatch):
    checked = []
    launch.window._task_sources = lambda **_: (_task("first"), _task("second"))
    monkeypatch.setattr(TermSourceInspector, "all_empty", lambda _config, path: checked.append(path) or True)
    monkeypatch.setattr(task_runtime.QMessageBox, "question", lambda *_: task_runtime.QMessageBox.StandardButton.No)
    launch.start()
    assert checked == ["first.esp", "second.esp"]
    assert not launch.controller.is_running and not launch.progress and not launch.activities
    assert not launch.events and not launch.messages


def test_empty_source_scope_has_no_preflight_or_side_effects(launch):
    launch.window._task_sources = lambda **_: ()
    launch.start()
    assert launch.messages == [("AI 翻译", "所选来源没有符合范围的可处理词条。")]
    assert not launch.preflighted and not launch.progress and not launch.events


def test_session_construction_failure_releases_controller_and_keeps_window_open(launch, monkeypatch):
    def session(*_, **_kwargs):
        raise ValueError("来源在启动期间被移除")

    monkeypatch.setattr(task_session, "TaskSession", SimpleNamespace(create_async=session))
    launch.start()
    assert not launch.controller.is_running and not launch.progress
    assert not launch.events and not launch.activities
    assert launch.messages == [("AI 任务未启动", "来源在启动期间被移除")]
    request = launch.controller.begin("translate", launch.config, [_task("retry").entries[0]])
    assert launch.controller.accepts(request.run_id)
    launch.controller.close()


def test_project_directory_provider_is_resolved_at_launch_and_passed_to_session(launch, tmp_path):
    directory = tmp_path / "active-project"
    provider = Mock(return_value=directory)
    launch.window._task_project_directory_provider = provider
    launch.start()
    provider.assert_called_once_with()
    assert launch.progress[0].session.project_dir == directory
    assert not launch.messages


def test_missing_directory_provider_preserves_legacy_session_fallback(launch):
    launch.start()
    assert launch.progress[0].session.project_dir is None
    assert not launch.messages


def test_project_directory_lookup_failure_keeps_configuration_and_releases_run(launch):
    launch.window._task_project_directory_provider = Mock(side_effect=RuntimeError("项目位置不可用"))
    launch.start()
    assert not launch.progress and not launch.controller.is_running
    assert launch.messages == [("AI 任务未启动", "项目位置不可用")]
    assert not any(event[0] == "window-close" for event in launch.events)


def test_activity_construction_failure_releases_run_guard(launch):
    def create_activity(_request):
        raise RuntimeError("活动中心不可用")

    launch.controller.create_activity = create_activity
    launch.start()
    assert not launch.controller.is_running and not launch.progress
    assert launch.messages == [("AI 任务未启动", "活动中心不可用")]


def test_source_projection_failure_is_presented_before_run_is_created(launch):
    def sources(**_):
        raise ValueError("来源已变化")

    launch.window._task_sources = sources
    launch.start()
    assert launch.messages == [("AI 任务未启动", "来源已变化")]
    assert not launch.controller.is_running and not launch.progress


def test_progress_preparation_failure_terminates_progress_without_closing_configuration(launch, monkeypatch):
    def prepare(_self):
        raise RuntimeError("执行前快照不可用")

    monkeypatch.setattr(task_progress.AiTaskProgressWindow, "prepare", prepare)
    launch.start()
    (progress,) = launch.progress
    assert not launch.controller.is_running and not progress.running
    assert progress.closed
    assert not any(event[0] == "window-close" for event in launch.events)
    assert launch.messages == [("AI 任务未启动", "执行前快照不可用")]


def test_mixed_preflight_preserves_each_sources_actual_stage_presence(launch):
    translation = _task("first")
    source = _task("second")
    entry = replace(source.translate_entries[0], translation="已有译文", stage=1)
    polish = replace(
        source, collection=TranslationEntryCollection([entry]), translate_entries=(), polish_entries=(entry,)
    )
    launch.window._view_port.mode = "mixed"
    launch.window._task_sources = lambda **_: (translation, polish)
    launch.start()
    assert [call[3]["mixed_has_translation"] for call in launch.preflighted] == [True, False]
    assert [call[0] for call in launch.preflighted] == ["mixed", "mixed"]
    assert launch.progress[0].session.tasks == (translation, polish)


def test_progress_constructor_failure_closes_created_remote_client(launch, monkeypatch):
    client = SimpleNamespace(closed=False)

    def close_client():
        client.closed = True

    client.close = close_client
    monkeypatch.setattr("transbridge.ui.paratranz.target_context.bound_paratranz_project", lambda _: {"id": 42})
    monkeypatch.setattr("transbridge.paratranz.api.paratranz_terms_api.ParatranzTermsAPI", lambda _: client)

    def progress(*_args, **kwargs):
        assert kwargs["client"] is client and kwargs["project_id"] == 42
        raise RuntimeError("进度窗口创建失败")

    monkeypatch.setattr(task_progress, "AiTaskProgressWindow", progress)
    launch.start()
    assert client.closed
    assert not launch.controller.is_running
    assert launch.messages == [("AI 任务未启动", "进度窗口创建失败")]


@pytest.mark.parametrize("still_busy", [False, True])
def test_preparation_failure_leaves_client_cleanup_to_progress_owner(launch, monkeypatch, still_busy):
    client = Mock()
    monkeypatch.setattr("transbridge.ui.paratranz.target_context.bound_paratranz_project", lambda _: {"id": 42})
    monkeypatch.setattr("transbridge.paratranz.api.paratranz_terms_api.ParatranzTermsAPI", lambda _: client)

    def prepare(progress):
        progress.running = still_busy
        raise RuntimeError("snapshot failure")

    monkeypatch.setattr(task_progress.AiTaskProgressWindow, "prepare", prepare)
    if still_busy:
        monkeypatch.setattr(task_progress.AiTaskProgressWindow, "_prepare_failed", lambda progress, error: None)
    launch.start()
    progress = launch.progress[0]
    if still_busy:
        assert not progress.closed
        client.close.assert_not_called()
        progress.running = False
    progress.close()
    progress.close()
    client.close.assert_called_once_with()


@pytest.mark.parametrize("mode,count", [("translate", 1), ("translate", 3), ("mixed", 2)])
@pytest.mark.parametrize("ignore", [True, False])
def test_optional_retrieval_is_decided_once_before_task_creation(launch, monkeypatch, mode, count, ignore):
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from transbridge.ui.tools.ai_translator import run_preparation
    from transbridge.ui.tools.ai_translator.run_spec import _config_digest, preflight_ai_run

    app = QApplication.instance() or QApplication([])
    launch.config.embedding.mode = "api"
    launch.config.embedding.api_key = ""
    launch.window._view_port.mode = mode
    launch.window._task_sources = lambda **_: tuple(_task(str(index)) for index in range(count))
    before = _config_digest(launch.config)
    prompts, checked = [], []

    def preflight(mode, config, entries, **kwargs):
        checked.append(config)
        return preflight_ai_run(mode, config, entries, dependency_available=lambda _: True, **kwargs)

    def answer(dialog):
        prompts.append(dialog.windowTitle())
        return QMessageBox.StandardButton.Ignore if ignore else QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(task_launch, "preflight_ai_run", preflight)
    monkeypatch.setattr(QMessageBox, "exec", answer)
    monkeypatch.setattr(
        task_launch,
        "confirm_optional_retrieval",
        lambda _window, config, issues: run_preparation.confirm_optional_retrieval(None, config, issues),
    )
    launch.start()

    assert prompts == ["语义检索不可用"]
    assert _config_digest(launch.config) == before
    assert not launch.messages
    if ignore:
        (progress,) = launch.progress
        assert len(checked) == count * 2
        assert all(config is not launch.config and _config_digest(config) == before for config in checked[:count])
        assert all(config.enable_semantic_match is False for config in checked[count:])
        assert progress.request.config.embedding.mode == "disabled"
        assert progress.request.spec.config_digest == _config_digest(checked[-1])
        assert progress.request.config.retrieval_enabled
        assert progress.running
    else:
        assert not launch.progress and not launch.activities and not launch.events
    app.processEvents()


def test_optional_retrieval_cannot_bypass_missing_llm_credentials(launch, monkeypatch):
    from transbridge.ui.tools.ai_translator.run_spec import preflight_ai_run

    launch.config.api_key = ""
    launch.config.embedding.mode = "api"
    monkeypatch.setattr(
        task_launch,
        "preflight_ai_run",
        lambda mode, config, entries, **kwargs: preflight_ai_run(
            mode, config, entries, dependency_available=lambda _: True, **kwargs
        ),
    )
    monkeypatch.setattr(
        task_launch,
        "confirm_optional_retrieval",
        lambda *_: pytest.fail("core blockers must be handled before optional retrieval"),
    )

    launch.start()

    assert launch.messages == [("AI 运行条件未满足", "first：尚未配置 API Key")]
    assert not launch.progress and not launch.activities and not launch.events


def test_empty_recovery_finishes_before_any_preflight_or_run_creation(launch, tmp_path):
    from tests.ui.tools.test_task_recovery_binding import setup_binding

    binding, request, _session, restored = setup_binding(tmp_path)
    binding.release()
    binding.record.pop("recovery_contract")  # Preserve the historical proofreading completion behavior.
    for slot in restored._ctx.slots.values():
        for entry in slot.collection:
            entry.stage = 9
    launch.window._ctx = restored._ctx
    launch.window._recovery_selection = restored._recovery_selection
    launch.window._view_port = restored._view_port
    launch.window._config_presenter = SimpleNamespace(build=lambda: request.config)
    launch.window._task_project_directory_provider = lambda: tmp_path

    launch.start()

    assert binding.store.load(binding.record["task_id"])["state"] == "completed"
    assert not launch.preflighted and not launch.progress and not launch.activities and not launch.messages
    assert launch.events == [("window-close", None)]
