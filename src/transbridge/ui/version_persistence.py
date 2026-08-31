"""Persistence adapter for version-scoped UI workflows."""

from __future__ import annotations


class VersionPersistence:
    """Save and snapshot one active V2 or legacy Project/Variant."""

    def __init__(self, context: object, expected_identity: tuple[str, str]) -> None:
        self._context = context
        self._expected_identity = expected_identity
        self._expected_project_revision = getattr(context, "project_revision", None)
        self._expected_variant_revision = getattr(context, "variant_revision", None)
        self._translations_committed = False
        self._translation_commit_result = None
        self._version_saved = False

    def create_snapshot(self, name: str, entries: tuple[object, ...]):
        self._require_identity()
        if self._context.uses_authoritative_projection:
            commands, runtime_context = self._v2_ports()
            return commands.save_snapshot(name, runtime_context)

        project, variant_store, variant_name = self._legacy_state()
        was_dirty = variant_store.dirty
        variant_store.collect_from(list(entries), self._context.entry_labels, self._context.label_library)
        try:
            return variant_store.save_snapshot(project.variant_dir(variant_name) / "snapshots", name)
        finally:
            variant_store.dirty = was_dirty

    def commit_translation(self, entries: tuple[object, ...]):
        """Commit translated entries once without making the version durable."""

        self._require_identity()
        if self._translations_committed:
            return self._translation_commit_result

        if self._context.uses_authoritative_projection:
            from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef

            commands, runtime_context = self._v2_ports()
            states = {entry.identity: (entry.translation, entry.stage) for entry in entries}
            project_id, variant_id = self._expected_identity
            committed = commands.replace_entry_states(
                states,
                runtime_context,
                expected_project_revision=self._expected_project_revision,
                expected_variant_revision=self._expected_variant_revision,
                expected_variant_ref=VariantRef(VariantId(variant_id), ProjectId(project_id)),
            )
            if committed.is_success:
                self._translations_committed = True
                self._translation_commit_result = committed
                revision = committed.value.get("revision") if isinstance(committed.value, dict) else None
                if revision is not None:
                    self._expected_variant_revision = int(revision)
            return committed

        _project, variant_store, _variant_name = self._legacy_state()
        variant_store.collect_from(list(entries), self._context.entry_labels, self._context.label_library)
        self._translations_committed = True
        return None

    def save_translation(self, entries: tuple[object, ...], snapshot_name: str):
        committed = self.commit_translation(entries)
        if self._context.uses_authoritative_projection:
            commands, runtime_context = self._v2_ports()
            if committed is not None and not committed.is_success:
                return committed
            if not self._version_saved:
                saved = commands.save(runtime_context)
                if not saved.is_success:
                    return saved
                self._version_saved = True
            return commands.save_snapshot(snapshot_name, runtime_context)

        project, variant_store, variant_name = self._legacy_state()
        if not self._version_saved:
            variant_store.save()
            self._version_saved = True
        return variant_store.save_snapshot(project.variant_dir(variant_name) / "snapshots", snapshot_name)

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

    def _legacy_state(self):
        project = self._context.active_project
        variant_store = self._context.variant_store
        variant_name = self._context.active_variant
        if project is None or variant_store is None or variant_name is None:
            raise RuntimeError("请先打开一个项目版本。")
        return project, variant_store, variant_name


__all__ = ["VersionPersistence"]
