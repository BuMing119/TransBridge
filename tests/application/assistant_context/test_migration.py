from dataclasses import replace

import pytest

from transbridge.application.assistant_context.migration import import_legacy_summary
from transbridge.application.assistant_context.models import PreparationWait
from transbridge.application.assistant_context.projection import append_context
from transbridge.application.assistant_requests.models import RequestItem, UserRequest
from transbridge.application.assistant_requests.summaries import plan_summary


def test_older_excerpt_survives_new_turns_and_revisions_without_claiming_coverage():
    request = UserRequest("r", "s", "goal", (RequestItem("i", "Explain"),), source_message_ids=("m0",))
    history = [
        {
            "role": "user",
            "message_id": f"m{n}",
            "sequence": n + 1,
            "request_ids": ["r"],
            "content": f"Decision {n}: do not write files. " * 20,
        }
        for n in range(20)
    ]
    legacy = plan_summary(request, history)
    history.append({
        "role": "user",
        "message_id": "later",
        "sequence": 21,
        "request_ids": ["r"],
        "content": "An additional question",
    })
    updated = replace(request, revision=2)
    imported = import_legacy_summary(legacy.to_dict(), updated, history, {})
    assert imported.text == legacy.text
    assert imported.covered_sources == ()
    assert imported.background_refs == legacy.source_ids
    with pytest.raises(PreparationWait, match="CONTEXT_LEGACY_INVALID"):
        import_legacy_summary(legacy.to_dict(), updated, history, {legacy.source_ids[0]: "another-request"})


def test_latest_system_revision_replaces_only_rules_and_keeps_old_summary():
    request = UserRequest("r", "s", "goal", (RequestItem("i", "Explain"),), source_message_ids=("u",))
    history = [
        {"role": "system", "message_id": "old", "content": "Old fixed rule"},
        {"role": "user", "message_id": "u", "content": "question"},
    ]
    first = append_context(history, request, {}, config_digest="same")
    history.append({"role": "system", "message_id": "new", "content": "Current fixed rule"})
    changed = append_context(history, request, {}, config_digest="same", previous=first)
    assert changed.messages[0]["content"] == "Current fixed rule"
    assert "Old fixed rule" not in str(changed.messages)
    assert changed.epoch_id != first.epoch_id
    assert changed.source_digests == first.source_digests
