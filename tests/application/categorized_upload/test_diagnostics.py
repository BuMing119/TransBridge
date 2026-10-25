import json
import logging

import pytest

from transbridge.application.categorized_upload.diagnostics import diagnostic_scope, log_event

LOGGER = "transbridge.application.categorized_upload.diagnostics"


def events(caplog):
    return [json.loads(record.getMessage()) for record in caplog.records if record.name == LOGGER]


def test_context_is_reset_after_exception_and_credentials_are_removed_before_log_capture(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    with pytest.raises(ValueError):
        with diagnostic_scope("compare", credential="opaque-provider-value", session_id="first"):
            raise ValueError("rejected opaque-provider-value\nsecond line")
    with diagnostic_scope("preview", session_id="second"):
        log_event("preview_summary", sources=0)
    records = events(caplog)
    assert "opaque-provider-value" not in caplog.text
    first = [item for item in records if item["session_id"] == "first"]
    second = [item for item in records if item["session_id"] == "second"]
    assert {item["operation_id"] for item in first}.isdisjoint(item["operation_id"] for item in second)
    assert all("\n" not in record.getMessage() for record in caplog.records if record.name == LOGGER)
