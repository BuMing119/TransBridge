from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
import pytest

from transbridge.application.io import FormatId
from transbridge.config.ui_preferences import GuidanceMode, UiPreferenceSaveResult, UiPreferenceSnapshot
from transbridge.converter.translation_entry import STAGE_QUESTIONABLE, STAGE_UNTRANSLATED
from transbridge.ui.guidance.controller import GuidanceController
from transbridge.ui.guidance.models import GuidanceContextIdentity, GuidanceKind, GuidanceProjection
from transbridge.ui.guidance.qt import GuidanceBanner, GuidanceBinding
from transbridge.ui.project_rows import ProjectRowsCollection
from transbridge.ui.shell.action_catalog import IntentId


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Context(QObject):
    project_changed = pyqtSignal()
    variant_changed = pyqtSignal(str)
    collection_changed = pyqtSignal(object)
    collection_list_changed = pyqtSignal()
    dirty_changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.active_project_id = None
        self.project_name = None
        self.active_variant_id = None
        self.active_variant = None
        self.active_key = None
        self.collection = None


class _Preferences:
    def __init__(self) -> None:
        self.mode = GuidanceMode.AUTO

    def load(self):
        return UiPreferenceSnapshot(self.mode, 1)

    def save_guidance_mode(self, mode):
        self.mode = mode
        return UiPreferenceSaveResult(mode, True, UiPreferenceSnapshot(mode, 2))


def test_binding_projects_context_events_and_dispatches_one_intent(qapp):
    context = _Context()
    view = GuidanceBanner()
    view.resize(1200, 70)
    dispatched = []
    preferences = _Preferences()
    preferences.mode = GuidanceMode.GUIDED
    binding = GuidanceBinding(context, view, dispatched.append, preferences=preferences)

    assert view.accessibleName() == "当前任务引导"
    assert view.isVisible()
    assert view._primary.accessibleName()
    assert view.findChild(type(view._headline), "guidance-headline").text() == "新建本地翻译工程"
    context.active_project_id = "project-1"
    context.project_name = "Demo"
    context.active_variant_id = "variant-1"
    context.active_key = "content-1"
    context.collection = [SimpleNamespace(stage=STAGE_UNTRANSLATED)]
    context.project_changed.emit()
    assert view._headline.text() == "开始翻译未完成的内容"

    view._primary.click()
    view._primary.click()
    assert dispatched == [IntentId.TRANSLATION_AI_RUN]

    context.collection = [SimpleNamespace(stage=STAGE_QUESTIONABLE)]
    context.collection_changed.emit(context.collection)
    assert view._headline.text() == "检查翻译问题"
    binding.close()
    view.close()


def test_compact_mode_keeps_primary_capability(qapp):
    context = _Context()
    view = GuidanceBanner()
    preferences = _Preferences()
    binding = GuidanceBinding(context, view, lambda _intent: None, preferences=preferences)
    primary = view._primary.text()

    view.mode_requested.emit(GuidanceMode.COMPACT.value)

    assert preferences.mode is GuidanceMode.COMPACT
    assert view.isVisible()
    assert view._primary.text() == primary
    assert view._primary.isEnabled()
    binding.close()
    view.close()


def test_auto_banner_releases_layout_space_and_reappears_for_failures(qapp):
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setSpacing(8)
    header = QLabel("工程")
    layout.addWidget(header)
    view = GuidanceBanner(host)
    layout.addWidget(view)
    table = QLabel("词条")
    layout.addWidget(table, 1)
    controller = GuidanceController(view.render, lambda _intent: None)
    identity = GuidanceContextIdentity(project_id="project")
    host.resize(750, 450)
    host.show()
    try:
        controller.project(GuidanceProjection(identity, 0, 0, GuidanceKind.REVIEW_PENDING))
        qapp.processEvents()
        compact_y = table.y()
        assert view.isHidden()
        assert not view._collapse.isVisibleTo(host)
        assert compact_y == header.y() + header.height() + layout.spacing()

        controller.project(GuidanceProjection(identity, 0, 1, GuidanceKind.FAILED, reason="任务写入失败"))
        qapp.processEvents()
        assert view.isVisibleTo(host)
        assert "任务写入失败" in view._reason.text()
        assert table.y() > compact_y

        controller.project(GuidanceProjection(identity, 0, 2, GuidanceKind.PUBLISH_PENDING))
        qapp.processEvents()
        assert view.isHidden()
        assert table.y() == compact_y
    finally:
        host.close()


def test_paged_guidance_uses_current_statistics_without_iterating_sources(qapp):
    class Paging(QObject):
        statistics_ready = pyqtSignal()
        counts = None

        def current_stage_counts(self, _collection):
            return self.counts

    context = _Context()
    context.project_name = "Demo"
    context.active_project_id = "project-1"
    context.active_variant_id = "variant-1"
    context.active_key = "source-1"
    reader = SimpleNamespace(
        token=SimpleNamespace(
            project_id=SimpleNamespace(value="project-1"),
            variant_id=SimpleNamespace(value="variant-1"),
            variant_revision=1,
        ),
        full_rows=lambda **_: pytest.fail("Guidance must not iterate project rows"),
    )
    source = SimpleNamespace(preview=(), count=1, info=SimpleNamespace(format_id=FormatId.PLUGIN_SSE))
    context.collection = ProjectRowsCollection(reader, source)
    view = GuidanceBanner()
    paging = Paging()
    binding = GuidanceBinding(context, view, lambda _: None, paging=paging)
    assert view.property("tbStatusId") == "loading_content"
    assert not view._primary.isEnabled()

    paging.counts = Counter({STAGE_QUESTIONABLE: 1})
    paging.statistics_ready.emit()
    assert view.property("tbStatusId") == "review_pending"

    context.active_variant_id = "variant-2"
    binding.refresh()
    assert view.property("tbStatusId") == "loading_content"
    assert not view._primary.isEnabled()
    binding.close()
