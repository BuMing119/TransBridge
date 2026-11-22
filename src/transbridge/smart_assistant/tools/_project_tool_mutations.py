# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Commit copied tool results against the captured workbench targets."""

from __future__ import annotations

from dataclasses import dataclass

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

    def acknowledge_projection(self, *, preserve_scope: bool = False) -> None:
        """Advance this execution's target only after its own successful command."""
        from .types import ExecutionContext
        from .workbench_rows import workbench_source_identity

        if self.identity != self.app.active_version_identity:
            if preserve_scope:
                return
            raise ValueError("活动工程版本已变化，未更新操作目标。")
        current_key = getattr(self.app, "active_key", None)
        source = workbench_source_identity(self.app)
        if preserve_scope and isinstance(self.context, ExecutionContext):
            if self.context._target_source_key != current_key or self.context._target_workbench_source != source:
                return
        self.collection = self.app.collection
        self.project_revision = self.app.project_revision
        self.variant_revision = self.app.variant_revision
        if isinstance(self.context, ExecutionContext):
            if preserve_scope and getattr(self.context, "_source_input_full_collection", None) is not None:
                from transbridge.converter.translation_entry_collection import TranslationEntryCollection

                scoped = self.context._target_collection
                object.__setattr__(self.context, "_source_input_full_collection", self.collection)
                self.collection = TranslationEntryCollection(
                    current for entry in scoped if (current := self.collection.get(entry.identity)) is not None
                )
            if self.context._target_source_key != current_key or self.context._target_workbench_source != source:
                from transbridge.application.projects.dictionary_apply import dictionary_mod_id

                object.__setattr__(self.context, "_target_dictionary_mod_id", dictionary_mod_id(self.app))
            object.__setattr__(self.context, "_target_collection", self.collection)
            object.__setattr__(self.context, "_target_source_key", current_key)
            object.__setattr__(self.context, "_target_workbench_source", source)
            if "collection" in self.context.__dict__:
                object.__setattr__(self.context, "collection", self.collection)
            object.__setattr__(self.context, "_target_project_revision", self.project_revision)
            object.__setattr__(self.context, "_target_variant_revision", self.variant_revision)
