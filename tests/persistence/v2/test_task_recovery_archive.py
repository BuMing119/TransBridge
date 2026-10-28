from pathlib import Path
import zipfile

from tests.persistence.v2 import test_project_archive as archive_support
from transbridge.application.translation.task_recovery import TaskRecoveryStore

exported_project = archive_support.exported_project
services_factory = archive_support.services_factory


def test_portable_project_retains_split_recovery_inputs_and_state(tmp_path, exported_project, services_factory):
    project = exported_project
    owned = Path(project.services.root) / "projects" / project.project_ref.identity.encoded
    store = TaskRecoveryStore(owned / "ai-task-recovery")
    entry = project.services.project_lifecycle.active.variant.snapshot().entries[0]
    key = entry.entry_key.to_dict()
    sources = [
        {
            "key": "source",
            "label": "Source",
            "translate_keys": [key],
            "polish_keys": [],
            "inputs": [
                {
                    "entry_key": key,
                    "original": "Hello",
                    "translation": "",
                    "context": "",
                    "stage": 0,
                    "revision": 0,
                }
            ],
        }
    ]
    store.create({
        "task_id": "archive-recovery",
        "project_id": project.project_ref.identity.value,
        "variant_id": project.variant_refs[1].identity.value,
        "mode": "translate",
        "config_digest": "fixed",
        "created_at": "2026-10-26T00:00:00+00:00",
        "state": "running",
        "sources": sources,
        "execution_inputs": {"archive-recovery": sources},
    })
    store.update_metadata(
        "archive-recovery",
        state="completed",
        applied_entries=[{"entry_key": key, "original": "Hello", "text": "你好", "stage": 1}],
    )
    expected = store.load("archive-recovery")
    bundle = tmp_path / "with-recovery.transbridge"
    project.services.project_archive.export_project(str(bundle), project.context)
    with zipfile.ZipFile(bundle) as archive:
        assert any(name.startswith("assets/ai-task-recovery/inputs/") for name in archive.namelist())
        assert any(name.startswith("assets/ai-task-recovery/state/") for name in archive.namelist())
    restored_services = services_factory("restored-recovery")
    restored_services.project_archive.import_project(str(bundle), project.context)
    restored_owned = Path(restored_services.root) / "projects" / project.project_ref.identity.encoded
    restored = TaskRecoveryStore(restored_owned / "ai-task-recovery")
    assert restored.load("archive-recovery") == expected
