"""Safety boundaries and a small real coordinator run for the open benchmark."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from scripts import benchmark_project_open as benchmark


@pytest.fixture(scope="module", autouse=True)
def _qt_application():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.parametrize(
    "options", [["--repeats", "4"], ["--entries", "0"], ["--timeout", "0"], ["--case", "0"], ["--case", "1", "1"]]
)
def test_invalid_options_do_not_create_output(tmp_path, options):
    output = tmp_path / "report.json"
    with pytest.raises(SystemExit, match="2"):
        benchmark.main(["--output", str(output), *options])
    assert not output.exists()


def test_existing_output_and_output_inside_input_are_rejected(tmp_path):
    output = tmp_path / "report.json"
    output.write_text("user-owned", encoding="utf-8")
    with pytest.raises(SystemExit, match="2"):
        benchmark.main(["--output", str(output)])
    with pytest.raises(SystemExit, match="2"):
        benchmark.main(["--output", str(tmp_path / "new.json"), "--data-root", str(tmp_path)])
    assert output.read_text(encoding="utf-8") == "user-owned"
    assert not (tmp_path / "new.json").exists()


def test_copy_is_limited_to_records_and_preserves_source_hashes(tmp_path):
    source, destination = tmp_path / "input", tmp_path / "copy"
    projects = source / "projects"
    projects.mkdir(parents=True)
    origin = tmp_path / "original.json"
    origin.write_text("private source text", encoding="utf-8")
    project = projects / "one.json"
    project.write_text(json.dumps({"data": {"sources": [{"location": str(origin)}]}}), encoding="utf-8")
    variant = projects / "one/variants/one.json"
    variant.parent.mkdir(parents=True)
    variant.write_text("private translation", encoding="utf-8")
    unrelated = source / "credentials.json"
    unrelated.write_text("private token", encoding="utf-8")

    copied, hashes = benchmark.copy_records(source, destination)

    assert copied == [destination / "projects/one.json"]
    assert (destination / "projects/one/variants/one.json").read_bytes() == variant.read_bytes()
    assert not (destination / "credentials.json").exists()
    assert benchmark.verify_inputs(hashes)
    origin.write_text("changed externally", encoding="utf-8")
    assert not benchmark.verify_inputs(hashes)


def test_copy_rejects_overlapping_or_existing_destination(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for destination in (source, source / "nested", tmp_path):
        with pytest.raises(ValueError, match="outside"):
            benchmark.copy_records(source, destination)
    sibling = tmp_path / "existing"
    sibling.mkdir()
    with pytest.raises(FileExistsError):
        benchmark.copy_records(source, sibling)


def _saved_project(source, origin):
    from transbridge.application.contracts import RequestContext
    from transbridge.application.io import FormatId
    from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest

    origin.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    services = benchmark._services(source)
    try:
        created = services.gui_project_commands.create_project(
            ProjectProvisioningRequest("Saved", source=ProjectSourceRequest(str(origin), FormatId.XML_EET)),
            RequestContext("benchmark-copy", run_id="create"),
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        return (
            Path(services.projects.path_for(active.project_ref)),
            active.project.envelope.data["sources"][0]["source_artifact_id"],
        )
    finally:
        services.close()


def test_copy_saved_artifact_closure_is_read_only_and_opens_after_original_source_disappears(tmp_path, monkeypatch):
    from transbridge.application.contracts import RequestContext
    from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
    from transbridge.persistence.source_artifact_store import ProjectSourceArtifactStore

    source, destination, origin = tmp_path / "input", tmp_path / "copy", tmp_path / "original.xml"
    project, artifact_id = _saved_project(source, origin)
    expected_records = {
        Path("source_artifacts") / relative
        for relative, _content in ProjectSourceArtifactStore(str(source)).iter_records(artifact_id)
    }
    unreferenced = source / "source_artifacts" / "blobs" / f"{'f' * 64}.bin"
    unreferenced.write_bytes(b"unreferenced private content")
    before = {path.relative_to(source): benchmark._digest(path) for path in source.rglob("*") if path.is_file()}
    origin.unlink()
    monkeypatch.setattr(
        TranslationIoProjectSourcePreparer,
        "prepare_source",
        lambda *_args, **_kwargs: pytest.fail("Saved source copy must not parse live input"),
    )
    with monkeypatch.context() as copying:
        copying.setattr(benchmark, "_services", lambda *_args: pytest.fail("Copy must not construct original services"))
        copied, hashes = benchmark.copy_records(source, destination)
    assert copied == [destination / project.relative_to(source)]
    assert {
        path.relative_to(destination) for path in (destination / "source_artifacts").rglob("*") if path.is_file()
    } == expected_records
    assert all(path.is_relative_to(source) for path in hashes)
    assert benchmark.verify_inputs(hashes)
    assert before == {path.relative_to(source): benchmark._digest(path) for path in source.rglob("*") if path.is_file()}
    services = benchmark._services(destination)
    try:
        context = RequestContext("benchmark-copy", run_id="open")
        prepared = services.current_project_opener.prepare_path(copied[0], context)
        assert prepared.is_success, prepared.diagnostics
        assert prepared.value.recovery is None
        assert prepared.value.hydrations[0].entries
        assert services.current_project_opener.activate(prepared.value, context).is_success
    finally:
        services.close()
    assert before == {path.relative_to(source): benchmark._digest(path) for path in source.rglob("*") if path.is_file()}
    original_blob = next(path for path in hashes if path.suffix == ".bin")
    original_blob.write_bytes(b"changed externally after copy")
    assert not benchmark.verify_inputs(hashes)


@pytest.mark.parametrize("damage", ["missing_manifest", "corrupt_manifest", "missing_blob", "corrupt_blob"])
def test_copy_rejects_missing_or_corrupt_saved_artifact_dependencies_before_creating_destination(tmp_path, damage):
    from transbridge.persistence.source_artifact_store import (
        ProjectSourceArtifactStore,
        SourceArtifactCorruptError,
        SourceArtifactMissingError,
    )

    source, destination = tmp_path / "input", tmp_path / "copy"
    _project, artifact_id = _saved_project(source, tmp_path / "original.xml")
    records = tuple(ProjectSourceArtifactStore(str(source)).iter_records(artifact_id))
    relative = (
        f"manifests/{artifact_id}.json"
        if damage.endswith("manifest")
        else next(path for path, _ in records if path.endswith(".bin"))
    )
    path = source / "source_artifacts" / relative
    if damage.startswith("missing"):
        path.unlink()
    else:
        path.write_bytes(b"corrupt saved artifact")
    before = {path.relative_to(source): benchmark._digest(path) for path in source.rglob("*") if path.is_file()}
    error = SourceArtifactMissingError if damage == "missing_manifest" else SourceArtifactCorruptError
    with pytest.raises(error):
        benchmark.copy_records(source, destination)
    assert not destination.exists()
    assert before == {path.relative_to(source): benchmark._digest(path) for path in source.rglob("*") if path.is_file()}


def test_source_checkout_requires_a_src_directory(tmp_path):
    output = tmp_path / "result.json"
    with pytest.raises(SystemExit, match="2"):
        benchmark.main(["--output", str(output), "--source-root", str(tmp_path)])
    assert not output.exists()


def test_small_real_coordinator_produces_cold_and_five_reload_measurements(tmp_path):
    output = tmp_path / "result.json"
    status = benchmark.main(["--output", str(output), "--entries", "3"])
    report = json.loads(output.read_text(encoding="utf-8"))

    assert status == 0, report
    assert report["completed"] is True
    assert report["synthetic"] is True
    assert report["source_inputs_unchanged"] is None
    result = report["results"][0]
    assert len(result["samples"]) == 6
    assert result["samples"][0]["phase"] == "cold_open"
    assert all(row["success"] and row["entries"] == 3 for row in result["samples"])
    assert result["reload_metrics_ms"]["max_event_loop_gap_ms"]["median"] >= 0
    assert all(row["ms"]["all_rows_ready_ms"] is not None for row in result["samples"])
    assert all(row["ms"]["first_viewport_paint_processed_ms"] is not None for row in result["samples"])
    for row in result["samples"]:
        if row["table_mode"] == "virtual_model":
            assert row["ms"]["max_table_batch_ms"] is None
        else:
            assert row["ms"]["max_table_batch_ms"] > 0
    assert "Synthetic translation" not in output.read_text(encoding="utf-8")
    assert "Synthetic original" not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("legacy", [False, True])
def test_render_observation_handles_virtual_models_and_baseline_batches(_qt_application, legacy):
    from PyQt6.QtGui import QStandardItemModel
    from PyQt6.QtWidgets import QTableView, QTableWidget

    class LegacyTable(QTableWidget):
        def __init__(self):
            super().__init__(0, 1)

        def start_render(self, session):
            self.render_session = session
            self.setRowCount(0)
            self.append_batch()

        def append_batch(self):
            self.setRowCount(min(3, self.rowCount() + 1))

    class VirtualTable(QTableView):
        def __init__(self):
            super().__init__()
            self.setModel(QStandardItemModel(0, 1, self))

        def start_render(self, session):
            self.render_session = session
            self.model().setRowCount(len(session.entries))

    table = LegacyTable() if legacy else VirtualTable()
    table.resize(400, 240)
    table.show()
    _qt_application.processEvents()
    measurements = benchmark.Measurements()
    try:
        with ExitStack() as stack:
            observation = benchmark.RenderObservation(table, measurements, stack)
            table.start_render(SimpleNamespace(entries=(1, 2, 3)))
            if legacy:
                assert observation.row_count() == 1
                assert measurements.finished_rows is None
                table.append_batch()
                table.append_batch()
                assert measurements.max_batch_ms > 0
            else:
                assert not hasattr(table, "append_batch")
                assert measurements.max_batch_ms == 0
            assert observation.row_count() == 3
            assert measurements.first_rows is not None
            assert measurements.finished_rows is not None
            assert observation.first_viewport_paint_ms is None
            deadline = time.monotonic() + 1
            while observation.first_viewport_paint_ms is None and time.monotonic() < deadline:
                _qt_application.processEvents()
                observation.process_events_completed()
            assert observation.first_viewport_paint_ms is not None
            assert observation.first_viewport_paint_ms >= measurements.first_rows
            observation.reset()
            assert observation.first_viewport_paint_ms is None
            assert not observation.paint_observed
    finally:
        table.close()
        table.deleteLater()
        _qt_application.processEvents()


def test_setup_failure_is_reported_without_private_exception_text(tmp_path, monkeypatch):
    def fail(*_args):
        raise ValueError("sensitive translation and path")

    monkeypatch.setattr(benchmark, "create_fixture", fail)
    output = tmp_path / "result.json"
    assert benchmark.main(["--output", str(output), "--entries", "1"]) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["completed"] is False
    assert report["failure_type"] == "ValueError"
    assert "sensitive" not in output.read_text(encoding="utf-8")


def test_case_selection_retains_original_case_number_and_rejects_missing_case(tmp_path):
    selected = tmp_path / "selected.json"
    assert benchmark.main(["--output", str(selected), "--entries", "1", "2", "--case", "2"]) == 0
    report = json.loads(selected.read_text(encoding="utf-8"))
    assert report["selected_cases"] == [2]
    assert [result["case"] for result in report["results"]] == [2]
    assert all(row["entries"] == 2 for row in report["results"][0]["samples"])

    missing = tmp_path / "missing.json"
    assert benchmark.main(["--output", str(missing), "--entries", "1", "--case", "2"]) == 1
    report = json.loads(missing.read_text(encoding="utf-8"))
    assert report["completed"] is False
    assert report["results"] == []


def test_real_data_option_runs_on_copy_and_preserves_all_original_files(tmp_path):
    source = tmp_path / "original"
    benchmark.create_fixture(source, 2)
    before = {path.relative_to(source): benchmark._digest(path) for path in source.rglob("*") if path.is_file()}
    output = tmp_path / "result.json"

    assert benchmark.main(["--output", str(output), "--data-root", str(source)]) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    after = {path.relative_to(source): benchmark._digest(path) for path in source.rglob("*") if path.is_file()}
    assert before == after
    assert report["synthetic"] is False
    assert report["source_inputs_unchanged"] is True
    assert all(row["entries"] == 2 for row in report["results"][0]["samples"])


def test_activation_failure_never_becomes_successful_timing(tmp_path, monkeypatch):
    from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
    from transbridge.persistence.current_project import CurrentProjectOpener

    monkeypatch.setattr(
        CurrentProjectOpener,
        "activate",
        lambda *_args, **_kwargs: OperationResult.from_exception(
            DomainError(ErrorCategory.CONFLICT, "SYNTHETIC_CONFLICT", "private translation")
        ),
    )
    output = tmp_path / "result.json"

    assert benchmark.main(["--output", str(output), "--entries", "2"]) == 1

    report = json.loads(output.read_text(encoding="utf-8"))
    result = report["results"][0]
    assert result["completed"] is False
    assert result["reload_metrics_ms"] == {}
    assert len(result["samples"]) == 1
    assert result["samples"][0]["success"] is False
    assert "SYNTHETIC_CONFLICT" in result["samples"][0]["failure_codes"]
    assert "private translation" not in output.read_text(encoding="utf-8")
