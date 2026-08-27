from types import SimpleNamespace

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.io.stage_policy import Stage
from transbridge.application.projects.gui_facade import GuiProjectCommandFacade
from transbridge.persistence.v2 import (
    SourceFingerprint,
    VariantAggregate,
    VariantEntryState,
    VariantId,
    VariantRef,
    VariantSnapshot,
)
from transbridge.persistence.v2.ids import ProjectId


def _facade():
    project_id = ProjectId("project")
    variant_ref = VariantRef(VariantId("variant"), project_id)
    namespace = SourceNamespace("source:plugin:fixture")
    entry_key = EntryKey(namespace, "entry")
    aggregate = VariantAggregate(
        VariantSnapshot(
            variant_ref,
            (SourceFingerprint(namespace, "a" * 64),),
            (VariantEntryState(entry_key),),
        )
    )

    class Lifecycle:
        def __init__(self) -> None:
            self.active = SimpleNamespace(variant=aggregate, formal_variant_ref=variant_ref)
            self.snapshot_names: list[str] = []

        def save_snapshot(self, name, context):
            self.snapshot_names.append(name)
            from transbridge.application.contracts import OperationResult

            return OperationResult.completed({"name": name}, run_id=context.run_id)

    lifecycle = Lifecycle()
    projections: list[int] = []
    facade = GuiProjectCommandFacade(lifecycle, None, lambda: projections.append(aggregate.revision))
    context = RequestContext(
        owner_id="ui",
        run_id="ai-run",
        project_id="project",
        variant_id="variant",
    )
    return facade, lifecycle, aggregate, entry_key, context, projections


def test_ai_entry_states_commit_once_and_snapshot_delegates_to_lifecycle() -> None:
    facade, lifecycle, aggregate, entry_key, context, projections = _facade()

    committed = facade.replace_entry_states({entry_key: ("译文", 1)}, context)
    snapshotted = facade.save_snapshot("AI-翻译前", context)

    assert committed.is_success
    assert aggregate.revision == 1
    state = aggregate.snapshot().entries[0]
    assert state.translation == "译文"
    assert state.stage is Stage.TRANSLATED
    assert projections == [1]
    assert snapshotted.is_success
    assert lifecycle.snapshot_names == ["AI-翻译前"]


def test_ai_entry_state_commit_rejects_unknown_identity_without_partial_write() -> None:
    facade, _lifecycle, aggregate, _entry_key, context, projections = _facade()
    missing = EntryKey(SourceNamespace("source:plugin:fixture"), "missing")

    result = facade.replace_entry_states({missing: ("译文", 1)}, context)

    assert not result.is_success
    assert aggregate.revision == 0
    assert projections == []
