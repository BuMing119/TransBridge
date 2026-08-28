from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from tests.application.terminology.test_incremental import _Cache, _entry, _Parser, _snapshot, _source
from transbridge.application.contracts import RequestContext
from transbridge.application.terminology.build import TerminologyFullBuilder
from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
from transbridge.application.terminology.incremental import BuildReuseDecision, IncrementalTerminologyBuilder
from transbridge.persistence.terminology import SqliteTerminologyRepository

pytestmark = pytest.mark.integration


def test_single_source_change_recomputes_only_its_component_and_matches_full_build() -> None:
    sources = tuple(_source(f"s{index}") for index in range(10))
    values = {
        source.registration.source_id: (_entry(source.registration.source_id, f"Name {index}", f"名{index}"),)
        for index, source in enumerate(sources)
    }
    parser = _Parser(values)
    repository = InMemoryTerminologyRepository()
    cache = _Cache()
    incremental = IncrementalTerminologyBuilder(TerminologyFullBuilder(parser, repository), repository, cache)
    context = RequestContext("test", run_id="incremental")

    incremental.build(_snapshot(sources), context)
    changed_sources = (_source("s0", b"changed"), *sources[1:])
    values["s0"] = (_entry("s0", "Changed", "已改"),)
    parser.calls = 0
    changed_snapshot = _snapshot(changed_sources)
    changed = incremental.build(changed_snapshot, context)

    full_parser = _Parser(values)
    full = TerminologyFullBuilder(full_parser, InMemoryTerminologyRepository()).build(changed_snapshot, context)
    assert changed.result.ref.content_digest == full.result.ref.content_digest
    assert changed.result == full.result
    assert changed.plan.decision is BuildReuseDecision.INCREMENTAL
    assert changed.plan.reparsed_source_count == 1
    assert changed.plan.reused_component_count == 9
    assert changed.plan.recomputed_component_count == 1
    assert parser.calls == 1


def test_draft_identity_change_reuses_all_components_but_revalidates_global_result() -> None:
    source = _source("a")
    values = {"a": (_entry("a", "Dragon", "龙"),)}
    parser = _Parser(values)
    repository = InMemoryTerminologyRepository()
    builder = IncrementalTerminologyBuilder(TerminologyFullBuilder(parser, repository), repository, _Cache())
    context = RequestContext("test", run_id="incremental")
    initial_snapshot = _snapshot((source,))
    initial = builder.build(initial_snapshot, context)
    parser.calls = 0

    changed_snapshot = replace(
        initial_snapshot,
        draft_id="draft-2",
        draft_base_version_id="version-1",
        draft_revision=3,
        decision_digest=hashlib.sha256(b"decision-2").hexdigest(),
    )
    changed = builder.build(changed_snapshot, context)
    full = TerminologyFullBuilder(_Parser(values), InMemoryTerminologyRepository()).build(changed_snapshot, context)

    assert changed.result == full.result
    assert changed.result.ref.content_digest == initial.result.ref.content_digest
    assert changed.plan.decision is BuildReuseDecision.INCREMENTAL
    assert changed.plan.reused_component_count == 1
    assert changed.plan.recomputed_component_count == 0
    assert parser.calls == 0


def test_sqlite_cache_and_formal_repository_share_connection_without_leaking_transactions(tmp_path: Path) -> None:
    source = _source("a")
    parser = _Parser({"a": (_entry("a", "Dragon", "龙"),)})
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        builder = IncrementalTerminologyBuilder(
            TerminologyFullBuilder(parser, repository), repository, repository.cache
        )
        first = builder.build(_snapshot((source,)), RequestContext("test", run_id="sqlite"))
        second = builder.build(_snapshot((source,)), RequestContext("test", run_id="sqlite"))
        assert first.result == second.result
        assert second.plan.decision is BuildReuseDecision.EXACT
        assert parser.calls == 1
    finally:
        repository.close()
