"""State-specific actions and the draft/effective boundary remain visible together."""

from threading import Event
from types import SimpleNamespace

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from tests.ui.tools.terminology import test_workflow_routes as workflow_tests
from tests.ui.tools.terminology.test_workflow_routes import _wait
from transbridge.ui.tools.terminology.asset_snapshot import WorkbenchAssets


@pytest.fixture(name="workbench")
def guidance_workbench(tmp_path, monkeypatch):
    yield from workflow_tests.workbench.__wrapped__(tmp_path, monkeypatch)


def _assets(*, draft=False, version=False, complete="full", freshness="current", review=0, conflicts=0):
    return WorkbenchAssets(
        draft=SimpleNamespace(decisions=()) if draft else None,
        version_ref=SimpleNamespace(version_id="published-1") if version else None,
        build=SimpleNamespace(completeness=complete, freshness=freshness),
        review_counts=SimpleNamespace(review_required_terms=review, unresolved_conflicts=conflicts),
    )


@pytest.mark.parametrize(
    "assets,action,explanation",
    [
        (_assets(), "build", "生成草稿"),
        (_assets(version=True), "edit", "继续编辑"),
        (_assets(draft=True, complete="partial", review=2), "build", "重新整理来源"),
        (_assets(draft=True, freshness="stale", conflicts=1), "build", "重新整理来源"),
        (_assets(draft=True, conflicts=2, review=3), "conflicts", "处理异译"),
        (_assets(draft=True, review=3), "review", "复核术语"),
        (_assets(draft=True), "publish", "检查影响"),
        (_assets(draft=True, version=True), "publish", "检查影响"),
    ],
)
def test_only_the_next_required_action_is_primary(workbench, assets, action, explanation):
    window, _harness = workbench
    window._workflow.render(assets)
    terms = window.terms_view
    actions = {
        "build": window.build_view.build_button,
        "edit": terms.workflow.edit_button,
        "conflicts": terms.attention_button,
        "review": terms.review_button,
        "publish": terms.workflow.publish_button,
    }
    assert [
        key for key, button in actions.items() if not button.isHidden() and button.property("tbTerminologyPrimary")
    ] == [action]
    assert explanation in terms.workflow.detail.text()
    assert explanation in window.versions_view.draft_status.text()
    assert "下一步" in terms.workflow.detail.text()
    assert terms.workflow.publish_button.isHidden() is (action != "publish")
    assert terms.workflow.publish_button.isEnabled() is (action == "publish")
    assert window.versions_view.continue_button.isHidden() is (action == "publish")


@pytest.mark.parametrize("entry", ["terms", "versions"])
def test_switching_content_explains_what_is_edited_and_when_it_takes_effect(workbench, layout_app, entry):
    window, harness = workbench
    harness.build()
    published = harness.publish()
    window._bind_existing_assets()
    _wait(lambda: window._workflow.state is not None and window._workflow.state.has_version)
    window.show()
    settle_layout(layout_app)
    terms = window.terms_view
    assert "当前生效" in terms.content_context.text()
    if entry == "versions":
        window.workspace.set_current_index(1)
        QTest.mouseClick(window.versions_view.continue_button, Qt.MouseButton.LeftButton)
    else:
        QTest.mouseClick(terms.workflow.edit_button, Qt.MouseButton.LeftButton)
    _wait(lambda: window._workflow.state.has_draft and window.draft_model.rowCount() == 1)
    assert "未发布草稿" in terms.content_context.text()
    assert "发布后" in terms.content_context.text()
    assert harness.repository.effective_version("project-1", "main").ref == published.ref
    QTest.mouseClick(terms.effective_button, Qt.MouseButton.LeftButton)
    assert "当前生效" in terms.content_context.text()
    QTest.mouseClick(terms.source_button, Qt.MouseButton.LeftButton)
    assert "方案映射" in terms.content_context.text()
    assert "当前生效" in terms.content_context.text()


@pytest.mark.parametrize(
    "assets,page",
    [
        (_assets(draft=True, freshness="stale"), 0),
        (_assets(draft=True, conflicts=2), 2),
        (_assets(draft=True, review=3), 1),
    ],
)
def test_version_page_routes_to_the_blocking_step_without_building_or_publishing(workbench, layout_app, assets, page):
    window, harness = workbench
    original = harness.build()
    original_request = harness.submit.request
    window._workflow.render(assets)
    window.draft_view.filters.search.setText("unrelated search")
    window.workspace.set_current_index(1)
    window.show()
    settle_layout(layout_app)
    QTest.mouseClick(window.versions_view.continue_button, Qt.MouseButton.LeftButton)
    assert window.workspace.pages.currentWidget() is window.terms_view
    assert window.terms_view.pages.currentIndex() == page
    if window._workflow.state.next_action == "build":
        assert window.terms_view.build_details_button.isChecked()
    elif window._workflow.state.next_action == "review":
        assert window.draft_view.filters.search.text() == ""
    assert harness.repository.active_draft("project-1", "main").ref == original.ref
    assert harness.repository.effective_version("project-1", "main") is None
    assert harness.submit.request is original_request


def test_saving_explains_the_publish_blocker_until_the_write_finishes(workbench, layout_app):
    window, harness = workbench
    draft = harness.build()
    window._bind_existing_assets()
    _wait(lambda: window._workflow.state is not None and window._workflow.state.can_publish)
    window.show()
    release = Event()

    def save():
        assert release.wait(3)
        return harness.commands.apply_decision(
            "change_translation", harness.context, term_id=draft.decisions[0].term_id, translation="新译名"
        )

    try:
        window._run_draft_command(save)
        settle_layout(layout_app)
        assert not window.terms_view.workflow.publish_button.isEnabled()
        assert not window.versions_view.publish_button.isEnabled()
        assert "正在保存" in window.terms_view.workflow.title.text()
        assert "保存完成" in window.terms_view.workflow.detail.text()
        assert "正在保存" in window.versions_view.draft_status.text()
        assert window.versions_view.continue_button.isHidden()
    finally:
        release.set()
        _wait(lambda: window._draft_writes.pending_count == 0)
    _wait(lambda: window.terms_view.workflow.publish_button.isEnabled())
    assert "正在保存" not in window.terms_view.workflow.title.text()
    assert harness.repository.active_draft("project-1", "main").decisions[0].translation == "新译名"
    assert harness.repository.effective_version("project-1", "main") is None


@pytest.mark.parametrize("width", [920, 1180])
def test_guidance_and_required_actions_fit_the_supported_narrow_window(workbench, layout_app, width):
    window, _harness = workbench
    window._workflow.render(_assets(draft=True, version=True, review=13, conflicts=8))
    window.resize(width, 620)
    window.show()
    settle_layout(layout_app)
    terms = window.terms_view
    for button, expected in ((terms.all_terms_button, "未发布草稿"), (terms.attention_button, "草稿中的异译")):
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        settle_layout(layout_app)
        assert expected in terms.content_context.text()
        assert window.width() == width
        for widget in (terms.workflow, terms.content_context, terms.review_button, terms.attention_button, terms.pages):
            top_left = widget.mapTo(window, QPoint())
            assert top_left.x() >= 0 and top_left.y() >= 0
            assert top_left.x() + widget.width() <= window.width()
            assert top_left.y() + widget.height() <= window.height()
        for label in (terms.workflow.title, terms.workflow.detail, terms.content_context):
            assert label.height() >= label.heightForWidth(label.width())
        assert not terms.review_button.geometry().intersects(terms.attention_button.geometry())
    window.workspace.set_current_index(1)
    settle_layout(layout_app)
    versions = window.versions_view
    assert versions.draft_status.height() >= versions.draft_status.heightForWidth(versions.draft_status.width())
    controls = (versions.continue_button, versions.publish_button, versions.discard_button)
    for button in controls:
        top_left = button.mapTo(window, QPoint())
        assert 0 <= top_left.x() <= window.width() - button.width()
        assert 0 <= top_left.y() <= window.height() - button.height()
    assert not controls[0].geometry().intersects(controls[1].geometry())
    assert not controls[1].geometry().intersects(controls[2].geometry())
