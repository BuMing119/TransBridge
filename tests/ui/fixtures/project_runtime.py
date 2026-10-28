"""Build the production project services under a caller-owned test directory."""

from transbridge.application.contracts import RequestContext
from transbridge.bootstrap import build_runtime
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
