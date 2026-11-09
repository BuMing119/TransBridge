"""Portable SQLite projects export only declared business assets."""

from pathlib import Path
import zipfile

from tests.persistence.v2 import test_project_archive as support

exported_project = support.exported_project
services_factory = support.services_factory


def test_export_excludes_unrelated_and_task_files_even_under_the_project(tmp_path, exported_project, services_factory):
    project = exported_project
    owned = Path(project.services.projects.path_for(project.project_ref)).parent
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
        "terminology/artifacts/terms.csv",
    )
    for relative in excluded:
        path = owned / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    bundle = tmp_path / "business.transbridge"

    project.services.project_archive.export_project(str(bundle), project.context)

    with zipfile.ZipFile(bundle) as archive:
        assert set(archive.namelist()) == {"manifest.json", "project.sqlite3"}
    target = services_factory("clean-import")
    installed = Path(target.project_archive.import_project(str(bundle), project.context))
    assert target.current_project_opener.open_path(installed, project.context).is_success
    assert all(not (installed.parent / relative).exists() for relative in excluded)
    assert all((owned / relative).read_text(encoding="utf-8") == relative for relative in excluded)
