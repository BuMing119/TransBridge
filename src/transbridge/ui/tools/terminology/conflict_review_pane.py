# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""One conflict at a time: deliberate choice and readable, paged source examples."""

from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.terminology.conflict_usage import conflict_usage
from transbridge.application.terminology.conflicts import ConflictResolutionOperation
from transbridge.application.terminology.models import DecisionStatus
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .conflict_review_dialog import ConflictReviewChoice


def _label(parent, *, title=False):
    label = QLabel(parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    if title:
        label.setProperty("tbTerminologySectionTitle", True)
    return label


def _button(text, parent):
    button = QPushButton(text, parent)
    button.setAutoDefault(False)
    ComponentStyle.apply_static(button, ComponentKind.BUTTON)
    return button


class ConflictReviewPane(QWidget):
    save_requested = pyqtSignal(object)
    skip_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.details = None
        self._saving = False
        self._active = True
        self._initial_inputs = None
        self._examples = ()
        self._example_index = 0
        self._usage = ()
        self._resolved = False
        self._reveal_target = None
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._scroll_to_disclosure)
        self.setMinimumSize(0, 0)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 0, 0, 0)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.body = QWidget(self.scroll)
        layout = QVBoxLayout(self.body)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(10)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)
        self.title = _label(self, title=True)
        self.title.setText("选择左侧术语，开始处理")
        self.current_label = _label(self)
        layout.addWidget(self.title)
        layout.addWidget(self.current_label)
        self.candidates = QWidget(self)
        self.choices = QVBoxLayout(self.candidates)
        self.choices.setContentsMargins(0, 0, 0, 0)
        self.candidate_group = QButtonGroup(self)
        self.candidate_buttons = []
        layout.addWidget(self.candidates)
        self.custom_toggle = _button("使用其他译名", self)
        self.custom_toggle.setCheckable(True)
        self.translation = QLineEdit(self)
        self.translation.setPlaceholderText("输入要采用的译名")
        self.translation.setAccessibleName("采用的自定义译名")
        self.translation.setProperty("tbComponentKind", "input")
        self.translation.hide()
        self.custom_toggle.toggled.connect(self.translation.setVisible)
        self.custom_toggle.clicked.connect(lambda opened: self._reveal_disclosure(self.translation, opened))
        self.translation.textChanged.connect(self._choice_changed)
        layout.addWidget(self.custom_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.translation)
        self.evidence_heading = _label(self)
        self.evidence_heading.setText("使用示例 · 选择译名后查看")
        layout.addWidget(self.evidence_heading)
        self.source_label = _label(self)
        self.source_label.setMaximumHeight(54)
        layout.addWidget(self.source_label)
        self.reader = QSplitter(Qt.Orientation.Horizontal, self)
        self.reader.setChildrenCollapsible(False)
        self.original_text = QPlainTextEdit(self)
        self.translation_text = QPlainTextEdit(self)
        for name, editor in (("原文", self.original_text), ("现有译文", self.translation_text)):
            pane = QWidget(self.reader)
            pane.setMinimumWidth(0)
            column = QVBoxLayout(pane)
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(QLabel(name, pane))
            editor.setReadOnly(True)
            editor.setMinimumSize(0, 90)
            editor.setAccessibleName(name)
            column.addWidget(editor)
            self.reader.addWidget(pane)
        layout.addWidget(self.reader, 1)
        examples = QHBoxLayout()
        self.previous_example = _button("上一例", self)
        self.next_example = _button("下一例", self)
        self.example_position = _label(self)
        self.previous_example.clicked.connect(lambda: self._move_example(-1))
        self.next_example.clicked.connect(lambda: self._move_example(1))
        examples.addWidget(self.previous_example)
        examples.addWidget(self.example_position, 1)
        examples.addWidget(self.next_example)
        layout.addLayout(examples)
        self.judgment_toggle = _button("查看 LLM 判断依据", self)
        self.judgment_toggle.setCheckable(True)
        self.judgment_text = QPlainTextEdit(self)
        self.judgment_text.setReadOnly(True)
        self.judgment_text.setMaximumHeight(110)
        self.judgment_text.hide()
        self.judgment_toggle.toggled.connect(self.judgment_text.setVisible)
        self.judgment_toggle.toggled.connect(
            lambda opened: self.judgment_toggle.setText("收起 LLM 判断依据" if opened else "查看 LLM 判断依据")
        )
        self.judgment_toggle.clicked.connect(lambda opened: self._reveal_disclosure(self.judgment_text, opened))
        layout.addWidget(self.judgment_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.judgment_text)
        self.advanced_toggle = _button("插件特例设置", self)
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.hide()
        self.custom_toggle.toggled.connect(self.advanced_toggle.setVisible)
        self.advanced = QWidget(self)
        form = QFormLayout(self.advanced)
        form.setContentsMargins(0, 0, 0, 0)
        self.operation = QComboBox(self)
        for name, value in (
            ("统一采用译名", ConflictResolutionOperation.UNIFY),
            ("仅用于指定插件", ConflictResolutionOperation.PLUGIN_EXCEPTION),
            ("排除此术语", ConflictResolutionOperation.IGNORE),
        ):
            self.operation.addItem(name, value)
        self.plugin = QComboBox(self)
        form.addRow("处理方式", self.operation)
        form.addRow("插件", self.plugin)
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        self.advanced_toggle.toggled.connect(
            lambda opened: self.advanced_toggle.setText("收起插件特例设置" if opened else "插件特例设置")
        )
        self.advanced_toggle.clicked.connect(lambda opened: self._reveal_disclosure(self.advanced, opened))
        self.operation.currentIndexChanged.connect(self._update_controls)
        self.plugin.currentIndexChanged.connect(self._update_controls)
        self.advanced.hide()
        layout.insertWidget(layout.indexOf(self.translation) + 1, self.advanced_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.advanced)
        self.exclude_button = _button("排除此术语", self)
        self.exclude_button.setToolTip("保存排除决定并进入下一条；可撤销上次决定。应用后生效，原文译文不删除。")
        self.exclude_button.clicked.connect(self._exclude)
        layout.addWidget(self.exclude_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.error = _label(self)
        self.error.setAccessibleName("审核保存状态")
        root.addWidget(self.error)
        footer = QHBoxLayout()
        self.skip_button = _button("稍后处理", self)
        self.save_button = _button("保存并处理下一条", self)
        self.save_button.setProperty("tbTerminologyPrimary", True)
        self.skip_button.clicked.connect(self.skip_requested)
        self.save_button.clicked.connect(self._save)
        footer.addWidget(self.skip_button)
        footer.addStretch(1)
        footer.addWidget(self.save_button)
        root.addLayout(footer)
        self._update_controls()

    def set_details(self, details):
        self._reveal_timer.stop()
        self.details = details
        self._saving = False
        self.error.clear()
        self.body.setEnabled(self._active)
        self.title.setText(details.conflict.normalized_original)
        status = str(getattr(details.conflict, "status", "unresolved"))
        self._resolved = status not in {"unresolved", "uncertain", "equivalent"}
        usable = [item for item in details.decisions if item.status is not DecisionStatus.UNRESOLVED]
        current = "；".join(
            f"{item.scope.plugin_id or '项目'}：{'已排除' if item.suppressed else item.translation}" for item in usable
        )
        self.current_label.setText(f"当前采用 · {current}" if current else "选择一个译名保存到草稿")
        while self.choices.count():
            widget = self.choices.takeAt(0).widget()
            self.candidate_group.removeButton(widget)
            widget.deleteLater()
        self.candidate_buttons.clear()
        self._usage = conflict_usage(details.conflict, details.evidence)
        for usage in self._usage:
            button = QRadioButton(f"{usage.translation}  ·  {usage.count} 次", self.candidates)
            button.setProperty("translation", usage.translation)
            button.setToolTip(usage.translation)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, text=usage.translation: self.translation.setText(text))
            self.candidate_group.addButton(button)
            self.candidate_buttons.append(button)
            self.choices.addWidget(button)
        effective = next(
            (
                item
                for item in usable
                if not item.suppressed and bool(item.scope.plugin_id) == (status == "plugin_exception")
            ),
            None,
        )
        with QSignalBlocker(self.translation), QSignalBlocker(self.operation), QSignalBlocker(self.plugin):
            self.translation.setText(effective.translation if effective is not None else "")
            self.operation.setCurrentIndex({"plugin_exception": 1, "ignored": 2}.get(status, 0))
            self.plugin.clear()
            self.plugin.addItems(list(details.plugin_ids))
            if effective is not None and effective.scope.plugin_id:
                self.plugin.setCurrentText(effective.scope.plugin_id)
        self.advanced_toggle.setChecked(status == "plugin_exception")
        self.custom_toggle.setChecked(
            status == "plugin_exception"
            or (
                bool(self.translation.text())
                and all(item.translation != self.translation.text() for item in self._usage)
            )
        )
        self.judgment_toggle.setChecked(False)
        notes = getattr(details.conflict, "notes", "")
        self.judgment_text.setPlainText(notes)
        self.judgment_toggle.setVisible(bool(notes))
        self._choice_changed()
        self._initial_inputs = self._inputs()
        self.scroll.verticalScrollBar().setValue(0)

    def _reveal_disclosure(self, widget, opened):
        self._reveal_timer.stop()
        if opened:
            self._reveal_target = widget
            # Wait for the expanded layout to update the scroll range.
            self._reveal_timer.start(0)

    def _scroll_to_disclosure(self):
        if self._reveal_target is not None and self._reveal_target.isVisible():
            self.body.layout().activate()
            self.scroll.ensureWidgetVisible(self._reveal_target, 0, 12)

    def _choice_changed(self):
        self.candidate_group.setExclusive(False)
        for button in self.candidate_buttons:
            button.setChecked(button.property("translation") == self.translation.text())
        self.candidate_group.setExclusive(True)
        usage = next((item for item in self._usage if item.translation == self.translation.text()), None)
        self._examples = usage.evidence if usage is not None else ()
        self._example_index = 0
        self.evidence_heading.setText(f"使用示例 · {usage.translation}" if usage else "使用示例 · 选择候选译名后查看")
        self._render_example()
        self._update_controls()

    def _move_example(self, offset):
        self._example_index = max(0, min(len(self._examples) - 1, self._example_index + offset))
        self._render_example()

    def _render_example(self):
        self.previous_example.setEnabled(self._example_index > 0)
        self.next_example.setEnabled(self._example_index + 1 < len(self._examples))
        self.example_position.setText(
            f"{self._example_index + 1} / {len(self._examples)}" if self._examples else "暂无示例"
        )
        item = self._examples[self._example_index] if self._examples else None
        self.original_text.setPlainText(item.original if item else "")
        self.translation_text.setPlainText(item.translation if item else "")
        source = (
            f"{item.plugin_scope or '项目'} · {' → '.join(item.source_chain)}\n{item.entry_key} · {item.context}"
            if item
            else ""
        )
        self.source_label.setText(f"{item.plugin_scope or '项目来源'} · {item.context}" if item else "")
        self.source_label.setToolTip(source)

    def _inputs(self):
        return self.operation.currentData(), self.translation.text(), self.plugin.currentText()

    @property
    def has_unsaved_inputs(self):
        return self.details is not None and self._initial_inputs is not None and self._inputs() != self._initial_inputs

    def _update_controls(self):
        enabled = self._active and not self._saving and self.details is not None
        excluded = self.operation.currentData() is ConflictResolutionOperation.IGNORE
        scoped = self.operation.currentData() is ConflictResolutionOperation.PLUGIN_EXCEPTION
        valid = excluded or (bool(self.translation.text().strip()) and (not scoped or bool(self.plugin.currentText())))
        self.save_button.setEnabled(enabled and valid)
        self.exclude_button.setEnabled(enabled)
        self.exclude_button.setText("正在保存…" if self._saving and excluded else "排除此术语")
        self.skip_button.setEnabled(enabled)
        self.operation.setEnabled(enabled)
        self.plugin.setEnabled(enabled and scoped)
        self.translation.setEnabled((enabled and not excluded) or not self._active)
        self.custom_toggle.setEnabled(enabled and not excluded)
        for button in self.candidate_buttons:
            button.setEnabled(enabled and not excluded)
        self.save_button.setText("正在保存…" if self._saving else "保存修改" if self._resolved else "保存并处理下一条")

    def _exclude(self):
        if not self.exclude_button.isEnabled():
            return
        self.operation.setCurrentIndex(self.operation.findData(ConflictResolutionOperation.IGNORE))
        self._save()

    def _save(self):
        if not self.save_button.isEnabled():
            return
        operation = self.operation.currentData()
        choice = ConflictReviewChoice(
            operation,
            None if operation is ConflictResolutionOperation.IGNORE else self.translation.text().strip(),
            self.plugin.currentText() if operation is ConflictResolutionOperation.PLUGIN_EXCEPTION else None,
        )
        self.set_saving(True)
        self.save_requested.emit(choice)

    def set_loading(self):
        self.details = None
        self._initial_inputs = None
        self.body.setEnabled(False)
        self.title.setText("正在读取术语与来源…")
        self.error.clear()
        self._update_controls()

    def set_error(self, error):
        self._saving = False
        self.error.setText(f"操作未完成：{error}")
        self._update_controls()

    def set_saving(self, saving):
        self._saving = saving
        self.error.setText("正在保存…" if saving else "")
        self._update_controls()

    def saved(self, result=None):
        self._saving = False
        self._initial_inputs = self._inputs()
        self.error.setText("已保存到草稿；应用后生效。")
        self._update_controls()

    def invalidate_context(self):
        self._active = False
        self.error.setText("工程或翻译版本已变化，请重新打开。当前输入仍可复制。")
        self.translation.setReadOnly(True)
        self._update_controls()


__all__ = ["ConflictReviewPane"]
