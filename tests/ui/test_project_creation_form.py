"""Creation form behavior through the real draft coordinator and controller."""

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton
import pytest

from tests.ui import test_start_center_multi_plugin as source_fixtures
from transbridge.application.io.contracts import FormatId

creation = source_fixtures.creation


def test_add_remove_sources_preserves_edited_project_fields_and_deduplicates(creation, monkeypatch):
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/mods/Base.esp")
    coordinator.set_project_name("My project")
    coordinator.set_variant_name("Chinese")
    coordinator.set_parse_option("skip_empty", True)
    form = creation.view._draft_page
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_: (["D:/mods/Base.esp", "D:/mods/extra.esl"], ""))
    form.add_sources.click()
    assert len(coordinator.state.source_paths) == 2
    assert form.source_list.count() == 2
    assert coordinator.state.project_name == "My project"
    assert coordinator.state.default_variant_name == "Chinese"
    assert dict(coordinator.state.parse_options)["skip_empty"]
    form.source_list.item(0).setSelected(True)
    form.remove_sources.click()
    assert coordinator.state.source_paths == (str(Path("D:/mods/extra.esl")),)
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
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/mods/Base.esp")
    form = creation.view._draft_page
    choices = iter([(["D:/file.json"], "DSD JSON (*.json)"), (["D:/file.eet"], "支持的文件")])
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_: next(choices))
    monkeypatch.setattr(QMessageBox, "exec", lambda _dialog: QMessageBox.StandardButton.Yes)
    form.add_incompatible.click()
    assert coordinator.state.source_format_hint == "json.dsd"
    form.add_incompatible.click()
    assert coordinator.state.source_format_hint is None


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
    confirmed = []

    def confirm(dialog):
        confirmed.append((dialog.windowTitle(), dialog.text(), dialog.defaultButton().text()))
        assert dialog.escapeButton() is dialog.defaultButton()
        assert dialog.button(QMessageBox.StandardButton.Yes).text() == "继续导入"
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", confirm)
    form = creation.view._draft_page
    assert form.add_incompatible.text() == "不安全导入"
    form.add_incompatible.click()
    assert len(coordinator.state.source_paths) == 2
    assert coordinator.state.source_format_hint == "json.paratranz"
    assert form.source_format.findData("plugin.sse") == -1
    assert form.source_format.currentData() == "json.paratranz"
    assert form.skip_empty.isEnabled()
    assert len(confirmed) == 1
    [(title, message, default)] = confirmed
    assert title == "不安全导入"
    assert default == "取消"
    assert "直接写回插件" in message and "同时导入已有译文" in message

    assert coordinator.create()
    operation, _callbacks = creation.pending.pop(0)
    operation()
    assert [source.format_hint for source in creation.commands.requests[-1].sources] == [
        FormatId.PLUGIN_SSE,
        FormatId.JSON_PARATRANZ,
    ]


@pytest.mark.parametrize("entry", ["landing", "form"])
def test_ordinary_source_picker_rejects_non_plugin_without_changing_draft(creation, monkeypatch, entry):
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/mods/Base.esp")
    state = coordinator.state
    captured = []

    def choose(_host, _title, _directory, file_filter):
        captured.append(file_filter)
        return ["D:/mods/Valid.ESM", "D:/mods/extra.eet"], ""

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", choose)
    monkeypatch.setattr(QMessageBox, "exec", lambda _dialog: pytest.fail("普通入口不可静默转为不安全导入"))
    view = creation.view
    (view.choose_plugin_button if entry == "landing" else view._draft_page.add_sources).click()
    assert captured == ["Bethesda 插件 (*.esp *.esm *.esl)"]
    assert coordinator.state is state
    assert "创建工程表单中使用“不安全导入”" in creation.messages[-1]


def test_source_format_choice_cannot_relabel_a_plugin(creation):
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/mods/Base.ESP")
    state = coordinator.state
    form = creation.view._draft_page
    assert form.source_format.count() == 1
    assert form.source_format.currentData() == "plugin.sse"
    assert not form.source_format.isEnabled()
    assert not coordinator.set_source_format("json.dsd")
    assert coordinator.state is state
    assert coordinator.create()
    operation, _callbacks = creation.pending.pop(0)
    operation()
    assert creation.commands.requests[-1].source.format_hint is FormatId.PLUGIN_SSE


@pytest.mark.parametrize("entry", ["form", "direct"])
def test_declining_incompatible_source_warning_keeps_the_draft(creation, monkeypatch, entry):
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/mods/Base.esp")
    state = coordinator.state
    pickers = []
    confirmed = []
    path = "D:/files/Translation.JSON"

    def choose(_host, title, _directory, file_filter):
        pickers.append((title, file_filter))
        return [path], "ParaTranz JSON (*.json)"

    def decline(dialog):
        confirmed.append(dialog.defaultButton().text())
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", choose)
    monkeypatch.setattr(QMessageBox, "exec", decline)
    if entry == "direct":
        creation.controller.choose_source_path(path)
    else:
        creation.view._draft_page.add_incompatible.click()
    assert confirmed == ["取消"]
    if entry != "direct":
        [(title, file_filter)] = pickers
        assert title == "不安全导入（可多选）"
        assert all(pattern in file_filter for pattern in ("*.xml", "*.sst", "*.eet", "*.json", "*.strings"))
        assert "*.esp" not in file_filter and "所有文件" not in file_filter
    else:
        assert pickers == []
    assert coordinator.state is state
    assert creation.pending == []


def test_non_plugin_intent_path_does_not_let_unsupported_files_into_draft(creation, monkeypatch):
    coordinator = creation.controller.guided_project
    state = coordinator.state
    monkeypatch.setattr(QMessageBox, "exec", lambda _dialog: pytest.fail("未知格式不应提示确认"))
    creation.controller.choose_source_path("D:/files/Unsupported.zip")
    assert coordinator.state is state
    assert creation.messages[-1].startswith("不安全导入仅支持非插件来源文件")


def test_non_plugin_import_button_is_only_in_creation_form(creation):
    view = creation.view
    import_labels = {"不安全导入", "不兼容导入"}
    assert not import_labels.intersection(button.text() for button in view._landing_page.findChildren(QPushButton))
    draft_buttons = view._draft_page.findChildren(QPushButton)
    assert [button.text() for button in draft_buttons if button.text() in import_labels] == ["不安全导入"]


def test_source_selection_does_not_replace_a_draft_in_flight(creation, monkeypatch):
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/files/Plugin.esp")
    assert coordinator.create()
    state = coordinator.state
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_: pytest.fail("正在建项时不可打开来源选择"))
    monkeypatch.setattr(QMessageBox, "exec", lambda _dialog: pytest.fail("正在建项时不可打开来源确认"))
    creation.controller.choose_source()
    assert not creation.view._draft_page.add_incompatible.isEnabled()
    creation.view.add_incompatible_requested.emit()
    creation.controller.choose_source_path("D:/files/Source.eet")
    assert coordinator.state is state


def test_incompatible_format_hint_does_not_label_different_existing_non_plugin_sources(creation, monkeypatch):
    coordinator = creation.controller.guided_project
    coordinator.begin("D:/files/Direct.xml")
    coordinator.set_source_format("xml.eet")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_: (["D:/files/Third.json"], "DSD JSON (*.json)"))
    monkeypatch.setattr(QMessageBox, "exec", lambda _dialog: QMessageBox.StandardButton.Yes)
    form = creation.view._draft_page
    form.add_incompatible.click()
    assert coordinator.state.source_format_hint is None
    assert form.source_format.count() == 1
    assert form.source_format.currentData() is None
    assert not form.source_format.isEnabled()
    assert coordinator.create()
    operation, _callbacks = creation.pending.pop(0)
    operation()
    assert [source.format_hint for source in creation.commands.requests[-1].sources] == [None, None]


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
    monkeypatch.setattr(QMessageBox, "exec", lambda _dialog: pytest.fail("已有译文不应触发不兼容来源确认"))
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
