from types import SimpleNamespace

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import EntryKey, ExternalEntryRef, SourceNamespace
from transbridge.application.io.stage_policy import Stage
from transbridge.application.projects.gui_facade import GuiProjectCommandFacade
from transbridge.application.projects.variant_commands import EntryStatePatch
from transbridge.persistence.v2 import (
    ProjectDto,
    ProjectRef,
    SchemaEnvelope,
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
    project_ref = ProjectRef(project_id)
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

    projections: list[int] = []

    class Lifecycle:
        def __init__(self) -> None:
            project = ProjectDto(
                SchemaEnvelope(
                    2,
                    project_ref.kind,
                    project_id.value,
                    0,
                    {
                        "name": "project",
                        "sources": [],
                        "variant_ids": [variant_ref.identity.value],
                        "active_variant_id": variant_ref.identity.value,
                    },
                )
            )
            self.active = SimpleNamespace(
                project=project,
                variant=aggregate,
                formal_variant_ref=variant_ref,
            )
            self.snapshot_names: list[str] = []

        def commit_active_variant(self, change_set, context, *, expected_project_revision):
            from transbridge.application.contracts import OperationResult

            if expected_project_revision != self.active.project.envelope.revision:
                return OperationResult.failed(ValueError("stale project"), run_id=context.run_id)
            revision = aggregate.commit(change_set, context)
            projections.append(revision)
            return OperationResult.completed({"revision": revision}, run_id=context.run_id)

        def save_snapshot(self, name, context):
            self.snapshot_names.append(name)
            from transbridge.application.contracts import OperationResult

            return OperationResult.completed({"name": name}, run_id=context.run_id)

    lifecycle = Lifecycle()
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
    assert state.revision.value == 1
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


def test_ai_entry_state_commit_rejects_stale_expected_revision_without_write() -> None:
    facade, _lifecycle, aggregate, entry_key, context, projections = _facade()

    result = facade.replace_entry_states(
        {entry_key: ("译文", 1)},
        context,
        expected_variant_revision=9,
    )

    assert not result.is_success
    assert aggregate.revision == 0
    assert projections == []


def test_label_commit_rejects_stale_expected_revision_without_write() -> None:
    facade, _lifecycle, aggregate, entry_key, context, projections = _facade()

    result = facade.replace_labels(
        {entry_key: {"review"}},
        {"review": {"name": "Review", "color": "#fff"}},
        context,
        expected_project_revision=0,
        expected_variant_revision=9,
        expected_variant_ref=aggregate.snapshot().ref,
    )

    assert not result.is_success
    assert aggregate.revision == 0
    assert aggregate.snapshot().entries[0].labels == ()
    assert projections == []


def test_full_entry_patch_persists_external_identity_with_variant_revision() -> None:
    facade, _lifecycle, aggregate, entry_key, context, _projections = _facade()
    reference = ExternalEntryRef("paratranz", "project:42", 17)

    result = facade.replace_entry_records(
        {entry_key: EntryStatePatch("远端译文", Stage.CHECKED, (reference,))},
        context,
        expected_project_revision=0,
        expected_variant_revision=0,
    )

    assert result.is_success
    state = aggregate.snapshot().entries[0]
    assert state.external_refs == (reference,)
    assert state.revision.value == 1
