"""Persistence adapter for version-scoped UI workflows."""

from __future__ import annotations

from dataclasses import replace

from transbridge.application.contracts import RequestContext


class VersionPersistence:
    """Merge task results and save the current authoritative project."""

    def __init__(self, context: object, expected_identity: tuple[str, str]) -> None:
        self._context = context
        self._expected_identity = expected_identity
        self._translations_committed = False
        self._version_saved = False

    @property
    def project_saved(self) -> bool:
        return self._version_saved

    def mark_project_saved(self) -> None:
        """Acknowledge an external save of this session's already committed version."""
        self._require_identity()
        if not self._translations_committed:
            raise RuntimeError("任务结果尚未应用，不能确认项目已保存。")
        self._version_saved = True

    def merge_translation(self, candidates, policy, *, commit_mutation=None, before_commit=None, decision_states=None):
        """Merge a frozen task's selected results into the latest target content."""
        result = self.freeze_merge_translation(
            candidates,
            policy,
            commit_mutation=commit_mutation,
            before_commit=before_commit,
            decision_states=decision_states,
        )()
        if result.is_success:
            self._translations_committed = True
            self._version_saved = False
        return result

    def freeze_merge_translation(
        self, candidates, policy, *, commit_mutation=None, before_commit=None, decision_states=None
    ):
        """Freeze authoritative ports on Qt; perform merge and evidence I/O in a worker."""
        self._require_identity()
        from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef

        commands, context = self._frozen_v2_ports()
        project_id, variant_id = self._expected_identity
        target = VariantRef(VariantId(variant_id), ProjectId(project_id))
        candidates = tuple(candidates)

        def operation():
            return commands.merge_entry_states(
                candidates,
                context,
                expected_variant_ref=target,
                policy=policy,
                commit_mutation=commit_mutation,
                before_commit=before_commit,
                decision_states=decision_states,
            )

        return operation

    def acknowledge_merge(self, result):
        """Latch a completed worker commit after projection delivery on Qt."""
        self._require_identity()
        self._translations_committed = True
        self._version_saved = False

    def freeze_save_project(self):
        """Persist the current project without generating automatic full history."""
        self._require_identity()
        commands, context = self._frozen_v2_ports()
        from transbridge.application.projects.gui_facade import GuiProjectCommandFacade

        options = (
            {"expected_active_identity": self._expected_identity}
            if isinstance(commands, GuiProjectCommandFacade)
            else {}
        )

        def operation():
            result = commands.save(context, **options)
            if result.is_success:
                self._version_saved = True
            return result

        return operation

    def acknowledge_save(self):
        self._require_identity()

    def _require_identity(self) -> None:
        current = self._context.active_version_identity
        if current is None:
            raise RuntimeError("请先打开一个项目版本。")
        if current != self._expected_identity:
            raise RuntimeError("活动项目或版本已变化，请重新运行 AI 工作流。")

    def _v2_ports(self):
        commands = self._context.project_commands
        runtime_context = self._context.runtime_context
        if commands is None or runtime_context is None:
            raise RuntimeError("权威版本保存服务不可用。")
        return commands, runtime_context

    def _frozen_v2_ports(self):
        commands, context = self._v2_ports()
        if isinstance(context, RequestContext):
            context = replace(context, project_id=self._expected_identity[0], variant_id=self._expected_identity[1])
        return commands, context


__all__ = ["VersionPersistence"]
