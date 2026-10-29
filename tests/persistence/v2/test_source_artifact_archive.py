from dataclasses import replace
from pathlib import Path
from uuid import uuid4
import zipfile

import pytest

from tests.bsa_fixtures import write_bsa
from tests.contracts.io.test_localized_hydrated_publication import _run
from tests.persistence.v2.test_legacy_project_archive import _project, _write_archive, _xml_source
from tests.persistence.v2.test_project_archive import _bundle_content, _localized_plugin, _write_bundle
from transbridge.application.contracts import RequestContext
from transbridge.application.io import ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWritePreflightService
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer


@pytest.fixture
def services(tmp_path):
    opened = []

    def build(name):
        result = build_persistence_v2_services(
            tmp_path / name, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-10-26T00:00:00+08:00"
        )
        opened.append(result)
        return result

    yield build
    for result in reversed(opened):
        result.close()


def _ok(result):
    assert result.is_success, result.diagnostics
    return result.value


def _source_project(tmp_path, services, *, bsa):
    source = tmp_path / "Portable.esp"
    strings = _localized_plugin(source)
    companions = {path.name: path.read_bytes() for path in strings.glob("Portable_*")}
    if bsa:
        write_bsa(source.with_suffix(".bsa"), {f"strings/{name.lower()}": raw for name, raw in companions.items()})
        for path in strings.glob("Portable_*"):
            path.unlink()
    origin = services("origin")
    context = RequestContext("gui", run_id="saved-source-bundle")
    _ok(
        origin.gui_project_commands.create_project(
            ProjectProvisioningRequest("Saved source", source=ProjectSourceRequest(str(source))), context
        )
    )
    return origin, source, companions, context


@pytest.mark.parametrize("bsa", [False, True])
def test_saved_source_package_survives_missing_original_inputs(tmp_path, services, monkeypatch, bsa):
    origin, source, companions, context = _source_project(tmp_path, services, bsa=bsa)
    expected = origin.project_lifecycle.active.variant.snapshot().entries
    source.unlink()
    if bsa:
        source.with_suffix(".bsa").unlink()
    else:
        for path in (source.parent / "Strings").glob("Portable_*"):
            path.unlink()

    def no_parse(*_args, **_kwargs):
        raise AssertionError("Opening saved source data must not parse a live source")

    monkeypatch.setattr(TranslationIoProjectSourcePreparer, "prepare_source", no_parse)
    bundle = tmp_path / "saved.transbridge"
    origin.project_archive.export_project(str(bundle), context)
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        assert any(name.startswith("source_artifacts/manifests/") for name in names)
        for name, raw in companions.items():
            member = next(item for item in names if item.casefold() == f"sources/0/strings/{name}".casefold())
            assert archive.read(member) == raw
    target = services("target")
    imported = target.project_archive.import_project(str(bundle), context)
    opened = _ok(target.current_project_opener.prepare_path(imported, context))
    assert opened.recovery is None
    _ok(target.current_project_opener.activate(opened, context))
    assert target.project_lifecycle.active.variant.snapshot().entries == expected
    copy_path = target.project_archive.import_project(
        str(bundle), context, copy_on_identity_conflict=True, requested_name="Saved source copy"
    )
    copy = _ok(target.current_project_opener.prepare_path(copy_path, context))
    assert copy.recovery is None
    assert copy.project_ref != opened.project_ref
    assert copy.hydrations[0].reference_location == opened.hydrations[0].reference_location == ""
    assert copy.hydrations[0].source_snapshot.content == opened.hydrations[0].source_snapshot.content
    hydration = opened.hydrations[0]
    output = tmp_path / "written" / "Saved.esp"
    draft = HydratedWriteDraft(
        hydration.source_snapshot,
        hydration.format_id,
        tuple(replace(entry, translation="Saved translation", stage=3) for entry in hydration.entries),
        str(output),
        0,
        context,
    )
    checked = HydratedWritePreflightService().preflight(draft)
    assert checked.ready, checked.checks
    _ok(_run(checked))
    parsed = TranslationIoUseCase().parse(ParseRequest(SourceDescriptor(str(output)), context, hydration.format_id))
    assert parsed.entries[0].original == "Saved translation"


def test_package_artifact_must_be_present_even_if_destination_has_same_artifact(tmp_path, services):
    origin, _source, _companions, context = _source_project(tmp_path, services, bsa=False)
    bundle = tmp_path / "original.transbridge"
    origin.project_archive.export_project(str(bundle), context)
    manifest, members = _bundle_content(bundle)
    missing = _write_bundle(
        tmp_path / "missing.transbridge",
        manifest,
        [(name, raw) for name, raw in members if not name.startswith("source_artifacts/")],
    )
    with pytest.raises(ValueError, match="成果"):
        origin.project_archive.inspect_import(str(missing), context)


def test_package_companion_must_match_saved_artifact(tmp_path, services):
    origin, _source, _companions, context = _source_project(tmp_path, services, bsa=True)
    bundle = tmp_path / "original.transbridge"
    origin.project_archive.export_project(str(bundle), context)
    manifest, members = _bundle_content(bundle)
    damaged = _write_bundle(
        tmp_path / "changed.transbridge",
        manifest,
        [(name, b"changed" if name.endswith(".strings") else raw) for name, raw in members],
    )
    target = services("target")
    with pytest.raises(ValueError, match="不一致"):
        target.project_archive.import_project(str(damaged), context)
    assert not list(Path(target.root, "projects").glob("*.json"))


def test_pure_record_legacy_import_preserves_translation_without_reading_reference(tmp_path, services, monkeypatch):
    source = tmp_path / "legacy.xml"
    descriptor, prepared = _xml_source(source)
    key = prepared.hydration.entries[0].legacy_id
    archive = _write_archive(
        tmp_path / "legacy.transbridge",
        _project(descriptor),
        {
            "主版本/current.json": {
                "variant": "主版本",
                "translations": {key: "Recovered"},
                "labels": {},
                "label_library": {},
            }
        },
    )
    target = services("legacy-target")
    context = RequestContext("gui", run_id="legacy-artifact")

    def no_parse(*_args, **_kwargs):
        pytest.fail("A record-only archive must not parse its external reference")

    monkeypatch.setattr(TranslationIoProjectSourcePreparer, "prepare_source", no_parse)
    path = target.project_archive.import_project(str(archive), context)
    source.unlink()
    result = target.current_project_opener.open_path(path, context)
    assert result.is_success, result.diagnostics
    assert result.value["read_only"]
    assert target.project_lifecycle.active is None
    assert result.value["recovery"].variant.entries[0].translation == "Recovered"


def test_older_portable_bundle_persists_only_its_captured_source_input(tmp_path, services, monkeypatch):
    origin, source, _companions, context = _source_project(tmp_path, services, bsa=False)
    expected = origin.project_lifecycle.active.variant.snapshot().entries
    bundle = tmp_path / "current.transbridge"
    origin.project_archive.export_project(str(bundle), context)
    manifest, members = _bundle_content(bundle)
    for registration in manifest["project"]["data"]["sources"]:
        registration.pop("source_artifact_id")
    older = _write_bundle(
        tmp_path / "older.transbridge",
        manifest,
        [(name, raw) for name, raw in members if not name.startswith("source_artifacts/")],
    )
    source.unlink()
    for path in (source.parent / "Strings").glob("Portable_*"):
        path.unlink()
    target = services("older-target")
    prepare = TranslationIoProjectSourcePreparer.prepare_source
    parsed = []

    def package_input_only(self, request, *args, **kwargs):
        assert Path(request.location).is_relative_to(Path(target.root) / "projects")
        parsed.append(request.location)
        return prepare(self, request, *args, **kwargs)

    monkeypatch.setattr(TranslationIoProjectSourcePreparer, "prepare_source", package_input_only)
    target.project_archive.inspect_import(str(older), context)
    assert parsed == []
    imported = target.project_archive.import_project(str(older), context)
    assert len(parsed) == 1
    registration = target.projects.read_snapshot(origin.project_lifecycle.active.project_ref).envelope.data["sources"][
        0
    ]
    assert registration["source_artifact_id"] and registration["location"] == ""
    for captured in (Path(target.root) / "projects").rglob("*"):
        if captured.is_file() and captured.suffix.casefold() in {".esp", ".strings", ".dlstrings", ".ilstrings"}:
            captured.unlink()
    older.unlink()

    def no_parse(*_args, **_kwargs):
        pytest.fail("Imported content must now use its saved artifact")

    monkeypatch.setattr(TranslationIoProjectSourcePreparer, "prepare_source", no_parse)
    opened = _ok(target.current_project_opener.prepare_path(imported, context))
    assert opened.recovery is None
    _ok(target.current_project_opener.activate(opened, context))
    assert target.project_lifecycle.active.variant.snapshot().entries == expected
