"""Build the production project services under a caller-owned test directory."""

from contextlib import closing
import json
from uuid import uuid4
from zipfile import ZipFile

from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectProvisioningRequest
from transbridge.bootstrap import build_runtime
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.ui.context import AppContext


def project_window_dependencies(root):
    runtime = build_runtime({"persistence_v2_root": root / "data", "ui_config_path": root / "config.ini"})
    request = RequestContext("gui", run_id="window-fixture", authorized_roots=(str(root),))
    context = AppContext(
        project_projection=runtime.use_cases.resolve("project_projection"),
        project_commands=runtime.use_cases.resolve("gui_project_commands"),
        project_catalog=runtime.use_cases.resolve("project_catalog"),
        runtime_context=request,
    )
    return {"app_context": context, "runtime": runtime, "runtime_context": request}


def write_project_package(tmp_path):
    package = tmp_path / "project.transbridge"
    with closing(
        build_persistence_v2_services(
            tmp_path / "source", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
        )
    ) as services:
        context = RequestContext("gui", run_id=uuid4().hex)
        created = services.gui_project_commands.create_project(ProjectProvisioningRequest("GUI project"), context)
        assert created.is_success, created.diagnostics
        services.project_archive.export_project(str(package), context)
    return package


def write_legacy_project_package(tmp_path):
    package = tmp_path / "legacy.transbridge"
    with ZipFile(package, "w") as archive:
        archive.writestr(
            "project.json",
            json.dumps({
                "name": "GUI project",
                "sources": [],
                "variants": [{"name": "main"}],
                "active_variant": "main",
            }),
        )
        archive.writestr("main/current.json", json.dumps({"variant": "main", "translations": {}}))
    return package
