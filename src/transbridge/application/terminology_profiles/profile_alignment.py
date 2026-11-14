# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Collect ambiguous rows, resolve them in one optional job, then freeze replay."""

from types import MappingProxyType

from .entry_projection import ProfileEntryProjector
from .occurrence_alignment import FrozenAlignmentResolver, checked_decision
from .projection import TerminologyProfileProjector


class _Requests:
    def __init__(self, prior, retry_uncertain):
        self.prior = prior
        self.retry_uncertain = retry_uncertain
        self.requests = []

    def resolve(self, request):
        decision = self.prior.resolve(request)
        if decision is not None and (decision.certain or not self.retry_uncertain):
            return decision
        self.requests.append(request)
        return None


def align_profile_entries(
    entries,
    profile,
    base,
    *,
    source_profile=None,
    service,
    cancellation=None,
    progress=None,
    prior=None,
    retry_uncertain=True,
    cache=None,
    allow_model=True,
):
    entries = tuple(entries)
    # The Variant cache verifies edit fences before reuse; a previous display
    # snapshot must never bypass that validation after a committed edit.
    prior = FrozenAlignmentResolver() if cache is not None else (prior or FrozenAlignmentResolver())
    collector = _Requests(prior, retry_uncertain)
    projector = ProfileEntryProjector(
        profile, base, source_profile=source_profile, projector=TerminologyProfileProjector(resolver=collector)
    )
    output = {}
    for entry in entries:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        output[entry.entry_key] = projector.project_input(entry)
    requests = tuple(collector.requests)
    if cache is not None:
        decisions = cache.decide(
            requests,
            service if allow_model else None,
            retry_uncertain=retry_uncertain,
            cancellation=cancellation,
            progress=progress,
        )
    else:
        decisions = service.resolve_many(requests, cancellation=cancellation, progress=progress) if requests else ()
    if len(decisions) != len(requests):
        raise ValueError("AI 位置判断结果数量与请求不一致，未应用本批结果")
    records = tuple(
        (request, checked_decision(decision, request))
        for request, decision in zip(requests, decisions)
        if decision is not None
    )
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    # Replace identical old records (including uncertain results), retain other exact inputs.
    by_entry = {}
    for request, decision in prior.records + records:
        group = by_entry.setdefault(request.entry_key, [])
        group[:] = [(old, value) for old, value in group if old != request]
        group.append((request, decision))
    frozen = (
        cache.freeze()
        if cache is not None
        else FrozenAlignmentResolver(record for group in by_entry.values() for record in group)
    )
    resolved = ProfileEntryProjector(
        profile, base, source_profile=source_profile, projector=TerminologyProfileProjector(resolver=frozen)
    )
    for entry in entries:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        output[entry.entry_key] = resolved.project_input(entry)
    return frozen, MappingProxyType(output)
