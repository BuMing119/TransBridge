"""The desktop task builder, assistant preview and worker launch select the same identities."""

from types import SimpleNamespace

import pytest

from transbridge.application.translation.task_scope import TranslationScope
from transbridge.config.llm import LLMConfig
from transbridge.converter.context_categories import context_category
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.config_manager import ActionRule
from transbridge.smart_assistant.tools import tool_translator
from transbridge.smart_assistant.tools.translation_scope import translation_targets
from transbridge.smart_assistant.tools.translation_settings import TranslationSettings
from transbridge.smart_assistant.tools.types import ToolResult
from transbridge.ui.tools.ai_translator.task_scope import SourceWorkbenchScope, TaskScope


@pytest.mark.parametrize(("mode", "expected"), [("translate", ["a"]), ("polish", ["b"]), ("mixed", ["a", "b"])])
def test_desktop_and_assistant_execute_same_selected_identities(mode, expected, monkeypatch):
    entries = [
        TranslationEntry("shared-id", "a", "Book", "", 0, "BOOK:FULL"),
        TranslationEntry("shared-id", "b", "Other book", "已有译文", 1, "BOOK:FULL"),
        TranslationEntry("other", "c", "Locked", "锁定", 9, "BOOK:FULL"),
        TranslationEntry("absent", "d", " ", "旧", 1, "BOOK:FULL"),
    ]
    collection = TranslationEntryCollection(entries)
    ctx = SimpleNamespace(collection=collection, translation_scope={"labels": ["other"]}, entry_labels={})
    slot = SimpleNamespace(label="Source", esp_path=None, collection=collection)
    desk = SimpleNamespace(slots={"source": slot}, active_slot=slot, entry_labels={})
    selected = [entry.identity.serialize() for entry in entries]
    config = LLMConfig(
        api_key="unused",
        model="offline",
        action_rules=[
            ActionRule(status_filter={0}, action="translate"),
            ActionRule(status_filter={1}, action="polish"),
        ],
    )
    monkeypatch.setattr(LLMConfig, "load_from_file", lambda: config)
    launched = []

    def capture(_ctx, _collection, groups, _config, **_kwargs):
        launched.extend((*groups.translate, *groups.polish))
        return ToolResult.ok(data={})

    monkeypatch.setattr(tool_translator, "launch_translation_task", capture)
    args = {"mode": mode, "entry_ids": [*selected, selected[0]]}
    desktop = TaskScope(desk, SourceWorkbenchScope(entries, selected), lambda e: context_category(e.context))
    (task,) = desktop.build(
        [slot],
        TranslationScope(preset="selection", selected_entry_ids=frozenset(selected)),
        mode=mode,
        config=config,
        overwrite=False,
    )
    preview = TranslationSettings().get_scope_preview(args, ctx)
    assert preview.success and preview.data["matched"] == len(expected)
    assert preview.data["scope"] == {
        "preset": "selection",
        "selected_count": len(entries),
        "stages": [],
        "labels": [],
        "categories": [],
    }
    assert tool_translator.TranslationController().start_translation(args, ctx, collection).success
    assert [e.identity for e in task.entries] == [e.identity for e in launched]
    assert [e.key for e in launched] == expected


def test_offscreen_labels_survive_reduced_admission_and_explicit_clear():
    first = TranslationEntry("a", "a", "First", "draft", 0, "BOOK:FULL")
    second = TranslationEntry("b", "b", "Second", "", 1, "BOOK:FULL")
    slot_rows = TranslationEntryCollection([first, second])
    slot_rows.query_rows = lambda: iter([(first, "a", {"review"}), (second, "b", {"review"})])
    reduced = TranslationEntryCollection([second])
    ctx = SimpleNamespace(
        collection=reduced, _source_input_full_collection=slot_rows, translation_scope={}, entry_labels={}
    )
    settings = TranslationSettings()
    default = settings.get_scope_preview({}, ctx).data
    assert default["matched"] == 0 and default["scope"]["stages"] == [0]
    assert settings.get_scope_preview({"mode": "polish"}, ctx).data["scope"]["stages"] == [1, 2, 3, 5]
    assert settings.set_scope({"stages": [1], "categories": ["书名"], "labels": ["review"]}, ctx).success
    assert [e.identity for e in translation_targets({}, ctx, reduced, mode="translate")] == [second.identity]
    assert not settings.get_scope_preview({"entry_ids": ["a", "b"]}, ctx).success
    assert settings.set_scope({"labels": ["missing"]}, ctx).success
    assert settings.get_scope_preview({}, ctx).data["matched"] == 0
    assert settings.set_scope({}, ctx).success
    cleared = settings.get_scope_preview({}, ctx).data
    assert cleared["matched"] == 1 and cleared["scope"]["preset"] == "all"


def test_preview_and_launch_both_use_disabled_saved_polish_profile(monkeypatch):
    config = LLMConfig(enable_post_process=True, workflow_profiles={"polish": {"enable_post_process": False}})
    monkeypatch.setattr(LLMConfig, "load_from_file", lambda: config)
    collection = TranslationEntryCollection([TranslationEntry("b", "b", "Book", "已有译文", 1, "BOOK:FULL")])
    ctx = SimpleNamespace(collection=collection, translation_scope={}, entry_labels={})
    args = {"mode": "polish"}
    preview = TranslationSettings().get_scope_preview(args, ctx)
    assert preview.success and preview.data["matched"] == 0
    result = tool_translator.TranslationController().start_polish(args, ctx, collection)
    assert not result.success and "没有可处理的词条" in result.message
    assert config.enable_post_process


@pytest.mark.parametrize("reference", ["Missing", "Mixed"])
def test_invalid_named_polish_profile_blocks_preview_and_task_launch(tmp_path, monkeypatch, reference):
    from transbridge.application.translation.custom_workflow_profile import CustomWorkflowProfile
    from transbridge.config.ai_workflow_profiles import AiWorkflowProfileRepository

    repo = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    repo.upsert(CustomWorkflowProfile.from_config("Mixed", "mixed", LLMConfig()))
    monkeypatch.setattr(
        "transbridge.smart_assistant.tools.translation_options.AiWorkflowProfileRepository", lambda: repo
    )
    monkeypatch.setattr(tool_translator, "launch_translation_task", lambda *_a, **_k: pytest.fail("unexpected launch"))
    collection = TranslationEntryCollection([TranslationEntry("b", "b", "Book", "已有译文", 1, "BOOK:FULL")])
    ctx = SimpleNamespace(collection=collection, translation_scope={}, entry_labels={})
    args = {"mode": "polish", "profile": reference}
    preview = TranslationSettings().get_scope_preview(args, ctx)
    result = tool_translator.TranslationController().start_polish(args, ctx, collection)
    for outcome in (preview, result):
        assert not outcome.success and reference in outcome.message
