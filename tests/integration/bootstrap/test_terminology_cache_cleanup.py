from dataclasses import replace

import pytest

from tests.integration.bootstrap.test_terminology_build_review import _Harness
from transbridge.persistence.terminology import CacheKind, SqliteTerminologyRepository


@pytest.fixture
def harness(tmp_path, monkeypatch):
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        yield _Harness(repository, monkeypatch)
    finally:
        repository.close()


def test_clear_project_extraction_cache_preserves_draft_versions_build_and_other_caches(harness, tmp_path):
    harness.build()
    version = harness.publish()
    draft = harness.commands.prepare_draft(harness.context)
    harness.commands.apply_decision(
        "change_translation", harness.context, term_id=draft.decisions[0].term_id, translation="人工译名"
    )
    repo = harness.repository
    draft = repo.active_draft("project-1", "main")
    build = repo.latest_build("project-1", "main")
    for kind in CacheKind:
        repo.cache.put(kind, f"{kind}-key", b"cache payload")
    repo.cache.put(CacheKind.EXTRACTION, "another-variant-batch", b"other batch")
    with_other = SqliteTerminologyRepository.open(str(tmp_path), "other-project")
    try:
        with_other.cache.put(CacheKind.EXTRACTION, "keep", b"other project")
        harness.repositories.for_project = lambda project: repo if project == "project-1" else with_other
        assert harness.commands.clear_extraction_cache(harness.context) == 2
        assert repo.cache.get(CacheKind.EXTRACTION, "extraction-key") is None
        assert repo.cache.get(CacheKind.EXTRACTION, "another-variant-batch") is None
        assert repo.cache.get(CacheKind.BUILD, "build-key").payload == b"cache payload"
        assert repo.cache.get(CacheKind.PARSE, "parse-key").payload == b"cache payload"
        assert with_other.cache.get(CacheKind.EXTRACTION, "keep").payload == b"other project"
        assert repo.active_draft("project-1", "main") == draft
        assert repo.effective_version("project-1", "main") == version
        assert repo.latest_build("project-1", "main") == build
        assert harness.commands.clear_extraction_cache(harness.context) == 0
    finally:
        with_other.close()


def test_cache_cleanup_requires_project_context(harness):
    harness.repository.cache.put(CacheKind.EXTRACTION, "keep", b"payload")
    with pytest.raises(RuntimeError, match="当前工程"):
        harness.commands.clear_extraction_cache(replace(harness.context, project_id=None))
    assert harness.repository.cache.get(CacheKind.EXTRACTION, "keep") is not None
