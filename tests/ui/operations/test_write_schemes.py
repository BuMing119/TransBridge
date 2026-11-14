import json
from pathlib import Path

import pytest

from tests.ui.operations.test_strict_write_routes import (
    _context,
    _submit,
    _texts,
    feature as feature_fixture,
    qapp as qapp_fixture,
)
from transbridge.application.contracts import OperationOutcome
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision
from transbridge.application.terminology_profiles import (
    FrozenProfileWriteProjection,
    InMemoryTerminologyProfileRepository,
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileProjector,
    TerminologyProfileService,
)
from transbridge.application.terminology_profiles.export_source import ProfileExportSource
from transbridge.ui.operations.write_dialog import WritePlanDialog
from transbridge.ui.operations.write_schemes import PREFIX, scheme_directories

feature = feature_fixture
qapp = qapp_fixture


def _schemes():
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    choices = []
    for name, translation in (("方案 A", "甲译名"), ("方案 B", "乙译名")):
        profile = service.create_with_content(
            "project", name, TerminologyProfileContent(mappings=(ProfileTermMapping("First", translation, "译文0"),))
        )
        service.publish(profile.profile_id, expected_draft_revision=0)
        choices.append((profile.profile_id, name))
    base = EffectiveTerminologySnapshot(
        "project",
        "variant",
        EffectiveSnapshotStatus.READY,
        version_id="base",
        content_digest="base",
        decisions=(
            TermDecision("first", "project", "variant", "First", "first", "译文0", status=DecisionStatus.ADOPTED),
        ),
    )
    return ProfileExportSource(
        tuple(choices),
        service,
        FrozenProfileWriteProjection("project", "variant", None, TerminologyProfileProjector(), base),
    )


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("output_format", [None, "json.paratranz"])
def test_two_schemes_export_different_text_without_changing_source(tmp_path, feature, batch, output_format):
    context = _context(tmp_path, 2 if batch else 1)
    originals = {path: Path(path).read_bytes() for path in context.slots}
    source = _schemes()
    values = {"export_schemes": source}
    if output_format:
        values["output_format"] = output_format
    draft = feature.feature.create_draft(context, batch, values)
    fields = tuple((field.field_id, field.value) for field in draft.editable_fields)
    fields += (("scheme_directory", str(tmp_path / "exports")),)
    fields += tuple((PREFIX + key, "true") for key, _ in source.choices)
    edited = feature.feature.edit_draft(draft, fields)
    result = _submit(feature, edited)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert len(result.artifact_refs) == len(context.slots) * 2
    for name, text in (("方案 A", "甲译名"), ("方案 B", "乙译名")):
        for index in range(len(context.slots)):
            path = tmp_path / "exports" / name / f"source-{index}"
            if output_format:
                rows = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
                assert rows[0]["translation"] == text
            else:
                assert _texts(path.with_suffix(".esp")) == [text, "译文1"]
    assert all(Path(path).read_bytes() == content for path, content in originals.items())
    assert next(iter(context.collection)).translation == "译文0"
    assert source.service.selection("project", "variant") is None


def test_scheme_directory_names_are_safe_and_unique():
    names = scheme_directories(("A/B", "A\\B", "con", "..", "NAME", "name"))
    assert len(set(name.casefold() for name in names)) == 6
    assert names == ("A_B", "A_B (2)", "_con", "术语方案", "NAME", "name (2)")


def test_preflight_freezes_schemes_and_repeated_export_requires_overwrite(tmp_path, feature):
    context, source = _context(tmp_path), _schemes()
    draft = feature.feature.create_draft(context, False, {"export_schemes": source})
    fields = (("scheme_directory", str(tmp_path / "out")), *tuple((PREFIX + key, "true") for key, _ in source.choices))
    draft = feature.feature.edit_draft(draft, fields)
    checked = feature.feature.mapper.preflight(draft)
    assert checked.ready
    profile_id = source.choices[0][0]
    changed = source.service.save_draft(
        profile_id,
        TerminologyProfileContent(mappings=(ProfileTermMapping("First", "后来修改", "译文0"),)),
        expected_revision=0,
    )
    source.service.publish(profile_id, expected_draft_revision=changed.draft_revision)
    ref = feature.feature.submit(draft, checked, feature.owner, feature.tasks)
    assert feature.adapter.result(ref, feature.owner).outcome is OperationOutcome.COMPLETED
    assert _texts(tmp_path / "out" / "方案 A" / "source-0.esp")[0] == "甲译名"
    second = feature.feature.edit_draft(draft, fields)
    recheck = feature.feature.mapper.preflight(second)
    assert not recheck.ready
    assert _texts(tmp_path / "out" / "方案 A" / "source-0.esp")[0] == "甲译名"


def test_multi_scheme_plugin_transfer_keeps_template_guards(tmp_path):
    from types import SimpleNamespace

    from tests.plugin_fixtures import write_plugin
    from tests.ui.operations.test_production_facade import _Clock, _Ids
    from transbridge.application.contracts import RequestContext
    from transbridge.application.io.operation_write import HydratedWritePreflightService
    from transbridge.application.tasks import CallbackThreadBackend, OwnerRef, TaskRuntime
    from transbridge.ui.operations.runtime_adapter import OperationTaskAdapter
    from transbridge.ui.operations.write_feature import build_write_feature

    tasks = TaskRuntime(id_generator=_Ids(), clock=_Clock(), backend=CallbackThreadBackend(lambda _run_id, fn: fn()))
    adapter = OperationTaskAdapter(tasks)
    case = SimpleNamespace(
        tasks=tasks,
        adapter=adapter,
        owner=OwnerRef("gui", "gui.operation-plan"),
        feature=build_write_feature(lambda _ctx: RequestContext("gui"), adapter, HydratedWritePreflightService()),
    )
    context, source = _context(tmp_path), _schemes()
    template = write_plugin(tmp_path / "template.esp", [(0x800, "A", "First"), (0x801, "B", "Second")])
    before = template.read_bytes()
    draft = case.feature.create_draft(
        context,
        False,
        {"export_schemes": source, "plugin_path": str(template), "target_path": str(tmp_path / "custom.esp")},
    )
    draft = case.feature.edit_draft(
        draft,
        (("scheme_directory", str(tmp_path / "out")), *tuple((PREFIX + key, "true") for key, _ in source.choices)),
    )
    result = _submit(case, draft)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert len(result.artifact_refs) == 2
    assert all(Path(path).name == template.name for path in result.artifact_refs)
    assert _texts(result.artifact_refs[0])[0] == "甲译名"
    assert _texts(result.artifact_refs[1])[0] == "乙译名"
    assert template.read_bytes() == before


def test_dialog_checklist_uses_one_root_and_preserves_choices_on_render(tmp_path, feature, qapp):
    context = _context(tmp_path)
    source = _schemes()
    draft = feature.feature.create_draft(context, False, {"export_schemes": source})
    dialog = WritePlanDialog(feature.feature.mapper.present("test", 1, draft), context)
    dialog.show()
    qapp.processEvents()
    assert dialog._field_edits["scheme_directory"].isHidden() or not dialog._field_edits["scheme_directory"].isVisible()
    for key, _ in source.choices:
        dialog._field_edits[PREFIX + key].setChecked(True)
    assert dialog._field_edits["scheme_directory"].isVisible()
    assert not dialog._field_edits["destination_mode"].isVisible()
    assert not dialog._field_edits["output_directory"].isVisible()
    dialog._field_edits["scheme_directory"].setText(str(tmp_path / "exports"))
    edited = feature.feature.edit_draft(draft, dialog.edited_values())
    dialog.render_plan(feature.feature.mapper.present("test", 2, edited))
    assert all(dialog._field_edits[PREFIX + key].isChecked() for key, _ in source.choices)
    dialog.close()
