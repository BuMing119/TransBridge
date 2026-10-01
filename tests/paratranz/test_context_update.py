from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

import pytest

from transbridge.application.ports.paratranz import ExternalServiceError, ParaTranzEntry
from transbridge.paratranz.service import ParaTranzService


def test_context_update_does_not_send_or_overwrite_translation_state():
    record = {
        "id": 71,
        "key": "stable",
        "original": "Original",
        "translation": "最新人工译文",
        "stage": 5,
        "context": "00000001|BOOK:FULL",
    }
    before = deepcopy(record)
    strings = Mock()

    def update(project, remote_id, payload, *, cancellation):
        assert (project, remote_id) == (7, 71)
        assert payload == {"context": "00000428|BOOK:FULL"}
        record.update(payload)
        return record

    strings.update_string.side_effect = update
    service = ParaTranzService(Mock(), strings, Mock(), Mock())
    # Even stale caller fields cannot overwrite fields absent from the patch.
    entry = ParaTranzEntry(71, "stable", "Old original", "旧译文", "00000428|BOOK:FULL", 0)
    result = service.update_entry_context(7, entry)
    assert record == {**before, "context": entry.context}
    assert result.translation == "最新人工译文"
    assert result.stage == 5
    strings.create_string.assert_not_called()
    strings.list_strings.assert_not_called()


@pytest.mark.parametrize("remote_id", [None, True, 0, -1, "71"])
def test_context_update_requires_known_remote_id(remote_id):
    strings = Mock()
    service = ParaTranzService(Mock(), strings, Mock(), Mock())
    entry = ParaTranzEntry(remote_id, "stable", "Original", "译文", "00000428|BOOK:FULL", 1)
    with pytest.raises(ValueError, match="remote_id"):
        service.update_entry_context(7, entry)
    strings.update_string.assert_not_called()


def test_context_update_handles_empty_success_and_passes_cancellation():
    strings = Mock()
    strings.update_string.return_value = None
    cancellation = Mock()
    entry = ParaTranzEntry(71, "stable", "Original", "译文", "00000428|BOOK:FULL", 1)
    result = ParaTranzService(Mock(), strings, Mock(), Mock()).update_entry_context(7, entry, cancellation=cancellation)
    assert result == entry
    strings.update_string.assert_called_once_with(7, 71, {"context": entry.context}, cancellation=cancellation)


@pytest.mark.parametrize("changes", [{"remote_id": 72}, {"key": "other"}, {"context": "BOOK:FULL"}])
def test_context_update_rejects_mismatched_server_response(changes):
    strings = Mock()
    entry = ParaTranzEntry(71, "stable", "Original", "译文", "00000428|BOOK:FULL", 1)
    response = replace(entry, **changes)
    strings.update_string.return_value = {"id": response.remote_id, **response.to_remote_payload()}
    with pytest.raises(ExternalServiceError, match="does not match"):
        ParaTranzService(Mock(), strings, Mock(), Mock()).update_entry_context(7, entry)
