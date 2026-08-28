"""翻译词典 GUI 相关测试（offscreen，不含真实交互）。

覆盖：SaveToDictionaryDialog 参数收集逻辑（mod 名推断、scope 切换、tags 分割）、
DictionaryPanel 数据加载与表格填充（脱离真实 collection，mock manager）。
"""

from __future__ import annotations

import os
import sys

import pytest

# offscreen 平台，避免 CI/无显示环境崩溃
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_save_dialog_result_parsing(qapp):
    from transbridge.ui.tools.dictionary_dialog import SaveToDictionaryDialog

    dlg = SaveToDictionaryDialog(source_path="/path/to/LegacyPatch.esp")
    # 默认 global + mod 名预填（从路径推断）+ 全量 + 无词典标签
    mod_id, scope, selected_only, tags = dlg.result()
    assert mod_id == "LegacyPatch"
    assert scope == "global"
    assert selected_only is False
    assert tags == []

    # 词典标签逗号分隔（含重复与空白），去重
    dlg._tags_edit.setText("术语, 已校对, 术语")
    mod_id, scope, selected_only, tags = dlg.result()
    assert mod_id == "LegacyPatch"
    assert scope == "global"
    assert selected_only is False
    assert tags == ["术语", "已校对"]  # 去重

    # 切到 project
    dlg._scope_combo.setCurrentIndex(1)  # project
    mod_id, scope, selected_only, tags = dlg.result()
    assert scope == "project"


def test_save_dialog_manual_mod_id(qapp):
    from transbridge.ui.tools.dictionary_dialog import SaveToDictionaryDialog

    # 无 source_path 时，用户手填 mod 名
    dlg = SaveToDictionaryDialog(mod_file_id="MyMod")
    mod_id, scope, _, _ = dlg.result()
    assert mod_id == "MyMod"


def test_dictionary_panel_loads_empty(qapp, tm_tmp_dir):
    from transbridge.ui.context import AppContext
    from transbridge.ui.tools.dictionary_panel import DictionaryPanel

    ctx = AppContext()
    # 传入隔离的临时目录，避免加载真实 data/translation_memory 数据
    panel = DictionaryPanel(ctx, base_dir=tm_tmp_dir)
    # 空词典目录，表格应为空
    assert panel._table.rowCount() == 0
    # 下拉应有「全部词典」一项
    assert panel._dict_combo.itemText(0) == "(全部词典)"


def test_panel_default_source_path_from_esp(qapp, tm_tmp_dir):
    """有 esp_path 时，面板能从当前解析文件推断 mod 名。"""
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection
    from transbridge.ui.context import AppContext, CollectionSlot
    from transbridge.ui.tools.dictionary_panel import DictionaryPanel

    ctx = AppContext()
    slot = CollectionSlot(
        label="LegacyPatch",
        collection=TranslationEntryCollection([]),
        esp_path="D:/mods/LegacyPatch.esp",
    )
    ctx.add_slot("D:/mods/LegacyPatch.esp", slot)

    panel = DictionaryPanel(ctx, base_dir=tm_tmp_dir)
    assert panel._default_source_path() == "D:/mods/LegacyPatch.esp"
    assert panel._default_mod_id() == "LegacyPatch"
