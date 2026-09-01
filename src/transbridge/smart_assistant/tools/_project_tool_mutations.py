"""Commit copied tool results before changing the captured workbench projection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import uuid4

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import Provenance
from transbridge.application.io.mutation import ChangeSet, EntryPatch, MutationStatus
from transbridge.application.projects.variant_commands import EntryStatePatch
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
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
        app = getattr(context, "app_context", None) or context
        return cls(
            context,
            app,
            getattr(app, "collection", None),
            getattr(app, "active_version_identity", None),
            getattr(app, "project_revision", None),
            getattr(app, "variant_revision", None),
        )

    @property
    def authoritative(self) -> bool:
        return bool(getattr(self.app, "uses_authoritative_projection", False))

    def check(self) -> None:
        if getattr(self.app, "collection", None) is not self.collection:
            raise ValueError("活动集合已变化，操作未提交。")
        if not self.authoritative:
            return
        if self.identity is None or self.identity != self.app.active_version_identity:
            raise ValueError("活动工程版本已变化，操作未提交。")
        if self.project_revision != self.app.project_revision or self.variant_revision != self.app.variant_revision:
            raise ValueError("工程版本修订号已变化，请重新执行操作。")

    def expected(self) -> dict:
        self.check()
        project_id, variant_id = self.identity
        return {
            "expected_project_revision": self.project_revision,
            "expected_variant_revision": self.variant_revision,
            "expected_variant_ref": VariantRef(VariantId(variant_id), ProjectId(project_id)),
        }

    def dispatch(self, action):
        from .types import ExecutionContext

        context = self.context if isinstance(self.context, ExecutionContext) else ExecutionContext(app_context=self.app)
        return context.safe_mutate_wait(action)

    def commit_records(self, records) -> None:
        """CAS existing full EntryKeys; failures leave the collection untouched."""
        records = tuple(records)
        if not records:
            return

        def commit():
            self.check()
            for record in records:
                current = self.collection.get(record.identity)
                if current is None or (current.original, current.context) != (record.original, record.context):
                    raise ValueError("导入条目不能映射到当前来源；请使用 create_slot 登记独立来源。")
                if (current.metadata, current.string_id) != (record.metadata, record.string_id):
                    raise ValueError("来源元数据或定位信息已变化，不能通过条目更新静默替换。")
            changes = {
                record.identity: record
                for record in records
                if (record.translation, record.stage, record.external_refs)
                != (
                    self.collection.get(record.identity).translation,
                    self.collection.get(record.identity).stage,
                    self.collection.get(record.identity).external_refs,
                )
            }
            if not changes:
                return
            # Validate the complete candidate, including the external-ID index,
            # before committing anything to the authoritative aggregate.
            candidate = TranslationEntryCollection(
                replace(
                    entry,
                    translation=changes[entry.identity].translation,
                    stage=changes[entry.identity].stage,
                    external_refs=changes[entry.identity].external_refs,
                )
                if entry.identity in changes
                else entry
                for entry in self.collection
            )
            if self.authoritative:
                result = self.app.project_commands.replace_entry_records(
                    {
                        item.identity: EntryStatePatch(item.translation, item.stage, item.external_refs)
                        for item in records
                    },
                    self.app.runtime_context,
                    **self.expected(),
                )
                if not result.is_success:
                    raise ValueError("；".join(item.message for item in result.diagnostics))
                self.variant_revision = result.value["revision"]
                from transbridge.ui.source_hydration import apply_variant_projection

                snapshot = self.app._project_projection.snapshot()
                candidate = apply_variant_projection(candidate, snapshot.to_dict()["values"].get("entries", ()))
                self.app.active_slot.collection = candidate
                self.collection = candidate
            else:
                run_id = uuid4().hex
                request = RequestContext(
                    "assistant.entry-command",
                    run_id=run_id,
                    permissions=frozenset({
                        "entry.translation.write",
                        "entry.stage.write",
                        "entry.external_refs.write",
                    }),
                )
                result = self.collection.apply(
                    ChangeSet(
                        run_id,
                        tuple(
                            EntryPatch.create(
                                record.identity,
                                translation=record.translation,
                                stage=record.stage,
                                external_refs=record.external_refs,
                            )
                            for record in changes.values()
                        ),
                        tuple((key, self.collection.get(key).revision) for key in changes),
                        Provenance(run_id, request.owner_id, "assistant.entry-command"),
                    ),
                    request,
                )
                if result.status is not MutationStatus.APPLIED:
                    raise ValueError("；".join(item.message for item in result.diagnostics))
                if hasattr(self.app, "mark_dirty"):
                    self.app.mark_dirty()
            signal = getattr(self.app, "collection_changed", None)
            if signal is not None:
                signal.emit(self.collection)

        self.dispatch(commit)
