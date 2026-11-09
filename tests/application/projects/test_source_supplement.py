"""Legacy source repair candidates must leave both projects untouched."""

import json
from pathlib import Path

import pytest

from tests.application.projects.test_source_update import project  # noqa: F401


@pytest.mark.parametrize("legacy_state", ["missing", "dangling_artifact", "unregistered"])
def test_legacy_source_repair_candidates_are_rejected_without_reading_old_inputs(project, monkeypatch, legacy_state):  # noqa: F811
    services, updates, context, source, path, _ = project
    active = services.project_lifecycle.active
    active_bytes = Path(path).read_bytes()
    legacy_root = source.parent / "legacy"
    legacy_root.mkdir()
    legacy_project = legacy_root / "project.json"
    legacy_variant = legacy_root / "current.json"
    sources = []
    if legacy_state != "unregistered":
        registration = {"location": str(source), "content_state": "missing"}
        if legacy_state == "dangling_artifact":
            registration["source_artifact_id"] = "f" * 64
            registration["content_state"] = "complete"
        sources.append(registration)
    legacy_project.write_text(
        json.dumps({"name": "Incomplete legacy project", "active_variant": "main", "sources": sources}),
        encoding="utf-8",
    )
    legacy_variant.write_text(json.dumps({"translations": {"first": "Saved translation"}}), encoding="utf-8")
    original_contents = {file: file.read_bytes() for file in legacy_root.iterdir()}
    open_file = Path.open

    def deny_source_read(file, *args, **kwargs):
        assert file != source, "rejecting a legacy project must not read a historical source"
        return open_file(file, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_source_read)
    opened = services.current_project_opener.open_path(legacy_project, context)
    assert not opened.is_success and opened.diagnostics[0].code == "PROJECT_FORMAT_UNSUPPORTED"
    prepared = updates.prepare(legacy_project, "legacy-source", source, context)
    assert not prepared.is_success and prepared.diagnostics[0].code == "PROJECT_FORMAT_UNSUPPORTED"
    targets = updates.list_sources(legacy_project, context)
    assert not targets.is_success and targets.diagnostics[0].code == "PROJECT_FORMAT_UNSUPPORTED"

    assert services.project_lifecycle.active is active
    assert Path(path).read_bytes() == active_bytes
    assert {file: file.read_bytes() for file in legacy_root.iterdir()} == original_contents
