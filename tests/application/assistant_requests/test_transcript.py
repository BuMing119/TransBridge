from __future__ import annotations

from dataclasses import replace

import pytest

from transbridge.application.assistant_requests.transcript import (
    AttachmentRef,
    TranscriptManifest,
    TranscriptMessage,
    validate_message_order,
)


def test_message_captures_input_metadata_without_mutable_aliases() -> None:
    selection = {"project_id": "project-a", "selected_ids": ["first"]}
    message = TranscriptMessage("input-1", 1, "user", "translate selected", metadata=selection)
    selection["selected_ids"].append("second")
    exported = message.to_dict()
    exported["metadata"]["selected_ids"].append("third")
    assert message.to_dict()["metadata"]["selected_ids"] == ["first"]
    with pytest.raises(TypeError):
        message.metadata["project_id"] = "other"
    assert TranscriptMessage.from_dict(message.to_dict()) == message


def test_native_tool_results_link_to_exact_prior_call_once() -> None:
    user = TranscriptMessage("input-1", 1, "user", "inspect")
    call = TranscriptMessage("call-1", 2, "assistant", None, tool_calls=({"id": "tool-1"},))
    result = TranscriptMessage("result-1", 3, "tool", "done", tool_call_id="tool-1")
    validate_message_order((user, call, result))
    validate_message_order((user, call))
    with pytest.raises(ValueError, match="orphan"):
        validate_message_order((user, replace(result, sequence=2)))
    with pytest.raises(ValueError, match="orphan"):
        validate_message_order((user, call, result, replace(result, message_id="duplicate", sequence=4)))


@pytest.mark.parametrize("path", ["/outside", "../outside", "a/../b", "C:/outside", "a\\b", "a//b", "./a"])
def test_attachment_rejects_noncanonical_relative_paths(path: str) -> None:
    with pytest.raises(ValueError, match="relative"):
        AttachmentRef(path, "a" * 64, 10)


def test_manifest_watermark_cannot_advance_past_committed_records() -> None:
    with pytest.raises(ValueError, match="watermark"):
        TranscriptManifest(input_watermark=1)
    with pytest.raises(ValueError, match="count"):
        TranscriptManifest(last_sequence=1)
