"""Single-entry comparison, with actions described in the user's language."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from transbridge.converter.translation_entry import STAGE_LABELS
from transbridge.ui.foundation.accessibility import configure_accessible_widget
from transbridge.ui.foundation.components import ThemedCard, make_primary_button

from .sync_review_direction import SyncReviewDirection
from .sync_review_view import SyncReviewRow


def _label(text: str, parent: QWidget, *, heading: bool = False) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if heading:
        font = label.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 2)
        label.setFont(font)
    return label


class TranslationComparisonCard(ThemedCard):
    """Keep original, translation and supplied context readable for long strings."""

    def __init__(self, title: str, parent=None, *, context_title: str) -> None:
        super().__init__(parent)
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        layout.addWidget(_label(title, self, heading=True))
        self.source = QVBoxLayout()
        layout.addLayout(self.source)
        self.original = self._field("原文", 70)
        self.translation = self._field("译文", 92)
        self.stage = _label("", self)
        layout.addWidget(self.stage)
        self.context = self._field(context_title, 65)
        layout.addStretch(1)

    def _field(self, title: str, height: int) -> QPlainTextEdit:
        self.layout().addWidget(_label(title, self))
        field = QPlainTextEdit(self)
        field.setReadOnly(True)
        field.setMinimumWidth(0)
        field.setFixedHeight(height)
        field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        configure_accessible_widget(field, name=title)
        self.layout().addWidget(field)
        return field

    def set_content(
        self, original: str = "", translation: str = "", context: str = "", stage: int | None = None
    ) -> None:
        self.original.setPlainText(original)
        self.translation.setPlainText(translation)
        self.context.setPlainText(context)
        self.stage.setText("翻译状态：" + STAGE_LABELS.get(stage, str(stage)) if stage is not None else "翻译状态：—")
        self.original.setPlaceholderText("没有原文")
        self.translation.setPlaceholderText("还没有译文")
        self.context.setPlaceholderText("没有提供补充说明")


class SyncReviewDetail(QWidget):
    """Render one row, leaving selection state and execution to the caller."""

    def __init__(self, direction: SyncReviewDirection, parent=None) -> None:
        super().__init__(parent)
        self.direction = direction
        self.row: SyncReviewRow | None = None
        self.decision = ""
        self.setMinimumWidth(0)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 0, 0, 0)
        root.setSpacing(12)
        self.heading = _label("请选择一条翻译", self, heading=True)
        self.reason = _label("", self)
        root.addWidget(self.heading)
        root.addWidget(self.reason)
        self.comparison = QWidget(self)
        cards = QHBoxLayout(self.comparison)
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(12)
        self.local = TranslationComparisonCard("我的内容", self.comparison, context_title="补充说明（工程中已有）")
        local_source = _label("当前工程中的翻译", self.local)
        self.local.source.addWidget(local_source)
        self.remote = TranslationComparisonCard(
            "ParaTranz 上的内容", self.comparison, context_title="补充说明（云端提供）"
        )
        self.candidates = QComboBox(self.remote)
        self.candidates.setMinimumWidth(0)
        self.candidates.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.candidates.setMinimumContentsLength(8)
        local_source.setFixedHeight(self.candidates.sizeHint().height())
        configure_accessible_widget(self.candidates, name="选择 ParaTranz 上对应的内容")
        self.remote.source.addWidget(self.candidates)
        cards.addWidget(self.local, 1)
        cards.addWidget(self.remote, 1)
        root.addWidget(self.comparison, 1)
        self.consequence = _label("", self)
        root.addWidget(self.consequence)
        actions = QHBoxLayout()
        self.use_remote = make_primary_button(direction.candidate_action, self)
        self.keep = QPushButton(direction.keep_label, self)
        self.next = QPushButton("下一条未决定 →", self)
        for button in (self.use_remote, self.keep, self.next):
            button.setAutoDefault(False)
            actions.addWidget(button)
        actions.addStretch(1)
        root.addLayout(actions)
        status = QHBoxLayout()
        self.choice = _label("", self)
        self.undo = QPushButton("撤销选择", self)
        self.undo.setAutoDefault(False)
        status.addWidget(self.choice, 1)
        status.addWidget(self.undo)
        root.addLayout(status)
        self.details_toggle = QPushButton("查看记录信息", self)
        self.details_toggle.setCheckable(True)
        self.details_toggle.setAutoDefault(False)
        root.addWidget(self.details_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(85)
        self.details.hide()
        self.details_toggle.toggled.connect(self.details.setVisible)
        root.addWidget(self.details)
        self.candidates.currentIndexChanged.connect(self.show_candidate)

    def show_row(self, row: SyncReviewRow | None, decision: str, selected_count: int) -> None:
        self.row = row
        self.decision = decision
        self.comparison.setVisible(row is not None)
        self.consequence.setVisible(row is not None)
        self.use_remote.setVisible(row is not None)
        self.details_toggle.setVisible(row is not None)
        self.details_toggle.setChecked(False)
        self.keep.setEnabled(selected_count > 0)
        self.keep.setText(
            self.direction.keep_label if selected_count < 2 else f"这 {selected_count:,} 条都先不{self.direction.verb}"
        )
        self.candidates.blockSignals(True)
        self.candidates.clear()
        self.candidates.addItem("选择对应的内容…", "")
        self.candidates.setEnabled(row is not None and bool(row.candidates))
        self.local.set_content()
        self.remote.set_content()
        if row is None:
            self.heading.setText(f"已选中 {selected_count:,} 条翻译" if selected_count else "没有选中的翻译")
            self.reason.setText(
                f"可以一起跳过这几条，保持{self.direction.retained_side}内容不变。要比较内容，请只选一条。"
                if selected_count
                else "可以换个搜索词或筛选条件，也可以完成选择，返回继续同步。"
            )
            self.choice.clear()
        else:
            self.heading.setText(
                "没有找到可以使用的云端内容"
                if not row.candidates
                else "这条翻译对应 ParaTranz 上的哪一条？"
                if row.can_select_remote and any(item.selectable for item in row.candidates)
                else "这些云端内容暂时不能使用"
            )
            self.local.set_content(row.original, row.translation, row.context, row.stage)
            for index, candidate in enumerate(row.candidates, start=1):
                scene = " ".join(candidate.context.split())[:45]
                text = " ".join((candidate.translation or candidate.original or "空内容").split())[:60]
                stage = STAGE_LABELS.get(candidate.stage, str(candidate.stage))
                label = (
                    f"{index}. 相同译文 ×{candidate.source_count:,} · {stage}"
                    if candidate.source_count > 1
                    else f"{index}. [{stage}] " + (f"{scene} · " if scene else "") + text
                )
                if not candidate.selectable:
                    label += "（仅供查看）"
                self.candidates.addItem(label, candidate.candidate_id)
                self.candidates.setItemData(index, candidate.label, Qt.ItemDataRole.ToolTipRole)
            if not row.candidates:
                self.candidates.setItemText(0, "没有可选内容")
            existing = self.candidates.findData(decision)
            if existing > 0:
                self.candidates.setCurrentIndex(existing)
        self.candidates.blockSignals(False)
        self.show_candidate()

    def show_candidate(self, *_args) -> None:
        row = self.row
        token = self.candidates.currentData()
        candidate = next((item for item in row.candidates if item.candidate_id == token), None) if row else None
        self.use_remote.setEnabled(candidate is not None and row.can_select_remote and candidate.selectable)
        self.remote.set_content()
        if row is None:
            return
        if self.decision == self.direction.keep_choice:
            self.choice.setText(f"已选择：本次不{self.direction.verb}这条。")
        elif self.decision:
            chosen = next(index for index, item in enumerate(row.candidates, 1) if item.candidate_id == self.decision)
            self.choice.setText(
                f"已选择第 {chosen} 条，尚未执行。"
                if token == self.decision
                else (
                    f"当前选择仍是第 {chosen} 条。这里只是在查看其他内容；"
                    f"点击“{self.direction.candidate_action}”才会改选。"
                )
            )
        else:
            self.choice.setText("还没决定，这条暂时不会同步。")
        consequence = self.direction.consequence
        if candidate is not None and candidate.source_count > 1:
            consequence += (
                f"\n这 {candidate.source_count:,} 条云端记录内容相同，本次只采用共同译文。"
                "以后上传时，若仍有多个目标，需要另行确认。"
            )
        if (
            not row.can_select_remote
            or not any(item.selectable for item in row.candidates)
            or (candidate is not None and not candidate.selectable)
        ):
            consequence = f"这条内容目前无法{self.direction.verb}。可以先跳过，不影响其他翻译。"
        self.consequence.setText(consequence)
        reasons = (row.reason, row.selection_disabled_reason, candidate.disabled_reason if candidate else "")
        self.reason.setText("\n".join(dict.fromkeys(reason for reason in reasons if reason)))
        self.details.setPlainText(
            f"本地记录：{row.key}" + (f"\nParaTranz 记录：{candidate.label}" if candidate else "")
        )
        if candidate is None:
            self.remote.original.setPlaceholderText("先在上方选择要比较的内容" if row.candidates else "找不到对应内容")
            self.remote.translation.setPlaceholderText("")
            self.remote.context.setPlaceholderText("")
        else:
            self.remote.set_content(candidate.original, candidate.translation, candidate.context, candidate.stage)
