"""Atomic translation-stage updates for a captured Workbench selection."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from PyQt6.QtWidgets import QMessageBox, QWidget

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.ui.context import AppContext
from transbridge.ui.workbench.entry_action_scope import resolve_entry_action_scope


@dataclass(frozen=True, slots=True)
class TranslationStageChange:
    entry: TranslationEntry
    previous_stage: int


class TranslationStageAction:
    """Commit one stage change for either the clicked row or its selected scope."""

    def __init__(
        self,
        context: AppContext,
        clicked_entry: TranslationEntry,
        stage: int,
        *,
        entries: Iterable[TranslationEntry],
        selected_ids: Iterable[str],
        parent: QWidget,
    ) -> None:
        scope = resolve_entry_action_scope(clicked_entry, entries, selected_ids)
        self._changes = tuple(TranslationStageChange(entry, entry.stage) for entry in scope if entry.stage != stage)
        self._context = context
        self._stage = stage
        self._parent = parent
        self._version_identity = context.active_version_identity
        self._source_key = context.active_key
        self._collection = context.collection
        self._project_revision = context.project_revision
        self._variant_revision = context.variant_revision
        self._before_states = tuple((change.entry.translation, change.entry.stage) for change in self._changes)

    def run(self) -> tuple[TranslationStageChange, ...]:
        """Apply the captured stage changes only after the authoritative commit succeeds."""

        if not self._changes:
            return ()
        context = self._context
        if (
            context.active_version_identity != self._version_identity
            or context.active_key != self._source_key
            or context.collection is not self._collection
            or tuple((change.entry.translation, change.entry.stage) for change in self._changes) != self._before_states
        ):
            QMessageBox.warning(self._parent, "翻译状态未修改", "翻译内容已变化，请重新选择词条后再试。")
            return ()
        if context.uses_authoritative_projection:
            commands = context.project_commands
            runtime = context.runtime_context
            if commands is None or runtime is None or self._version_identity is None:
                QMessageBox.warning(
                    self._parent, "翻译状态修改失败", "工程写入服务不可用，状态未改变。请重新打开工程后再试。"
                )
                return ()
            project_id, variant_id = self._version_identity
            result = commands.replace_entry_states(
                {change.entry.identity: (change.entry.translation, self._stage) for change in self._changes},
                runtime,
                expected_project_revision=self._project_revision,
                expected_variant_revision=self._variant_revision,
                expected_variant_ref=VariantRef(VariantId(variant_id), ProjectId(project_id)),
            )
            if not result.is_success:
                detail = result.diagnostics[0].message if result.diagnostics else "工程提交失败"
                QMessageBox.warning(self._parent, "翻译状态修改失败", f"{detail}\n\n状态未改变，请刷新后重试。")
                return ()

        for change in self._changes:
            change.entry.stage = self._stage
        if not context.uses_authoritative_projection:
            context.mark_dirty()
        return self._changes


__all__ = ["TranslationStageAction", "TranslationStageChange"]
