from pathlib import Path
import zipfile

from tests.persistence.v2 import test_project_archive as support

exported_project = support.exported_project
services_factory = support.services_factory


def test_export_excludes_unrelated_and_task_files_even_under_the_project(tmp_path, exported_project):
    project = exported_project
    owned = Path(project.services.root) / "projects" / project.project_ref.identity.encoded
    excluded = (
        "notes.txt",
        "logs/run.log",
        "cache/dialogue.json",
        "runtime-v2/translation.json",
        "ai-task-history/run/metadata.json",
        "ai-task-recovery/task.json",
        "ai-proofread-resume/progress.sqlite3",
        "ai-translation-resume/postprocess/task.json",
        "download-results/run/entries.json",
        "legacy-ai/index.json",
        "terminology/staging/current.sqlite3",
        "terminology/artifacts/run.log",
    )
    for relative in excluded:
        path = owned / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"excluded")
    artifact = owned / "terminology" / "artifacts" / "terms.csv"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"original,translation\nOne,First\n")
    bundle = tmp_path / "business.transbridge"

    project.services.project_archive.export_project(str(bundle), project.context)

    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        assert "assets/terminology/artifacts/terms.csv" in names
        assert all("assets/" + relative not in names for relative in excluded)


def test_old_bundle_extraneous_assets_are_ignored_without_blocking_its_project(
    tmp_path, exported_project, services_factory
):
    project = exported_project
    manifest, members = support._bundle_content(project.bundle)
    bundle = support._write_bundle(
        tmp_path / "old-assets.transbridge",
        manifest,
        [*members, ("assets/ai-task-recovery/task.json", b"old task"), ("assets/logs/run.log", b"old log")],
    )
    target = services_factory("clean-import")

    path = target.project_archive.import_project(str(bundle), project.context)

    assert target.current_project_opener.prepare_path(path, project.context).is_success
    owned = Path(target.root) / "projects" / project.project_ref.identity.encoded
    assert not (owned / "ai-task-recovery").exists()
    assert not (owned / "logs").exists()
