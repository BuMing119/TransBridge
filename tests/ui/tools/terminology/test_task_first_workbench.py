"""Daily terminology tasks keep advanced tables in separate windows."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.ui.tools.terminology import test_workflow_routes as workflow_tests
from tests.ui.tools.terminology.test_workflow_guidance import _assets
from tests.ui.tools.terminology.test_workflow_routes import _wait
from transbridge.application.terminology.models import DecisionStatus, TermDecision
from transbridge.ui.tools.terminology.workflow_state import TerminologyWorkflowState

project_runtime = workflow_tests.project_runtime
workbench = workflow_tests.workbench


def test_tables_are_only_shown_after_explicit_advanced_entry(workbench):
    window, _harness = workbench
    window.show()
    terms = window.terms_view
    assert not terms.editor_dialog.isVisible()
    assert not terms.conflict_dialog.isVisible()
    assert not window.effective_view.isVisible()
    assert not window.draft_view.isVisible()
    assert not window.conflicts_view.isVisible()
    terms.browse_button.click()
    assert terms.editor_dialog.isVisible()
    assert window.effective_view.isVisible()
    assert window.effective_view.window() is terms.editor_dialog
    assert not terms.conflict_dialog.isVisible()
    terms.editor_dialog.close()
    assert not window.effective_view.isVisible()
    assert window.isVisible()


@pytest.mark.parametrize("conflicts", [0, 2])
def test_build_completion_uses_fresh_counts_and_opens_conflicts_only_once(workbench, conflicts):
    window, _harness = workbench
    terms = window.terms_view
    window._workflow.render(_assets())
    window._workflow.build_completed()
    assert not terms.conflict_dialog.isVisible()
    window._workflow.render(_assets(draft=True, conflicts=conflicts))
    assert terms.conflict_dialog.isVisible() is bool(conflicts)
    assert not terms.editor_dialog.isVisible()
    assert not window._workflow.follow_build
    terms.conflict_dialog.close()
    window._workflow.render(_assets(draft=True, conflicts=conflicts))
    assert not terms.conflict_dialog.isVisible()
    if conflicts:
        terms.attention_button.click()
        assert terms.conflict_dialog.isVisible()


@pytest.mark.parametrize("confirmed", [False, True])
def test_main_discard_removes_only_pending_results(workbench, monkeypatch, confirmed):
    window, harness = workbench
    harness.build()
    published = harness.publish()
    pending = harness.build()
    window._bind_existing_assets()
    _wait(lambda: window._workflow.state is not None and window._workflow.state.has_draft)
    prompts = []

    def confirm(_parent, title, message, _accept):
        prompts.append((title, message))
        return confirmed

    monkeypatch.setattr("transbridge.ui.tools.terminology.version_interactions._confirm", confirm)
    window.terms_view.discard_button.click()
    _wait(lambda: bool(prompts))
    project_id, variant_id = harness.context.project_id, harness.context.variant_id
    if confirmed:
        _wait(lambda: not window._workflow.state.has_draft)
        assert harness.repository.active_draft(project_id, variant_id) is None
        assert not window.terms_view.discard_button.isEnabled()
    else:
        assert harness.repository.active_draft(project_id, variant_id).ref == pending.ref
    assert harness.repository.effective_version(project_id, variant_id).ref == published.ref
    assert prompts[0][0] == "丢弃本次结果"
    assert "当前生效术语和历史会保留" in prompts[0][1]


def test_history_and_reports_open_separately_without_replacing_main_task(workbench):
    window, _harness = workbench
    for index, page in ((1, window.versions_view), (2, window.reports_view)):
        window.workspace.set_current_index(index)
        assert window.workspace.pages.currentWidget() is window.terms_view
        assert page.window() is window.workspace._dialogs[index]
        assert page.window().isVisible()
        page.window().close()


def test_summary_compares_semantic_terms_without_counting_evidence_refresh_as_change():
    old = TermDecision("a", "p", "v", "Dragon", "dragon", "巨龙", status=DecisionStatus.ADOPTED)
    new = replace(old, term_id="b", original="Sword", normalized_original="sword", translation="剑")
    assets = replace(
        _assets(draft=True, version=True),
        draft=SimpleNamespace(decisions=(replace(old, evidence_ids=("new-evidence",)), new)),
        version=SimpleNamespace(decisions=(old,)),
    )
    state = TerminologyWorkflowState.from_assets(assets)
    assert "新增 1 条 · 无变化 1 条" in state.detail
    assert state.can_publish


def test_active_task_blocks_apply_and_discard_until_settled(workbench):
    window, _harness = workbench
    window._workflow.render(_assets(draft=True))
    window._workflow.set_task_busy(True)
    assert not window.terms_view.discard_button.isEnabled()
    assert not window.terms_view.workflow.publish_button.isEnabled()
    window._workflow.set_task_busy(False)
    assert window.terms_view.discard_button.isEnabled()
    assert window.terms_view.workflow.publish_button.isEnabled()
