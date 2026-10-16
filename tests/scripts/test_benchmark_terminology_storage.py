"""Small real-SQLite smoke and non-overwrite boundaries for the storage benchmark."""

import json
from pathlib import Path

import pytest

from scripts import benchmark_terminology_storage as benchmark


@pytest.mark.parametrize("existing", ["workspace", "output"])
def test_cli_refuses_existing_paths_without_touching_contents(tmp_path: Path, existing: str) -> None:
    workspace, output = tmp_path / "fixtures", tmp_path / "result.json"
    protected = workspace if existing == "workspace" else output
    if existing == "workspace":
        protected.mkdir()
        protected = protected / "sentinel.txt"
    protected.write_text("user-owned", encoding="utf-8")

    with pytest.raises(SystemExit, match="2"):
        benchmark.main(["--workspace", str(workspace), "--output", str(output)])

    assert protected.read_text(encoding="utf-8") == "user-owned"
    assert not (workspace / "data-1000").exists()


@pytest.mark.parametrize("invalid", [["--repeats", "4"], ["--sizes", "0"], ["--edit-sizes", "-1"]])
def test_invalid_measurement_options_fail_before_creating_paths(tmp_path: Path, invalid: list[str]) -> None:
    workspace, output = tmp_path / "fixtures", tmp_path / "result.json"
    with pytest.raises(SystemExit, match="2"):
        benchmark.main(["--workspace", str(workspace), "--output", str(output), *invalid])
    assert not workspace.exists() and not output.exists()


def test_cli_rejects_output_ancestor_and_requires_both_paths(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="2"):
        benchmark.main(["--workspace", str(tmp_path / "result" / "fixtures"), "--output", str(tmp_path / "result")])
    assert not (tmp_path / "result").exists()
    with pytest.raises(SystemExit, match="2"):
        benchmark.main([])


def test_helpers_reject_existing_or_unmarked_fixtures(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        benchmark.create_fixture(tmp_path, 1)
    with pytest.raises(ValueError, match="positive"):
        benchmark.create_fixture(tmp_path / "bad-count", 0)
    with pytest.raises(ValueError, match="not a generated"):
        benchmark.open_browse(tmp_path)
    assert tuple(tmp_path.iterdir()) == ()
    marker = tmp_path / benchmark.MANIFEST
    marker.write_text(json.dumps({"kind": benchmark.FIXTURE_KIND}), encoding="utf-8")
    with pytest.raises(ValueError, match="database is missing"):
        benchmark.open_browse(tmp_path)
    assert tuple(tmp_path.iterdir()) == (marker,)


def test_small_real_fixture_checks_cold_warm_legacy_and_isolated_save(tmp_path: Path) -> None:
    fixture = benchmark.create_fixture(tmp_path / "fixture", 201)
    legacy = benchmark.measure_legacy(fixture, 201)
    assert list(legacy["ms"]) == [
        "draft_read",
        "base_snapshot",
        "describe",
        "effective_first",
        "effective_next",
        "effective_search",
        "draft_first",
        "draft_search",
    ]
    assert legacy["rss"] == {"status": "disabled"}
    for kind in ("effective", "draft"):
        measured = benchmark.measure_browse(fixture, 201, kind)
        assert list(measured["ms"]) == ["cold_first_total", "warm_first", "warm_next", "warm_search"]
        assert all(value >= 0 for value in measured["ms"].values())

    saved = benchmark.measure_edits(fixture, tmp_path / "edits", 201, 1)
    assert len(saved["seconds"]) == 1 and saved["median_seconds"] >= 0
    repository, profiles, _, _ = benchmark.open_browse(fixture)
    try:
        commands, context = benchmark.commands_for(repository)
        draft = commands.active_draft(context)
        assert draft.ref.revision == 0 and draft.actions == ()
        assert draft.decisions[-1].original == "Term 000200"
        assert draft.decisions[-1].translation == "术语 000200"
        assert profiles.selected_revision(benchmark.PROJECT, benchmark.VARIANT) is not None
    finally:
        repository.close()


def test_one_term_operation_probe_keeps_real_save_and_read_counts(tmp_path: Path) -> None:
    measured = benchmark.measure_read_counts(tmp_path / "counts")
    assert list(measured) == [
        "change_one_translation",
        "post_edit_read_assets",
        "effective_first_page",
        "review_conflicts_first_page",
    ]
    save = measured["change_one_translation"]["operations"]
    assert save["decode:TerminologyDraft"] == 2
    assert save.get("decode:TerminologyVersion", 0) == 0
    assert save["encode:TerminologyDraft"] == 1 and save["encode:ManualAction"] == 1
    assert measured["change_one_translation"]["selects"]


def test_report_records_partial_failure_without_overwriting(tmp_path: Path, monkeypatch) -> None:
    def fail_fixture(*_args):
        raise RuntimeError("synthetic fixture creation failed")

    monkeypatch.setattr(benchmark, "create_fixture", fail_fixture)
    output = tmp_path / "report.json"
    with pytest.raises(RuntimeError, match="synthetic fixture creation failed"):
        benchmark.main(["--workspace", str(tmp_path / "fixture"), "--output", str(output), "--sizes", "1"])
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["completed"] is False and report["samples"] == []
    assert report["arguments"]["repeats"] == 5
