from dataclasses import replace
import json

import pytest

from transbridge.application.assistant_context.models import CompactionSummary, PreparationWait
from transbridge.application.assistant_context.projection import append_context
from transbridge.application.assistant_requests.models import RequestItem, UserRequest
from transbridge.persistence.assistant_context_store import AssistantContextStore
from transbridge.persistence.assistant_transcript_store import AssistantTranscriptStore


def request():
    return UserRequest("r", "s", "Keep sources", (RequestItem("i", "answer"),), source_message_ids=("u0",))


def history(n):
    return [{"role": "system", "content": "Rules", "message_id": "system"}] + [
        {"role": "user", "content": f"Question {i}", "message_id": f"u{i}", "request_ids": ["r"]} for i in range(n)
    ]


def test_fifty_appends_keep_exact_model_prefix_and_restore(tmp_path):
    store = AssistantContextStore(AssistantTranscriptStore(str(tmp_path)))
    previous = None
    for count in range(1, 51):
        epoch = append_context(
            history(count),
            request(),
            {"goal": "Keep sources"},
            config_digest="config",
            previous=previous.epoch if previous else None,
        )
        if previous:
            assert epoch.messages[: len(previous.epoch.messages)] == previous.epoch.messages
        staged = store.stage(epoch, previous)
        if previous:
            raw = json.loads(store.artifacts.read_artifact("s", staged.references[-1]))
            assert raw["mode"] == "append"
            assert len(raw["items"]) == 1
            assert "summaries" not in raw
        previous = staged
    reopened = AssistantContextStore(AssistantTranscriptStore(str(tmp_path)))
    restored = reopened.read("s", "r", (), previous.head)
    assert restored.epoch == previous.epoch
    assert len(restored.epoch.messages) == 52


def test_summary_chain_is_immutable_and_corrupt_body_is_not_regenerated(tmp_path):
    artifacts = AssistantTranscriptStore(str(tmp_path))
    store = AssistantContextStore(artifacts)
    epoch = append_context(history(2), request(), {}, config_digest="config")
    segment = CompactionSummary("S1", "First summary", ("u0",))
    epoch = replace(epoch, summaries=(segment,), items=tuple(i for i in epoch.items if i.item_id != "u0"))
    first = store.stage(epoch)
    for summaries in ((), (replace(segment, text="changed"),)):
        with pytest.raises(PreparationWait, match="CONTEXT_SUMMARY_CHANGED"):
            store.stage(replace(epoch, summaries=summaries, epoch_id="next"), first)
    second = store.stage(
        replace(
            epoch,
            summaries=(segment, CompactionSummary("S2", "Second", ("u1",))),
            items=tuple(i for i in epoch.items if i.item_id != "u1"),
            epoch_id="next",
        ),
        first,
    )
    assert store.read("s", "r", (), second.head).epoch.summaries[0] == segment
    with pytest.raises(PreparationWait):
        store.read("s", "other", (), second.head)
    path = tmp_path.joinpath(*second.summary_refs[0].path.split("/"))
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(Exception, match="digest|恢复"):
        store.read("s", "r", (), second.head)


def test_state_changes_are_appended_and_source_reassignment_blocks():
    first = append_context(history(2), request(), {"constraint": "Do not write"}, config_digest="config")
    changed = append_context(
        history(3), request(), {"constraint": "Preview only"}, config_digest="config", previous=first
    )
    assert changed.messages[: len(first.messages)] == first.messages
    assert changed.messages[-2]["role"] == "user"
    with pytest.raises(PreparationWait, match="CONTEXT_SOURCE_CHANGED"):
        append_context(history(3), request(), {}, config_digest="config", previous=changed, owners={"u0": "other"})


def test_new_configuration_preserves_summary_but_rebuilds_provider_history():
    segment = CompactionSummary("S1", "First summary", ("u0",))
    first = append_context(history(2), request(), {}, config_digest="old")
    first = replace(first, summaries=(segment,), items=tuple(i for i in first.items if i.item_id != "u0"))
    next_epoch = append_context(history(3), request(), {}, config_digest="new", previous=first)
    assert next_epoch.summaries == first.summaries
    assert next_epoch.epoch_id != first.epoch_id
    assert [i.item_id for i in next_epoch.items if i.kind == "history"] == ["u1", "u2"]
