from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from transbridge.application.sessions.scope_cache import SessionScopeCache
from transbridge.persistence.v2.ids import ProjectId, SessionId, SessionRef, VariantId


def test_scope_cache_reuses_unchanged_record_but_revalidates_modified_or_missing_source(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"project_id": "project-a", "variant_id": "variant-a"}))
    reads = []

    def read(ref):
        reads.append(ref)
        value = json.loads(path.read_text())
        return SimpleNamespace(project_id=ProjectId(value["project_id"]), variant_id=VariantId(value["variant_id"]))

    cache = SessionScopeCache(SimpleNamespace(path_for=lambda _ref: path), read)
    ref = SessionRef(SessionId("session"))
    assert cache.read(ref) == ("project-a", "variant-a")
    assert cache.read(ref) == ("project-a", "variant-a")
    assert len(reads) == 1

    path.write_text(json.dumps({"project_id": "other-project", "variant_id": "other-variant"}))
    assert cache.read(ref) == ("other-project", "other-variant")
    assert len(reads) == 2

    path.unlink()
    with pytest.raises(FileNotFoundError):
        cache.read(ref)
    assert len(reads) == 3


def test_scope_cache_without_stat_support_revalidates_each_read():
    scopes = iter([
        SimpleNamespace(project_id=ProjectId("project-a"), variant_id=VariantId("variant-a")),
        SimpleNamespace(project_id=None, variant_id=None),
    ])
    cache = SessionScopeCache(SimpleNamespace(), lambda _ref: next(scopes))
    ref = SessionRef(SessionId("session"))

    assert cache.read(ref) == ("project-a", "variant-a")
    assert cache.read(ref) == (None, None)


def test_scope_cache_does_not_cache_snapshot_if_record_changes_during_read(tmp_path):
    path = tmp_path / "session.json"
    path.write_text("initial")
    reads = []

    def read(_ref):
        reads.append(True)
        if len(reads) == 1:
            path.write_text("changed while validating")
            return SimpleNamespace(project_id=ProjectId("project-a"), variant_id=VariantId("variant-a"))
        return SimpleNamespace(project_id=ProjectId("project-b"), variant_id=VariantId("variant-b"))

    cache = SessionScopeCache(SimpleNamespace(path_for=lambda _ref: path), read)
    ref = SessionRef(SessionId("session"))
    assert cache.read(ref) == ("project-a", "variant-a")
    assert cache.read(ref) == ("project-b", "variant-b")
    assert len(reads) == 2
