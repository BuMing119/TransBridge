from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QComboBox, QWidget

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.paratranz.config_manager import ActionRule, apply_rules
from transbridge.ui.tools.ai_translator._rule_editor_widget import (
    _CheckableFilterButton,
    _RuleEditorWidget,
)
from transbridge.ui.tools.ai_translator.config_view import AITranslatorView, ConfigAutosaveBinding

_APP = QApplication.instance() or QApplication([])


def _app() -> QApplication:
    return _APP


def _set_checked(button: _CheckableFilterButton, label: str) -> None:
    action = next(action for action in button._actions if action.text() == label)
    action.setChecked(True)


def test_rule_editor_uses_constrained_controls_and_projects_multi_selects() -> None:
    _app()
    editor = _RuleEditorWidget()
    changes: list[None] = []
    editor.rules_changed.connect(lambda: changes.append(None))

    editor._on_add()
    row = editor._table.rowCount() - 1
    priority = editor._table.item(row, 0)
    stage = editor._table.cellWidget(row, 1)
    category = editor._table.cellWidget(row, 2)
    action = editor._table.cellWidget(row, 3)

    assert not priority.flags() & Qt.ItemFlag.ItemIsEditable
    assert isinstance(stage, _CheckableFilterButton)
    assert isinstance(category, _CheckableFilterButton)
    assert isinstance(action, QComboBox)

    _set_checked(stage, "未翻译")
    _set_checked(stage, "有疑问")
    _set_checked(category, "对话")
    action.setCurrentIndex(action.findData("translate"))

    rule = editor.get_rules()[row]
    assert rule.status_filter == {0, 2}
    assert rule.category_filter == {"对话"}
    assert rule.action == "translate"
    assert len(changes) >= 5
    editor.close()


def test_rule_move_reindexes_priorities_and_changes_runtime_order() -> None:
    _app()
    editor = _RuleEditorWidget()
    editor.set_rules([
        ActionRule("first", 0, {0}, action="skip"),
        ActionRule("second", 1, {0}, action="translate"),
    ])
    editor._table.selectRow(1)

    editor._on_move_up()

    rules = editor.get_rules()
    assert [(rule.rule_id, rule.priority) for rule in rules] == [("second", 0), ("first", 1)]
    entry = TranslationEntry("entry", "key", "source", "", 0, "INFO:NAM1|quest")
    assert apply_rules(rules, [entry])[entry.id] == "translate"
    editor.close()


def test_loading_legacy_rules_preserves_priority_order_before_reindexing() -> None:
    _app()
    editor = _RuleEditorWidget()
    editor.set_rules([
        ActionRule("listed-first", 9, {0}, action="skip"),
        ActionRule("priority-first", 1, {0}, action="translate"),
    ])

    rules = editor.get_rules()

    assert [(rule.rule_id, rule.priority) for rule in rules] == [("priority-first", 0), ("listed-first", 1)]
    entry = TranslationEntry("entry", "key", "source", "", 0, "INFO:NAM1|quest")
    assert apply_rules(rules, [entry])[entry.id] == "translate"
    editor.close()


def test_loading_raw_legacy_category_keeps_filter_visible_and_effective() -> None:
    _app()
    editor = _RuleEditorWidget()
    editor.set_rules([ActionRule("legacy", 0, category_filter={"INFO:NAM1"}, action="translate")])

    category = editor._table.cellWidget(0, 2)
    rules = editor.get_rules()

    assert isinstance(category, _CheckableFilterButton)
    assert "兼容原始值：INFO:NAM1" in category.text()
    assert rules[0].category_filter == {"INFO:NAM1"}
    entry = TranslationEntry("entry", "key", "source", "", 0, "INFO:NAM1|quest")
    assert apply_rules(rules, [entry])[entry.id] == "translate"
    editor.close()


def test_user_facing_category_filter_matches_raw_entry_context() -> None:
    entry = TranslationEntry("entry", "key", "source", "", 0, "INFO:NAM1|quest")
    rule = ActionRule("dialogue", 0, category_filter={"对话"}, action="translate")

    assert rule.match(entry)
    assert apply_rules([rule], [entry])[entry.id] == "translate"


def test_mixed_rule_changes_refresh_scope_and_join_autosave_binding() -> None:
    _app()

    class Callbacks:
        def __init__(self) -> None:
            self.estimate_updates = 0
            self.quick_updates = 0

        def update_estimate(self) -> None:
            self.estimate_updates += 1

        def update_quick_run(self) -> None:
            self.quick_updates += 1

        def __getattr__(self, _name):
            return lambda *_args: None

    parent = QWidget()
    callbacks = Callbacks()
    view = AITranslatorView(parent, callbacks)
    saves: list[None] = []
    binding = ConfigAutosaveBinding(view, parent, lambda: saves.append(None), callbacks)
    binding.start()

    view.controls.rule_editor._on_add()
    assert callbacks.estimate_updates == 1
    assert callbacks.quick_updates == 1

    view.controls.order_combo.setCurrentIndex(1)
    assert callbacks.quick_updates == 2
    binding.close()
    assert saves == [None]
    parent.close()
