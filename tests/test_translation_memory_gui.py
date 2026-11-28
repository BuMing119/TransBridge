"""Exercise the read-only lookup window against SQLite libraries."""

from pathlib import PureWindowsPath
from threading import Event
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QComboBox, QPushButton, QWidget
import pytest

from tests.ui.fixtures.dictionary import wait_for_dictionary
from transbridge.application.projects.dictionary_apply import dictionary_mod_id, dictionary_source_path
from transbridge.config.text_encoding import TextEncodings
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.translation_memory.library import DictionaryLibrary
from transbridge.ui.context import AppContext
from transbridge.ui.shell.tool_windows import ToolWindows
from transbridge.ui.tools import dictionary_lookup
from transbridge.ui.tools.dictionary_lookup import DictionaryLookupDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def context(qapp, monkeypatch):
    monkeypatch.setattr("transbridge.ui.context.ParatranzConfig.create_or_load", lambda: SimpleNamespace(token=""))
    ctx = AppContext(text_encoding_defaults=SimpleNamespace(load=TextEncodings))
    yield ctx
    ctx.close_projection()


def visible_rows(panel):
    model = panel._table.model()
    return [tuple(model.index(i, col).data() for col in range(3)) for i in range(model.rowCount())]


def test_dictionary_tool_opens_one_modeless_lookup_without_an_active_source(context, tmp_path, monkeypatch):
    monkeypatch.setattr(dictionary_lookup, "dictionary_library", lambda *_: DictionaryLibrary(tmp_path))
    host = QWidget()
    host.context = context
    host.show()
    tools = ToolWindows(host)
    try:
        tools.open_dictionary()
        panel = tools._dictionary_window
        wait_for_dictionary(panel)
        assert panel.isVisible() and not panel.isModal()
        assert panel.parent() is None
        assert panel.focusWidget() is panel._search
        assert not panel.findChildren(QComboBox) and not panel.findChildren(QPushButton)
        assert list(tmp_path.iterdir()) == []
        assert panel._count.text() == "0 条结果"
        tools.open_dictionary()
        assert tools._dictionary_window is panel
        panel.close()
        assert tools._dictionary_window is None
    finally:
        tools.dispose()
        host.close()


def test_lookup_matches_either_text_across_libraries_and_preserves_sources(context, tmp_path):
    lib = DictionaryLibrary(tmp_path)
    lib.save([TranslationEntry("one", "one", "Light", "照明", 1, "MISC:FULL")], "Torches")
    lib.save([TranslationEntry("two", "two", "Light", "轻型", 1, "NPC_:FULL")], "ArmorPatch", main="未选用")
    lib.save([TranslationEntry("three", "three", "[Torch]", "火把", 1, "NPC_:FULL")], "Misc")
    panel = DictionaryLookupDialog(context, base_dir=tmp_path)
    try:
        panel.focus_query("lIgHt")
        wait_for_dictionary(panel)
        assert set(visible_rows(panel)) == {("Light", "轻型", "ArmorPatch"), ("Light", "照明", "Torches")}
        for query, expected in (("型", ["轻型"]), ("armorpatch", []), ("[", ["火把"]), ("", ["火把", "轻型", "照明"])):
            panel._search.setText(query)
            wait_for_dictionary(panel)
            assert sorted(row[1] for row in visible_rows(panel)) == sorted(expected)
            assert panel._count.text() == f"{len(expected)} 条结果"
        assert lib.active_main() == ""
    finally:
        panel.close()


def test_lookup_refreshes_language_and_ignores_retired_reads(context, tmp_path, monkeypatch):
    entry = TranslationEntry("one", "one", "Hello", "你好", 1, "NPC_:FULL")
    DictionaryLibrary(tmp_path).save([entry], "EnglishFile")
    DictionaryLibrary(tmp_path, source_locale="de").save([entry], "GermanFile")
    started, release, exited = Event(), Event(), Event()
    read = DictionaryLibrary.lookup_rows

    def slow_english_read(library):
        if library.source_locale == "en":
            started.set()
            try:
                assert release.wait(5)
                yield from read(library)
            finally:
                exited.set()
        else:
            yield from read(library)

    monkeypatch.setattr(DictionaryLibrary, "lookup_rows", slow_english_read)
    panel = DictionaryLookupDialog(context, base_dir=tmp_path)
    try:
        assert started.wait(5)
        panel.focus_query("你")
        context.text_encoding_defaults.load = lambda: TextEncodings(source_language="german")
        context.text_encodings_changed.emit()
        wait_for_dictionary(panel)
        release.set()
        assert exited.wait(5)
        QApplication.processEvents()
        assert visible_rows(panel) == [("Hello", "你好", "GermanFile")]
    finally:
        release.set()
        panel.close()


def test_workbench_dictionary_source_path_from_esp():
    ctx = SimpleNamespace(active_slot=SimpleNamespace(esp_path="D:/mods/LegacyPatch.esp"))
    assert PureWindowsPath(dictionary_source_path(ctx)) == PureWindowsPath("D:/mods/LegacyPatch.esp")
    assert dictionary_mod_id(ctx) == "LegacyPatch"
