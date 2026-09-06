from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator import task_progress, task_runtime, task_session
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.task_scope import SourceTask
from transbridge.ui.tools.ai_translator.term_source_inspector import TermSourceInspector


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
def launch(monkeypatch):
    events = []
    messages = []
    progress_instances = []
    activities = []
    preflighted = []
    config = LLMConfig(api_key="test-key", model="test-model", max_concurrent=2)
    context = SimpleNamespace(active_project_id="project", active_variant_id="variant", config=object())
    controller = RunController()
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
        controller.close()

    window.close = close

    def activity(request):
        value = SimpleNamespace(request=request, fail=lambda message: events.append(("failed", message)))
        activities.append(value)
        return value

    controller.create_activity = activity

    class Session:
        def __init__(self, ctx, tasks, spec):
            self.tasks = tasks
            self.spec = spec
            self.ctx = ctx

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
        return SimpleNamespace(ready=True, reason=None)

    monkeypatch.setattr(task_session, "TaskSession", Session)
    monkeypatch.setattr(task_progress, "AiTaskProgressWindow", Progress)
    monkeypatch.setattr(task_runtime, "show_and_activate", lambda progress: events.append(("shown", progress)))
    monkeypatch.setattr(task_runtime, "preflight_ai_run", preflight)
    monkeypatch.setattr(
        task_runtime.QMessageBox, "warning", lambda _w, title, message: messages.append((title, message))
    )
    monkeypatch.setattr(task_runtime.QMessageBox, "question", lambda *_: task_runtime.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(TermSourceInspector, "all_empty", lambda *_: False)
    monkeypatch.setattr("transbridge.ui.paratranz.target_context.bound_paratranz_project", lambda _: None)
    return SimpleNamespace(
        window=window,
        config=config,
        controller=controller,
        events=events,
        messages=messages,
        progress=progress_instances,
        activities=activities,
        preflighted=preflighted,
    )


@pytest.mark.parametrize("count", [1, 3])
def test_sources_launch_one_frozen_task_and_outlive_closed_configuration_window(launch, count):
    tasks = tuple(_task(f"source-{i}") for i in range(count))
    launch.window._task_sources = lambda **_: tasks
    task_runtime.start_task(launch.window)

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
        return SimpleNamespace(ready=kwargs["esp_path"] != "bad.esp", reason="缺少运行依赖")

    monkeypatch.setattr(task_runtime, "preflight_ai_run", preflight)
    task_runtime.start_task(launch.window)
    assert checked == ["first.esp", "bad.esp"]
    assert launch.messages == [("AI 运行条件未满足", "bad：缺少运行依赖")]
    assert not launch.controller.is_running and not launch.progress and not launch.activities
    assert not launch.events


def test_declining_empty_terms_does_not_freeze_or_start_task(launch, monkeypatch):
    checked = []
    launch.window._task_sources = lambda **_: (_task("first"), _task("second"))
    monkeypatch.setattr(TermSourceInspector, "all_empty", lambda _config, path: checked.append(path) or True)
    monkeypatch.setattr(task_runtime.QMessageBox, "question", lambda *_: task_runtime.QMessageBox.StandardButton.No)
    task_runtime.start_task(launch.window)
    assert checked == ["first.esp", "second.esp"]
    assert not launch.controller.is_running and not launch.progress and not launch.activities
    assert not launch.events and not launch.messages


def test_empty_source_scope_has_no_preflight_or_side_effects(launch):
    launch.window._task_sources = lambda **_: ()
    task_runtime.start_task(launch.window)
    assert launch.messages == [("AI 翻译", "所选来源没有符合范围的可处理词条。")]
    assert not launch.preflighted and not launch.progress and not launch.events


def test_session_construction_failure_releases_controller_and_keeps_window_open(launch, monkeypatch):
    def session(*_):
        raise ValueError("来源在启动期间被移除")

    monkeypatch.setattr(task_session, "TaskSession", session)
    task_runtime.start_task(launch.window)
    assert not launch.controller.is_running and not launch.progress
    assert launch.events == [("failed", "来源在启动期间被移除")]
    assert launch.messages == [("AI 任务未启动", "来源在启动期间被移除")]
    request = launch.controller.begin("translate", launch.config, [_task("retry").entries[0]])
    assert launch.controller.accepts(request.run_id)
    launch.controller.close()


def test_activity_construction_failure_releases_run_guard(launch):
    def create_activity(_request):
        raise RuntimeError("活动中心不可用")

    launch.controller.create_activity = create_activity
    task_runtime.start_task(launch.window)
    assert not launch.controller.is_running and not launch.progress
    assert launch.messages == [("AI 任务未启动", "活动中心不可用")]


def test_source_projection_failure_is_presented_before_run_is_created(launch):
    def sources(**_):
        raise ValueError("来源已变化")

    launch.window._task_sources = sources
    task_runtime.start_task(launch.window)
    assert launch.messages == [("AI 任务未启动", "来源已变化")]
    assert not launch.controller.is_running and not launch.progress


def test_progress_preparation_failure_terminates_progress_without_closing_configuration(launch, monkeypatch):
    def prepare(_self):
        raise RuntimeError("执行前快照不可用")

    monkeypatch.setattr(task_progress.AiTaskProgressWindow, "prepare", prepare)
    task_runtime.start_task(launch.window)
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
    task_runtime.start_task(launch.window)
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
    task_runtime.start_task(launch.window)
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
    task_runtime.start_task(launch.window)
    progress = launch.progress[0]
    if still_busy:
        assert not progress.closed
        client.close.assert_not_called()
        progress.running = False
    progress.close()
    progress.close()
    client.close.assert_called_once_with()
