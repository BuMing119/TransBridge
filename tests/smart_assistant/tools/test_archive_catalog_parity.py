"""Read the same cloud pages and process the same local files through both production entrypoints."""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from tests.test_fomod_staging_publish import _build_dir, _spec, _stage_context
from tests.ui.test_paratranz_navigation_flow import _Context, _Worker, qapp as qapp_fixture
from transbridge.application.contracts import RequestContext
from transbridge.application.fomod import StagingPackPublisher
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.fomod.stages import DiffStage, ExtractStage
from transbridge.paratranz.paratranz_client import ParatranzClient
from transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard
from transbridge.smart_assistant.tools.tool_archive import (
    _tool_diff_directories,
    _tool_extract_archive,
    _tool_pack_archive,
)
from transbridge.smart_assistant.tools.tool_paratranz import _tool_get_upload_history, _tool_list_projects
from transbridge.ui.paratranz import history_tab, project_panel

qapp = qapp_fixture


def _contents(root):
    return {file.relative_to(root).as_posix(): file.read_bytes() for file in Path(root).rglob("*") if file.is_file()}


def test_archive_tools_match_fomod_pack_extract_and_diff(tmp_path):
    build = _build_dir(tmp_path)
    (build / "assets.bsa").write_bytes(b"data")
    ctx = SimpleNamespace(request_context=RequestContext("archive", authorized_roots=(str(tmp_path),)))
    spec = _spec(tmp_path)
    published = StagingPackPublisher().publish(spec, str(build))
    assert published.succeeded
    args = {"src_dir": "build", "archive_path": "agent.zip"}
    assert InputValidationGuard().before_execute({"tool": "pack_archive", "args": args}, ctx).allowed
    packed = _tool_pack_archive(args, ctx)
    assert packed.success, packed.message
    with zipfile.ZipFile(spec.output_archive) as desktop, zipfile.ZipFile(packed.data["archive_path"]) as assistant:
        assert {name: desktop.read(name) for name in desktop.namelist()} == {
            name: assistant.read(name) for name in assistant.namelist()
        }
    old = tmp_path / "old.zip"
    with zipfile.ZipFile(old, "w") as bundle:
        bundle.writestr("fomod/ModuleConfig.xml", "<config/>")
        bundle.writestr("assets.bsa", b"old data")
    context = _stage_context(
        tmp_path,
        build,
        replace(spec, new_archive=spec.output_archive, old_archive=str(old), old_archive_hash="fixture"),
    )
    context.workspace.mkdir(parents=True)
    extracted = ExtractStage().execute(context)
    artifacts = {item.artifact_id: item for item in extracted.artifacts}
    for key, archive in [("new", spec.output_archive), ("old", str(old))]:
        target = tmp_path / f"agent-{key}"
        result = _tool_extract_archive({"archive_path": archive, "dest_dir": str(target)}, ctx)
        assert result.success, result.message
        assert _contents(target) == _contents(artifacts[f"{key}_extracted"].location)
        assert result.data["extracted_count"] == len(_contents(target))
    diff = DiffStage().execute(replace(context, artifacts=artifacts))
    expect = json.loads(Path(diff.artifacts[0].location).read_text(encoding="utf-8"))
    actual = _tool_diff_directories({"old_dir": "agent-old", "new_dir": "agent-new"}, ctx)
    assert actual.success and actual.data == expect
    assert actual.data["hash_reprocessed"] == ["assets.bsa"]


def test_cancelled_archive_leaves_existing_output_and_partial_extraction_untouched(tmp_path):
    build = _build_dir(tmp_path)
    source = tmp_path / "input.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("test", "data")
    before = source.read_bytes()
    ctx = SimpleNamespace(authorized_roots=(str(tmp_path),), cancellation_token=SimpleNamespace(is_cancelled=True))
    for call, args in [
        (_tool_pack_archive, {"src_dir": str(build), "archive_path": str(source)}),
        (_tool_extract_archive, {"archive_path": str(source), "dest_dir": "output"}),
    ]:
        assert call(args, ctx).error_code == "ARCHIVE_CANCELLED"
    assert not (tmp_path / "output").exists()
    assert source.read_bytes() == before
    assert not tuple(tmp_path.glob(".tb_pack_*"))


def test_7z_outputs_reopen_with_the_same_relative_files(tmp_path):
    pytest.importorskip("py7zr")
    build = _build_dir(tmp_path)
    spec = replace(_spec(tmp_path), output_archive=str(tmp_path / "desktop.7z"), output_format="7z")
    ctx = SimpleNamespace(authorized_roots=(str(tmp_path),))
    published = StagingPackPublisher().publish(spec, str(build))
    assert published.succeeded, published
    packed = _tool_pack_archive({"src_dir": str(build), "archive_path": "assistant.7z", "fmt": "7z"}, ctx)
    assert packed.success, packed.message
    for archive in ("desktop.7z", "assistant.7z"):
        target = tmp_path / Path(archive).stem
        result = _tool_extract_archive({"archive_path": archive, "dest_dir": str(target)}, ctx)
        assert result.success, result.message
        assert _contents(target) == _contents(build)


def test_only_current_archive_and_history_arguments_pass_admission(tmp_path):
    ctx = SimpleNamespace(authorized_roots=(str(tmp_path),))
    guard = InputValidationGuard()
    for name, args in [
        ("get_upload_history", {"limit": 20}),
        ("list_projects", {"uid": "other-user"}),
        ("extract_archive", {"archive_path": "input.zip", "dest_dir": "output", "files": ["example.txt"]}),
    ]:
        assert guard.before_execute({"tool": name, "args": args}, ctx).code == "ARGUMENT_SCHEMA_INVALID"
    assert not _tool_extract_archive({"files": []}, ctx).success
    for tool, args in [
        ("pack_archive", {"src_dir": str(tmp_path.parent), "archive_path": "new.zip"}),
        ("extract_archive", {"archive_path": str(tmp_path.parent), "dest_dir": "new"}),
    ]:
        assert guard.before_execute({"tool": tool, "args": args}, ctx).code == "PATH_OUTSIDE_GRANT"


def test_directory_diff_checks_the_effective_fomod_root(tmp_path):
    (tmp_path / "fomod").mkdir()
    granted = tmp_path / "nested"
    granted.mkdir()
    ctx = SimpleNamespace(authorized_roots=(str(granted),))
    assert _tool_diff_directories({"old_dir": ".", "new_dir": "."}, ctx).error_code == "PATH_OUTSIDE_GRANT"


@pytest.fixture
def cloud(tmp_path, monkeypatch, qapp):
    ctx = _Context()
    ctx.config = ParatranzConfig(
        token="offline",
        user_id=7,
        environment={},
        config_path=tmp_path / "config.ini",
        credential_store=UnavailableCredentialStore(),
    )
    calls = []

    def read(_client, method, path, *, params, **_kwargs):
        assert method == "GET"
        calls.append((path, params))
        page = params["page"]
        if path == "/projects":
            return {
                "data": [
                    {"id": number, "name": f"project-{number}"} for number in (range(1, 201) if page == 1 else (201,))
                ]
            }
        assert path == "/projects/11/files/revisions"
        return {"data": [{"name": "plugin.json", "type": "update", "uid": 7, "insert": page, "update": 3, "remove": 1}]}

    monkeypatch.setattr(ParatranzClient, "_request", read)
    monkeypatch.setattr(project_panel, "ApiWorker", _Worker)
    monkeypatch.setattr(history_tab, "ApiWorker", _Worker)
    return ctx, calls


@pytest.mark.parametrize("mine", [False, True])
def test_project_browser_and_assistant_request_identical_pages(cloud, mine):
    ctx, calls = cloud
    panel = project_panel.ProjectListPanel(ctx)
    try:
        panel._tab_bar.setCurrentIndex(int(mine))
        panel.load_projects()
        panel._workers[-1].complete()
        desktop = list(calls)
        assert [params["page"] for _, params in desktop] == [1, 2]
        assert all(params["pageSize"] == 200 and params.get("uid") == (7 if mine else None) for _, params in desktop)
        calls.clear()
        shown = [panel._list.item(index).text() for index in range(panel._list.count())]
        result = _tool_list_projects({} if mine else {"uid": ""}, ctx)
        assert result.success, result.message
        assert [row["name"] for row in result.data["projects"]] == shown
        assert calls == desktop
        calls.clear()
        assert not _tool_list_projects({"uid": "9"}, ctx).success and not calls
    finally:
        panel.close()


def test_revision_browser_and_assistant_request_same_page(cloud):
    ctx, calls = cloud
    tab = history_tab.HistoryTab(ctx)
    try:
        tab._project_id = 11
        tab._rev_pager.set_page(2)
        tab.load_revisions()
        tab._workers[-1].complete()
        desktop = list(calls)
        assert desktop == [("/projects/11/files/revisions", {"page": 2, "pageSize": 50})]
        calls.clear()
        history = _tool_get_upload_history({"project_id": 11, "page": 2}, ctx)
        assert history.success, history.message
        row = history.data["history"][0]
        assert tab._rev_table.item(0, 1).text() == row["name"]
        assert tab._rev_table.item(0, 4).text() == str(row["insert"])
        assert calls == desktop
        calls.clear()
        for args in ({"limit": 1}, {"page": 0}, {"page": 10000}):
            assert not _tool_get_upload_history({"project_id": 11, **args}, ctx).success
        assert not calls
    finally:
        tab.close()
