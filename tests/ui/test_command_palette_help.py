from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShortcut
from PyQt6.QtWidgets import QApplication, QDockWidget, QMainWindow, QSizePolicy, QWidget

from transbridge.ui.shell.action_catalog import (
    DEFAULT_ACTION_CATALOG,
    ActionAvailability,
    DangerLevel,
    IntentId,
)
from transbridge.ui.shell.command_palette import (
    CommandCandidateKind,
    CommandPaletteController,
    CommandPaletteModel,
    DynamicCommandCandidate,
)
from transbridge.ui.shell.command_palette_qt import CommandPaletteDialog
from transbridge.ui.shell.context_help import DEFAULT_CONTEXT_HELP, ContextHelpController
from transbridge.ui.shell.context_help_qt import ContextHelpPanel
from transbridge.ui.shell.intent_composition import ShellIntentComposition
from transbridge.ui.shell.overlay_geometry import workspace_overlay_rect

_APP = QApplication.instance() or QApplication([])


def _availability(
    *,
    disabled: dict[IntentId, str] | None = None,
) -> tuple[ActionAvailability, ...]:
    reasons = disabled or {}
    return tuple(
        DEFAULT_ACTION_CATALOG.availability(
            descriptor.intent_id,
            enabled=descriptor.intent_id not in reasons,
            reason=reasons.get(descriptor.intent_id),
        )
        for descriptor in DEFAULT_ACTION_CATALOG.all()
    )


def test_search_consumes_catalog_user_language_aliases_and_has_stable_order() -> None:
    model = CommandPaletteModel(_availability)

    labels = [item.label for item in model.search("翻译").results]
    repeated = [item.label for item in model.search("翻译").results]
    plugin_alias = model.search("ESP").results
    assistant_alias = model.search("聊天").results

    assert labels == repeated
    assert labels[0] == "翻译词典…"
    assert "AI 自动翻译…" in labels
    assert plugin_alias[0].intent_id is IntentId.SOURCE_PARSE
    assert assistant_alias[0].intent_id is IntentId.VIEW_SMART_ASSISTANT


def test_search_result_preserves_authoritative_disabled_reason() -> None:
    reason = "当前没有可翻译内容，请先选择插件"
    model = CommandPaletteModel(lambda: _availability(disabled={IntentId.TRANSLATION_AI: reason}))

    result = model.search("AI").results[0]
    controller = CommandPaletteController(model)
    snapshot = controller.open("AI")
    activation = controller.activate(snapshot.results[0].result_id)

    assert result.disabled_reason == reason
    assert activation.request is None
    assert activation.blocked_reason == reason
    assert controller.is_open


def test_dangerous_result_returns_confirmation_request_and_never_dispatches_itself() -> None:
    model = CommandPaletteModel(_availability)
    controller = CommandPaletteController(model)
    snapshot = controller.open("下载并合并")
    result = snapshot.results[0]

    activation = controller.activate(result.result_id)
    duplicate = controller.activate(result.result_id)

    assert result.availability.descriptor.danger is DangerLevel.CAUTION
    assert activation.request is not None
    assert activation.request.intent_id is IntentId.SYNC_DOWNLOAD
    assert activation.request.requires_confirmation
    assert not controller.is_open
    assert duplicate.request is None
    assert duplicate.blocked_reason == "命令搜索已关闭"


def test_safe_result_forwards_one_stable_intent_without_confirmation() -> None:
    controller = CommandPaletteController(CommandPaletteModel(_availability))
    snapshot = controller.open("关于")

    activation = controller.activate(snapshot.results[0].result_id)

    assert activation.request is not None
    assert activation.request.intent_id is IntentId.HELP_ABOUT
    assert not activation.request.requires_confirmation


def test_recent_project_and_translation_content_are_injected_with_payload() -> None:
    dynamic = (
        DynamicCommandCandidate(
            "project:demo",
            CommandCandidateKind.RECENT_PROJECT,
            "继续 Demo 本地翻译工程",
            IntentId.PROJECT_OPEN,
            aliases=("上次工程",),
            payload={"project_id": "demo"},
        ),
        DynamicCommandCandidate(
            "content:main",
            CommandCandidateKind.TRANSLATION_CONTENT,
            "打开主插件翻译内容",
            IntentId.WORKBENCH_MANAGE,
            aliases=("主词条",),
            payload={"content_id": "main"},
        ),
    )
    model = CommandPaletteModel(_availability, dynamic_source=lambda: dynamic)

    recent = model.search("上次工程").results[0]
    content = model.search("主词条").results[0]

    assert recent.kind is CommandCandidateKind.RECENT_PROJECT
    assert recent.intent_id is IntentId.PROJECT_OPEN
    assert recent.payload == {"project_id": "demo"}
    assert content.kind is CommandCandidateKind.TRANSLATION_CONTENT
    assert content.intent_id is IntentId.WORKBENCH_MANAGE
    assert content.payload == {"content_id": "main"}


def test_stale_recent_target_is_visible_with_reason_and_refreshes_from_source() -> None:
    stale = True

    def dynamic_source() -> tuple[DynamicCommandCandidate, ...]:
        return (
            DynamicCommandCandidate(
                "project:removed",
                CommandCandidateKind.RECENT_PROJECT,
                "继续已移动的工程",
                IntentId.PROJECT_OPEN,
                stale_reason="最近工程已移动或不存在" if stale else None,
            ),
        )

    model = CommandPaletteModel(_availability, dynamic_source=dynamic_source)
    controller = CommandPaletteController(model)
    first = controller.open("已移动")

    assert not first.results[0].availability.enabled
    assert first.results[0].disabled_reason == "最近工程已移动或不存在"

    stale = False
    refreshed = controller.set_query("已移动")
    assert refreshed.results[0].availability.enabled
    assert refreshed.revision > first.revision


def test_disappeared_dynamic_result_cannot_activate_from_an_old_snapshot() -> None:
    candidates = [
        DynamicCommandCandidate(
            "project:one",
            CommandCandidateKind.RECENT_PROJECT,
            "继续 One 工程",
            IntentId.PROJECT_OPEN,
        )
    ]
    controller = CommandPaletteController(CommandPaletteModel(_availability, dynamic_source=lambda: tuple(candidates)))
    old = controller.open("One")
    old_result_id = old.results[0].result_id
    candidates.clear()

    activation = controller.activate(old_result_id)

    assert activation.request is None
    assert activation.blocked_reason == "搜索结果已失效，请重新搜索"


def test_context_help_uses_user_terms_and_stays_in_current_context() -> None:
    controller = ContextHelpController(DEFAULT_CONTEXT_HELP)

    matches = controller.search("ESP")
    state = controller.show(matches[0].topic_id, context_identity="project:demo/content:main")

    assert matches[0].title == "插件"
    assert "真实" in state.topic.purpose
    assert state.topic.when_to_use
    assert state.context_identity == "project:demo/content:main"
    assert not hasattr(controller, "navigate")
    controller.close()
    assert controller.current is None


def test_context_help_can_be_resolved_from_the_current_intent() -> None:
    controller = ContextHelpController(DEFAULT_CONTEXT_HELP)

    state = controller.show_for_intent(
        IntentId.TRANSLATION_AI,
        context_identity="project:demo/content:main",
    )

    assert state.topic.title == "AI 自动翻译"
    assert "何时" not in state.topic.when_to_use
    assert "翻译内容" in state.topic.purpose


def test_qt_palette_emits_one_request_and_closes_its_controller() -> None:
    controller = CommandPaletteController(CommandPaletteModel(_availability))
    dialog = CommandPaletteDialog(controller)
    requests: list[object] = []
    dialog.intent_requested.connect(requests.append)
    dialog.open_palette("关于")
    item = dialog._results.item(0)

    dialog._results.itemActivated.emit(item)
    QApplication.processEvents()

    assert len(requests) == 1
    assert requests[0].intent_id is IntentId.HELP_ABOUT
    assert not controller.is_open
    dialog.close()


def test_qt_views_have_accessible_search_help_and_explicit_close_lifecycle() -> None:
    palette_controller = CommandPaletteController(CommandPaletteModel(_availability))
    dialog = CommandPaletteDialog(palette_controller)
    dialog.open_palette()
    assert dialog._search.accessibleName() == "命令搜索"
    assert not dialog.findChildren(QShortcut)
    dialog.close()
    assert not palette_controller.is_open

    help_controller = ContextHelpController(DEFAULT_CONTEXT_HELP)
    panel = ContextHelpPanel(help_controller)
    state = panel.show_topic("task", context_identity="workbench:current")
    assert state.topic.title == "任务"
    assert panel._purpose.accessibleName() == "用途说明"
    assert panel._when.text().startswith("何时使用：")
    panel.close()
    assert help_controller.current is None


def test_context_help_is_reused_as_readable_overlay_without_resizing_main_content() -> None:
    host = QMainWindow()
    host.context = SimpleNamespace(collection=object(), active_project_id="project:demo")
    central = QWidget()
    host.setCentralWidget(central)
    host.resize(1280, 720)
    host.show()
    _APP.processEvents()
    central_geometry = central.geometry()

    composition = ShellIntentComposition.__new__(ShellIntentComposition)
    composition._host = host
    composition._help_dock = None
    composition._help_overlay_host_geometry = None
    composition._show_context_help()
    _APP.processEvents()

    dock = composition._help_dock
    panel = dock.widget()
    assert dock.parent() is host
    assert dock.isFloating()
    assert dock.windowFlags() & Qt.WindowType.Window
    assert dock.allowedAreas() == Qt.DockWidgetArea.NoDockWidgetArea
    assert dock.features() == QDockWidget.DockWidgetFeature.DockWidgetClosable
    assert host.dockWidgetArea(dock) == Qt.DockWidgetArea.NoDockWidgetArea
    assert central.geometry() == central_geometry
    assert dock.size() == workspace_overlay_rect(host.rect()).size()
    assert panel._title.text() == "插件"
    assert panel._purpose.sizePolicy().verticalPolicy() is QSizePolicy.Policy.Maximum
    assert panel._when.y() - panel._purpose.geometry().bottom() <= panel.layout().spacing() + 1

    panel.close_requested.emit()
    _APP.processEvents()
    assert not dock.isVisible()
    host.context.collection = None
    host.context.active_project_id = None
    composition._show_context_help()
    _APP.processEvents()
    assert composition._help_dock is dock
    assert panel._title.text() == "本地翻译工程"
    assert dock.isVisible()
    assert central.geometry() == central_geometry

    dock.close()
    dock.deleteLater()
    host.close()


def test_model_and_controller_modules_do_not_import_qt() -> None:
    from transbridge.ui.shell import command_palette, context_help

    assert not hasattr(command_palette, "QDialog")
    assert not hasattr(context_help, "QWidget")


def test_dynamic_payload_is_immutable() -> None:
    source_payload = {"project_id": "demo"}
    candidate = DynamicCommandCandidate(
        "project:demo",
        CommandCandidateKind.RECENT_PROJECT,
        "Demo",
        IntentId.PROJECT_OPEN,
        payload=source_payload,
    )
    source_payload["project_id"] = "changed"

    assert candidate.payload["project_id"] == "demo"
    try:
        candidate.payload["project_id"] = "other"  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("dynamic candidate payload must be immutable")
