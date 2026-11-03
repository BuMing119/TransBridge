from pathlib import Path
import zipfile

from tests.persistence.v2 import test_project_archive as archive_support

exported_project = archive_support.exported_project
services_factory = archive_support.services_factory


def test_portable_project_excludes_split_recovery_inputs_and_state(tmp_path, exported_project, services_factory):
    project = exported_project
    owned = Path(project.services.root) / "projects" / project.project_ref.identity.encoded
    for relative in ("task.json", "inputs/fixed.json", "state/task/owner.json", "state/task/entries.json"):
        path = owned / "ai-task-recovery" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"task":"archive-recovery"}')
    bundle = tmp_path / "with-recovery.transbridge"
    project.services.project_archive.export_project(str(bundle), project.context)
    with zipfile.ZipFile(bundle) as archive:
        assert not any(name.startswith("assets/ai-task-recovery/") for name in archive.namelist())
    restored_services = services_factory("restored-recovery")
    restored_services.project_archive.import_project(str(bundle), project.context)
    restored_owned = Path(restored_services.root) / "projects" / project.project_ref.identity.encoded
    assert not (restored_owned / "ai-task-recovery").exists()
