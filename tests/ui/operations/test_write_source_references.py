from concurrent.futures import ThreadPoolExecutor
from itertools import count
from threading import get_ident
from types import SimpleNamespace

from PyQt6.QtWidgets import QFileDialog
import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectProvisioningRequest
from transbridge.application.projects.models import TransitionTarget
from transbridge.application.projects.provisioning import ProjectSourceRequest
from transbridge.application.projects.remote_binding import ParaTranzProjectBinding
from transbridge.application.projects.source_extraction import extract_source
from transbridge.application.projects.source_references import ProjectSourceReferences
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.bootstrap.runtime import UseCaseRegistry
from transbridge.config.text_encoding import TextEncodings
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.project_store.paged_reads import read_workbench_candidate
from transbridge.persistence.project_store.records import WorkspaceProjects
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from transbridge.ui.coordinators import write_input_coordinator
from transbridge.ui.project_rows import build_paged_slots
from transbridge.ui.source_hydration import slot_from_hydration
from transbridge.ui.source_input_state import SourceInputScope


@pytest.fixture
def project(tmp_path, monkeypatch):
    source = tmp_path / "original"
    source.mkdir()
    plugins = tuple(
        write_plugin(source / f"source-{index}.esp", [(0x800, "Name", f"Original {index}")]) for index in range(2)
    )
    projects = WorkspaceProjects(tmp_path / "projects")
    project_id, variant_id = ProjectId("refs"), VariantId("main")
    store = projects.store_for(project_id)
    context = RequestContext("gui")
    preparer = TranslationIoProjectSourcePreparer()
    sources = tuple(
        extract_source(
            preparer.prepare_source(
                ProjectSourceRequest(str(path), options=(("source_namespace", f"plugin:{path.name}"),)),
                context,
                role="primary",
                common_options=(),
            )
        )
        for path in plugins
    )
    store.create(project_id, "Plugin references", variant_id, "Main", sources)
    target = TransitionTarget(ProjectRef(project_id), VariantRef(variant_id, project_id))
    records = read_workbench_candidate(projects, target)
    slots = dict(build_paged_slots(records.paged_read))
    gui = SimpleNamespace(
        active_version_identity=(project_id.value, variant_id.value),
        project_revision=records.active.project.envelope.revision,
        variant_revision=records.active.variant.revision,
        slots=slots,
    )
    use_cases = UseCaseRegistry()
    use_cases.register("project_source_references", projects)
    use_cases.register(
        "project_projection", SimpleNamespace(snapshot=lambda: SimpleNamespace(values=records.active.summary()))
    )
    queued = []

    def start(operation, *, on_result, on_error, message):
        queued.append((operation, on_result, on_error))
        return True

    host = SimpleNamespace(
        context=gui,
        runtime_context=context,
        app_runtime=SimpleNamespace(use_cases=use_cases),
        start_foreground_task=start,
    )
    gui_thread = get_ident()
    original_bind = write_input_coordinator.bind_plugin_write_input

    def bind(*args, input_encoding):
        assert get_ident() != gui_thread
        return original_bind(*args, input_encoding=input_encoding)

    monkeypatch.setattr(write_input_coordinator, "bind_plugin_write_input", bind)
    with ThreadPoolExecutor(max_workers=1) as executor:

        def complete_next():
            operation, on_result, on_error = queued.pop(0)
            future = executor.submit(operation)
            try:
                result = future.result(timeout=5)
            except Exception as exc:
                on_error(str(exc))
            else:
                on_result(result)

        def complete_all():
            while queued:
                complete_next()

        yield SimpleNamespace(
            plugins=plugins,
            store=store,
            host=host,
            scope=SourceInputScope(gui, tuple(slots)),
            complete_next=complete_next,
            complete_all=complete_all,
        )


def _select(project, *, is_current=None):
    resumed, unavailable = [], []
    write_input_coordinator.select_write_inputs(
        project.host, project.scope, resumed.append, on_unavailable=unavailable.append, is_current=is_current
    )
    return resumed, unavailable


def _paths(project):
    return {info.name: info.reference_path for info in project.store.sources(native_fields=False)}


def test_valid_saved_paths_supply_plugins_without_dialog(project, monkeypatch):
    def unexpected_dialog(*args):
        pytest.fail("A validated saved plugin reference should not prompt")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", unexpected_dialog)
    project.host.context.project_text_encodings = SimpleNamespace(
        active_encodings=lambda _request: TextEncodings("cp1252", "cp936")
    )
    original_bind = write_input_coordinator.bind_plugin_write_input

    def bind(*args, input_encoding):
        assert input_encoding == "cp1252"
        return original_bind(*args, input_encoding=input_encoding)

    monkeypatch.setattr(write_input_coordinator, "bind_plugin_write_input", bind)
    resumed, unavailable = _select(project)
    project.complete_all()

    assert not unavailable
    assert resumed[0].text_encodings == TextEncodings("cp1252", "cp936")
    assert {slot.source_snapshot.source.uri for slot in resumed[0].slots.values()} == set(_paths(project).values())
    assert all(slot.source_snapshot.content is None for slot in project.scope.slots.values())


def test_one_manual_selection_repairs_matching_sibling_and_reuses_both_paths(project, tmp_path, monkeypatch):
    moved = tmp_path / "moved"
    moved.mkdir()
    targets = {path.name: path.rename(moved / path.name) for path in project.plugins}
    prompts = []

    def select(_host, title, _directory, _filter):
        prompts.append(title)
        assert "不存在" in title
        return str(next(path for name, path in targets.items() if name in title)), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", select)
    resumed, unavailable = _select(project)
    project.complete_all()
    assert not unavailable and resumed
    assert len(prompts) == 1
    assert _paths(project) == {name: str(path) for name, path in targets.items()}

    resumed, unavailable = _select(project)
    project.complete_all()
    assert not unavailable and resumed
    assert len(prompts) == 1


def test_different_content_and_nested_matches_leave_missing_plugin_for_manual_selection(project, tmp_path, monkeypatch):
    moved = tmp_path / "moved"
    moved.mkdir()
    nested = moved / "nested"
    nested.mkdir()
    prompts = []
    original_paths = _paths(project)
    first_name, second_name = (slot.source_snapshot.source.display_name for slot in project.scope.slots.values())
    first = (project.plugins[0].parent / first_name).rename(moved / first_name)
    (project.plugins[0].parent / second_name).rename(nested / second_name)
    write_plugin(moved / second_name, [(0x800, "Name", "Wrong plugin content")])

    def select(_host, title, _directory, _filter):
        prompts.append(title)
        return (str(first), "") if len(prompts) == 1 else ("", "")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", select)
    resumed, unavailable = _select(project)
    project.complete_all()
    assert not resumed and unavailable == [None]
    assert len(prompts) == 2 and "指纹不一致" in prompts[1]
    assert _paths(project) == original_paths


@pytest.mark.parametrize("state_change", ["dialog_closed", "project_switched", "variant_edited"])
def test_discarded_preparation_never_remembers_paths(project, tmp_path, monkeypatch, state_change):
    moved = tmp_path / "moved"
    moved.mkdir()
    targets = {path.name: path.rename(moved / path.name) for path in project.plugins}
    original_paths = _paths(project)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda _host, title, *_args: (str(next(path for name, path in targets.items() if name in title)), ""),
    )
    active = True
    resumed, unavailable = _select(project, is_current=lambda: active)
    project.complete_next()
    project.complete_next()
    if state_change == "dialog_closed":
        active = False
    elif state_change == "project_switched":
        project.host.context.active_version_identity = ("another-project", "main")
    else:
        project.host.context.variant_revision += 1
    project.complete_all()

    assert not resumed and unavailable
    assert _paths(project) == original_paths


@pytest.mark.parametrize("metadata_saved", [False, True])
def test_pending_source_repair_is_included_in_the_next_project_save(project, tmp_path, monkeypatch, metadata_saved):
    original = write_plugin(tmp_path / "Pending.esp", [(0x802, "Pending", "To translate")])
    ids = count()
    services = build_persistence_v2_services(
        tmp_path / "pending", id_factory=lambda: str(next(ids)), timestamp_factory=lambda: "2026-01-01T00:00:00+00:00"
    )
    commands, lifecycle = services.gui_project_commands, services.project_lifecycle
    request = project.host.runtime_context
    try:
        created = commands.create_project(ProjectProvisioningRequest("Pending source"), request)
        assert created.is_success, created.diagnostics
        added = commands.add_source(ProjectSourceRequest(str(original)), request)
        assert added.is_success, added.diagnostics
        if metadata_saved:
            binding = ParaTranzProjectBinding(42, "Linked project", "https://paratranz.cn")
            result = services.project_remote_bindings.set_paratranz_binding(binding, request)
            assert result.is_success, result.diagnostics
        active = lifecycle.active
        slot = slot_from_hydration(added.value.hydration)
        gui = project.host.context
        gui.active_version_identity = (active.project_ref.identity.value, active.formal_variant_ref.identity.value)
        gui.project_revision, gui.variant_revision = active.project.envelope.revision, active.variant.revision
        gui.slots = {added.value.source_id: slot}
        project.scope = SourceInputScope(gui, tuple(gui.slots))
        use_cases = UseCaseRegistry()
        use_cases.register("project_source_references", ProjectSourceReferences(services.projects, lifecycle))
        use_cases.register(
            "project_projection", SimpleNamespace(snapshot=lambda: SimpleNamespace(values=lifecycle.active.summary()))
        )
        project.host.app_runtime.use_cases = use_cases
        moved = original.rename(tmp_path / "Relocated.esp")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(moved), ""))

        resumed, unavailable = _select(project)
        project.complete_all()

        assert not unavailable and resumed
        store = services.projects.store_for(active.project_ref.identity)
        assert store.sources() == ()
        assert lifecycle.active.source_additions[0].reference_path == str(moved)
        assert lifecycle.active.project.envelope.revision == gui.project_revision
        saved = commands.save(request)
        assert saved.is_success, saved.diagnostics
        assert store.sources()[0].reference_path == str(moved)
    finally:
        services.close()
