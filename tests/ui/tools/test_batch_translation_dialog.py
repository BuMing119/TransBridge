from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox
import pytest

from transbridge.application.translation.custom_workflow_profile import CustomWorkflowProfile
from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator._batch_translation_dialog import _BatchTranslationDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _entry(*, translated: bool = False):
    return SimpleNamespace(translation="译文" if translated else "", stage=2 if translated else 0)


def _slot(name: str, *, untranslated: int, translated: int = 0, esp_path: str | None = None):
    return SimpleNamespace(
        label=name,
        esp_path=esp_path if esp_path is not None else f"C:/{name}.esp",
        collection=[*(_entry() for _ in range(untranslated)), *(_entry(translated=True) for _ in range(translated))],
    )


def _context():
    return SimpleNamespace(
        project_name="Northern Roads 汉化",
        slots={
            "main": _slot("NorthernRoads.esp", untranslated=3, translated=2),
            "patch": _slot("NorthernRoads-Patch.esp", untranslated=1),
            "done": _slot("NorthernRoads-Items.esl", untranslated=0, translated=4),
        },
    )


def _config() -> LLMConfig:
    return LLMConfig(api_key="secret-never-render", model="deepseek-chat", max_concurrent=3)


def test_dialog_exposes_four_task_pages_and_secret_free_service_summary(qapp) -> None:
    dialog = _BatchTranslationDialog(_context(), llm_config=_config())

    assert dialog.windowTitle() == "AI 翻译任务 · 多个插件"
    assert dialog.property("tbTaskDialog") is True
    assert dialog.property("tbDialog") is True
    assert dialog._tabs.property("tbComponentKind") == "tabs"
    assert [dialog._tabs.tabText(index) for index in range(dialog._tabs.count())] == [
        "基础配置",
        "术语库",
        "质量处理",
        "运行参数",
    ]
    assert dialog._project_label.full_text == "当前工程 · Northern Roads 汉化"
    assert "deepseek-chat" in dialog._config_label.full_text
    assert "secret-never-render" not in dialog._config_label.full_text
    assert "API Key" not in dialog._config_label.full_text
    assert dialog._ok_btn.property("tbTaskPrimary") is True
    assert dialog._plugins.list.property("tbTaskList") is True
    assert dialog._quality_page.strategy.property("tbComponentKind") == "input"
    dialog.close()


def test_plugin_defaults_filters_order_and_live_counts(qapp) -> None:
    dialog = _BatchTranslationDialog(_context(), llm_config=_config())

    assert [slot.label for slot in dialog.get_selected_slots()] == ["NorthernRoads.esp", "NorthernRoads-Patch.esp"]
    assert "约 4 条未翻译内容" in dialog._status_label.text()

    dialog._btn_none.click()
    assert dialog.get_selected_slots() == []
    assert not dialog._ok_btn.isEnabled()
    assert "至少一个插件" in dialog._ok_btn.toolTip()

    dialog._btn_all.click()
    assert len(dialog.get_selected_slots()) == 3
    dialog._overwrite_check.setChecked(True)
    assert "约 10 条全部内容" in dialog._status_label.text()

    moved = dialog._list.takeItem(2)
    dialog._list.insertItem(0, moved)
    assert [slot.label for slot in dialog.get_selected_slots()][0] == "NorthernRoads-Items.esl"

    dialog._btn_untranslated.click()
    assert [slot.label for slot in dialog.get_selected_slots()] == ["NorthernRoads.esp", "NorthernRoads-Patch.esp"]
    dialog.close()


def test_task_controls_map_to_detached_config_and_cancel_never_persists(qapp, monkeypatch) -> None:
    saved = _config()
    saved.target_lang = "zh_CN"
    saved.max_concurrent = 3
    saved.term_priority = ["dynamic", "json", "csv", "excel", "paratranz"]
    saves: list[bool] = []
    monkeypatch.setattr(LLMConfig, "save_to_file", lambda _self, **_kwargs: saves.append(True))
    dialog = _BatchTranslationDialog(_context(), llm_config=saved)

    dialog._runtime_page.concurrent.setValue(7)
    dialog._runtime_page.retries.setValue(4)
    dialog._runtime_page.input_tokens.setValue(4200)
    dialog._runtime_page.output_tokens.setValue(8192)
    dialog._terms_page.max_terms.setValue(80)
    dialog._quality_page.polish.setChecked(True)
    dialog._overwrite_check.setChecked(True)
    execution = dialog.get_llm_config()

    assert execution is not saved
    assert execution.max_concurrent == 7
    assert execution.llm_max_retries == 4
    assert execution.max_tokens_per_batch == 4200
    assert execution.max_output_tokens == 8192
    assert execution.max_terms_per_batch == 80
    assert execution.pp_enable_polish
    assert dialog.is_overwrite()
    assert saved.max_concurrent == 3
    assert saved.max_terms_per_batch == 50
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert saves == []


@pytest.mark.parametrize(
    ("config", "ctx", "reason"),
    [
        (LLMConfig(model="model"), _context(), "API Key"),
        (LLMConfig(api_key="key"), _context(), "AI 模型"),
        (_config(), SimpleNamespace(slots={"empty": _slot("empty", untranslated=0, translated=0)}), "有效词条"),
        (_config(), SimpleNamespace(slots={"bad": _slot("bad", untranslated=1, esp_path="")}), "源文件"),
    ],
)
def test_invalid_task_cannot_start(qapp, config, ctx, reason) -> None:
    dialog = _BatchTranslationDialog(ctx, llm_config=config)
    if reason == "有效词条":
        dialog._btn_all.click()

    assert not dialog._ok_btn.isEnabled()
    assert reason in dialog._ok_btn.toolTip()
    dialog._ok_btn.click()
    assert dialog.result() == 0
    dialog.close()


def test_settings_signal_refreshes_only_service_fields(qapp, monkeypatch) -> None:
    original = _config()
    dialog = _BatchTranslationDialog(_context(), llm_config=original)
    dialog._runtime_page.concurrent.setValue(9)
    refreshed = LLMConfig(api_key="new-secret", provider="anthropic", model="claude-new")
    monkeypatch.setattr(LLMConfig, "load_from_file", classmethod(lambda cls, **_kwargs: refreshed))
    requests: list[bool] = []
    dialog.open_settings_requested.connect(lambda: requests.append(True))

    dialog._settings_button.click()
    execution = dialog.get_llm_config()

    assert requests == [True]
    assert execution.provider == "anthropic"
    assert execution.model == "claude-new"
    assert execution.api_key == "new-secret"
    assert execution.max_concurrent == 9
    assert "new-secret" not in dialog._config_label.full_text
    dialog.close()


def test_save_task_preset_persists_only_safe_execution_profile(qapp, monkeypatch) -> None:
    saved = []
    repository = SimpleNamespace(upsert=lambda profile, select: saved.append((profile, select)))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("夜间批量", True))
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: None)
    dialog = _BatchTranslationDialog(_context(), llm_config=_config(), profile_repository=repository)
    dialog._runtime_page.concurrent.setValue(8)
    dialog._quality_page.polish.setChecked(True)

    dialog._save_preset_btn.click()

    assert len(saved) == 1
    profile, selected = saved[0]
    assert selected is True
    assert profile.name == "夜间批量"
    assert profile.base_mode == "translate"
    assert profile.limits["max_concurrent"] == 8
    assert profile.workflow["pp_enable_polish"] is True
    assert "api_key" not in str(profile.to_dict())
    dialog.close()


def test_selected_translate_preset_is_applied_when_next_batch_dialog_opens(qapp) -> None:
    preset_config = _config()
    preset_config.max_concurrent = 11
    preset_config.pp_enable_polish = True
    profile = CustomWorkflowProfile.from_config("夜间批量", "translate", preset_config)
    repository = SimpleNamespace(selected=lambda: profile)

    dialog = _BatchTranslationDialog(_context(), llm_config=_config(), profile_repository=repository)

    assert dialog._runtime_page.concurrent.value() == 11
    assert dialog._quality_page.polish.isChecked()
    assert dialog._preset_label.text() == "任务预设 · 夜间批量"
    dialog.close()


def test_keyboard_order_reaches_settings_and_actions(qapp) -> None:
    dialog = _BatchTranslationDialog(_context(), llm_config=_config())

    focus_chain = []
    widget = dialog
    for _index in range(200):
        widget = widget.nextInFocusChain()
        if widget is dialog:
            break
        focus_chain.append(widget)
    for control in (
        dialog._list,
        dialog._btn_all,
        dialog._btn_none,
        dialog._btn_untranslated,
        dialog._tabs,
        dialog._settings_button,
        dialog._cancel_btn,
        dialog._ok_btn,
    ):
        assert control in focus_chain
    assert focus_chain.index(dialog._settings_button) < focus_chain.index(dialog._cancel_btn)
    assert focus_chain.index(dialog._cancel_btn) < focus_chain.index(dialog._ok_btn)
    assert dialog._tabs.accessibleName() == "批量翻译任务配置"
    assert dialog._list.accessibleName() == "批量翻译插件列表"
    assert dialog._quality_page.strategy.accessibleName() == "质量处理策略"
    assert dialog._quality_page.polish_scope.accessibleName() == "润色范围"
    assert dialog._quality_page.polish_level.accessibleName() == "润色强度"
    dialog.close()


def test_long_plugin_and_project_names_do_not_expand_dialog_minimum(qapp) -> None:
    short = _BatchTranslationDialog(_context(), llm_config=_config())
    long_name = "超长插件名称" * 200
    long = _BatchTranslationDialog(
        SimpleNamespace(project_name=long_name, slots={"long": _slot(long_name, untranslated=1)}),
        llm_config=_config(),
    )
    short.show()
    long.show()
    qapp.processEvents()

    assert long.minimumSizeHint().width() == short.minimumSizeHint().width()
    assert long._project_label.toolTip().startswith("当前工程 · 超长插件名称")
    assert long._list.item(0).toolTip().startswith(long_name)
    assert long._list.horizontalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    short.close()
    long.close()


def test_batch_dialog_remains_screen_fit_at_200_percent_font_scale(qapp) -> None:
    previous = QFont(qapp.font())
    enlarged = QFont(previous)
    enlarged.setPointSizeF(max(18.0, previous.pointSizeF() * 2))
    qapp.setFont(enlarged)
    try:
        dialog = _BatchTranslationDialog(_context(), llm_config=_config())
        hint = dialog.minimumSizeHint()
        assert hint.width() <= 960
        assert hint.height() <= 540
        dialog.close()
    finally:
        qapp.setFont(previous)
