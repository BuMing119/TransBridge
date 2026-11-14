from dataclasses import replace

import pytest

from tests.application.terminology.test_build import _entry, _Parser, _snapshot, _source
from tests.application.terminology.test_build_drafts import _build, _candidate, _decision, _reconcile
from transbridge.application.contracts import RequestContext
from transbridge.application.terminology.build import FullBuildCancelled, TerminologyFullBuilder
from transbridge.application.terminology.conflict_judgment import ConflictJudgment, ConflictJudgmentCancelled
from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
from transbridge.application.terminology.incremental import IncrementalTerminologyBuilder
from transbridge.application.terminology.models import ConflictStatus, DecisionStatus, TermScope


class Judge:
    def __init__(self, classification="equivalent"):
        self.classification = classification
        self.calls = 0

    def judge(self, conflicts, evidence, *, cancellation=None):
        self.calls += 1
        return tuple(
            ConflictJudgment(
                item.conflict_group_id,
                self.classification,
                "同一核心术语，语言后缀来自词条表达。",
                "西罗帝尔" if self.classification == "equivalent" else None,
            )
            for item in conflicts
        )


def test_unexecuted_judgment_is_not_counted_as_model_uncertainty():
    class NotRun:
        def judge(self, conflicts, evidence, *, cancellation=None):
            return tuple(
                ConflictJudgment(
                    item.conflict_group_id,
                    "uncertain",
                    "最小证据超出预算。",
                    execution_status="not_run",
                    request_count=0,
                )
                for item in conflicts
            )

    parser = _Parser({
        "a": (
            _entry("a", "1", "Cyrodilic", "西罗帝尔"),
            _entry("a", "2", "Cyrodilic", "西罗帝尔语"),
        )
    })
    result = (
        TerminologyFullBuilder(parser, InMemoryTerminologyRepository(), conflict_judge=NotRun())
        .build(_snapshot((_source("a"),)), RequestContext("test"))
        .result
    )
    assert "CONFLICT_JUDGMENT_COMPLETED:0" in result.diagnostics
    assert "CONFLICT_JUDGMENT_NOT_RUN:1" in result.diagnostics
    assert "CONFLICT_JUDGMENT_REQUESTS:0" in result.diagnostics
    assert result.conflicts[0].notes.startswith("未执行冲突判断")
    assert "LLM 判断" not in result.conflicts[0].notes


def test_equivalent_build_keeps_evidence_and_emits_single_term():
    parser = _Parser({
        "a": (
            _entry("a", "1", "Cyrodilic", "西罗帝尔"),
            _entry("a", "2", "Cyrodilic", "西罗帝尔语"),
        )
    })
    judge = Judge()
    build = TerminologyFullBuilder(parser, InMemoryTerminologyRepository(), conflict_judge=judge).build(
        _snapshot((_source("a"),)), RequestContext("test")
    )
    assert build.result.summary.conflict_count == 0
    assert build.result.conflicts[0].status is ConflictStatus.EQUIVALENT
    assert len(build.result.evidence) == 2
    assert len(build.result.candidates) == 2
    assert [item.translation for item in build.effective_candidates] == ["西罗帝尔"]


def test_judgment_does_not_replace_manual_value_or_reuse_old_automatic_resolution():
    build = _build(_candidate("Cyrodilic", "西罗帝尔", "1"), _candidate("Cyrodilic", "西罗帝尔语", "2"))
    group = replace(
        build.conflicts[0], status=ConflictStatus.EQUIVALENT, recommended_translation="西罗帝尔", notes="同一核心术语"
    )
    judged = replace(build, conflicts=(group,))
    first = _reconcile(judged)
    assert len(first.decisions) == 1
    assert first.decisions[0].translation == "西罗帝尔"
    manual = _decision("Cyrodilic", "西罗帝尔语", notes="人工保留")
    kept = _reconcile(judged, decisions=(manual,))
    assert kept.decisions[0].translation == "西罗帝尔语"
    assert kept.decisions[0].status is DecisionStatus.MANUAL_CONFIRMED
    assert kept.decisions[0].notes == "人工保留"
    reconsidered = _reconcile(build, decisions=first.decisions, conflicts=(group,))
    assert not reconsidered.conflict_resolutions
    assert reconsidered.decisions[0].status is DecisionStatus.REVIEW_REQUIRED


def test_cancelled_judgment_never_freezes_result():
    class CancelledJudge:
        def judge(self, *_args, **_kwargs):
            raise ConflictJudgmentCancelled("cancelled")

    repository = InMemoryTerminologyRepository()
    parser = _Parser({"a": (_entry("a", "1", "Cyrodilic", "西罗帝尔"),)})
    with pytest.raises(FullBuildCancelled):
        TerminologyFullBuilder(parser, repository, conflict_judge=CancelledJudge()).build(
            _snapshot((_source("a"),)), RequestContext("test")
        )
    assert not repository._builds


def test_equivalence_does_not_drop_plugin_with_other_normal_expression():
    first = replace(_candidate("Cyrodilic", "西罗帝尔", "1"), scope=TermScope.plugin("A.esp"))
    second = replace(_candidate("Cyrodilic", "西罗帝尔语", "2"), scope=TermScope.plugin("B.esp"))
    build = _build(first, second)
    group = replace(
        build.conflicts[0], status=ConflictStatus.EQUIVALENT, recommended_translation="西罗帝尔", notes="核心术语一致"
    )
    draft = _reconcile(replace(build, conflicts=(group,)))
    assert {(item.scope.plugin_id, item.translation) for item in draft.decisions} == {
        ("A.esp", "西罗帝尔"),
        ("B.esp", "西罗帝尔语"),
    }


def test_incremental_cache_reuses_extraction_but_not_judgment():
    from tests.application.terminology.test_incremental import _Cache

    repository = InMemoryTerminologyRepository()
    parser = _Parser({
        "a": (
            _entry("a", "1", "Cyrodilic", "西罗帝尔"),
            _entry("a", "2", "Cyrodilic", "西罗帝尔语"),
        )
    })
    judge = Judge()
    builder = IncrementalTerminologyBuilder(
        TerminologyFullBuilder(parser, repository, conflict_judge=judge), repository, _Cache()
    )
    snapshot = _snapshot((_source("a"),))
    first = builder.build(snapshot, RequestContext("test"))
    judge.classification = "conflict"
    second = builder.build(snapshot, RequestContext("test"))
    assert judge.calls == 2
    assert second.plan.reused_component_ids
    assert first.result.summary.conflict_count == 0
    assert second.result.summary.conflict_count == 1
