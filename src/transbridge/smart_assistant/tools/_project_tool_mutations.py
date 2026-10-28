"""Commit copied tool results before changing the captured workbench projection."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.projects.variant_commands import EntryStatePatch
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef


@dataclass
class ProjectToolTarget:
    context: object
    app: object
    collection: object
    identity: object
    project_revision: int | None
    variant_revision: int | None

    @classmethod
    def capture(cls, context):
        from .types import ExecutionContext

        app = getattr(context, "app_context", None) or context
        if isinstance(context, ExecutionContext):
            return cls(
                context,
                app,
                context._target_collection,
                context._target_version_identity,
                context._target_project_revision,
                context._target_variant_revision,
            )
        return cls(
            context,
            app,
            getattr(app, "collection", None),
            getattr(app, "active_version_identity", None),
            getattr(app, "project_revision", None),
            getattr(app, "variant_revision", None),
        )

    def check(self) -> None:
        if self.identity is None or self.identity != self.app.active_version_identity:
            raise ValueError("活动工程版本已变化，操作未提交。")
        source_key = getattr(self.context, "_target_source_key", None)
        if source_key is not None and source_key != getattr(self.app, "active_key", None):
            raise ValueError("活动来源已变化，操作未提交。")

    def expected(self) -> dict:
        self.check()
        project_id, variant_id = self.identity
        return {
            "expected_project_revision": self.app.project_revision,
            "expected_variant_revision": self.app.variant_revision,
            "expected_variant_ref": VariantRef(VariantId(variant_id), ProjectId(project_id)),
        }

    def dispatch(self, action):
        from .types import ExecutionContext

        context = self.context if isinstance(self.context, ExecutionContext) else ExecutionContext(app_context=self.app)
        return context.safe_mutate_wait(action)

    def acknowledge_projection(self) -> None:
        """Advance this execution's target only after its own successful command."""
        from .types import ExecutionContext

        if self.identity != self.app.active_version_identity:
            raise ValueError("活动工程版本已变化，未更新操作目标。")
        self.collection = self.app.collection
        self.project_revision = self.app.project_revision
        self.variant_revision = self.app.variant_revision
        if isinstance(self.context, ExecutionContext):
            object.__setattr__(self.context, "_target_collection", self.collection)
            object.__setattr__(self.context, "_target_source_key", getattr(self.app, "active_key", None))
            if "collection" in self.context.__dict__:
                object.__setattr__(self.context, "collection", self.collection)
            object.__setattr__(self.context, "_target_project_revision", self.project_revision)
            object.__setattr__(self.context, "_target_variant_revision", self.variant_revision)

    def commit_records(self, records, *, before=None) -> list[str]:
        """CAS existing full EntryKeys; failures leave the collection untouched."""
        records = tuple(records)
        if not records:
            return []
        warnings = []
        before = (
            {record.identity: self.collection.get(record.identity).snapshot() for record in records}
            if before is None
            else {entry.entry_key: entry for entry in before}
        )

        def commit():
            self.check()
            from transbridge.ui.entry_projection import EntryProjectionUpdate

            collection = self.app.collection
            update = EntryProjectionUpdate.from_snapshot(
                self.app._project_projection.snapshot(), keys=(record.identity for record in records)
            )
            for record in records:
                if self.collection is None or self.collection.get(record.identity) is None:
                    raise ValueError("条目不属于捕获的来源，操作未提交。")
                current = None if collection is None else collection.get(record.identity)
                if current is None or (current.original, current.context) != (record.original, record.context):
                    raise ValueError("导入条目不能映射到当前来源；请使用 create_slot 登记独立来源。")
                if (current.metadata, current.string_id) != (record.metadata, record.string_id):
                    raise ValueError("来源元数据或定位信息已变化，不能通过条目更新静默替换。")
                baseline = _record_state(before[record.identity])
                if _record_state(current) != baseline or _record_state(update.entry(current)) != baseline:
                    raise ValueError("目标词条已被其他操作修改，请重新载入后核对。")
            changes = {
                record.identity: record
                for record in records
                if (record.translation, record.stage, record.external_refs)
                != (
                    collection.get(record.identity).translation,
                    collection.get(record.identity).stage,
                    collection.get(record.identity).external_refs,
                )
            }
            if not changes:
                return
            from .undo_capture import capture_variant_command

            result = capture_variant_command(
                self.context,
                lambda: self.app.project_commands.replace_entry_records(
                    {
                        item.identity: EntryStatePatch(item.translation, item.stage, item.external_refs)
                        for item in changes.values()
                    },
                    self.app.runtime_context,
                    **self.expected(),
                ),
                warnings=warnings,
            )
            if not result.is_success:
                raise ValueError("；".join(item.message for item in result.diagnostics))
            self.variant_revision = result.value["revision"]
            snapshot = self.app._project_projection.snapshot()
            update = EntryProjectionUpdate.from_snapshot(snapshot, keys=changes)
            previous = self.app.collection
            projected = update.collection(previous)
            self.app.active_slot.collection = projected
            self.acknowledge_projection()
            signal = getattr(self.app, "collection_changed", None)
            if signal is not None and projected is not previous:
                signal.emit(self.collection)

        self.dispatch(commit)
        return warnings


def _record_state(entry):
    return (
        entry.original,
        entry.context,
        entry.metadata,
        entry.string_id,
        entry.translation,
        entry.stage,
        entry.external_refs,
    )
