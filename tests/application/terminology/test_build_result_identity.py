from dataclasses import replace

import pytest

from tests.application.terminology.test_build import _entry, _Parser, _snapshot, _source
from transbridge.application.contracts import RequestContext
from transbridge.application.terminology.build import TerminologyFullBuilder
from transbridge.application.terminology.extraction import LlmTermProposal, TerminologyExtractionService
from transbridge.application.terminology.identity import build_key
from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
from transbridge.application.terminology.models import BuildCompleteness, BuildResultRef
from transbridge.persistence.terminology import SqliteTerminologyRepository


@pytest.fixture(params=["memory", "sqlite"])
def repository(request, tmp_path):
    repo = (
        InMemoryTerminologyRepository()
        if request.param == "memory"
        else SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    )
    yield repo
    if request.param == "sqlite":
        repo.close()


def test_model_reextraction_preserves_old_result_and_legacy_input_key(repository):
    source = _source("source-a")
    snapshot = _snapshot((source,))
    entry = _entry("source-a", "one", "Dragon carries sword", "龙携带剑")
    entry.context = "BOOK:DESC"
    parser = _Parser({"source-a": (entry,)})

    class Model:
        original = "Dragon"
        translation = "龙"

        def extract(self, batch):
            return (LlmTermProposal(batch[0].evidence_id, self.original, self.translation),)

    model = Model()
    builder = TerminologyFullBuilder(parser, repository, extraction=TerminologyExtractionService(llm=model))
    context = RequestContext("test")
    first = builder.build(snapshot, context, llm_enabled=True).result
    legacy = replace(first, ref=BuildResultRef(build_key(snapshot), first.ref.content_digest))
    repository.put_build(legacy)
    model.original, model.translation = "sword", "剑"
    second = builder.build(snapshot, context, llm_enabled=True).result

    assert first.ref != second.ref
    assert first.candidates[0].translation == "龙"
    assert second.candidates[0].translation == "剑"
    assert repository.get_build(first.ref) == first
    assert repository.get_build(legacy.ref) == legacy
    assert repository.get_build(second.ref) == second
    assert builder.build(snapshot, context, llm_enabled=True).result == second


def test_partial_source_retry_keeps_both_results(repository):
    snapshot = _snapshot((_source("good"), _source("bad")))
    parser = _Parser({"good": (_entry("good", "one", "Dragon", "龙"),), "bad": None})
    builder = TerminologyFullBuilder(parser, repository)
    first = builder.build(snapshot, RequestContext("test")).result
    parser.entries["bad"] = (_entry("bad", "two", "Sword", "剑"),)
    second = builder.build(snapshot, RequestContext("test")).result

    assert first.completeness is BuildCompleteness.PARTIAL
    assert second.completeness is BuildCompleteness.FULL
    assert repository.get_build(first.ref) == first
    assert repository.get_build(second.ref) == second
