"""Creation form behavior through the real draft coordinator and controller."""

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog
import pytest

from tests.ui import test_start_center_multi_plugin as source_fixtures

creation = source_fixtures.creation


def test_add_remove_sources_preserves_edited_project_fields_and_deduplicates(creation, monkeypatch):
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/mods/Base.esp")
    coordinator.set_project_name("My project")
    coordinator.set_variant_name("Chinese")
    coordinator.set_parse_option("skip_empty", True)
    form = creation.view._draft_page
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_: (["D:/mods/Base.esp", "D:/mods/extra.eet"], ""))
    form.add_sources.click()
    assert len(coordinator.state.source_paths) == 2
    assert form.source_list.count() == 2
    assert coordinator.state.project_name == "My project"
    assert coordinator.state.default_variant_name == "Chinese"
    assert dict(coordinator.state.parse_options)["skip_empty"]
    form.source_list.item(0).setSelected(True)
    form.remove_sources.click()
    assert coordinator.state.source_paths == (str(Path("D:/mods/extra.eet")),)
    assert coordinator.state.project_name == "My project"


def test_optional_translation_preserves_inputs_and_is_omitted_when_disabled(creation):
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/mods/Base.esp")
    form = creation.view._draft_page
    assert not form.translation_fields.isEnabled()
    assert form.translation_fields.graphicsEffect().opacity() < 1
    form.with_translations.setChecked(True)
    assert not form.primary.isEnabled()
    coordinator.set_translation_options(
        enabled=True, path="D:/translated.json", format_hint="json.dsd", replace_existing=True, strings_lang="chinese"
    )
    assert form.primary.isEnabled()
    form.with_translations.setChecked(False)
    assert coordinator.state.translation_path == "D:/translated.json"
    assert form.primary.isEnabled()
    form.with_translations.setChecked(True)
    assert form.translation_path.text() == "D:/translated.json"
    assert form.translation_format.currentData() == "json.dsd"
    assert form.replace_existing.isChecked()


def test_file_filters_choose_json_and_return_to_auto_on_another_file(creation, monkeypatch):
    choices = iter([(["D:/file.json"], "DSD JSON (*.json)"), (["D:/file.eet"], "支持的文件")])
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_: next(choices))
    creation.controller.choose_source()
    assert creation.controller.guided_project.state.source_format_hint == "json.dsd"
    creation.controller.choose_source()
    assert creation.controller.guided_project.state.source_format_hint is None


def test_strings_directory_picker_and_cancel_preserve_selection(creation, monkeypatch):
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/mods/Base.esp")
    form = creation.view._draft_page
    form.with_translations.setChecked(True)
    form.translation_format.setCurrentIndex(form.translation_format.findData("strings-directory"))
    assert form.strings_language.isEnabled()
    paths = iter(["D:/Strings", ""])
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_: next(paths))
    form.translation_browse.click()
    form.translation_browse.click()
    assert coordinator.state.translation_path == "D:/Strings"
    assert coordinator.state.translation_format_hint == "strings-directory"


@pytest.mark.parametrize("font_size", [9, 12, 16])
def test_fixed_footer_and_list_survive_optional_controls_and_font_sizes(creation, font_size):
    app = QApplication.instance()
    original = app.font()
    app.setFont(QFont(original.family(), font_size))
    try:
        coordinator = creation.controller.guided_project
        coordinator.begin_sources(tuple(f"D:/mods/source-{i}.esp" for i in range(80)))
        view, form = creation.view, creation.view._draft_page
        view.resize(850, 650)
        view.show()
        for _ in range(5):
            app.processEvents()
        before = (view.size(), form.primary.mapTo(view, QPoint()), form.source_list.height())
        assert view.rect().contains(form.primary.rect().translated(form.primary.mapTo(view, QPoint())))
        assert form.source_list.verticalScrollBar().maximum() > 0
        for enabled in (True, False, True):
            form.with_translations.setChecked(enabled)
            for hint in ("strings-directory", "binary.eet", "json.paratranz"):
                form.translation_format.setCurrentIndex(form.translation_format.findData(hint))
                app.processEvents()
                assert (view.size(), form.primary.mapTo(view, QPoint()), form.source_list.height()) == before
        state = coordinator.state
        view.render_draft(
            replace(state, diagnostic_code="FAILED", diagnostic_message="错误" * 100, revision=state.revision + 1)
        )
        app.processEvents()
        assert (view.size(), form.primary.mapTo(view, QPoint()), form.source_list.height()) == before
    finally:
        app.setFont(original)


def test_completing_creation_surfaces_no_translation_match_note(creation):
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/mods/Base.esp")
    coordinator._state = replace(coordinator.state, completion_notes=("所选译文没有匹配更新。",))
    creation.controller._on_created({})
    assert creation.messages[-1] == "本地工程“Base”已创建\n所选译文没有匹配更新。"


def test_escape_cannot_leave_a_draft_during_creation(creation):
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/mods/Base.esp")
    returned = []
    creation.view.return_to_landing_requested.connect(lambda: returned.append(True))
    creation.view._draft_primary.click()
    assert coordinator.state.in_flight
    assert not creation.view._draft_back.isEnabled()
    QTest.keyClick(creation.view, Qt.Key.Key_Escape)
    assert returned == []


def test_appending_a_different_file_does_not_force_picker_format_on_existing_sources(creation, monkeypatch):
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/mods/Base.esp")
    coordinator.set_source_format("plugin.sse")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames", lambda *_: (["D:/translations.json"], "ParaTranz JSON (*.json)")
    )
    creation.view._draft_page.add_sources.click()
    assert len(coordinator.state.source_paths) == 2
    assert coordinator.state.source_format_hint is None


def test_select_many_translation_files_cancel_clear_and_reenable(creation, monkeypatch):
    coordinator = creation.controller.guided_project
    coordinator.begin_sources(tuple(f"D:/mods/Plugin-{index}.esp" for index in range(80)))
    form = creation.view._draft_page
    creation.view.resize(850, 650)
    creation.view.show()
    app = QApplication.instance()
    for _ in range(5):
        app.processEvents()
    geometry = (creation.view.size(), form.primary.mapTo(creation.view, QPoint()))
    paths = tuple(f"D:/translations/Plugin-{index}_english_chinese.sst" for index in range(80))
    choices = iter([(list(paths), "支持的文件"), ([], ""), ([paths[1]], "")])
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_: next(choices))
    form.with_translations.setChecked(True)
    form.translation_browse.click()
    assert form.translation_path.isReadOnly()
    assert form.translation_path.text() == "已选择 80 个译文文件"
    assert form.translation_path.toolTip().splitlines() == list(coordinator.state.translation_paths)
    assert form.translation_options()["paths"] == coordinator.state.translation_paths
    assert len(coordinator.state.translation_paths) == 80
    selected = coordinator.state.translation_paths
    form.translation_browse.click()
    assert coordinator.state.translation_paths == selected
    form.with_translations.setChecked(False)
    assert coordinator.state.translation_paths == selected
    assert not form.translation_fields.isEnabled()
    form.with_translations.setChecked(True)
    assert coordinator.state.translation_paths == selected
    assert form.translation_path.text() == "已选择 80 个译文文件"
    form.translation_clear.click()
    assert coordinator.state.translation_paths == ()
    assert not form.primary.isEnabled()
    assert not form.translation_clear.isEnabled()
    form.translation_browse.click()
    assert len(coordinator.state.translation_paths) == 1
    assert form.translation_path.text() == coordinator.state.translation_path
    assert form.primary.isEnabled()
    app.processEvents()
    assert (creation.view.size(), form.primary.mapTo(creation.view, QPoint())) == geometry


def test_mixed_translation_formats_use_auto_detection_after_reselection(creation, monkeypatch):
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/mods/Base.esp")
    form = creation.view._draft_page
    form.with_translations.setChecked(True)
    form.translation_format.setCurrentIndex(form.translation_format.findData("json.dsd"))
    paths = ["D:/translations/a.sst", "D:/translations/b.eet", "D:/translations/c.json"]
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_: (paths, "支持的文件"))
    form.translation_browse.click()
    assert len(coordinator.state.translation_paths) == 3
    assert coordinator.state.translation_format_hint is None
    assert form.translation_format.currentData() is None
