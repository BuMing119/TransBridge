"""Confirmed, atomic translation clearing for a captured Workbench selection."""

from __future__ import annotations

from collections.abc import Iterable

from PyQt6.QtWidgets import QMessageBox, QWidget

from transbridge.converter.translation_entry import STAGE_UNTRANSLATED, TranslationEntry
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.ui.context import AppContext


class TranslationResetAction:
    """Own selection, confirmation and the commit boundary without retaining Qt items."""

    def __init__(
        self,
        context: AppContext,
        clicked_entry: TranslationEntry,
        *,
        entries: Iterable[TranslationEntry],
        selected_ids: Iterable[str],
        parent: QWidget,
    ) -> None:
        selected = set(selected_ids)
        scope = (
            (entry for entry in entries if entry.id in selected) if clicked_entry.id in selected else (clicked_entry,)
        )
        self._entries = tuple(entry for entry in scope if entry.translation or entry.stage != STAGE_UNTRANSLATED)
        self._context = context
        self._parent = parent
        self._version_identity = context.active_version_identity
        self._source_key = context.active_key
        self._collection = context.collection
        self._project_revision = context.project_revision
        self._variant_revision = context.variant_revision
        self._before_states = tuple((entry.translation, entry.stage) for entry in self._entries)

    @property
    def enabled(self) -> bool:
        return bool(self._entries)

    def run(self) -> bool:
        """Clear the captured scope only after confirmation and a successful commit."""
        if not self.enabled:
            return False
        decision = QMessageBox.question(
            self._parent,
            "取消翻译",
            f"将清空 {len(self._entries)} 条词条的译文，并将状态恢复为“未翻译”。\n"
            "原文和标签保持不变。清空译文后无法直接撤销，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if decision != QMessageBox.StandardButton.Yes:
            return False
        context = self._context
        if (
            context.active_version_identity != self._version_identity
            or context.active_key != self._source_key
            or context.collection is not self._collection
            or tuple((entry.translation, entry.stage) for entry in self._entries) != self._before_states
        ):
            QMessageBox.warning(self._parent, "取消翻译未执行", "翻译内容已变化，请重新选择词条后再试。")
            return False
        if context.uses_authoritative_projection:
            commands = context.project_commands
            runtime = context.runtime_context
            if commands is None or runtime is None or self._version_identity is None:
                QMessageBox.warning(
                    self._parent, "取消翻译失败", "工程写入服务不可用，译文未改变。请重新打开工程后再试。"
                )
                return False
            project_id, variant_id = self._version_identity
            result = commands.replace_entry_states(
                {entry.identity: ("", STAGE_UNTRANSLATED) for entry in self._entries},
                runtime,
                expected_project_revision=self._project_revision,
                expected_variant_revision=self._variant_revision,
                expected_variant_ref=VariantRef(VariantId(variant_id), ProjectId(project_id)),
            )
            if not result.is_success:
                detail = result.diagnostics[0].message if result.diagnostics else "工程提交失败"
                QMessageBox.warning(self._parent, "取消翻译失败", f"{detail}\n\n译文未改变，请刷新后重试。")
                return False

        # Keep the legacy visible entry objects in sync only after authority accepts
        # the whole batch, matching the existing inline-edit projection boundary.
        for entry in self._entries:
            entry.translation = ""
            entry.stage = STAGE_UNTRANSLATED
        if not context.uses_authoritative_projection:
            context.mark_dirty()
        return True
