"""Current lifecycle, command and projection fixture for widget editing tests."""

from dataclasses import replace

from tests.application.projects.test_lifecycle import _active, _harness, _refs
from transbridge.application.contracts import RequestContext
from transbridge.application.projections import ProjectionStore
from transbridge.application.projections.adapters import ProjectProjectionPublisher
from transbridge.application.projects import GuiProjectCommandFacade
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.v2 import SourceFingerprint, VariantAggregate, VariantEntryState, VariantSnapshot
from transbridge.ui.context import AppContext, CollectionSlot


class CurrentEditingProject:
    def __init__(self, entries, *, key="fixture.esp", labels=None, label_library=None, **slot_options):
        self.project_ref, self.variant_ref = _refs("project", "variant")
        self.store = ProjectionStore()
        self.publisher = ProjectProjectionPublisher(self.store)
        active = _active(self.project_ref, self.variant_ref)
        self.harness = _harness(active, {}, event=self.publisher)
        self.publisher.bind(self.harness.service)
        self.commands = GuiProjectCommandFacade(self.harness.service, self.publisher.rebuild)
        self.context = AppContext(
            project_projection=self.store,
            project_commands=self.commands,
            runtime_context=RequestContext("ui", run_id="editing"),
        )
        self.install(entries, key=key, labels=labels, label_library=label_library, **slot_options)

    @property
    def aggregate(self):
        return self.harness.service.active.variant

    def install(self, entries, *, key=None, labels=None, label_library=None, **slot_options):
        entries = tuple(entries)
        labels = labels or {}
        fingerprints = tuple(
            SourceFingerprint(namespace, "a" * 64) for namespace in {e.identity.namespace for e in entries}
        )
        snapshot = VariantSnapshot(
            self.variant_ref,
            fingerprints,
            tuple(
                VariantEntryState(
                    e.identity,
                    e.translation,
                    e.stage,
                    revision=e.revision,
                    provenance=e.provenance,
                    labels=tuple(labels.get(e.identity, labels.get(e.id, ()))),
                    external_refs=e.external_refs,
                )
                for e in entries
            ),
            label_library=tuple((label_library or {}).items()),
        )
        self.harness.service._active = replace(
            self.harness.service.active,
            variant=VariantAggregate(snapshot),
            persisted_variant_revision=snapshot.revision,
        )
        self.publisher.rebuild()
        key = key or self.context.active_key or "fixture.esp"
        self.context.add_slot(key, CollectionSlot("Fixture", TranslationEntryCollection(entries), **slot_options))

    def close(self):
        self.context.close_projection()
        self.store.close()
