# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Versioned position evidence, independent of the requested replacement spelling."""

from dataclasses import asdict
import json

from .occurrence_alignment import validate_alignment_decision

RULE_VERSION = 1


def location_payload(request):
    payload = asdict(request)
    for candidate in payload["candidates"]:
        candidate.pop("translation")
    return payload


def location_key(request):
    return json.dumps(
        (RULE_VERSION, location_payload(request)), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def same_location_input(left, right):
    if (
        left.entry_key != right.entry_key
        or left.original != right.original
        or left.translation != right.translation
        or left.plugin_id != right.plugin_id
        or left.counts != right.counts
        or len(left.candidates) != len(right.candidates)
    ):
        return False
    return all(
        (a.start, a.end, a.expected_text, a.term_key, a.original)
        == (b.start, b.end, b.expected_text, b.term_key, b.original)
        for a, b in zip(left.candidates, right.candidates)
    )


def encode_decision(request, decision):
    return json.dumps({"entry_key": request.entry_key, **asdict(decision)}, ensure_ascii=False, separators=(",", ":"))


def decode_decision(request, payload):
    return validate_alignment_decision(json.loads(payload), request)
