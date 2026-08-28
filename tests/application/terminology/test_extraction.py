from dataclasses import dataclass

from transbridge.application.terminology.extraction import (
    DeterministicTermExtractor,
    LlmTermProposal,
    TerminologyExtractionService,
)
from transbridge.application.terminology.models import (
    BilingualEvidence,
    ExtractionMethod,
    LlmExtractionStatus,
)


def _evidence(identity: str, original: str, translation: str, context: str) -> BilingualEvidence:
    return BilingualEvidence(
        identity,
        "project-1",
        "main",
        ("source-1",),
        "source:1",
        f'["source:1","{identity}"]',
        original,
        translation,
        "test",
        "fingerprint",
        context,
        "translated",
    )


def test_deterministic_extraction_only_uses_name_contexts() -> None:
    evidence = (
        _evidence("e-text", "The dragon arrived", "龙来了", "BOOK:DESC"),
        _evidence("e-name", "Dragon", "龙", "NPC_:FULL"),
    )

    result = DeterministicTermExtractor().extract(evidence)

    assert len(result) == 1
    assert result[0].original == "Dragon"
    assert result[0].extraction_method is ExtractionMethod.DETERMINISTIC_NAME


@dataclass
class _Llm:
    proposals: tuple[LlmTermProposal, ...]

    def extract(self, batch):
        return self.proposals


def test_llm_candidates_must_locate_both_terms_in_the_same_evidence() -> None:
    evidence = (_evidence("e-1", "The dragon carries a sword", "龙携带一把剑", "BOOK:DESC"),)
    llm = _Llm((
        LlmTermProposal("e-1", "dragon", "龙"),
        LlmTermProposal("e-1", "missing", "剑"),
        LlmTermProposal("unknown", "dragon", "龙"),
    ))

    result = TerminologyExtractionService(llm=llm).extract(evidence, llm_enabled=True)

    assert result.llm_status is LlmExtractionStatus.PERFORMED
    assert [(item.original, item.translation) for item in result.candidates] == [("dragon", "龙")]
    assert result.diagnostics == ("LLM_EVIDENCE_UNKNOWN:unknown", "LLM_PROPOSAL_NOT_LOCATED:e-1")


class _FailingLlm:
    def extract(self, batch):
        raise TimeoutError("provider stalled")


def test_llm_failure_does_not_block_deterministic_results() -> None:
    evidence = (
        _evidence("e-name", "Dragon", "龙", "NPC_:FULL"),
        _evidence("e-text", "The sword", "这把剑", "BOOK:DESC"),
    )

    result = TerminologyExtractionService(llm=_FailingLlm()).extract(evidence, llm_enabled=True)

    assert result.llm_status is LlmExtractionStatus.PARTIAL
    assert len(result.candidates) == 1
    assert result.candidates[0].extraction_method is ExtractionMethod.DETERMINISTIC_NAME
    assert result.diagnostics[0].startswith("LLM_BATCH_FAILED")


def test_disabled_and_unavailable_llm_states_are_explicit() -> None:
    evidence = (_evidence("e-text", "The sword", "这把剑", "BOOK:DESC"),)
    service = TerminologyExtractionService()

    assert service.extract(evidence, llm_enabled=False).llm_status is LlmExtractionStatus.SKIPPED
    assert service.extract(evidence, llm_enabled=True).llm_status is LlmExtractionStatus.UNAVAILABLE
