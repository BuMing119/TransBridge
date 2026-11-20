from collections import Counter
from dataclasses import replace

import pytest

from tests.contracts.io.test_localized_hydrated_publication import _run
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWritePreflightService
from transbridge.application.io.plugin_destination import PLUGIN_DESTINATION_MODE
from transbridge.application.io.publish import ConflictPolicy


def _read(path):
    parsed = TranslationIoUseCase().parse(
        ParseRequest(
            SourceDescriptor(str(path)), RequestContext("test"), FormatId.PLUGIN_SSE, options=(("skip_empty", False),)
        )
    )
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    return parsed


@pytest.mark.parametrize("mode", ["directory", "original"])
@pytest.mark.parametrize("skyrim_plugin_copy", ["ccbgssse050-ba_daedric.esl", "ccbgssse016-umbra.esm"], indirect=True)
def test_localized_write_on_fresh_skyrim_copy(skyrim_plugin_copy, mode, tmp_path):
    source = skyrim_plugin_copy
    parsed = _read(source)
    counts = Counter(entry.string_id for entry in parsed.entries)
    name = next(
        entry
        for entry in parsed.entries
        if entry.string_id
        and counts[entry.string_id] == 1
        and entry.original
        and (entry.context or "").endswith(":FULL")
    )
    assert any(e.string_id == 0 and e.original == "" for e in parsed.entries)
    translated = replace(name, translation="测试名称", stage=1)
    target = source if mode == "original" else tmp_path / "export" / source.name
    target.parent.mkdir(exist_ok=True)
    draft = HydratedWriteDraft(
        parsed.source_snapshot,
        FormatId.PLUGIN_SSE,
        (translated.snapshot(),),
        str(target),
        1,
        RequestContext("test"),
        conflict_policy=ConflictPolicy.EXPLICIT_OVERWRITE if mode == "original" else ConflictPolicy.FAIL,
        options=((PLUGIN_DESTINATION_MODE, mode),),
    )
    checked = HydratedWritePreflightService().preflight(draft)
    assert checked.ready, checked.checks
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert target.read_bytes() == parsed.source_snapshot.content
    for before, after in zip(parsed.entries, _read(target).entries, strict=True):
        assert (after.key, after.context, after.string_id) == (before.key, before.context, before.string_id)
        assert after.original == (translated.translation if before.string_id == name.string_id else before.original)
