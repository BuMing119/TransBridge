"""Browsing starts with usable terms; advanced actions retain explicit routes."""

from PyQt6.QtCore import QPoint
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from tests.ui.tools.terminology import test_workflow_routes as workflow_tests
from tests.ui.tools.terminology.test_workflow_routes import _wait
from transbridge.ui.tools.terminology.schemes_view import TerminologySchemesView
from transbridge.ui.tools.terminology.view_models import TerminologyArea
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBarState, TerminologyProfileChoice

project_runtime = workflow_tests.project_runtime
workbench = workflow_tests.workbench


@pytest.mark.parametrize("page", [TerminologyArea.VERSIONS, TerminologyArea.REPORTS])
@pytest.mark.parametrize("action", ["extract", "source", "edit"])
def test_shared_header_actions_reveal_their_destination_from_other_pages(workbench, page, action):
    window, harness = workbench
    harness.build()
    harness.publish()
    window._bind_existing_assets()
    _wait(lambda: window._workflow.state is not None and window._workflow.state.has_version)
    window.workspace.set_current_area(page)
    if action == "extract":
        window.schemes_view.extract_action.trigger()
        assert window.terms_view.build_details_button.isChecked()
    elif action == "source":
        window.schemes_view.source_action.trigger()
        assert window.terms_view.pages.currentWidget() is window.terms_view.source_browser
    else:
        window.schemes_view.edit_button.click()
        _wait(lambda: window.terms_view.pages.currentWidget() is window.draft_view)
    assert window.workspace.current_area() is TerminologyArea.TERMS


def test_empty_state_keeps_import_primary_and_hides_unavailable_editing():
    view = TerminologySchemesView()
    state = TerminologyProfileBarState(enabled=True, can_manage=True)
    view.render(state)
    assert view.import_button.isEnabled() and not view.import_button.isHidden()
    assert view.edit_button.isHidden()
    assert view.metadata.isHidden()
    assert view.extract_action.text() == "从项目提取术语…"
    view.set_project_editable(True)
    assert not view.edit_button.isHidden()
    assert view.edit_button.text() == "编辑当前术语…"
    view.render(
        TerminologyProfileBarState(
            enabled=True,
            can_manage=True,
            selected_profile_id="scheme",
            choices=(TerminologyProfileChoice("scheme", "社区译名"),),
        )
    )
    view.set_project_editable(False)
    assert not view.edit_button.isHidden()
    assert view.edit_button.text() == "编辑当前方案…"
    assert view.source_action.isEnabled()
    view.close()


@pytest.mark.parametrize("width,height", [(920, 620), (1180, 760)])
def test_browsing_header_keeps_import_and_table_space_at_supported_sizes(workbench, layout_app, width, height):
    window, _harness = workbench
    window._bind_existing_assets()
    _wait(lambda: window._workflow.state is not None)
    window.workspace.set_context("天际特别版翻译工程", "简体中文", None)
    window.schemes_view.render(TerminologyProfileBarState(enabled=True, can_manage=True))
    window.resize(width, height)
    window.show()
    settle_layout(layout_app)
    assert window.width() == width
    assert window.terms_view.workflow.isHidden()
    assert window.terms_view.content_context.isHidden()
    assert window.terms_view._project_panel.isHidden()
    assert "0 个" not in window.workspace.project_caption.toolTip()
    for widget in (window.schemes_view.scheme_combo, window.schemes_view.import_button, window.terms_view.pages):
        origin = widget.mapTo(window, QPoint())
        assert 0 <= origin.x() < width and 0 <= origin.y() < height
        assert origin.x() + widget.width() <= width
        assert origin.y() + widget.height() <= height
    assert window.terms_view.pages.height() > height / 2


def test_normal_browsing_does_not_show_extraction_failure_until_requested(workbench):
    from transbridge.ui.tools.terminology.view_models import TerminologyPreflightViewState

    window, _harness = workbench
    window._preflight_ready(
        TerminologyPreflightViewState(
            ready=False,
            title="项目来源较多，暂不能提取",
            message="当前有51个来源，仍可直接导入已有术语。",
            project_display_name="真实工程",
            variant_display_name="简体中文",
            source_count=51,
        )
    )
    assert window.workspace.project_name.text() == "真实工程"
    assert window.workspace.project_caption.toolTip() == "51 个来源"
    assert window.terms_view._project_panel.isHidden()
    assert window.operation_notice.isHidden()
    window.schemes_view.extract_action.trigger()
    assert not window.terms_view._project_panel.isHidden()
    assert "51" in window.build_view.message.text()
