from __future__ import annotations

from datetime import UTC, datetime
import time
from types import SimpleNamespace

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QPushButton

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.application.tasks import JobState, TaskRuntime
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.variant_store import VariantStore
from transbridge.ui.context import AppContext
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.version_snapshot import AiVersionSnapshotSession
from transbridge.ui.tools.ai_translator.versioned_run import start_versioned_translation
from transbridge.ui.version_persistence import VersionPersistence

_APP = QApplication.instance() or QApplication([])


class _Commands:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.expected: list[dict[str, object]] = []
        self.fail_snapshot = False

    def replace_entry_states(self, states, context, **expected):
        self.calls.append(("replace", states))
        self.expected.append(expected)
        return OperationResult.completed({"revision": 1}, run_id="run")

    def save(self, context):
        self.calls.append(("save", context))
        return OperationResult.completed({}, run_id="run")

    def save_snapshot(self, name, context):
        self.calls.append(("snapshot", name))
        if self.fail_snapshot:
            return SimpleNamespace(is_success=False)
        return OperationResult.completed({"name": name}, run_id="run")


def _entry() -> TranslationEntry:
    return TranslationEntry("entry", "entry", "source", "译文", 1, "INFO:FULL")


def test_v2_save_translation_commits_saves_then_snapshots() -> None:
    commands = _Commands()
    context = AppContext(project_commands=commands, runtime_context=object())
    context._project_projection = object()
    context._active_project_id = "project"
    context._active_variant_id = "variant"
    context._project_revision = 3
    context._variant_revision = 4

    result = VersionPersistence(context, ("project", "variant")).save_translation((_entry(),), "AI-翻译后")

    assert result.is_success
    assert [name for name, _value in commands.calls] == ["replace", "save", "snapshot"]
    states = commands.calls[0][1]
    assert next(iter(states.values())) == ("译文", 1)
    assert commands.expected[0]["expected_project_revision"] == 3
    assert commands.expected[0]["expected_variant_revision"] == 4
    assert commands.expected[0]["expected_variant_ref"].identity.value == "variant"


def test_v2_translation_commit_can_precede_save_without_repeating_mutation() -> None:
    commands = _Commands()
    context = AppContext(project_commands=commands, runtime_context=object())
    context._project_projection = object()
    context._active_project_id = "project"
    context._active_variant_id = "variant"
    persistence = VersionPersistence(context, ("project", "variant"))

    committed = persistence.commit_translation((_entry(),))
    committed_again = persistence.commit_translation((_entry(),))
    saved = persistence.save_translation((_entry(),), "AI-翻译后")

    assert committed.is_success
    assert committed_again is committed
    assert saved.is_success
    assert [name for name, _value in commands.calls] == ["replace", "save", "snapshot"]


def test_failed_v2_translation_commit_can_be_retried() -> None:
    class Commands(_Commands):
        def __init__(self) -> None:
            super().__init__()
            self.fail_commit = True

        def replace_entry_states(self, states, context, **expected):
            self.calls.append(("replace", states))
            if self.fail_commit:
                return SimpleNamespace(is_success=False)
            return OperationResult.completed({"revision": 1}, run_id="run")

    commands = Commands()
    context = AppContext(project_commands=commands, runtime_context=object())
    context._project_projection = object()
    context._active_project_id = "project"
    context._active_variant_id = "variant"
    persistence = VersionPersistence(context, ("project", "variant"))

    failed = persistence.commit_translation((_entry(),))
    commands.fail_commit = False
    retried = persistence.save_translation((_entry(),), "AI-翻译后")

    assert not failed.is_success
    assert retried.is_success
    assert [name for name, _value in commands.calls] == ["replace", "replace", "save", "snapshot"]


def test_version_identity_change_blocks_save_before_any_command() -> None:
    commands = _Commands()
    context = AppContext(project_commands=commands, runtime_context=object())
    context._project_projection = object()
    context._active_project_id = "other-project"
    context._active_variant_id = "variant"

    try:
        VersionPersistence(context, ("project", "variant")).save_translation((_entry(),), "AI-翻译后")
    except RuntimeError as exc:
        assert "已变化" in str(exc)
    else:
        raise AssertionError("identity mismatch should fail closed")
    assert commands.calls == []


def test_post_snapshot_retry_does_not_repeat_translation_commit_or_save() -> None:
    commands = _Commands()
    commands.fail_snapshot = True
    context = AppContext(project_commands=commands, runtime_context=object())
    context._project_projection = object()
    context._active_project_id = "project"
    context._active_variant_id = "variant"
    persistence = VersionPersistence(context, ("project", "variant"))

    failed = persistence.save_translation((_entry(),), "AI-翻译后")
    commands.fail_snapshot = False
    retried = persistence.save_translation((_entry(),), "AI-翻译后")

    assert not failed.is_success
    assert retried.is_success
    assert [name for name, _value in commands.calls] == ["replace", "save", "snapshot", "snapshot"]


def test_legacy_version_collects_before_snapshot_and_saves_before_after_snapshot(tmp_path) -> None:
    variant_root = tmp_path / "variant"
    project = SimpleNamespace(
        name="legacy-project",
        config_path=tmp_path / "project.json",
        variant_dir=lambda _name: variant_root,
    )
    context = AppContext()
    context._active_project = project
    context._active_variant = "main"
    context._variant_store = VariantStore(variant_root / "current.json")
    identity = context.active_version_identity
    assert identity is not None
    persistence = VersionPersistence(context, identity)

    before = persistence.create_snapshot("AI-翻译-执行前", (_entry(),))
    after = persistence.save_translation((_entry(),), "AI-翻译-保存后")

    assert before.is_file()
    assert (variant_root / "current.json").is_file()
    assert after.is_file()
    assert context.variant_store.translations == {"entry": "译文"}


def test_legacy_translation_commit_can_precede_save_without_recollecting(tmp_path, monkeypatch) -> None:
    variant_root = tmp_path / "variant"
    project = SimpleNamespace(
        name="legacy-project",
        config_path=tmp_path / "project.json",
        variant_dir=lambda _name: variant_root,
    )
    context = AppContext()
    context._active_project = project
    context._active_variant = "main"
    context._variant_store = VariantStore(variant_root / "current.json")
    identity = context.active_version_identity
    assert identity is not None
    persistence = VersionPersistence(context, identity)
    collected: list[int] = []
    original_collect = context.variant_store.collect_from

    def collect_once(*args, **kwargs) -> None:
        collected.append(1)
        original_collect(*args, **kwargs)

    monkeypatch.setattr(context.variant_store, "collect_from", collect_once)

    persistence.commit_translation((_entry(),))
    persistence.save_translation((_entry(),), "AI-翻译-保存后")

    assert collected == [1]
    assert (variant_root / "current.json").is_file()


def test_snapshot_session_is_async_and_post_snapshot_is_idempotent() -> None:
    app = _APP

    class Context:
        active_version_identity = ("project", "variant")
        collection = (_entry(),)
        uses_authoritative_projection = True
        runtime_context = object()

        def __init__(self) -> None:
            self.calls: list[str] = []
            self.project_commands = self

        def save_snapshot(self, name, _runtime_context):
            self.calls.append(f"snapshot:{name}")
            return {"name": name}

        def replace_entry_states(self, states, _runtime_context, **expected):
            self.calls.append("replace")
            assert len(states) == 1
            return OperationResult.completed({})

        def save(self, _runtime_context):
            self.calls.append("save")
            return OperationResult.completed({})

    context = Context()
    session = AiVersionSnapshotSession(context, SimpleNamespace(mode="translate", run_id="ai-run"))
    completed: list[object] = []
    errors: list[str] = []
    session.capture_before(on_success=completed.append, on_error=errors.append)
    _wait_until(app, lambda: len(completed) == 1)
    _wait_until(app, lambda: not session.is_busy)
    session.mark_completed()
    session.save_translation(on_success=completed.append, on_error=errors.append)
    _wait_until(app, lambda: len(completed) == 2)
    _wait_until(app, lambda: not session.is_busy)
    session.save_translation(on_success=completed.append, on_error=errors.append)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()

    assert errors == []
    assert len(context.calls) == 4
    assert context.calls[0].startswith("snapshot:AI-翻译-执行前-")
    assert context.calls[1:3] == ["replace", "save"]
    assert context.calls[3].startswith("snapshot:AI-翻译-保存后-")
    assert completed[-1]["already_saved"] is True


def test_cancelled_snapshot_session_restores_latest_authoritative_projection() -> None:
    entry = _entry()
    emitted: list[object] = []
    projection = ProjectionStore(
        ProjectionSnapshot(
            "project:project",
            5,
            5,
            {
                "project_id": "project",
                "variant_id": "variant",
                "entries": [
                    {
                        "entry_key": entry.identity.to_dict(),
                        "translation": "并发授权译文",
                        "stage": 3,
                    }
                ],
            },
        )
    )
    context = SimpleNamespace(
        active_version_identity=("project", "variant"),
        collection=(entry,),
        uses_authoritative_projection=True,
        _project_projection=projection,
        collection_changed=SimpleNamespace(emit=emitted.append),
    )
    session = AiVersionSnapshotSession(context, SimpleNamespace(mode="translate", run_id="ai-run"))
    entry.translation = "AI 部分结果"
    entry.stage = 1

    session.rollback_uncommitted()

    assert (entry.translation, entry.stage) == ("并发授权译文", 3)
    assert emitted == [context.collection]


def test_cancelled_snapshot_session_does_not_touch_a_newly_opened_version() -> None:
    old_entry = _entry()
    new_entry = TranslationEntry("new", "new", "source", "新版本译文", 1, "INFO:FULL")
    context = SimpleNamespace(
        active_version_identity=("project", "variant"),
        collection=(old_entry,),
        uses_authoritative_projection=False,
        collection_changed=SimpleNamespace(emit=lambda _value: None),
    )
    session = AiVersionSnapshotSession(context, SimpleNamespace(mode="translate", run_id="ai-run"))
    context.active_version_identity = ("project", "other-variant")
    context.collection = (new_entry,)
    new_entry.translation = "并发新版本编辑"

    session.rollback_uncommitted()

    assert new_entry.translation == "并发新版本编辑"


def test_pre_snapshot_failure_marks_task_failed_and_never_starts_translation(monkeypatch) -> None:
    app = _APP

    class Ids:
        def new_id(self) -> str:
            return "runtime-ai-run"

    class Clock:
        def now(self):
            return datetime(2026, 8, 27, tzinfo=UTC)

    class Context:
        active_version_identity = ("project", "variant")
        collection = (_entry(),)
        uses_authoritative_projection = True
        runtime_context = object()

        def __init__(self) -> None:
            self.project_commands = self

        def save_snapshot(self, _name, _runtime_context):
            return OperationResult.failed(
                DomainError(ErrorCategory.INTERNAL, "SNAPSHOT_FAILED", "磁盘写入失败。"),
                run_id="runtime-ai-run",
            )

    class Label:
        def set_full_text(self, _value) -> None:
            pass

        def setToolTip(self, _value) -> None:
            pass

        def setAccessibleDescription(self, _value) -> None:
            pass

    runtime = TaskRuntime(id_generator=Ids(), clock=Clock())
    controller = RunController(owner_id="window", task_runtime=runtime)
    request = controller.begin(
        "translate",
        SimpleNamespace(max_concurrent=1),
        [_entry()],
        project_id="project",
        variant_id="variant",
    )
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator.versioned_run.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    window = SimpleNamespace(
        _run_controller=controller,
        _ctx=Context(),
        _view=SimpleNamespace(controls=SimpleNamespace(preflight_label=Label(), start_btn=QPushButton())),
        _view_port=SimpleNamespace(selected_mode="translate"),
        update_quick_run=lambda: None,
    )

    start_versioned_translation(window, request)
    _wait_until(app, lambda: not controller.is_running)

    assert request.runtime_ref is not None
    assert runtime.get(request.runtime_ref, request.spec.owner).state is JobState.FAILED
    assert warnings == [("AI 翻译未启动", "翻译前版本快照创建失败：SNAPSHOT_FAILED: 磁盘写入失败。")]


def _wait_until(app: QApplication, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for background snapshot operation")
        app.processEvents()
        time.sleep(0.01)
