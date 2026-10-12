"""Project hydration must retain blank source locators throughout publication."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.parser.test_original_match_plugin import physical_texts, quest_plugin
from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io.operation_write import (
    HydratedWriteDraft,
    HydratedWritePreflightService,
    HydratedWriteWorkload,
)
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.projects.provisioning import ProjectSourceRequest
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer


@pytest.mark.parametrize("record_kind", ["npc", "quest"])
@pytest.mark.parametrize("options", [(), (("skip_empty", True),), (("skip_empty", False),)])
@pytest.mark.parametrize("translate_blanks", [False, True])
def test_project_hydration_publishes_blank_source_entries(tmp_path, record_kind, options, translate_blanks):
    texts = ["", " ", "\r\n", "Original"]
    source = tmp_path / "source.esp"
    if record_kind == "quest":
        quest_plugin(source, texts)
    else:
        write_plugin(source, [(0x800 + index, f"Actor{index}", text) for index, text in enumerate(texts)])
    source_bytes = source.read_bytes()
    context = RequestContext("project-hydrated-write", run_id="project-hydrated-write")
    prepared = TranslationIoProjectSourcePreparer().prepare_source(
        ProjectSourceRequest(str(source)), context, role="primary", common_options=()
    )
    hydration = prepared.hydration
    assert hydration is not None
    assert [entry.original for entry in hydration.entries] == texts
    entries = tuple(
        replace(entry, translation=f"Translated {index}", stage=1)
        if translate_blanks or entry.original.strip()
        else entry
        for index, entry in enumerate(hydration.entries)
    )
    target = tmp_path / "translated.esp"
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(
            hydration.source_snapshot, hydration.format_id, entries, str(target), 1, context, options=options
        )
    )
    assert checked.ready
    result = HydratedWriteWorkload(checked)(
        SimpleNamespace(
            ref=SimpleNamespace(run_id=context.run_id),
            cancellation=None,
            publish_commit_guard=lambda: ImmediateCommitGuard(context.run_id),
        )
    )
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    expected = [f"Translated {index}" if translate_blanks or text.strip() else text for index, text in enumerate(texts)]
    assert physical_texts(SSEPluginWithContext.from_file(target)) == expected
    assert source.read_bytes() == source_bytes
