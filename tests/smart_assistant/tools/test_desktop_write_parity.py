"""Compare the desktop's native export and FOMOD pipeline to the registered assistant operations."""

from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from tests.smart_assistant.tools.test_write_back_options import (
    _result,
    manager as manager_fixture,
    sources as sources_fixture,
)
from tests.ui.operations.test_strict_write_routes import _submit, feature as feature_fixture
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.fomod.pipeline import FomodPipeline
from transbridge.smart_assistant.tools.tool_archive import _tool_filter_files
from transbridge.smart_assistant.tools.tool_writer import _tool_write_back

feature = feature_fixture
manager = manager_fixture
sources = sources_fixture


@pytest.mark.parametrize("batch", [False, True])
def test_same_output_directory_produces_same_artifacts_bytes_diagnostics_and_backups(sources, feature, manager, batch):
    output = sources.root / "output"
    output.mkdir()
    existing = output / sources.plugin.name
    existing.write_bytes(b"previous output")
    options = {"output_directory": str(output), "overwrite_confirmed": True}
    desktop = _submit(feature, feature.feature.create_draft(sources.ctx, batch, options))
    assert desktop.outcome is OperationOutcome.COMPLETED
    expected = {path: Path(path).read_bytes() for path in desktop.artifact_refs}
    assert next(output.glob(".*.bak")).read_bytes() == b"previous output"
    for path in output.iterdir():
        path.unlink()
    existing.write_bytes(b"previous output")
    args = {"target": "esp", "output_dir": str(output), "overwrite": True}
    if batch:
        args.update(target="auto", source_keys=list(sources.ctx.slots))
    assistant = _result(manager, _tool_write_back(args, sources.ctx))
    assert assistant["outcome"] == desktop.outcome.value
    assert assistant["diagnostics"] == [item.to_dict() for item in desktop.diagnostics]
    assert {path: Path(path).read_bytes() for path in assistant["artifacts"]} == expected
    assert next(output.glob(".*.bak")).read_bytes() == b"previous output"


@pytest.mark.parametrize(
    "mode,code", [("directory", "PLUGIN_SOURCE_PROTECTED"), ("original", "SOURCE_FINGERPRINT_CONFLICT")]
)
def test_conflicting_directory_or_changed_original_is_blocked_on_both_routes(sources, feature, manager, mode, code):
    args = {"target": "esp", "overwrite": True, "destination_mode": mode}
    values = {"overwrite_confirmed": True, "destination_mode": mode}
    if mode == "directory":
        args["output_dir"] = values["output_directory"] = str(sources.plugin.parent)
    else:
        sources.plugin.write_bytes(b"different version after opening")
    before = sources.plugin.read_bytes()
    draft = feature.feature.create_draft(sources.ctx, False, values)
    checked = feature.feature.mapper.preflight(draft)
    assert not checked.ready and any(
        item.check_id == code and item.status.value == "blocked" for item in checked.checks
    )
    assistant = _tool_write_back(args, sources.ctx)
    assert assistant.error_code == code and not manager.list_all()
    assert sources.plugin.read_bytes() == before
    assert not tuple(sources.root.glob(".*.bak"))


def test_fomod_preview_matches_packaged_files_and_generic_filter_keeps_its_own_scope(tmp_path):
    files = {
        "fomod/ModuleConfig.xml": b'<config><image path="textures/banner.png"/></config>',
        "textures/banner.png": b"used",
        "textures/unused.png": b"unused",
    }
    source = tmp_path / "extracted"
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, content in files.items():
            path = source / name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(content)
            bundle.writestr(name, content)
    destination = tmp_path / "output.zip"
    FomodPipeline().run(
        str(archive), str(destination), work_dir=str(tmp_path / "work"), ai_enabled=False, target_lang="zh_CN"
    )
    args = {"files": list(files)}
    ctx = SimpleNamespace(request_context=RequestContext("preview", authorized_roots=(str(tmp_path),)))
    assert _tool_filter_files(args, ctx).data["stripped"] == ["textures/banner.png", "textures/unused.png"]
    result = _tool_filter_files({**args, "fomod_dir": "extracted"}, ctx)
    assert result.success, result.message
    with zipfile.ZipFile(destination) as bundle:
        assert set(result.data["kept"]) == {name for name in bundle.namelist() if not name.endswith("/")}
    assert result.data["stripped"] == ["textures/unused.png"]
    for key in ("fomod_dir", "rules_json"):
        denied = _tool_filter_files({**args, key: str(tmp_path.parent)}, ctx)
        assert denied.error_code == "PATH_OUTSIDE_GRANT"


def test_verified_original_requires_confirmed_overwrite_and_matches_desktop(sources, feature, manager):
    before = sources.plugin.read_bytes()
    args = {"target": "esp", "destination_mode": "original"}
    assert _tool_write_back(args, sources.ctx).error_code == "OVERWRITE_CONFIRMED"
    assert sources.plugin.read_bytes() == before and not manager.list_all()
    draft = feature.feature.create_draft(
        sources.ctx, False, {"destination_mode": "original", "overwrite_confirmed": True}
    )
    desktop = _submit(feature, draft)
    assert desktop.outcome is OperationOutcome.COMPLETED
    expected = sources.plugin.read_bytes()
    assert expected != before
    sources.plugin.write_bytes(before)
    result = _result(manager, _tool_write_back({**args, "overwrite": True}, sources.ctx))
    assert sources.plugin.read_bytes() == expected
    assert result["artifacts"] == list(desktop.artifact_refs) == [str(sources.plugin)]
    assert Path(result["backup_path"]).read_bytes() == before
