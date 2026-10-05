from types import SimpleNamespace

from tests.conftest import make_entry
from transbridge.application.projections.models import ProjectionSnapshot
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.dialogue.projection_sync import EditorProjectionSync


class Signal:
    def __init__(self):
        self.listeners = []

    def connect(self, listener):
        self.listeners.append(listener)

    def disconnect(self, listener):
        self.listeners.remove(listener)

    def emit(self, *_args):
        for listener in self.listeners:
            listener()


def setup_sync():
    entry = make_entry(translation="old")
    context = SimpleNamespace(
        collection=TranslationEntryCollection((entry,)),
        active_version_identity=("project", "version"),
        active_key="source",
        collection_changed=Signal(),
    )
    state = {"entry_key": entry.identity.to_dict(), "translation": "new", "stage": 1}
    store = SimpleNamespace(current=ProjectionSnapshot("stream", 1, 0, {"entries": [state]}))
    store.snapshot = lambda: store.current
    return context, store, EditorProjectionSync(store), entry


def test_unchanged_navigation_skips_serialization_and_preserves_collection(monkeypatch):
    context, store, sync, entry = setup_sync()
    assert sync.sync(context)
    assert context.collection.get(entry.identity).translation == "new"
    collection = context.collection
    monkeypatch.setattr(ProjectionSnapshot, "to_dict", lambda _: (_ for _ in ()).throw(AssertionError("rebuilt")))
    assert not sync.sync(context)
    assert context.collection is collection


def test_replaced_snapshot_with_same_revision_is_not_assumed_equal():
    context, store, sync, entry = setup_sync()
    sync.sync(context)
    state = store.current.to_dict()["values"]["entries"][0]
    state["translation"] = "external"
    store.current = ProjectionSnapshot("stream", 1, 0, {"entries": [state]})
    assert sync.sync(context)
    assert context.collection.get(entry.identity).translation == "external"


def test_replaced_collection_and_in_place_mutation_are_resynchronized():
    context, store, sync, entry = setup_sync()
    sync.sync(context)
    context.collection = TranslationEntryCollection((entry,))
    assert sync.sync(context)
    context.collection.remove(entry.identity)
    context.collection.add(entry)
    assert sync.sync(context)
    assert context.collection.get(entry.identity).translation == "new"


def test_source_and_version_changes_invalidate_cache(monkeypatch):
    context, store, sync, entry = setup_sync()
    sync.sync(context)
    original = ProjectionSnapshot.to_dict
    calls = []

    def record(snapshot):
        calls.append(snapshot)
        return original(snapshot)

    monkeypatch.setattr(ProjectionSnapshot, "to_dict", record)
    context.active_key = "other source"
    assert not sync.sync(context)
    context.active_version_identity = ("project", "other version")
    assert not sync.sync(context)
    assert len(calls) == 2


def test_notification_invalidates_legacy_mutation_cache():
    context, store, sync, entry = setup_sync()
    sync.sync(context)
    # Simulate legacy mutation without relying on its deprecated public setter.
    object.__setattr__(context.collection.get(entry.identity), "translation", "legacy")
    context.collection_changed.emit()
    assert sync.sync(context)
    assert context.collection.get(entry.identity).translation == "new"


def test_revisionless_snapshot_is_always_read_and_missing_snapshot_resets_cache():
    context, store, sync, entry = setup_sync()
    values = store.current.to_dict()
    store.current = SimpleNamespace(to_dict=lambda: values)
    assert sync.sync(context)
    values["values"]["entries"][0]["translation"] = "changed"
    assert sync.sync(context)
    assert context.collection.get(entry.identity).translation == "changed"
    store.current = None
    assert not sync.sync(context)


def test_new_revision_with_equal_values_does_not_replace_collection():
    context, store, sync, entry = setup_sync()
    sync.sync(context)
    collection = context.collection
    store.current = ProjectionSnapshot("stream", 2, 0, store.current.to_dict()["values"])
    assert not sync.sync(context)
    assert context.collection is collection


def test_collection_setter_notification_does_not_prevent_warm_cache(monkeypatch):
    context, store, sync, entry = setup_sync()

    class NotifyingContext:
        active_key = context.active_key
        active_version_identity = context.active_version_identity
        collection_changed = Signal()

        def __init__(self):
            self._collection = context.collection

        @property
        def collection(self):
            return self._collection

        @collection.setter
        def collection(self, value):
            self._collection = value
            self.collection_changed.emit()

    notifying = NotifyingContext()
    assert sync.sync(notifying)
    monkeypatch.setattr(ProjectionSnapshot, "to_dict", lambda _: (_ for _ in ()).throw(AssertionError("rebuilt")))
    assert not sync.sync(notifying)
    assert notifying.collection.get(entry.identity).translation == "new"


def test_absent_collection_then_reappearing_collection_is_synchronized():
    context, store, sync, entry = setup_sync()
    sync.sync(context)
    context.collection = None
    assert not sync.sync(context)
    context.collection = TranslationEntryCollection((entry,))
    assert sync.sync(context)


def test_applied_publication_retains_proof_without_reprojecting(monkeypatch):
    context, store, sync, entry = setup_sync()
    sync.sync(context)
    sync.publish_applied(context, store.current)
    monkeypatch.setattr(ProjectionSnapshot, "to_dict", lambda _: (_ for _ in ()).throw(AssertionError("rebuilt")))
    assert not sync.sync(context)
    assert context.collection.get(entry.identity).translation == "new"


def test_nested_legacy_mutation_during_publication_invalidates_proof():
    context, store, sync, entry = setup_sync()
    sync.sync(context)
    handled = False

    def modify(*_args):
        nonlocal handled
        if not handled:
            handled = True
            object.__setattr__(context.collection.get(entry.identity), "translation", "legacy nested change")
            context.collection_changed.emit()

    context.collection_changed.connect(modify)
    sync.publish_applied(context, store.current)
    assert sync.sync(context)
    assert context.collection.get(entry.identity).translation == "new"


def test_collection_replaced_during_publication_is_not_marked_synced():
    context, store, sync, entry = setup_sync()
    sync.sync(context)
    context.collection_changed.connect(lambda *_: setattr(context, "collection", TranslationEntryCollection((entry,))))
    sync.publish_applied(context, store.current)
    assert sync.sync(context)
    assert context.collection.get(entry.identity).translation == "new"


def test_snapshot_changed_during_publication_is_not_marked_synced():
    context, store, sync, entry = setup_sync()
    sync.sync(context)
    state = store.current.to_dict()["values"]["entries"][0]
    state["translation"] = "external during notification"
    newer = ProjectionSnapshot("stream", 2, 0, {"entries": [state]})
    context.collection_changed.connect(lambda *_: setattr(store, "current", newer))
    sync.publish_applied(context, store.current)
    assert sync.sync(context)
    assert context.collection.get(entry.identity).translation == "external during notification"
