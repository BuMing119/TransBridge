import pytest

from tests.ui.operations.test_strict_write_routes import _context, qapp as qapp_fixture
from tests.ui.operations.test_write_schemes import _schemes
from transbridge.application.contracts import RequestContext
from transbridge.application.io.operation_write import HydratedWritePreflightService
from transbridge.config.text_encoding import TextEncodings
from transbridge.ui.operations.write_dialog import WritePlanDialog
from transbridge.ui.operations.write_feature import build_write_feature
from transbridge.ui.operations.write_schemes import PREFIX, expand_scheme_export

qapp = qapp_fixture


@pytest.mark.parametrize("batch, transfer", [(False, False), (True, False), (False, True)])
def test_export_freezes_project_encoding_across_draft_edits_and_scheme_expansion(tmp_path, batch, transfer):
    context = _context(tmp_path, 2 if batch else 1)
    schemes = _schemes()
    captured = TextEncodings("cp1252", "cp936")
    feature = build_write_feature(
        lambda _ctx: RequestContext("gui"), None, HydratedWritePreflightService(), text_encodings=lambda _ctx: captured
    )
    values = {"export_schemes": schemes}
    if transfer:
        values["plugin_path"] = str(next(iter(context.slots)))
    draft = feature.create_draft(context, batch, values)
    captured = TextEncodings()
    assert "Windows-1252" in draft.mode_summary
    assert "Windows-936" in draft.mode_summary
    fields = (("scheme_directory", str(tmp_path / "out")), *((PREFIX + key, "true") for key, _ in schemes.choices))
    if transfer:
        fields += (("plugin_path", values["plugin_path"]),)
    edited = feature.edit_draft(draft, fields)
    expanded = expand_scheme_export(edited.request)
    assert len(expanded.request.drafts) == len(schemes.choices) * len(context.slots)
    for item in expanded.request.drafts:
        options = dict(item.write.options if transfer else item.options)
        assert (options["input_encoding"], options["output_encoding"]) == ("cp1252", "cp936")


def test_export_dialog_displays_encoding_for_plugins_and_hides_it_for_sst_and_json(tmp_path, qapp):
    context = _context(tmp_path)
    feature = build_write_feature(
        lambda _ctx: RequestContext("gui"),
        None,
        HydratedWritePreflightService(),
        text_encodings=lambda _ctx: TextEncodings("cp936", "utf-8"),
    )
    draft = feature.create_draft(context, False, {})
    dialog = WritePlanDialog(feature.mapper.present("plan", 1, draft), context)
    dialog.show()
    qapp.processEvents()
    formats = dialog._field_edits["output_format"]
    assert dialog._encoding_label.isVisible()
    assert "Windows-936" in dialog._encoding_label.text()
    for output in ("sst.ssu9", "json.dsd", "json.paratranz"):
        formats.setCurrentIndex(formats.findData(output))
        assert dialog._encoding_label.isHidden()
    formats.setCurrentIndex(formats.findData("plugin.sse"))
    assert dialog._encoding_label.isVisible()
    dialog.close()
