"""Existing original-text and file-template prerequisites for an operation scope."""

from dataclasses import dataclass

ORIGINAL_KNOWN = "transbridge.ui.original_known"
SOURCE_CONTEXT_KNOWN = "transbridge.source_context_known"


@dataclass(frozen=True, slots=True)
class SourceInputFacts:
    source_key: str
    original_available: bool
    template_available: bool


def missing_source_keys(facts, requirement: str) -> tuple[str, ...]:
    return tuple(
        fact.source_key
        for fact in facts
        if not (fact.template_available if requirement == "template" else fact.original_available)
    )
