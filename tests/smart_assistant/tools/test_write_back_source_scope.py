"""The persisted template repair range constrains actual source publication."""

from tests.smart_assistant.tools.test_write_back_options import (
    _result,
    manager as manager_fixture,
    sources as sources_fixture,
)
from transbridge.smart_assistant.tools import tool_writer

manager = manager_fixture
sources = sources_fixture


def _freeze_xml_scope(sources):
    sources.ctx._source_input_selection = {
        "source_scope": {
            "requested_source_keys": ["plugin-key", "xml-key"],
            "selected_source_keys": ["xml-key"],
            "resolved_source_keys": ["xml-key"],
        }
    }


def test_partial_source_scope_publishes_only_the_confirmed_source(sources, manager):
    _freeze_xml_scope(sources)
    output = sources.root / "output"
    started = tool_writer._tool_write_back(
        {"target": "auto", "source_keys": ["xml-key"], "output_dir": str(output)}, sources.ctx
    )
    assert _result(manager, started)["artifacts"] == [str(output / "second.xml")]
    assert {path.name for path in output.iterdir()} == {"second.xml"}


def test_original_full_source_arguments_cannot_restore_a_skipped_source(sources, manager):
    _freeze_xml_scope(sources)
    output = sources.root / "output"
    result = tool_writer._tool_write_back(
        {"target": "auto", "source_keys": list(sources.ctx.slots), "output_dir": str(output)}, sources.ctx
    )
    assert not result.success and result.error_code == "REQUEST_SCOPE_MISMATCH"
    assert manager.list_all() == [] and not output.exists()


def test_omitted_source_keys_use_the_frozen_range_instead_of_active_source(sources, manager):
    _freeze_xml_scope(sources)
    output = sources.root / "output"
    started = tool_writer._tool_write_back({"target": "eet", "output_dir": str(output)}, sources.ctx)
    assert _result(manager, started)["artifacts"] == [str(output / "second.xml")]
    assert sources.ctx.active_key == "plugin-key"
    assert {path.name for path in output.iterdir()} == {"second.xml"}
