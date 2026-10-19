from __future__ import annotations

from datetime import UTC, datetime
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QWidget
import pytest

from tests.ui.workbench.terminology_async_support import wait_preview, wait_profile
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfileContent,
    TerminologyProfileService,
    TerminologySourceEntry,
    TerminologySourceSnapshot,
)
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.tools.ai_translator.naming_scheme_controller import AiNamingSchemeBinding
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBar
from transbridge.ui.workbench.terminology_profile_controller import TerminologyProfileUiController
from transbridge.ui.workbench.translation_table import COL_KEY, COL_TRANSLATION

_APP = QApplication.instance() or QApplication([])


class _Config:
    token = ""


class _ProfileContext(QObject):
    project_changed = pyqtSignal()
    variant_changed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.active_version_identity = ("project-a", "variant-a")


class _Preview(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.profile = None

    def set_terminology_profile(self, profile, *, base_snapshot=None) -> None:
        self.profile = profile
        self.base_snapshot = base_snapshot


class _Factory:
    def __init__(self, service, base_snapshot=None) -> None:
        self.service = service
        self.base_snapshot = base_snapshot
        self.projects = []

    def profile_service_for(self, project_id: str):
        self.projects.append(project_id)
        return self.service

    def base_terminology_snapshot(self, project_id: str, variant_id: str):
        assert (project_id, variant_id) == ("project-a", "variant-a")
        return self.base_snapshot


def _published(profile_id: str, name: str, target: str) -> PublishedTerminologyProfile:
    content = TerminologyProfileContent((ProfileTermMapping("Dragon", target, "龙"),))
    return PublishedTerminologyProfile(
        profile_id=profile_id,
        project_id="project-a",
        revision=1,
        name=name,
        content_digest=content.content_digest,
        content=content,
        published_at=datetime.now(UTC).isoformat(),
    )


def _base_for(profile: PublishedTerminologyProfile) -> EffectiveTerminologySnapshot:
    return EffectiveTerminologySnapshot(
        profile.project_id,
        "variant-a",
        EffectiveSnapshotStatus.READY,
        version_id="base-v1",
        content_digest="b" * 64,
        decisions=tuple(
            TermDecision(
                str(index),
                profile.project_id,
                "variant-a",
                mapping.original,
                mapping.original.casefold(),
                mapping.base_translation,
                TermScope.project() if mapping.plugin_id is None else TermScope.plugin(mapping.plugin_id),
                DecisionStatus.ADOPTED,
            )
            for index, mapping in enumerate(profile.content.mappings)
        ),
    )


def _service_with_profile() -> tuple[TerminologyProfileService, str]:
    identifiers = iter(("official-profile", "draft-profile"))
    service = TerminologyProfileService(
        InMemoryTerminologyProfileRepository(),
        new_id=lambda: next(identifiers),
    )
    profile = service.create("project-a", "官中")
    content = TerminologyProfileContent((ProfileTermMapping("Dragon", "巨龙", "龙"),))
    saved = service.save_draft(profile.profile_id, content, expected_revision=0)
    service.publish(profile.profile_id, expected_draft_revision=saved.draft_revision)
    return service, profile.profile_id


def test_controller_lists_only_published_profiles_and_persists_selection() -> None:
    service, profile_id = _service_with_profile()
    service.create("project-a", "未发布草稿")
    context = _ProfileContext()
    bar = TerminologyProfileBar()
    preview = _Preview()
    controller = TerminologyProfileUiController(context, _Factory(service), bar, preview)
    wait_profile(controller)

    assert bar.combo.count() == 2
    assert bar.combo.itemData(0) is None
    assert bar.combo.itemText(0) == "使用项目默认译名"
    assert bar.combo.itemData(1) == profile_id
    assert bar.combo.itemText(1) == "官中"
    assert "r1" not in bar.combo.itemText(1)
    assert bar.manage_button.isEnabled()
    assert preview.profile is None
    assert controller.state.selected_revision is None

    ai_controls = type("Controls", (), {})()
    ai_controls.naming_scheme_combo = QComboBox()
    ai_controls.naming_scheme_manage_btn = QPushButton()
    ai_controls.naming_scheme_status_label = QLabel()
    refreshes = []
    binding = AiNamingSchemeBinding(controller, ai_controls, lambda: refreshes.append(True))
    assert ai_controls.naming_scheme_combo.currentText() == "保持当前译名"

    ai_controls.naming_scheme_combo.setCurrentIndex(1)
    assert "正在切换" in ai_controls.naming_scheme_status_label.text()
    assert refreshes == []
    wait_profile(controller)
    selected = service.selected_revision("project-a", "variant-a")
    assert selected is not None and selected.profile_id == profile_id
    assert preview.profile == selected
    assert bar.combo.currentData() == profile_id
    assert "本次采用“官中”" in ai_controls.naming_scheme_status_label.text()
    assert refreshes == [True]

    bar.combo.setCurrentIndex(0)
    wait_profile(controller)
    assert service.selected_revision("project-a", "variant-a") is None
    assert preview.profile is None
    assert ai_controls.naming_scheme_combo.currentData() is None
    controller.deleteLater()
    binding.deleteLater()
    ai_controls.naming_scheme_combo.close()
    ai_controls.naming_scheme_manage_btn.close()
    ai_controls.naming_scheme_status_label.close()
    bar.close()
    preview.close()


def test_failed_switch_retains_exact_published_rows_and_exposes_failure(monkeypatch) -> None:
    service, profile_id = _service_with_profile()
    service.select("project-a", "variant-a", profile_id)
    context = _ProfileContext()
    bar = TerminologyProfileBar()
    preview = _Preview()
    controller = TerminologyProfileUiController(context, _Factory(service), bar, preview)
    wait_profile(controller)
    original = controller.state.selected_revision
    assert original is not None

    def fail(*_args):
        raise PermissionError("文件只读")

    monkeypatch.setattr(service, "clear_selection", fail)
    controller.select(None)
    wait_profile(controller)

    assert controller.state.selected_revision == original
    assert controller.state.selected_profile_id == profile_id
    assert controller.state.selection_error == "切换失败：文件只读"
    assert preview.profile == original
    controller.deleteLater()
    bar.close()
    preview.close()


def test_controller_creates_published_scheme_from_source_without_selecting_it() -> None:
    service = TerminologyProfileService(
        InMemoryTerminologyProfileRepository(),
        new_id=lambda: "source-profile",
    )
    decision = TermDecision(
        "whiterun",
        "project-a",
        "variant-a",
        "Whiterun",
        "whiterun",
        "雪漫城",
        TermScope.project(),
        DecisionStatus.ADOPTED,
    )
    base = EffectiveTerminologySnapshot(
        "project-a",
        "variant-a",
        EffectiveSnapshotStatus.READY,
        version_id="base-v1",
        content_digest="b" * 64,
        decisions=(decision,),
    )
    context = _ProfileContext()
    bar = TerminologyProfileBar()
    view = _Preview()
    factory = _Factory(service, base)
    controller = TerminologyProfileUiController(context, factory, bar, view)
    wait_profile(controller)
    source = TerminologySourceSnapshot.capture(
        "json",
        "本地 JSON",
        (TerminologySourceEntry("Whiterun", "白漫城"),),
    )

    preview = controller.preview_source_import(source)
    result = controller.create_from_source_import("白漫方案", preview)
    wait_profile(controller)

    assert result.published.content.mappings[0].translation == "白漫城"
    assert result.selection is None
    assert service.selected_revision("project-a", "variant-a") is None
    assert bar.combo.findData("source-profile") >= 0

    factory.base_snapshot = EffectiveTerminologySnapshot(
        "project-a",
        "variant-a",
        EffectiveSnapshotStatus.READY,
        version_id="base-v2",
        content_digest="c" * 64,
        decisions=(decision,),
    )
    with pytest.raises(RuntimeError, match="发生了变化"):
        controller.create_from_source_import("过期方案", preview)
    controller.deleteLater()
    bar.close()
    view.close()


def test_profile_switch_projects_read_only_text_without_mutating_common_entry(monkeypatch) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    entry = TranslationEntry("one", "key-one", "Dragon arrives", "龙来了", 1, "NPC_:FULL")
    collection = TranslationEntryCollection((entry,))
    widget = Step2PreviewWidget(context_module.AppContext())
    widget.refresh(collection)
    _APP.processEvents()

    official = _published("official", "官中", "巨龙")
    community = _published("community", "社区版", "神龙")
    widget.set_terminology_profile(official, base_snapshot=_base_for(official))
    assert widget._terminology_preview.pending
    pending = widget._table.model().index(0, COL_TRANSLATION)
    assert pending.data(Qt.ItemDataRole.DisplayRole) == "龙来了"
    assert "正在生成方案预览" in pending.data(Qt.ItemDataRole.ToolTipRole)
    assert not pending.flags() & Qt.ItemFlag.ItemIsEditable
    wait_preview(widget)
    item = widget._table.model().index(0, COL_TRANSLATION)
    assert item.data(Qt.ItemDataRole.DisplayRole) == "巨龙来了"
    assert not item.flags() & Qt.ItemFlag.ItemIsEditable
    assert entry.translation == "龙来了"
    widget.set_editable_entry_keys((entry.identity,))
    assert not widget._table.activate_entry_editor(widget._table.model().index(0, COL_KEY))

    assert not widget._table.model().setData(item, "错误写回", Qt.ItemDataRole.EditRole)
    _APP.processEvents()
    assert item.data(Qt.ItemDataRole.DisplayRole) == "巨龙来了"
    assert entry.translation == "龙来了"

    widget.set_terminology_profile(community, base_snapshot=_base_for(community))
    wait_preview(widget)
    assert widget._table.model().index(0, COL_TRANSLATION).data(Qt.ItemDataRole.DisplayRole) == "神龙来了"
    widget.set_terminology_profile(official, base_snapshot=_base_for(official))
    wait_preview(widget)
    assert widget._table.model().index(0, COL_TRANSLATION).data(Qt.ItemDataRole.DisplayRole) == "巨龙来了"
    assert entry.translation == "龙来了"

    unlocatable = _published("unlocatable", "待确认", "飞龙")
    unlocatable_content = TerminologyProfileContent((ProfileTermMapping("Dragon", "飞龙", "不存在"),))
    unlocatable = PublishedTerminologyProfile(
        profile_id=unlocatable.profile_id,
        project_id=unlocatable.project_id,
        revision=unlocatable.revision,
        name=unlocatable.name,
        content_digest=unlocatable_content.content_digest,
        content=unlocatable_content,
        published_at=unlocatable.published_at,
    )
    widget.set_terminology_profile(unlocatable, base_snapshot=_base_for(unlocatable))
    wait_preview(widget)
    item = widget._table.model().index(0, COL_TRANSLATION)
    assert item.data(Qt.ItemDataRole.DisplayRole) == "龙来了"
    assert "无法安全定位" in item.data(Qt.ItemDataRole.ToolTipRole)

    widget.set_terminology_profile(None)
    _APP.processEvents()
    item = widget._table.model().index(0, COL_TRANSLATION)
    assert item.data(Qt.ItemDataRole.DisplayRole) == "龙来了"
    assert item.flags() & Qt.ItemFlag.ItemIsEditable
    assert widget._table.activate_entry_editor(widget._table.model().index(0, COL_KEY))
    widget.close()


def test_plugin_scoped_mapping_uses_entry_plugin_identity_and_disables_common_reset(monkeypatch) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    entry = TranslationEntry(
        "one",
        "key-one",
        "Dragon arrives",
        "龙来了",
        1,
        "NPC_:FULL",
        form_id_with_plugin="00000001|Skyrim.esm",
    )
    widget = Step2PreviewWidget(context_module.AppContext())
    widget.refresh(TranslationEntryCollection((entry,)))
    content = TerminologyProfileContent((
        ProfileTermMapping("Dragon", "天际巨龙", "龙", scope_kind="plugin", plugin_id="Skyrim.esm"),
    ))
    profile = PublishedTerminologyProfile(
        profile_id="plugin-profile",
        project_id="project-a",
        revision=1,
        name="插件范围",
        content_digest=content.content_digest,
        content=content,
        published_at=datetime.now(UTC).isoformat(),
    )

    widget.set_terminology_profile(profile, base_snapshot=_base_for(profile))
    wait_preview(widget)

    assert widget._table.model().index(0, COL_TRANSLATION).data(Qt.ItemDataRole.DisplayRole) == "天际巨龙来了"
    menu = widget._build_context_menu(0)
    reset = next(action for action in menu.actions() if action.text() == "取消翻译")
    assert not reset.isEnabled()
    assert entry.translation == "龙来了"
    menu.close()
    widget.close()


def test_name_only_publication_refreshes_selector_current_description_and_preview():
    service, profile_id = _service_with_profile()
    service.select("project-a", "variant-a", profile_id)
    historical = service.selected_revision("project-a", "variant-a")
    bar, preview, context = TerminologyProfileBar(), _Preview(), _ProfileContext()
    controller = TerminologyProfileUiController(context, _Factory(service), bar, preview)
    wait_profile(controller)
    service.rename(profile_id, "新名称")
    controller.refresh()
    wait_profile(controller)
    assert bar.combo.currentText() == "新名称（名称未发布）"
    review = service.preview_publication(
        profile_id, historical.content, expected_revision=1, target_variant_id="variant-a"
    )
    service.publish_reviewed(review)
    controller.refresh()
    wait_profile(controller)
    assert bar.combo.currentText() == "新名称"
    assert "新名称" in controller.state.detail
    assert "官中" not in controller.state.detail
    assert preview.profile.name == "新名称"
    assert service.published_revision(profile_id, 1).name == "官中"
    bar.close()
    preview.close()
