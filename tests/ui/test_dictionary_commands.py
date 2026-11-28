"""Exercise desktop dictionary writes through real workers and project commands."""

from time import monotonic
from types import SimpleNamespace

from PyQt6.QtTest import QTest
import pytest

from tests.ui.fixtures.current_editing import current_project_factory as project_fixture
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.translation_memory.library import DictionaryLibrary
from transbridge.ui.shell.dictionary_commands import DictionaryCommands

current_project = project_fixture


def wait_for_commands(commands):
    deadline = monotonic() + 5
    while commands.busy and monotonic() < deadline:
        QTest.qWait(5)
    assert not commands.busy


@pytest.fixture
def desktop(current_project, monkeypatch, tmp_path):
    monkeypatch.setattr("transbridge.translation_memory.library.default_library_dir", lambda: tmp_path)
    rows = (
        TranslationEntry("one", "one", "Wait.", "等等。", 1, "INFO:NAM1"),
        TranslationEntry("two", "two", "Stay.", "留下。", 1, "INFO:NAM1"),
    )
    ctx = current_project(rows, esp_path="Follower.esp").context
    messages, refreshed = [], []
    host = SimpleNamespace(
        context=ctx,
        show_message=messages.append,
        tool_windows=SimpleNamespace(refresh_open_dictionary=lambda: refreshed.append(True)),
    )
    commands = DictionaryCommands(host)
    yield commands, ctx, rows, messages, refreshed, DictionaryLibrary(tmp_path)
    commands.close()
    wait_for_commands(commands)


def test_desktop_save_and_apply_work_on_current_file(desktop):
    commands, ctx, entries, messages, refreshed, lib = desktop
    commands.save()
    assert commands.busy
    commands.save()
    wait_for_commands(commands)
    assert messages == ["已存入词典 2 条"] and refreshed == [True]
    assert sorted(lib.lookup_rows()) == [("Stay.", "留下。", "Follower"), ("Wait.", "等等。", "Follower")]
    commands.save()
    wait_for_commands(commands)
    assert messages[-1] == "没有新增或变更的词条" and refreshed == [True]
    assert ctx.update_projected_entry(entries[0].identity, translation="", stage=0).is_success
    assert ctx.update_projected_entry(entries[1].identity, translation="留在这儿。", stage=1).is_success
    commands.apply()
    wait_for_commands(commands)
    assert messages[-1] == "已填入 1 条译文"
    restored = ctx.collection.get(entries[0].identity)
    assert (restored.translation, restored.stage) == ("等等。", 0)
    assert ctx.collection.get(entries[1].identity).translation == "留在这儿。"


def test_closing_before_worker_commits_cancels_write(desktop):
    commands, _, _, messages, refreshed, lib = desktop
    commands.save()
    commands.close()
    wait_for_commands(commands)
    assert not list(lib.lookup_rows())
    assert not messages and not refreshed
