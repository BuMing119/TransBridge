"""Historical task sidecars survive package import as discoverable reports."""

import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest

from tests.application.translation.test_legacy_task_recovery import _history, _key, _record
from tests.persistence.v2.test_legacy_project_archive import _context, _project, _variant
from transbridge.application.translation.imported_task_history import ProjectTaskHistoryReader
from transbridge.application.translation.postprocess_checkpoint import (
    FilesystemPostProcessCheckpointPort,
    PostProcessCheckpoint,
)
from transbridge.application.translation.postprocess_report import JsonReportRenderer
from transbridge.application.translation.proofread_checkpoint import ProofreadCheckpoint
from transbridge.application.translation.task_history import restore_report_snapshot
from transbridge.application.translation.task_recovery import TaskRecoveryStore
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.v2 import OsPersistenceFilesystem, ProjectId, ProjectRef
from transbridge.ui.tools.ai_translator.task_recovery_prompt import read_recovery_records


def _directory(root: Path, project: dict, records: dict[str, dict]) -> Path:
    root.mkdir()
    for member, value in {"project.json": project, **records}.items():
        path = root / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return root


def _contents(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


class _FailingCatalogFilesystem(OsPersistenceFilesystem):
    after_replace = False
    triggered = False

    def replace(self, source, destination):
        if Path(destination).name == "project-catalog.json" and not self.triggered:
            self.triggered = True
            if self.after_replace:
                super().replace(source, destination)
            raise OSError("injected package import failure")
        super().replace(source, destination)


def _source(directory):
    target = directory.parent / "old.transbridge"
    with ZipFile(target, "w") as bundle:
        for member in directory.rglob("*"):
            if member.is_file():
                bundle.write(member, member.relative_to(directory).as_posix())
    return target


def _saved_results():
    return [
        {
            "entry_key": _key().to_dict(),
            "source": "source",
            "action": "translation",
            "before": {"original": "A place", "translation": "", "stage": 0, "context": "Place", "revision": 0},
            "status": "succeeded",
            "text": "不可丢失的 AI 成果",
            "stage": 1,
            "reason": "",
            "decision": "pending",
            "applied": False,
            "attempt": 1,
            "report_details": {"note": "旧结果"},
        }
    ]


def test_historical_writers_import_readonly_reports_and_keep_all_original_bytes(tmp_path):
    old = _directory(tmp_path / "old", _project(), {"主版本/current.json": _variant()})
    _history(old)
    recovery = TaskRecoveryStore(old / "ai-task-recovery")
    # Historical wire document; current create uses a hard link unavailable in
    # this Windows sandbox. Results and history still use their real writers.
    recovery.root.mkdir()
    recovery._path("old-task").write_text(json.dumps(_record()), encoding="utf-8")
    recovery.save_results("old-task", _saved_results())
    recovery.acquire("old-task")
    recovery.release()
    source = _source(old)
    before = _contents(old)
    services = build_persistence_v2_services(
        tmp_path / "new", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    try:
        imported = services.project_archive.import_project(str(source), _context())
        dto = services.projects.read_snapshot(ProjectRef(ProjectId(json.loads(Path(imported).read_bytes())["id"])))
        project_id = dto.envelope.identity
        owned = tmp_path / "new" / "projects" / ProjectId(project_id).encoded
        index = json.loads((owned / "legacy-ai" / "index.json").read_bytes())
        locks = [item for item in index["original_files"] if item["member"].startswith("ai-task-recovery/locks/")]
        assert len(locks) == 1
        assert locks[0]["asset"].endswith(".bin")
        assert not (owned / "ai-task-recovery" / "locks").exists()
        for original in index["original_files"]:
            assert (owned / original["asset"]).read_bytes() == before[original["member"]]
        reader = ProjectTaskHistoryReader(Path(imported).parent, project_id)
        reports = reader.list_records()
        assert len(reports) == 2
        assert all(row["project_id"] is None and row["variant_id"] is None and not row["supported"] for row in reports)
        assert all(row["legacy_import"]["project_id"] == "project" for row in reports)
        assert len({row["run_id"] for row in reports}) == 2
        loaded = tuple(reader.load(row["run_id"]) for row in reports)
        snapshots = [restore_report_snapshot(source["snapshot"]) for row in loaded for source in row["sources"]]
        assert any(
            candidate.text == "不可丢失的 AI 成果" for snapshot in snapshots for candidate in snapshot.candidates
        )
        assert all(candidate.entry_key == _key() for snapshot in snapshots for candidate in snapshot.candidates)
        assert all(JsonReportRenderer().render(snapshot).content for snapshot in snapshots)
        assert ProjectTaskHistoryReader(Path(imported).parent, "another-project").list_records() == ()
        assert read_recovery_records(Path(imported).parent, project_id=project_id) == ()
        assert _contents(old) == before
    finally:
        services.close()


@pytest.mark.parametrize("with_manifest", [False, True])
def test_results_without_history_remain_visible_and_foreign_scope_is_only_viewable(tmp_path, with_manifest):
    old = _directory(tmp_path / "old", _project(), {"主版本/current.json": _variant()})
    recovery = TaskRecoveryStore(old / "ai-task-recovery")
    recovery.save_results("old-task", _saved_results())
    if with_manifest:
        record = _record()
        record["sources"][0]["translate_keys"] = []
        recovery._path("old-task").write_text(json.dumps(record), encoding="utf-8")
    before = _contents(old)
    services = build_persistence_v2_services(
        tmp_path / "new", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    try:
        imported = services.project_archive.import_project(str(_source(old)), _context())
        project_id = json.loads(Path(imported).read_bytes())["id"]
        reports = ProjectTaskHistoryReader(Path(imported).parent, project_id).list_records()
        assert any(
            row["candidate"] == "不可丢失的 AI 成果"
            for report in reports
            for source in report["sources"]
            for row in source["snapshot"]["entries"]
        )
        assert all(report["project_id"] is None and not report["supported"] for report in reports)
        if with_manifest:
            assert any(
                "不属于原任务范围" in source.get("error", "") for report in reports for source in report["sources"]
            )
        assert _contents(old) == before
    finally:
        services.close()


def test_real_resume_and_download_writer_assets_are_retained_without_execution(tmp_path):
    old = _directory(tmp_path / "old", _project(), {"主版本/current.json": {"translations": {}}})
    proofreading = ProofreadCheckpoint(
        old / "ai-proofread-resume", project="old", variant="main", source="source", settings={}, task_id="old-task"
    )
    proofreading.write([("frozen-input", {"text": "历史校对候选", "stage": 1})])
    proofreading.path.with_name(proofreading.path.name + "-wal").write_bytes(b"opaque historical SQLite WAL")
    FilesystemPostProcessCheckpointPort(old / "ai-translation-resume" / "postprocess").save(
        PostProcessCheckpoint("old-run", "owner", "a" * 64)
    )
    legacy_download = old / "download-results" / "historical" / "result.json"
    legacy_download.parent.mkdir(parents=True)
    legacy_download.write_text(json.dumps({"schema": 1, "target": ["old-project", "main"]}), encoding="utf-8")
    before = _contents(old)
    source = _source(old)
    services = build_persistence_v2_services(
        tmp_path / "new", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    target = build_persistence_v2_services(
        tmp_path / "target", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    try:
        imported = services.project_archive.import_project(str(source), _context())
        project_id = json.loads(Path(imported).read_bytes())["id"]
        owned = tmp_path / "new" / "projects" / ProjectId(project_id).encoded
        index = json.loads((owned / "legacy-ai" / "index.json").read_bytes())
        assert {item["member"].split("/")[0] for item in index["original_files"]} == {
            "ai-proofread-resume",
            "ai-translation-resume",
            "download-results",
        }
        for original in index["original_files"]:
            assert (owned / original["asset"]).read_bytes() == before[original["member"]]
            assert original["asset"].endswith(".bin")
        assert ProjectTaskHistoryReader(Path(imported).parent, project_id).list_records() == ()
        assert read_recovery_records(Path(imported).parent, project_id=project_id) == ()
        assert services.current_project_opener.open_path(imported, _context()).is_success
        bundle = tmp_path / "portable.transbridge"
        services.project_archive.export_project(str(bundle), _context())
        restored = target.project_archive.import_project(str(bundle), _context())
        restored_id = json.loads(Path(restored).read_bytes())["id"]
        restored_owned = tmp_path / "target" / "projects" / ProjectId(restored_id).encoded
        restored_index = json.loads((restored_owned / "legacy-ai" / "index.json").read_bytes())
        assert restored_index == index
        for original in restored_index["original_files"]:
            assert (restored_owned / original["asset"]).read_bytes() == before[original["member"]]
        assert _contents(old) == before
    finally:
        target.close()
        services.close()


def test_bad_sidecar_preserves_raw_bytes_and_does_not_hide_other_good_history(tmp_path):
    old = _directory(tmp_path / "old", _project(), {"主版本/current.json": _variant()})
    _history(old)
    _history(old, _record(task_id="damaged-task"))
    broken = next(
        path for path in (old / "ai-task-history").glob("*/metadata.json") if "damaged-task" in path.read_text()
    )
    broken.write_bytes(b"{damaged json")
    before = _contents(old)
    services = build_persistence_v2_services(
        tmp_path / "new", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    try:
        imported = services.project_archive.import_project(str(_source(old)), _context())
        project_id = json.loads(Path(imported).read_bytes())["id"]
        reports = ProjectTaskHistoryReader(Path(imported).parent, project_id).list_records()
        assert any(report["legacy_import"]["run_id"] == "old-task" for report in reports)
        assert any("无法规范化" in source.get("error", "") for report in reports for source in report["sources"])
        assert all(report["project_id"] is None and not report["supported"] for report in reports)
        assert _contents(old) == before
    finally:
        services.close()


def test_variant_named_like_sidecar_directory_keeps_its_manual_history(tmp_path):
    name = "ai-task-history"
    old = _directory(
        tmp_path / "old",
        _project(names=(name,)),
        {f"{name}/current.json": _variant(name), f"{name}/snapshots/before.json": _variant(name)},
    )
    services = build_persistence_v2_services(
        tmp_path / "new", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    try:
        services.project_archive.import_project(str(_source(old)), _context())
        assert len(list((tmp_path / "new" / "snapshots").glob("*.json"))) == 1
        assert not list((tmp_path / "new" / "projects").glob("*/legacy-ai/index.json"))
    finally:
        services.close()


def test_imported_task_index_survives_current_archive_roundtrip_and_copy(tmp_path):
    # This test exercises self-contained reports. Missing translation input is
    # covered separately by read-only legacy recovery tests.
    old = _directory(tmp_path / "old", _project(), {"主版本/current.json": {"translations": {}}})
    _history(old)
    services = build_persistence_v2_services(
        tmp_path / "first", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    target = build_persistence_v2_services(
        tmp_path / "second", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    try:
        imported = services.project_archive.import_project(str(_source(old)), _context())
        assert services.current_project_opener.open_path(imported, _context()).is_success
        bundle = tmp_path / "current.transbridge"
        services.project_archive.export_project(str(bundle), _context())
        first = target.project_archive.import_project(str(bundle), _context())
        second = target.project_archive.import_project(
            str(bundle), _context(), copy_on_identity_conflict=True, requested_name="Imported copy"
        )
        for path in (first, second):
            identity = target.current_project_opener.prepare_path(path, _context()).value.project_ref.identity.value
            reports = ProjectTaskHistoryReader(Path(path).parent, identity).list_records()
            assert len(reports) == 1 and reports[0]["project_id"] is None
    finally:
        target.close()
        services.close()


@pytest.mark.parametrize("after_replace", [False, True])
def test_sidecar_publication_errors_roll_back_every_imported_asset(tmp_path, after_replace):
    old = _directory(tmp_path / "old", _project(), {"主版本/current.json": _variant()})
    _history(old)
    before = _contents(old)
    filesystem = _FailingCatalogFilesystem()
    filesystem.after_replace = after_replace
    services = build_persistence_v2_services(
        tmp_path / "new", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now", filesystem=filesystem
    )
    try:
        target_before = _contents(tmp_path / "new")
        with pytest.raises(OSError, match="injected"):
            services.project_archive.import_project(str(_source(old)), _context())
        assert _contents(tmp_path / "new") == target_before
        assert _contents(old) == before
    finally:
        services.close()
