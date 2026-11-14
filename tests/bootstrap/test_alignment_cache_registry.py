from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.bootstrap import alignment_cache
from transbridge.bootstrap.terminology import ProductionTerminologyComposition
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from transbridge.persistence.v2.translation_epochs import TranslationEpochTracker


class _Repositories:
    def __init__(self):
        self.paths = []
        self.closed = False

    def alignment_cache_path(self, project_id):
        self.paths.append(project_id)
        return Path(project_id) / "project.sqlite"

    @contextmanager
    def deletion_scope(self, _project_id):
        yield

    def close(self):
        self.closed = True


def _active(project="project", variant="main", epochs=None):
    return SimpleNamespace(
        project_ref=ProjectRef(ProjectId(project)),
        variant=SimpleNamespace(
            ref=VariantRef(VariantId(variant), ProjectId(project)),
            alignment_epochs=epochs or TranslationEpochTracker(),
        ),
    )


def _factory(monkeypatch):
    writes = []

    class Backend:
        def __init__(self, path):
            self.path = path

        def clear(self, variant, keys):
            writes.append((self.path, variant, keys))

    monkeypatch.setattr(alignment_cache, "ProjectAlignmentCache", Backend)
    repository = _Repositories()
    lifecycle = SimpleNamespace(active=_active())
    factory = ProductionTerminologyComposition(repository, None, None, None, None, None, lifecycle)
    return factory, repository, lifecycle, writes


def test_registry_reuses_only_same_project_and_variant_without_opening_databases(monkeypatch):
    factory, repository, _lifecycle, writes = _factory(monkeypatch)
    try:
        first = factory.alignment_cache_for("project", "main")
        assert factory.alignment_cache_for("project", "main") is first
        other_variant = factory.alignment_cache_for("project", "other")
        other_project = factory.alignment_cache_for("other-project", "main")
        assert first is not other_variant and first is not other_project
        assert repository.paths == ["project", "project", "other-project"]
        assert writes == []
    finally:
        factory.close()
    assert repository.closed


def test_live_edits_and_late_binding_clear_only_changed_entry_and_noop_rebinding_does_not(monkeypatch):
    factory, _repository, lifecycle, writes = _factory(monkeypatch)
    key = EntryKey(SourceNamespace("source"), "entry")
    tracker = lifecycle.active.variant.alignment_epochs
    tracker.invalidate((key,))
    try:
        cache = factory.alignment_cache_for("project", "main")
        cache.flush()
        assert len(writes) == 1 and writes[0][2] == (key.serialize(),)
        factory.alignment_cache_for("project", "main")
        cache.flush()
        assert len(writes) == 1
        tracker.invalidate((key,))
        cache.flush()
        assert len(writes) == 2
        next_tracker = tracker.fork()
        lifecycle.active = _active(epochs=next_tracker)
        assert factory.alignment_cache_for("project", "main") is cache
        cache.flush()
        assert len(writes) == 2
        tracker.invalidate((key,))
        cache.flush()
        assert len(writes) == 2
        next_tracker.invalidate((key,))
        cache.flush()
        assert len(writes) == 3
    finally:
        factory.close()


def test_project_deletion_drains_and_disconnects_its_cache_only(monkeypatch):
    factory, _repository, lifecycle, writes = _factory(monkeypatch)
    key = EntryKey(SourceNamespace("source"), "entry")
    try:
        first = factory.alignment_cache_for("project", "main")
        other = factory.alignment_cache_for("other-project", "main")
        tracker = lifecycle.active.variant.alignment_epochs
        tracker.invalidate((key,))
        with factory.deletion_scope("project"):
            assert first._closed
            assert not other._closed
            assert len(writes) == 1
        tracker.invalidate((key,))
        assert len(writes) == 1
    finally:
        factory.close()
