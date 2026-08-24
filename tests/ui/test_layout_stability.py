from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QComboBox, QMainWindow

from transbridge.config.ui_preferences import GuidanceMode
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui import context as context_module
from transbridge.ui.guidance.models import GuidanceContextIdentity, GuidanceKind, GuidanceProjection
from transbridge.ui.guidance.presentation import present_guidance
from transbridge.ui.guidance.qt import GuidanceBanner
from transbridge.ui.guidance.state_machine import build_guidance_state
from transbridge.ui.shell.status_presenter import StatusPresenter
from transbridge.ui.workbench._project_bar import ProjectBar
from transbridge.ui.workbench.filters_view import FiltersView
from transbridge.ui.workbench.progress_view import ProgressView
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.table_presenter import RenderSession
from transbridge.ui.workbench.translation_table import TranslationTable
from transbridge.ui.workbench.translation_table_columns import COL_KEY
from transbridge.ui.workbench.workflow_actions_view import WorkflowActionsView
from transbridge.ui.workbench.workflow_presenter import WorkbenchWorkflowPresenter

_APP = QApplication.instance() or QApplication([])


class _Signal:
    def connect(self, _callback) -> None:
        pass


class _ProjectContext:
    workspace_changed = _Signal()
    project_changed = _Signal()
    variant_changed = _Signal()
    uses_authoritative_projection = True
    active_project_id = "project-id"
    active_variant_id = "variant-id"
    project_sources = ()
    dirty = False

    def __init__(self) -> None:
        self.project_name = "短工程"
        self.project_variants = ({"id": "variant-id", "name": "默认"},)


class _StatusContext:
    user_changed = _Signal()
    project_selected = _Signal()


class _Config:
    token = ""


def _entry(key: str) -> TranslationEntry:
    return TranslationEntry(key, key, "Original", "译文", 1, "NPC_:FULL")


def test_project_bar_text_length_does_not_change_minimum_width() -> None:
    context = _ProjectContext()
    bar = ProjectBar(context)
    bar.resize(620, 40)
    bar.refresh()
    bar.show()
    _APP.processEvents()
    short_minimum = bar.minimumSizeHint().width()
    save_geometry = bar._save_btn.geometry()

    context.project_name = "非常长的本地工程名称" * 20
    context.project_variants = ({"id": "variant-id", "name": "非常长的翻译版本名称" * 20},)
    bar.refresh()
    bar._save_status.set_full_text("保存失败诊断" * 30)
    _APP.processEvents()

    assert bar.minimumSizeHint().width() == short_minimum
    assert bar._save_btn.geometry() == save_geometry
    assert bar._project_label.full_text == context.project_name
    bar.close()


def test_content_combo_policy_has_stable_size_hint_for_long_items() -> None:
    combo = QComboBox()
    combo.setMinimumWidth(180)
    combo.setMinimumContentsLength(16)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.addItem("短内容")
    short_hint = combo.sizeHint().width()
    combo.addItem("非常长的翻译内容名称" * 50)

    assert combo.sizeHint().width() == short_hint


def test_translation_render_preserves_user_key_column_width() -> None:
    table = TranslationTable(on_progress=lambda *_: None, on_batch=lambda: None)
    table.setColumnWidth(COL_KEY, 233)
    table.start_render(RenderSession(1, None, (_entry("short"),)), {}, {})
    assert table.columnWidth(COL_KEY) == 233

    table.start_render(RenderSession(2, None, (_entry("very-long-key-" * 80),)), {}, {})
    assert table.columnWidth(COL_KEY) == 233
    table.close()


def test_workflow_reason_does_not_move_action_buttons_or_change_height_hint() -> None:
    view = WorkflowActionsView()
    view.resize(1_000, 44)
    view.show()
    enabled = WorkbenchWorkflowPresenter.actions(
        has_context=True,
        visible_entries=10,
        needs_review=1,
        write_supported=True,
    )
    view.set_actions(enabled)
    _APP.processEvents()
    before = tuple(button.geometry() for button in view._buttons.values())
    before_height = view.sizeHint().height()

    disabled = WorkbenchWorkflowPresenter.actions(
        has_context=False,
        visible_entries=0,
        needs_review=0,
        write_supported=False,
    )
    view.set_actions(disabled)
    _APP.processEvents()

    assert tuple(button.geometry() for button in view._buttons.values()) == before
    assert view.sizeHint().height() == before_height
    assert "当前翻译内容没有可操作词条" in view._action_reason.full_text
    view.close()


def test_hidden_progress_view_retains_its_layout_slot() -> None:
    view = ProgressView()
    policy = view.sizePolicy()
    assert policy.retainSizeWhenHidden()
    assert policy.verticalPolicy() is policy.Policy.Fixed


def test_step2_render_progress_keeps_summary_vertical_position(monkeypatch) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    view = Step2PreviewWidget(context_module.AppContext())
    view.resize(1_000, 600)
    view.show()
    _APP.processEvents()
    before_y = view._summary_view.y()

    view._on_render_progress(1, 10)
    _APP.processEvents()
    active_y = view._summary_view.y()
    view._on_render_progress(10, 10)
    _APP.processEvents()

    assert view._progress.sizePolicy().retainSizeWhenHidden()
    assert active_y == before_y == view._summary_view.y()
    view.close()


def test_guidance_dynamic_copy_keeps_action_geometry() -> None:
    view = GuidanceBanner()
    view.resize(1_200, 72)
    view.show()
    short = build_guidance_state(GuidanceProjection(GuidanceContextIdentity(), 1, 1, GuidanceKind.NO_PROJECT))
    view.render(present_guidance(short, GuidanceMode.GUIDED))
    _APP.processEvents()
    before = (view._primary.geometry(), view._recovery.geometry(), view._collapse.geometry())

    long = build_guidance_state(
        GuidanceProjection(
            GuidanceContextIdentity(project_id="project"),
            2,
            1,
            GuidanceKind.MISSING_CONFIGURATION,
            reason="特别长的引导诊断" * 80,
            missing_configuration=("ParaTranz",),
        )
    )
    view.render(present_guidance(long, GuidanceMode.GUIDED))
    _APP.processEvents()

    assert (view._primary.geometry(), view._recovery.geometry(), view._collapse.geometry()) == before
    assert "特别长的引导诊断" in view._reason.full_text
    view.close()


def test_long_filter_label_is_bounded_and_keeps_full_tooltip() -> None:
    view = FiltersView(on_changed=lambda: None, on_manage_labels=lambda: None)
    entry = _entry("one")
    long_name = "特别长的用户标签名称" * 40
    view.build_labels(
        (entry,),
        {"long": {"name": long_name, "color": "#00aa77"}},
        {entry.id: {"long"}},
    )
    button = view._label_buttons[0]

    assert button.maximumWidth() == 196
    assert button.sizeHint().width() <= 196
    assert button.toolTip() == f"● {long_name} 1"
    assert button.text().endswith("…")
    view.close()


def test_status_bar_dynamic_text_has_fixed_geometry_budget() -> None:
    window = QMainWindow()
    presenter = StatusPresenter(window, _StatusContext())
    window.resize(1_000, 500)
    window.show()
    _APP.processEvents()
    before = (
        presenter.user_label.width(),
        presenter.project_label.width(),
        presenter.api_indicator.minimumWidth(),
    )

    presenter.render_user({"nickname": "超长用户名" * 50})
    presenter.render_project({"name": "超长项目名" * 50, "id": 123})
    presenter.api_indicator.on_request_started()
    _APP.processEvents()

    assert (
        presenter.user_label.width(),
        presenter.project_label.width(),
        presenter.api_indicator.minimumWidth(),
    ) == before
    assert presenter.user_label.full_text.startswith("用户: 超长用户名")
    assert presenter.project_label.full_text.startswith("项目: 超长项目名")
    presenter.close()
    window.close()
