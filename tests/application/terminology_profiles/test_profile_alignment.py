from dataclasses import replace

import pytest

from transbridge.application.terminology.query_control import QueryCancellation, QueryCancelled
from transbridge.application.terminology_profiles.entry_projection import ProfileEntryInput, ProfileEntryProjector
from transbridge.application.terminology_profiles.models import (
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfileContent,
)
from transbridge.application.terminology_profiles.occurrence_alignment import AlignmentDecision
from transbridge.application.terminology_profiles.profile_alignment import align_profile_entries
from transbridge.application.terminology_profiles.projection import TerminologyProfileProjector


def scheme(identifier, translation):
    content = TerminologyProfileContent((ProfileTermMapping("Nord", translation),))
    return PublishedTerminologyProfile(identifier, "project", 1, identifier, content.content_digest, content, "now")


class Service:
    def __init__(self):
        self.requests = []

    def resolve_many(self, requests, *, cancellation=None, progress=None):
        self.requests.extend(requests)
        if progress:
            progress(len(requests), len(requests))
        return tuple(AlignmentDecision((0,), True, "只替换第一处独立术语") for _request in requests)


def test_only_ambiguous_rows_reach_service_and_exact_replay_needs_no_model():
    source, target = scheme("A", "诺德"), scheme("B", "诺德人")
    entries = (
        ProfileEntryInput("fast", "Nord", "诺德", None),
        ProfileEntryInput("complex", "Nord greets Renod", "诺德问候雷诺德", None),
    )
    service = Service()
    frozen, output = align_profile_entries(entries, target, None, source_profile=source, service=service)
    assert len(service.requests) == 1
    assert output["complex"].translation == "诺德人问候雷诺德"
    assert output["fast"].translation == "诺德人"
    replay = ProfileEntryProjector(
        target, None, source_profile=source, projector=TerminologyProfileProjector(resolver=frozen)
    )
    assert replay.project_input(entries[1]) == output["complex"]
    again = Service()
    reused, repeated = align_profile_entries(entries, target, None, source_profile=source, service=again, prior=frozen)
    assert not again.requests
    assert repeated == output
    assert len(reused.records) == 1
    stale = replay.project_input(replace(entries[1], common_translation="诺德问候雷诺德！"))
    assert stale.diagnostics[0].code == "occurrence_alignment_required"


def test_cancelled_job_never_publishes_results():
    token = QueryCancellation()
    token.cancel()
    with pytest.raises(QueryCancelled):
        align_profile_entries(
            (ProfileEntryInput("e", "Nord", "诺德", None),),
            scheme("B", "诺德人"),
            None,
            source_profile=scheme("A", "诺德"),
            service=Service(),
            cancellation=token,
        )


def test_invalid_batch_length_rejected_before_freezing_any_result():
    class ShortService:
        def resolve_many(self, *_args, **_kwargs):
            return ()

    with pytest.raises(ValueError, match="数量"):
        align_profile_entries(
            (ProfileEntryInput("e", "Nord", "诺德与诺德", None),),
            scheme("B", "诺德人"),
            None,
            source_profile=scheme("A", "诺德"),
            service=ShortService(),
        )


def test_automatic_pass_does_not_retry_uncertain_judgment_but_explicit_retry_does():
    class UncertainService:
        calls = 0

        def resolve_many(self, requests, **_kwargs):
            self.calls += 1
            return tuple(AlignmentDecision(rationale="上下文不足") for _ in requests)

    service = UncertainService()
    entries = (ProfileEntryInput("e", "Nord", "诺德与诺德", None),)
    source, target = scheme("A", "诺德"), scheme("B", "诺德人")
    frozen, output = align_profile_entries(entries, target, None, source_profile=source, service=service)
    assert service.calls == 1 and output["e"].diagnostics
    replay, repeated = align_profile_entries(
        entries, target, None, source_profile=source, service=service, prior=frozen, retry_uncertain=False
    )
    assert service.calls == 1 and repeated == output
    align_profile_entries(entries, target, None, source_profile=source, service=service, prior=replay)
    assert service.calls == 2
