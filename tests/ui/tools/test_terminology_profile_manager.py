from __future__ import annotations

from itertools import cycle
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QInputDialog

from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfileEntryOverride,
    ProfileOccurrenceBinding,
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileService,
)
from transbridge.ui.tools.terminology_profiles import TerminologyProfileManagerDialog

_APP = QApplication.instance() or QApplication([])


def _service() -> tuple[TerminologyProfileService, str, TerminologyProfileContent]:
    identifiers = cycle(("profile-a", "profile-b", "profile-c"))
    service = TerminologyProfileService(
        InMemoryTerminologyProfileRepository(),
        new_id=lambda: next(identifiers),
    )
    profile = service.create("project-a", "官中")
    mapping = ProfileTermMapping("Dragon", "巨龙", "龙")
    content = TerminologyProfileContent(
        mappings=(mapping,),
        overrides=(ProfileEntryOverride("entry-a", "条目特例"),),
        bindings=(ProfileOccurrenceBinding("entry-a", mapping.term_key, 0, 1, "龙"),),
    )
    service.save_draft(profile.profile_id, content, expected_revision=0)
    return service, profile.profile_id, content


def test_manager_saves_mapping_without_dropping_overrides_or_bindings_and_publishes() -> None:
    service, profile_id, original = _service()
    dialog = TerminologyProfileManagerDialog(service, "project-a")
    target = dialog.mapping_table.item(0, 2)
    target.setText("神龙")

    dialog._save_draft()
    profile = next(item for item in service.list_profiles("project-a") if item.profile_id == profile_id)
    assert profile.draft.mappings[0].translation == "神龙"
    assert profile.draft.overrides == original.overrides
    assert profile.draft.bindings == original.bindings

    dialog.mapping_table.item(0, 2).setText("远古巨龙")
    dialog._publish()
    service.select("project-a", "variant-a", profile_id)
    published = service.selected_revision("project-a", "variant-a")
    assert published is not None
    assert published.content.mappings[0].translation == "远古巨龙"
    assert published.content.overrides == original.overrides
    assert published.content.bindings == original.bindings
    profile = next(item for item in service.list_profiles("project-a") if item.profile_id == profile_id)
    assert profile.latest_published_revision == 1
    assert dialog.windowTitle() == "管理译名方案"
    assert "可选择" in dialog.profile_combo.currentText()
    assert "r1" not in dialog.profile_combo.currentText()
    assert dialog.save_button.text() == "保存修改"
    assert dialog.publish_button.text() == "应用修改"
    assert [dialog.mapping_table.horizontalHeaderItem(index).text() for index in range(3)] == [
        "原文术语",
        "当前译文中的叫法",
        "此方案采用的译名",
    ]
    dialog.close()
    _APP.processEvents()


def test_manager_create_copy_rename_and_archive(monkeypatch) -> None:
    service, profile_id, _content = _service()
    dialog = TerminologyProfileManagerDialog(service, "project-a")
    answers = iter((("社区版", True), ("社区版副本", True), ("社区修订版", True)))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: next(answers))

    dialog._create_profile()
    created = next(item for item in service.list_profiles("project-a") if item.name == "社区版")
    assert created.latest_published_revision is None

    dialog._copy_profile()
    copied = next(item for item in service.list_profiles("project-a") if item.name == "社区版副本")
    assert copied.draft == created.draft

    dialog._rename_profile()
    assert any(item.name == "社区修订版" for item in service.list_profiles("project-a"))
    dialog._archive_profile()
    remaining = service.list_profiles("project-a")
    assert all(item.name != "社区修订版" for item in remaining)
    assert any(item.profile_id == profile_id for item in remaining)
    dialog.close()
    _APP.processEvents()
