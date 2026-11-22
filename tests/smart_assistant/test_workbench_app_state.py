"""Workbench identity, filters and credential metadata at the public assistant entry point."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication

from tests.conftest import make_test_collection
from tests.smart_assistant.test_request_execution import setup as request_setup
from transbridge.config.paratranz import ParatranzConfig
from transbridge.smart_assistant.request_execution import RequestExecutionGate
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import register_all
from transbridge.smart_assistant.tools.base import execute_with_guardrails
from transbridge.smart_assistant.tools.tool_default import _register_default_tools
from transbridge.smart_assistant.tools.tool_editor import _register_editor_tools
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.context import AppContext, CollectionSlot
from transbridge.ui.workbench.widget import WorkbenchWidget

setup = request_setup


def test_state_uses_live_source_selector_filters_and_credentials(current_project_factory, monkeypatch):
    project = current_project_factory(make_test_collection(2), esp_path=r"C:\private\translations\first.esp")
    app = project.context
    first = app.active_key
    config = ParatranzConfig(token="private-test-value", environment={})
    app.config = config
    view = WorkbenchWidget(app)
    try:
        register_all()
        tool = ToolRegistry.get("get_app_state")
        captured = ExecutionContext(app)

        def forbid_secret_read(*_args, **_kwargs):
            raise AssertionError("Reporting credential status must not read a secret or reload the file")

        monkeypatch.setattr(ParatranzConfig, "token", property(forbid_secret_read))
        monkeypatch.setattr(ParatranzConfig, "load_from_file", forbid_secret_read)
        result = tool.execute({}, captured)
        assert result.success and result.data["has_active_collection"]
        assert result.data["project"] == view.project_bar._project_label.toolTip()
        assert result.data["variant"] == view.project_bar._variant_combo.currentText()
        assert result.data["esp_file"] == "first.esp"
        assert result.data["paratranz_configured"]

        second = r"C:\private\translations\local.xml"
        app.add_slot(second, CollectionSlot("", make_test_collection(1), xt_path=second))
        stale = tool.execute({}, captured)
        assert not stale.success
        assert "来源" in stale.message
        view.preview.apply_filter_state({"stage": [2]})
        result = tool.execute({}, ExecutionContext(app))
        assert result.success
        assert result.data["collection_count"] == view._collection_combo.count() == 2
        assert result.data["active_collection"] == result.data["xt_file"] == "local.xml"
        assert view._collection_combo.currentText() == "翻译内容 · local.xml"
        assert result.data["esp_file"] is None
        assert result.data["filters"] == view.preview.get_filter_state()
        assert "private" not in str(result.data)

        # External navigation must update the same selector that the tool now reads.
        app.activate_slot(first)
        assert view._collection_combo.currentData() == first
        assert tool.execute({}, ExecutionContext(app)).data["active_collection"] == app.active_slot.label
    finally:
        view.close()
        view.deleteLater()


def test_request_state_keeps_loaded_source_and_effective_filter_after_gate_recreation(setup, current_project_factory):
    service, owner, initial, _, _ = setup
    app = current_project_factory(make_test_collection(2)).context
    _register_default_tools()
    _register_editor_tools()

    def capture(state):
        state["ingress"][0]["selection"] = {
            "active_version_identity": list(app.active_version_identity),
            "workbench_source_key": app.active_key,
            "filter_state": app.filter_state,
        }

    service.transact(owner, capture)
    service.scheduler.release(initial.admission)
    turn = service.scheduler.select_next_turn(
        owner.session_id, "view", (initial.current_request(),), allowed_tools=("set_filters", "get_app_state")
    )

    def call(name, args):
        gate = RequestExecutionGate(service, owner, turn.admission)
        context = ExecutionContext(app, assistant_gate=gate, request_context=owner, assistant_required=True)
        return execute_with_guardrails(ToolRegistry.get(name), args, context, middlewares=[])

    assert call("set_filters", {"stages": [2]}).success
    app.clear_filters()
    result = call("get_app_state", {})
    assert result.success, result.message
    assert result.data["has_active_collection"]
    assert result.data["active_collection"] == app.active_slot.label
    assert result.data["filters"]["stage"] == [2]
    app.add_slot("new", CollectionSlot("Other", make_test_collection(1)))
    assert not call("get_app_state", {}).success


def test_queued_read_rechecks_its_source_on_the_workbench_thread(current_project_factory, monkeypatch):
    app = current_project_factory(make_test_collection(1)).context
    first = app.active_key
    app.add_slot("second", CollectionSlot("Second", make_test_collection(1)))
    app.activate_slot(first)
    context = ExecutionContext(app)
    register_all()
    dispatched = Event()
    dispatch = app.safe_mutate

    def enqueue(callback):
        dispatch(callback)
        dispatched.set()

    monkeypatch.setattr(app, "safe_mutate", enqueue)
    with ThreadPoolExecutor(max_workers=1) as worker:
        read = worker.submit(ToolRegistry.get("get_app_state").execute, {}, context)
        assert dispatched.wait(2)
        app.activate_slot("second")
        QApplication.processEvents()
        result = read.result(timeout=2)
    assert not result.success and "来源" in result.message


def test_empty_workbench_has_no_active_source_or_file(monkeypatch):
    qt = QApplication.instance() or QApplication([])
    monkeypatch.setattr(ParatranzConfig, "create_or_load", lambda: SimpleNamespace(credential_configured=False))
    register_all()
    app = AppContext()
    try:
        result = ToolRegistry.get("get_app_state").execute({}, ExecutionContext(app))
        assert result.success and not result.data["has_active_collection"]
        assert result.data["collection_count"] == 0
        assert not result.data["paratranz_configured"]
        assert all(
            result.data[key] is None
            for key in ("active_collection", "project", "variant", "esp_file", "xt_file", "eet_file")
        )
    finally:
        app.close_projection()
        qt.processEvents()


def test_source_switch_reports_the_active_workbench_slot_with_a_frozen_request(current_project_factory):
    project = current_project_factory(make_test_collection(1))
    app = project.context
    first = app.active_key
    app.add_slot("second", CollectionSlot("Second", make_test_collection(1)))
    app.activate_slot(first)
    ctx = ExecutionContext(app)
    object.__setattr__(ctx, "active_slot", None)
    register_all()
    result = ToolRegistry.get("switch_collection").execute({"collection_name": "Second"}, ctx)
    assert app.active_key == "second" and result.data["active_collection"] == app.active_slot.label
    rows = ToolRegistry.get("list_collections").execute({}, ctx).data["collections"]
    assert [row["key"] for row in rows if row["is_active"]] == ["second"]
    current = ToolRegistry.get("get_current_project").execute({}, ctx)
    assert current.data["collection"] == app.active_slot.label
    app.add_slot("third", CollectionSlot("Second", make_test_collection(1)))
    active = app.active_key
    assert not ToolRegistry.get("switch_collection").execute({"collection_name": "Second"}, ctx).success
    assert app.active_key == active
    object.__setattr__(ctx, "_target_version_identity", ("other-project", "other-version"))
    assert not ToolRegistry.get("get_current_project").execute({}, ctx).success
    assert not ToolRegistry.get("list_collections").execute({}, ctx).success


def test_local_projects_match_the_start_center_directory(tmp_path):
    from types import SimpleNamespace

    from transbridge.bootstrap import build_runtime
    from transbridge.persistence.project_store.paths import project_path
    from transbridge.persistence.project_store.repository import ProjectStore
    from transbridge.persistence.v2 import ProjectId, VariantId
    from transbridge.ui.shell.start_center_directory import read_start_center_directory

    for key, name in (("one", "共享"), ("two", "另一个工程")):
        project_id = ProjectId(key)
        ProjectStore(project_path(tmp_path, project_id)).create(project_id, name, VariantId("main"), "默认")
    runtime = build_runtime({"persistence_v2_root": tmp_path, "ui_config_path": tmp_path / "ui.json"})
    try:
        desktop = read_start_center_directory(runtime.use_cases, None)
        ctx = SimpleNamespace(project_catalog=runtime.use_cases.resolve("project_catalog"))
        register_all()
        result = ToolRegistry.get("list_local_projects").execute({}, ctx)
        assert result.success and len(desktop.projects) == 2
        assert result.data["projects"] == [{"name": row.name} for row in desktop.project_rows(None)]
    finally:
        assert runtime.close().is_success
