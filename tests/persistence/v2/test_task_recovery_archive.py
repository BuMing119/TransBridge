from pathlib import Path
import zipfile

from tests.persistence.v2 import test_project_archive as archive_support
from transbridge.persistence.task_store import TaskStore

exported_project = archive_support.exported_project
services_factory = archive_support.services_factory


def test_portable_project_excludes_independent_task_results(tmp_path, exported_project, services_factory):
    project = exported_project
    store = TaskStore(Path(project.services.root) / "task-store-v1")
    recovery = store.recovery()
    run_id = "archive-recovery"
    recovery.create({
        "task_id": run_id,
        "project_id": project.project_ref.identity.value,
        "variant_id": project.variant_refs[0].identity.value,
        "mode": "translate",
        "config_digest": "archive-test",
        "state": "completed",
        "created_at": "2026-11-09T00:00:00+00:00",
        "sources": [],
    })
    recovery.save_results(
        run_id,
        [
            {
                "entry_key": project.key.to_dict(),
                "source": "Archive fixture",
                "action": "translation",
                "before": {"original": "Original", "translation": "", "stage": 0, "context": "", "revision": 0},
                "status": "succeeded",
                "text": "Independent task result",
                "stage": 1,
                "decision": "pending",
                "applied": False,
                "attempt": 1,
                "reason": "",
                "report_details": {},
            }
        ],
    )
    bundle = tmp_path / "with-recovery.transbridge"
    assert Path(project.services.project_archive.export_project(str(bundle), project.context)) == bundle
    with zipfile.ZipFile(bundle) as archive:
        assert not any(
            "task-store-v1" in Path(name).parts or "ai-task-recovery" in Path(name).parts for name in archive.namelist()
        )
    restored_services = services_factory("restored-recovery")
    restored = Path(restored_services.project_archive.import_project(str(bundle), project.context))
    assert restored == Path(restored_services.projects.path_for(project.project_ref))
    assert restored.is_file()
    restored_task_root = Path(restored_services.root) / "task-store-v1"
    assert not restored_task_root.exists()
    assert recovery.load_results(run_id)["rows"][0]["text"] == "Independent task result"
