from dataclasses import replace
import os

import pytest

from tests.contracts.io.test_localized_hydrated_publication import localized as localized_fixture
from tests.ui.operations.test_strict_write_routes import _context, feature as feature_fixture
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter
from transbridge.application.io.operation_write import HydratedWriteDraft
from transbridge.ui.operations.errors import OperationCompositionError
from transbridge.ui.operations.write_batch import _HydratedWriteBatch, _write_batch_plan

localized = localized_fixture
feature = feature_fixture


def _assert_blocked_without_publication(feature, draft):
    checked = feature.feature.mapper.preflight(draft)
    conflicts = [item for item in checked.checks if item.check_id == "BATCH_OUTPUT_DISJOINT"]
    assert not checked.ready
    assert len(conflicts) == 1
    assert "来源 1" in conflicts[0].reason
    assert "来源 2" in conflicts[0].reason
    # A caller that bypasses the presenter's disabled confirmation still cannot
    # schedule a batch with overlapping artifacts.
    with pytest.raises(OperationCompositionError, match="批量输出路径重叠"):
        feature.feature.submit(draft, checked, feature.owner, feature.tasks)
    assert feature.tasks.list(feature.owner) == ()
    return conflicts[0].reason


@pytest.mark.parametrize("alias", ["same", "dot", "case"])
def test_batch_rejects_primary_output_aliases_before_any_file_is_published(tmp_path, feature, alias):
    if alias == "case" and os.name != "nt":
        pytest.skip("Case-insensitive output alias uses Windows path semantics")
    context = _context(tmp_path, 2)
    initial = feature.feature.create_draft(context, True, {})
    directory = tmp_path / "output"
    directory.mkdir()
    target = directory / "result.esp"
    alias_path = {
        "same": target,
        "dot": directory / ".." / "output" / "result.esp",
        "case": directory / "RESULT.ESP",
    }[alias]
    draft = _write_batch_plan(
        _HydratedWriteBatch((
            replace(initial.request.drafts[0], target_path=str(target)),
            replace(initial.request.drafts[1], target_path=str(alias_path)),
        ))
    )

    _assert_blocked_without_publication(feature, draft)

    assert not tuple(directory.iterdir())


def test_batch_rejects_shared_localized_companions_for_different_plugin_extensions(localized, feature):
    source_esp = localized.source
    source_esl = source_esp.with_suffix(".esl")
    source_esl.write_bytes(source_esp.read_bytes())
    drafts = []
    for source in (source_esp, source_esl):
        parsed = SsePluginAdapter().parse(
            ParseRequest(SourceDescriptor(str(source)), RequestContext("gui"), FormatId.PLUGIN_SSE)
        )
        assert parsed.source_snapshot is not None
        assert parsed.entries[0].string_id == 1
        drafts.append(
            HydratedWriteDraft(
                parsed.source_snapshot,
                FormatId.PLUGIN_SSE,
                tuple(replace(entry, translation="译文", stage=1).snapshot() for entry in parsed.entries),
                str(localized.output / f"foo_translated{source.suffix}"),
                1,
                RequestContext("gui"),
            )
        )
    draft = _write_batch_plan(_HydratedWriteBatch(tuple(drafts)))

    reason = _assert_blocked_without_publication(feature, draft)

    assert "foo_translated_english.strings" in reason.lower()
    assert "foo_translated_english.dlstrings" in reason.lower()
    assert "foo_translated_english.ilstrings" in reason.lower()
    assert not tuple(localized.output.iterdir())
